"""Local text-to-speech: Kokoro-82M via MLX, with a macOS `say` fallback for testing."""
from __future__ import annotations

import hashlib
import logging
import re
import shutil
import time
import wave
from pathlib import Path

from ..core.config import Settings
from ..core.util import run
from ..domain.models import NarrationSegment, QualityFlag
from ..languages import (
    LanguageProfile,
    check_tts_dependencies,
    require_tts,
    require_voice_match,
    resolve_tts_language,
)

LOG = logging.getLogger("reachreel.engines.tts")

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


class KokoroEngine:
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
        self._model = load_model(self.settings.kokoro_model)
        return self._model

    def synthesize(self, text: str, out_wav: Path, *, speed: float = 1.0) -> float:
        import numpy as np

        profile = self._profile or self.language()
        self._ensure_g2p(profile)
        model = self._load()
        lang_code = self._lang_code
        voice = self.settings.resolved_voice()
        chunks = _split_for_tts(text, primary_language=profile.primary)
        if not chunks:
            raise RuntimeError("TTS text is empty")
        audio_parts: list[np.ndarray] = []
        sample_rate = 24000
        for index, chunk in enumerate(chunks):
            results = list(
                model.generate(
                    chunk,
                    voice=voice,
                    lang_code=lang_code,
                    speed=speed,
                )
            )
            for result in results:
                samples = getattr(result, "audio", None)
                if samples is None:
                    continue
                sample_rate = int(getattr(result, "sample_rate", sample_rate) or sample_rate)
                audio_parts.append(np.asarray(samples).reshape(-1).astype(np.float32))
            if index < len(chunks) - 1:
                audio_parts.append(np.zeros(int(sample_rate * 0.12), dtype=np.float32))
        if not audio_parts:
            raise RuntimeError("Kokoro returned no audio")
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

        # Convert to 48 kHz / mono PCM used by the mixer.
        run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(raw_wav),
                "-ar",
                "48000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(out_wav),
            ]
        )
        raw_wav.unlink(missing_ok=True)
        return _wav_duration(out_wav)


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
        run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(tmp),
                "-ar",
                "48000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(out_wav),
            ]
        )
        tmp.unlink(missing_ok=True)
        return _wav_duration(out_wav)


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as fh:
        return fh.getnframes() / float(fh.getframerate())


def make_engine(settings: Settings):
    if settings.tts_engine == "say":
        return SayEngine(settings)
    if settings.tts_engine == "kokoro":
        return KokoroEngine(settings)
    raise RuntimeError(f"Unknown TTS engine: {settings.tts_engine} (choose kokoro or say)")


def _cleanup_partial(out_wav: Path) -> None:
    for candidate in (out_wav, out_wav.with_suffix(".raw.wav"), out_wav.with_suffix(".aiff")):
        candidate.unlink(missing_ok=True)


def _synthesize_with_retries(
    engine: KokoroEngine | SayEngine,
    segment: NarrationSegment,
    out_wav: Path,
    *,
    speed: float,
    retries: int,
) -> float:
    attempts = max(1, retries + 1)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
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


def synthesize_segments(
    segments: list[NarrationSegment],
    audio_dir: Path,
    settings: Settings,
    *,
    speed: float = 1.0,
    force: bool = False,
    retries: int = 2,
    shared_dir: Path | None = None,
) -> list[QualityFlag]:
    """Synthesize each segment, retrying and isolating per-segment failures.

    A failed segment leaves ``audio_path`` empty and is recorded in the returned
    quality flags; the remaining segments still get synthesized. If every
    segment fails, the stage raises instead of producing a silent video.
    """
    audio_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings)
    if hasattr(engine, "language"):
        engine.language()
    voice_key = (
        settings.resolved_voice()
        if settings.tts_engine != "say"
        else (settings.voice.strip() or settings.resolved_say_voice())
    )
    flags: list[QualityFlag] = []
    failed = 0
    for index, segment in enumerate(segments, start=1):
        key = hashlib.sha1(
            f"{settings.tts_engine}|{settings.kokoro_model}|{voice_key}|{speed:.4f}|{segment.text}".encode()
        ).hexdigest()[:16]
        out = audio_dir / f"seg_{index:03d}_{key}.wav"
        shared = shared_dir / f"{key}.wav" if shared_dir is not None else None
        if out.exists() and not force and out.stat().st_size > 1000:
            segment.audio_path = str(out)
            segment.duration = _wav_duration(out)
            segment.tts_speed = speed
            LOG.info("  [%d/%d] TTS cache hit: %.2fs", index, len(segments), segment.duration)
            continue
        if shared is not None and shared.exists() and shared.stat().st_size > 1000:
            try:
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(shared, out)
            except OSError as exc:
                LOG.warning("Could not read the shared TTS cache %s: %s", shared, exc)
            else:
                segment.audio_path = str(out)
                segment.duration = _wav_duration(out)
                segment.tts_speed = speed
                LOG.info(
                    "  [%d/%d] TTS shared-cache hit: %.2fs",
                    index,
                    len(segments),
                    segment.duration,
                )
                continue
        LOG.info("  [%d/%d] Synthesizing: %s", index, len(segments), segment.text[:30])
        try:
            duration = _synthesize_with_retries(
                engine,
                segment,
                out,
                speed=speed,
                retries=retries,
            )
        except Exception as exc:  # noqa: BLE001 - isolate per-segment failures
            failed += 1
            _cleanup_partial(out)
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
        segment.tts_speed = speed
        if shared is not None:
            try:
                shared.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(out, shared)
            except OSError as exc:
                LOG.warning("Could not update the shared TTS cache %s: %s", shared, exc)
        LOG.info("         done in %.2fs", duration)

    if segments and failed == len(segments):
        raise RuntimeError(
            f"TTS failed for every segment ({failed}/{len(segments)}). "
            f"Check the engine ({settings.tts_engine}) and voice ({voice_key})."
        )
    return flags
