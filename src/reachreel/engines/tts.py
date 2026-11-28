"""Local text-to-speech: Kokoro-82M and Qwen3-TTS via MLX, with a macOS `say` fallback."""
from __future__ import annotations

import contextlib
import hashlib
import logging
import re
import shutil
import sys
import time
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from ..core.config import Settings
from ..core.util import clamp, ffmpeg_bin, local_hf_model, run, sha1_file
from ..domain.models import NarrationSegment, QualityFlag
from ..languages import (
    LanguageProfile,
    check_tts_dependencies,
    require_tts,
    require_voice_match,
    resolve_tts_language,
    voice_language,
)

LOG = logging.getLogger("reachreel.engines.tts")

SUPPORTED_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus"}

KOKORO_ZH_VOICES = [
    "zf_xiaobei",
    "zf_xiaoni",
    "zf_xiaoxiao",
    "zf_xiaoyi",
    "zm_yunjian",
    "zm_yunxi",
    "zm_yunxia",
    "zm_yunyang",
]

KOKORO_EN_US_VOICES = [
    "af_heart",
    "af_alloy",
    "af_aoede",
    "af_bella",
    "af_jessica",
    "af_kore",
    "af_nicole",
    "af_nova",
    "af_river",
    "af_sarah",
    "af_sky",
    "am_adam",
    "am_echo",
    "am_eric",
    "am_fenrir",
    "am_liam",
    "am_michael",
    "am_onyx",
    "am_puck",
]

KOKORO_EN_GB_VOICES = [
    "bf_alice",
    "bf_emma",
    "bf_isabella",
    "bf_lily",
    "bm_daniel",
    "bm_fable",
    "bm_george",
    "bm_lewis",
]

QWEN3_ZH_VOICES = [
    "Serena",
    "Vivian",
    "Uncle_Fu",
    "Dylan",
    "Eric",
]

QWEN3_EN_VOICES = [
    "Ryan",
    "Aiden",
]

# Concise voice traits for discovery (`reachreel voices` and `--json`).
VOICE_METADATA: dict[str, dict[str, str]] = {
    # Kokoro zh-CN
    "zf_xiaoxiao": {"gender": "female", "tone": "clear, natural (default)"},
    "zf_xiaobei": {"gender": "female", "tone": "bright, energetic"},
    "zf_xiaoni": {"gender": "female", "tone": "warm, gentle"},
    "zf_xiaoyi": {"gender": "female", "tone": "calm, narrative"},
    "zm_yunjian": {"gender": "male", "tone": "steady, commentary"},
    "zm_yunxi": {"gender": "male", "tone": "youthful, casual"},
    "zm_yunxia": {"gender": "male", "tone": "light, lively"},
    "zm_yunyang": {"gender": "male", "tone": "deep, documentary"},
    # Kokoro en-US
    "af_heart": {"gender": "female", "tone": "warm, expressive (default)"},
    "af_bella": {"gender": "female", "tone": "friendly, smooth"},
    "af_nicole": {"gender": "female", "tone": "soft, intimate"},
    "af_nova": {"gender": "female", "tone": "crisp, modern"},
    "af_sarah": {"gender": "female", "tone": "clear, balanced"},
    "af_sky": {"gender": "female", "tone": "bright, casual"},
    "am_adam": {"gender": "male", "tone": "deep, documentary"},
    "am_michael": {"gender": "male", "tone": "steady, conversational"},
    "am_liam": {"gender": "male", "tone": "natural, relaxed"},
    "am_onyx": {"gender": "male", "tone": "resonant, dramatic"},
    # Kokoro en-GB
    "bf_emma": {"gender": "female", "tone": "clear, poised (default)"},
    "bf_alice": {"gender": "female", "tone": "warm, narrative"},
    "bf_lily": {"gender": "female", "tone": "gentle, storytelling"},
    "bm_daniel": {"gender": "male", "tone": "measured, documentary"},
    "bm_george": {"gender": "male", "tone": "classic, calm"},
    "bm_lewis": {"gender": "male", "tone": "friendly, conversational"},
    # Qwen3-TTS
    "Serena": {"gender": "female", "tone": "warm, natural (default zh)"},
    "Vivian": {"gender": "female", "tone": "bright, expressive"},
    "Uncle_Fu": {"gender": "male", "tone": "mature, mellow storyteller"},
    "Dylan": {"gender": "male", "tone": "relaxed, Beijing casual"},
    "Eric": {"gender": "male", "tone": "lively, Sichuan casual"},
    "Ryan": {"gender": "male", "tone": "clear, energetic (default en)"},
    "Aiden": {"gender": "male", "tone": "sunny, conversational"},
}


def voice_metadata(name: str) -> dict[str, str]:
    """Return gender and tone hints for a built-in voice."""
    if name in VOICE_METADATA:
        return dict(VOICE_METADATA[name])
    lower = name.lower()
    if len(lower) >= 3 and lower[2] == "_" and lower[1] in {"f", "m"}:
        return {"gender": "female" if lower[1] == "f" else "male", "tone": "built-in"}
    return {"gender": "unknown", "tone": "built-in"}


def _pack_whitespace_pieces(pieces: list[str], limit: int) -> list[str]:
    """Pack whitespace-separated pieces into chunks up to ``limit`` characters."""
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        candidate = f"{current} {piece}" if current else piece
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(piece) <= limit:
            current = piece
            continue
        # Clause or sentence is still too long: wrap by words without splitting them.
        for word in piece.split():
            word_candidate = f"{current} {word}" if current else word
            if len(word_candidate) <= limit:
                current = word_candidate
                continue
            if current:
                chunks.append(current)
                current = ""
            while len(word) > limit:
                chunks.append(word[:limit])
                word = word[limit:]
            current = word
    if current:
        chunks.append(current)
    return chunks


def _split_for_tts(
    text: str,
    limit: int | None = None,
    *,
    primary_language: str = "zh",
) -> list[str]:
    text = text.strip()
    if not text:
        return []
    is_cjk = primary_language in {"zh", "ja", "ko"} and " " not in text
    if limit is None:
        limit = 70 if is_cjk else 250
    if len(text) <= limit:
        return [text]
    if not is_cjk:
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
        expanded: list[str] = []
        for sentence in sentences:
            if len(sentence) <= limit:
                expanded.append(sentence)
            else:
                expanded.extend(
                    c.strip() for c in re.split(r"(?<=[;,—:])\s+", sentence) if c.strip()
                )
        return _pack_whitespace_pieces(expanded, limit)
    pieces = re.split(r"(?<=[。！？；，、])", text)
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if not piece:
            continue
        if len(current) + len(piece) <= limit:
            current += piece
        else:
            if current:
                chunks.append(current)
            current = piece
    if current:
        chunks.append(current)
    # Hard split anything still too long.
    result: list[str] = []
    for chunk in chunks:
        while len(chunk) > limit:
            result.append(chunk[:limit])
            chunk = chunk[limit:]
        if chunk:
            result.append(chunk)
    return result


def _atempo_filters(factor: float) -> list[str]:
    """Express an arbitrary speed factor using FFmpeg ``atempo`` stages in [0.5, 2.0]."""
    factor = max(0.25, float(factor))
    stages: list[str] = []
    while factor > 2.0:
        stages.append("atempo=2.0")
        factor /= 2.0
    while factor < 0.5:
        stages.append("atempo=0.5")
        factor /= 0.5
    if abs(factor - 1.0) > 0.002:
        stages.append(f"atempo={factor:.5f}")
    return stages


def _tts_audio_filter_chain(
    *,
    speed: float = 1.0,
    pitch: float = 0.0,
    volume: float = 1.0,
    base_sample_rate: int = 24000,
) -> str | None:
    """Build an optional FFmpeg ``-af`` chain for speed, pitch shift, and gain."""
    filters: list[str] = []
    tempo = max(0.5, float(speed))
    semitones = clamp(float(pitch), -12.0, 12.0)
    if abs(semitones) > 0.01:
        pitch_factor = 2.0 ** (semitones / 12.0)
        shifted_rate = max(8000, round(base_sample_rate * pitch_factor))
        filters.append(f"aresample={base_sample_rate}")
        filters.append(f"asetrate={shifted_rate}")
        filters.append("aresample=48000")
        tempo /= pitch_factor
    filters.extend(_atempo_filters(tempo))
    vol = clamp(float(volume), 0.0, 4.0)
    if abs(vol - 1.0) > 0.005:
        filters.append(f"volume={vol:.4f}")
    return ",".join(filters) if filters else None


def _finalize_tts_wav(
    raw_path: Path,
    out_wav: Path,
    *,
    speed: float = 1.0,
    pitch: float = 0.0,
    volume: float = 1.0,
    base_sample_rate: int = 24000,
) -> float:
    """Normalize a raw TTS output file to 48 kHz mono PCM WAV with optional audio tuning."""
    cmd: list[str] = [
        ffmpeg_bin(),
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(raw_path),
    ]
    filter_chain = _tts_audio_filter_chain(
        speed=speed,
        pitch=pitch,
        volume=volume,
        base_sample_rate=base_sample_rate,
    )
    if filter_chain:
        cmd.extend(["-af", filter_chain])
    cmd.extend(
        [
            "-ar",
            "48000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(out_wav),
        ]
    )
    try:
        run(cmd)
    finally:
        raw_path.unlink(missing_ok=True)
    return _wav_duration(out_wav)


def _write_pcm_and_finalize(
    audio_parts: list[Any],
    *,
    sample_rate: int,
    out_wav: Path,
    speed: float = 1.0,
    pitch: float = 0.0,
    volume: float = 1.0,
) -> float:
    import numpy as np

    if not audio_parts:
        raise RuntimeError("TTS engine returned no audio")
    audio = np.concatenate(audio_parts)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 0:
        audio = audio / max(1.0, peak / 0.98)
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")

    raw_wav = out_wav.with_suffix(".raw.wav")
    with wave.open(str(raw_wav), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(sample_rate)
        fh.writeframes(pcm.tobytes())

    return _finalize_tts_wav(
        raw_wav,
        out_wav,
        speed=speed,
        pitch=pitch,
        volume=volume,
        base_sample_rate=sample_rate,
    )


def validate_ref_audio(path_text: str) -> Path:
    """Validate and resolve a user-provided reference audio path for voice cloning."""
    raw = (path_text or "").strip()
    if not raw or "\x00" in raw:
        raise ValueError("Invalid reference audio path")
    candidate = Path(raw).expanduser()
    resolved = candidate.resolve(strict=False)
    if not resolved.exists() or not resolved.is_file():
        raise FileNotFoundError(f"Reference audio file not found: {resolved}")
    if resolved.suffix.lower() not in SUPPORTED_AUDIO_EXTS:
        allowed = ", ".join(sorted(SUPPORTED_AUDIO_EXTS))
        raise ValueError(
            f"Unsupported reference audio format {resolved.suffix!r} (expected one of {allowed})"
        )
    return resolved


class KokoroEngine:
    supports_segment_overrides = True

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._model = None
        self._profile: LanguageProfile | None = None
        self._lang_code: str | None = None

    def language(self) -> LanguageProfile:
        """Resolve and validate the pronunciation language for this run."""
        voice = self.settings.resolved_voice()
        profile = resolve_tts_language(
            tts_language=self.settings.tts_language,
            content_language=self.settings.content_language,
            voice=voice,
        )
        # Status first: "planned" is more useful than "wrong voice".
        self._lang_code = require_tts(profile, "kokoro")
        require_voice_match(profile, voice)
        self._profile = profile
        return profile

    def _ensure_g2p(self, profile: LanguageProfile) -> None:
        ok, detail = check_tts_dependencies(profile, "kokoro")
        if not ok:
            raise RuntimeError(
                f"Kokoro {profile.tag} G2P dependencies are missing: {detail}"
            )

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            from mlx_audio.tts.utils import load_model
        except ImportError as exc:
            raise RuntimeError(
                "mlx-audio is not installed. Run: uv pip install mlx-audio 'misaki[zh]'"
            ) from exc
        LOG.info("Loading Kokoro model %s ...", self.settings.kokoro_model)
        resolved = local_hf_model(self.settings.kokoro_model)
        with contextlib.redirect_stdout(sys.stderr):
            self._model = load_model(resolved)
        return self._model

    def synthesize(
        self,
        text: str,
        out_wav: Path,
        *,
        speed: float = 1.0,
        voice: str | None = None,
        instruct: str | None = None,
    ) -> float:
        import numpy as np

        del instruct  # Kokoro does not consume natural-language instructions.
        profile = self._profile or self.language()
        self._ensure_g2p(profile)
        model = self._load()
        lang_code = self._lang_code
        active_voice = (voice or self.settings.resolved_voice()).strip()
        require_voice_match(profile, active_voice)
        chunks = _split_for_tts(text, primary_language=profile.primary)
        if not chunks:
            raise RuntimeError("TTS text is empty")
        chunk_pause = clamp(float(self.settings.tts_chunk_pause_seconds), 0.0, 2.0)
        safe_speed = clamp(float(speed), 0.5, 2.0)
        audio_parts: list[np.ndarray] = []
        sample_rate = 24000
        for index, chunk in enumerate(chunks):
            with contextlib.redirect_stdout(sys.stderr):
                results = list(
                    model.generate(
                        chunk,
                        voice=active_voice,
                        lang_code=lang_code,
                        speed=safe_speed,
                    )
                )
            for result in results:
                samples = getattr(result, "audio", None)
                if samples is None:
                    continue
                sample_rate = int(getattr(result, "sample_rate", sample_rate) or sample_rate)
                audio_parts.append(np.asarray(samples).reshape(-1).astype(np.float32))
            if index < len(chunks) - 1 and chunk_pause > 0.0:
                audio_parts.append(np.zeros(int(sample_rate * chunk_pause), dtype=np.float32))
        return _write_pcm_and_finalize(
            audio_parts,
            sample_rate=sample_rate,
            out_wav=out_wav,
            speed=1.0,  # Kokoro handles speed natively.
            pitch=self.settings.tts_pitch,
            volume=self.settings.tts_volume,
        )


class Qwen3TTSEngine:
    """Local expressive TTS via mlx-audio's Qwen3-TTS (CustomVoice, VoiceDesign, Base)."""

    supports_segment_overrides = True

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._model = None
        self._profile: LanguageProfile | None = None
        self._lang_code: str | None = None

    def language(self) -> LanguageProfile:
        voice = self.settings.resolved_voice()
        profile = resolve_tts_language(
            tts_language=self.settings.tts_language,
            content_language=self.settings.content_language,
            voice=voice,
        )
        self._lang_code = require_tts(profile, "qwen3")
        if voice and voice_language(voice) is not None:
            raise ValueError(
                f"voice {voice!r} is a Kokoro voice; for Qwen3-TTS choose a speaker "
                f"such as {', '.join(QWEN3_ZH_VOICES + QWEN3_EN_VOICES)} or leave voice empty"
            )
        self._profile = profile
        return profile

    def _ensure_deps(self, profile: LanguageProfile) -> None:
        ok, detail = check_tts_dependencies(profile, "qwen3")
        if not ok:
            raise RuntimeError(f"Qwen3-TTS dependencies are missing: {detail}")

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            from mlx_audio.tts.utils import load_model
        except ImportError as exc:
            raise RuntimeError(
                "mlx-audio is not installed. Run: uv pip install 'mlx-audio>=0.6.7'"
            ) from exc
        model_id = self.settings.resolved_qwen3_model()
        LOG.info("Loading Qwen3-TTS model %s ...", model_id)
        resolved = local_hf_model(model_id)
        with contextlib.redirect_stdout(sys.stderr):
            self._model = load_model(resolved)
        return self._model

    def synthesize(
        self,
        text: str,
        out_wav: Path,
        *,
        speed: float = 1.0,
        voice: str | None = None,
        instruct: str | None = None,
    ) -> float:
        import numpy as np

        profile = self._profile or self.language()
        self._ensure_deps(profile)
        model = self._load()
        lang_code = self._lang_code or "auto"
        tts_model_type = getattr(getattr(model, "config", None), "tts_model_type", "base")

        active_voice = (voice if voice is not None else self.settings.resolved_voice()).strip()
        active_instruct = (
            instruct if instruct is not None else self.settings.tts_instruct
        ).strip()

        kwargs: dict[str, Any] = {"lang_code": lang_code}
        if tts_model_type == "voice_design":
            if not active_instruct:
                raise RuntimeError(
                    "Qwen3 VoiceDesign model requires tts_instruct (--tts-instruct) "
                    "describing the desired voice"
                )
            kwargs["instruct"] = active_instruct
        elif tts_model_type == "custom_voice":
            if not active_voice:
                active_voice = (
                    profile.default_qwen3_voice
                    or ("Serena" if profile.primary == "zh" else "Ryan")
                )
            if voice_language(active_voice) is not None:
                raise ValueError(
                    f"voice {active_voice!r} is a Kokoro voice; choose a Qwen3 speaker "
                    f"such as {', '.join(QWEN3_ZH_VOICES + QWEN3_EN_VOICES)}"
                )
            supported = getattr(model, "supported_speakers", None) or []
            if supported and active_voice.lower() not in [s.lower() for s in supported]:
                raise ValueError(
                    f"Speaker {active_voice!r} is not supported by {self.settings.resolved_qwen3_model()}. "
                    f"Available: {', '.join(supported)}"
                )
            kwargs["voice"] = active_voice
            if active_instruct:
                kwargs["instruct"] = active_instruct
        else:
            ref_raw = self.settings.tts_ref_audio.strip()
            if not ref_raw:
                raise RuntimeError(
                    "Qwen3 Base model requires tts_ref_audio (--tts-ref-audio) for voice cloning; "
                    "or use a CustomVoice model such as mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-6bit"
                )
            ref_path = validate_ref_audio(ref_raw)
            kwargs["ref_audio"] = str(ref_path)
            if self.settings.tts_ref_text.strip():
                kwargs["ref_text"] = self.settings.tts_ref_text.strip()

        if self.settings.tts_temperature > 0:
            kwargs["temperature"] = float(self.settings.tts_temperature)
        if self.settings.tts_top_p > 0:
            kwargs["top_p"] = float(self.settings.tts_top_p)
        if self.settings.tts_top_k > 0:
            kwargs["top_k"] = int(self.settings.tts_top_k)
        if self.settings.tts_repetition_penalty > 0:
            kwargs["repetition_penalty"] = float(self.settings.tts_repetition_penalty)

        chunk_limit = 160 if profile.primary in {"zh", "ja", "ko"} else 350
        chunks = _split_for_tts(text, limit=chunk_limit, primary_language=profile.primary)
        if not chunks:
            raise RuntimeError("TTS text is empty")

        if self.settings.tts_seed >= 0:
            import mlx.core as mx

            mx.random.seed(int(self.settings.tts_seed))

        chunk_pause = clamp(float(self.settings.tts_chunk_pause_seconds), 0.0, 2.0)
        audio_parts: list[np.ndarray] = []
        sample_rate = 24000
        for index, chunk in enumerate(chunks):
            with contextlib.redirect_stdout(sys.stderr):
                results = list(model.generate(chunk, **kwargs))
            for result in results:
                samples = getattr(result, "audio", None)
                if samples is None:
                    continue
                sample_rate = int(getattr(result, "sample_rate", sample_rate) or sample_rate)
                audio_parts.append(np.asarray(samples).reshape(-1).astype(np.float32))
            if index < len(chunks) - 1 and chunk_pause > 0.0:
                audio_parts.append(np.zeros(int(sample_rate * chunk_pause), dtype=np.float32))

        return _write_pcm_and_finalize(
            audio_parts,
            sample_rate=sample_rate,
            out_wav=out_wav,
            speed=speed,  # Qwen3-TTS has no native speed control; adjust via FFmpeg atempo.
            pitch=self.settings.tts_pitch,
            volume=self.settings.tts_volume,
        )


class SayEngine:
    """Development fallback on macOS. Voices depend on what the system has.

    ``say`` can speak languages that Kokoro cannot yet, so it accepts planned
    profiles but says so loudly instead of pretending the pair is validated.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._warned = False

    def language(self) -> LanguageProfile:
        profile = resolve_tts_language(
            tts_language=self.settings.tts_language,
            content_language=self.settings.content_language,
            voice=self.settings.resolved_say_voice(),
        )
        if profile.status != "supported" and not self._warned:
            LOG.warning(
                "say is a debug engine: %s is not a validated voiceover language "
                "(see docs/en/LANGUAGES.md)",
                profile.tag,
            )
            self._warned = True
        return profile

    def synthesize(self, text: str, out_wav: Path, *, speed: float = 1.0) -> float:
        if shutil.which("say") is None:
            raise RuntimeError("macOS say command not found")
        rate = int(190 * max(0.5, speed))
        tmp = out_wav.with_suffix(".aiff")
        run(
            [
                "say",
                "-v",
                self.settings.resolved_say_voice(),
                "-r",
                str(rate),
                "-o",
                str(tmp),
                text,
            ]
        )
        return _finalize_tts_wav(
            tmp,
            out_wav,
            speed=1.0,  # `say` applies `-r` rate natively.
            pitch=self.settings.tts_pitch,
            volume=self.settings.tts_volume,
        )


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as fh:
        frames = fh.getnframes()
        rate = fh.getframerate()
        if frames <= 0 or rate <= 0 or fh.getsampwidth() != 2:
            raise wave.Error("Expected non-empty 16-bit PCM audio")
        # The header can advertise frames that were never written. Check the
        # last frame without reading the entire recording into memory.
        fh.setpos(frames - 1)
        if len(fh.readframes(1)) != 2 * fh.getnchannels():
            raise wave.Error("WAV audio data is truncated")
        return frames / float(rate)


def _copy_wav(source: Path, destination: Path) -> float:
    """Publish a complete, validated WAV with a rename on the destination filesystem."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".tts-cache-", dir=destination.parent) as tmp:
        staged = Path(tmp) / destination.name
        shutil.copy2(source, staged)
        duration = _wav_duration(staged)
        staged.replace(destination)
    return duration


def _cached_wav_duration(path: Path, *, copy_to: Path | None = None) -> float | None:
    try:
        return _wav_duration(path) if copy_to is None else _copy_wav(path, copy_to)
    except FileNotFoundError:
        return None
    except (OSError, EOFError, wave.Error) as exc:
        LOG.warning("Ignoring unusable TTS cache entry %s: %s", path, exc)
        return None


def make_engine(settings: Settings):
    if settings.tts_engine == "say":
        return SayEngine(settings)
    if settings.tts_engine == "kokoro":
        return KokoroEngine(settings)
    if settings.tts_engine == "qwen3":
        return Qwen3TTSEngine(settings)
    raise RuntimeError(
        f"Unknown TTS engine: {settings.tts_engine} (choose kokoro, qwen3, or say)"
    )


def tts_cache_key(
    settings: Settings,
    text: str,
    *,
    speed: float,
    voice: str,
    instruct: str = "",
) -> str:
    """Compute a deterministic cache key covering all active synthesis parameters."""
    model_id = (
        settings.resolved_qwen3_model()
        if settings.tts_engine == "qwen3"
        else settings.kokoro_model
    )
    parts = [
        f"{settings.tts_engine}|{model_id}|{voice}|{speed:.4f}|{text}",
    ]
    extras: list[str] = []
    if settings.tts_engine == "qwen3":
        extras.append(f"lang={settings.tts_language or settings.content_language}")
    if instruct.strip():
        extras.append(f"instruct={instruct.strip()}")
    if settings.tts_ref_audio.strip():
        ref_raw = settings.tts_ref_audio.strip()
        ref_path = Path(ref_raw).expanduser()
        ref_sig = sha1_file(ref_path)[:16] if ref_path.is_file() else ref_raw
        extras.append(f"ref={ref_sig}")
    if settings.tts_ref_text.strip():
        extras.append(f"ref_text={settings.tts_ref_text.strip()}")
    if settings.tts_temperature > 0:
        extras.append(f"temp={settings.tts_temperature:.4f}")
    if settings.tts_top_p > 0:
        extras.append(f"top_p={settings.tts_top_p:.4f}")
    if settings.tts_top_k > 0:
        extras.append(f"top_k={int(settings.tts_top_k)}")
    if settings.tts_repetition_penalty > 0:
        extras.append(f"rep={settings.tts_repetition_penalty:.4f}")
    if settings.tts_seed >= 0:
        extras.append(f"seed={int(settings.tts_seed)}")
    if abs(settings.tts_chunk_pause_seconds - 0.12) > 1e-6:
        extras.append(f"pause={settings.tts_chunk_pause_seconds:.4f}")
    if abs(settings.tts_pitch) > 1e-6:
        extras.append(f"pitch={settings.tts_pitch:.4f}")
    if abs(settings.tts_volume - 1.0) > 1e-6:
        extras.append(f"vol={settings.tts_volume:.4f}")
    if extras:
        parts.extend(extras)
    payload = "|".join(parts)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _synthesize_with_retries(
    engine: KokoroEngine | Qwen3TTSEngine | SayEngine,
    segment: NarrationSegment,
    out_wav: Path,
    *,
    speed: float,
    voice: str | None = None,
    instruct: str | None = None,
    retries: int,
) -> float:
    attempts = max(1, retries + 1)
    last_error: Exception | None = None
    supports_overrides = bool(getattr(engine, "supports_segment_overrides", False))
    for attempt in range(1, attempts + 1):
        try:
            if supports_overrides:
                return engine.synthesize(
                    segment.text,
                    out_wav,
                    speed=speed,
                    voice=voice,
                    instruct=instruct,
                )
            return engine.synthesize(segment.text, out_wav, speed=speed)
        except Exception as exc:  # noqa: BLE001 - engine-specific failures vary
            last_error = exc
            if attempt < attempts:
                wait = min(2**attempt, 5)
                LOG.warning(
                    "TTS attempt %d/%d failed for segment %d; retrying in %ss: %s",
                    attempt,
                    attempts,
                    segment.id,
                    wait,
                    exc,
                )
                time.sleep(wait)
    assert last_error is not None
    raise last_error


def _resolve_audio_override(audio_override: str, base_dir: Path) -> Path:
    """Resolve a segment audio override inside ``base_dir`` without escaping it."""
    raw = Path(audio_override)
    candidate = raw.resolve() if raw.is_absolute() else (base_dir / raw).resolve()
    resolved_base = base_dir.resolve()
    if not candidate.is_relative_to(resolved_base):
        raise RuntimeError(
            f"Segment audio_override points outside the work directory: {audio_override}"
        )
    if not candidate.is_file():
        raise RuntimeError(f"Segment audio_override file not found: {candidate}")
    return candidate


def synthesize_segments(
    segments: list[NarrationSegment],
    audio_dir: Path,
    settings: Settings,
    *,
    speed: float = 1.0,
    force: bool = False,
    retries: int = 2,
    shared_dir: Path | None = None,
    workdir: Path | None = None,
    start_index: int = 1,
) -> list[QualityFlag]:
    """Synthesize each segment, retrying and isolating per-segment failures.

    Disabled segments are skipped. Segments with ``audio_override`` reuse their
    imported WAV directly without calling the TTS engine. A failed segment
    leaves ``audio_path`` empty and is recorded in the returned quality flags;
    the remaining segments still get synthesized. If every active segment that
    requires synthesis fails, the stage raises instead of producing a silent
    video.
    """
    audio_dir.mkdir(parents=True, exist_ok=True)
    base_dir = (workdir or audio_dir.parent).resolve()
    engine: KokoroEngine | Qwen3TTSEngine | SayEngine | None = None
    voice_key = (
        settings.resolved_voice()
        if settings.tts_engine != "say"
        else (settings.voice.strip() or settings.resolved_say_voice())
    )
    flags: list[QualityFlag] = []
    attempted = 0
    failed = 0
    for index, segment in enumerate(segments, start=start_index):
        if not segment.enabled:
            segment.audio_path = ""
            segment.duration = 0.0
            segment.start = 0.0
            segment.end = 0.0
            LOG.info("  [%d/%d] Segment %d disabled; skipping TTS", index, len(segments), segment.id)
            continue
        if segment.audio_override:
            override_path = _resolve_audio_override(segment.audio_override, base_dir)
            override_duration = _cached_wav_duration(override_path)
            if override_duration is None:
                raise RuntimeError(
                    f"Segment {segment.id} audio_override is not a readable WAV file: {override_path}"
                )
            segment.audio_path = str(override_path)
            segment.duration = override_duration
            segment.tts_speed = 1.0
            LOG.info(
                "  [%d/%d] Using imported audio for segment %d: %.2fs (%s)",
                index,
                len(segments),
                segment.id,
                override_duration,
                override_path.name,
            )
            continue
        attempted += 1
        seg_speed = segment.speed if segment.speed > 0 else speed
        seg_voice = segment.voice.strip() or voice_key
        seg_instruct = segment.instruct.strip() or settings.tts_instruct.strip()
        key = tts_cache_key(
            settings,
            segment.text,
            speed=seg_speed,
            voice=seg_voice,
            instruct=seg_instruct,
        )
        out = audio_dir / f"seg_{index:03d}_{key}.wav"
        shared = shared_dir / f"{key}.wav" if shared_dir is not None else None
        if not force:
            duration = _cached_wav_duration(out)
            cache_name = "cache"
            if duration is None and shared is not None:
                duration = _cached_wav_duration(shared, copy_to=out)
                cache_name = "shared-cache"
            if duration is not None:
                segment.audio_path = str(out)
                segment.duration = duration
                segment.tts_speed = seg_speed
                LOG.info("  [%d/%d] TTS %s hit: %.2fs", index, len(segments), cache_name, duration)
                continue
        if engine is None:
            engine = make_engine(settings)
            if hasattr(engine, "language"):
                engine.language()
        LOG.info("  [%d/%d] Synthesizing: %s", index, len(segments), segment.text[:30])
        try:
            with TemporaryDirectory(prefix=".tts-", dir=audio_dir) as tmp:
                staged = Path(tmp) / out.name
                duration = _synthesize_with_retries(
                    engine,
                    segment,
                    staged,
                    speed=seg_speed,
                    voice=seg_voice or None,
                    instruct=seg_instruct,
                    retries=retries,
                )
                staged.replace(out)
        except Exception as exc:  # noqa: BLE001 - isolate per-segment failures
            failed += 1
            segment.audio_path = ""
            segment.duration = 0.0
            flags.append(
                QualityFlag(
                    code="tts.segment_failed",
                    params={
                        "id": segment.id,
                        "attempts": max(1, retries + 1),
                        "error": str(exc)[:200],
                    },
                    severity="error",
                )
            )
            LOG.error(
                "TTS failed for segment %d after %d attempts; skipping it: %s",
                segment.id,
                max(1, retries + 1),
                exc,
            )
            continue
        segment.audio_path = str(out)
        segment.duration = duration
        segment.tts_speed = seg_speed
        if shared is not None:
            try:
                _copy_wav(out, shared)
            except (OSError, EOFError, wave.Error) as exc:
                LOG.warning("Could not update the shared TTS cache %s: %s", shared, exc)
        LOG.info("         done in %.2fs", duration)

    if attempted and failed == attempted:
        raise RuntimeError(
            f"TTS failed for every segment ({failed}/{attempted}). "
            f"Check the engine ({settings.tts_engine}) and voice ({voice_key})."
        )
    return flags


def preview_voice(
    voice: str,
    settings: Settings,
    *,
    text: str | None = None,
    out_wav: Path,
    explicit_engine: bool = False,
) -> float:
    """Synthesize a short sample clip for auditioning a voice."""
    from ..prompts import text as prompt_text

    qwen3_zh = {name.lower(): name for name in QWEN3_ZH_VOICES}
    qwen3_en = {name.lower(): name for name in QWEN3_EN_VOICES}
    resolved_voice = voice.strip()
    lower_voice = resolved_voice.lower()
    if lower_voice in qwen3_zh:
        resolved_voice = qwen3_zh[lower_voice]
    elif lower_voice in qwen3_en:
        resolved_voice = qwen3_en[lower_voice]

    preview_settings = Settings(
        content_language=settings.content_language,
        tts_engine=settings.tts_engine,
        tts_language=settings.tts_language,
        kokoro_model=settings.kokoro_model,
        qwen3_model=settings.qwen3_model,
        voice=resolved_voice,
        tts_speed=settings.tts_speed,
        tts_instruct=settings.tts_instruct,
        tts_pitch=settings.tts_pitch,
        tts_volume=settings.tts_volume,
        say_voice=resolved_voice if settings.tts_engine == "say" else settings.say_voice,
    )
    inferred = voice_language(resolved_voice)
    if inferred:
        if not explicit_engine:
            preview_settings.tts_engine = "kokoro"
        if not preview_settings.tts_language:
            preview_settings.tts_language = inferred
            preview_settings.content_language = inferred.split("-", 1)[0]
    elif lower_voice in qwen3_zh:
        if not explicit_engine:
            preview_settings.tts_engine = "qwen3"
        preview_settings.content_language = "zh"
    elif lower_voice in qwen3_en:
        if not explicit_engine:
            preview_settings.tts_engine = "qwen3"
        preview_settings.content_language = "en"

    sample_text = (text or "").strip()
    if not sample_text:
        lang = preview_settings.content_language
        fallback = prompt_text(lang, "script.fallback_intro")
        sample_text = (
            fallback
            if fallback and fallback != "script.fallback_intro"
            else "Hello, this is a ReachReel voice preview."
        )
    engine = make_engine(preview_settings)
    if hasattr(engine, "language"):
        engine.language()
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    return engine.synthesize(
        sample_text,
        out_wav,
        speed=preview_settings.tts_speed,
        voice=resolved_voice,
        instruct=preview_settings.tts_instruct,
    )
