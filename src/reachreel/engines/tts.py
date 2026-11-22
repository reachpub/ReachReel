"""Local text-to-speech: Kokoro-82M via MLX, with a macOS `say` fallback for testing."""
from __future__ import annotations

import hashlib
import logging
import re
import shutil
import wave
from pathlib import Path

from ..core.config import Settings
from ..core.util import run
from ..domain.models import NarrationSegment

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


def _split_for_tts(text: str, limit: int = 70) -> list[str]:
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
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

        model = self._load()
        chunks = _split_for_tts(text)
        if not chunks:
            raise RuntimeError("TTS text is empty")
        audio_parts: list[np.ndarray] = []
        sample_rate = 24000
        for index, chunk in enumerate(chunks):
            results = list(
                model.generate(
                    chunk,
                    voice=self.settings.voice,
                    lang_code="z",
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
    """Development fallback. Chinese voice availability depends on macOS."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def synthesize(self, text: str, out_wav: Path, *, speed: float = 1.0) -> float:
        if shutil.which("say") is None:
            raise RuntimeError("macOS say command not found")
        rate = int(190 * max(0.5, speed))
        tmp = out_wav.with_suffix(".aiff")
        run(
            [
                "say",
                "-v",
                self.settings.say_voice,
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


def synthesize_segments(
    segments: list[NarrationSegment],
    audio_dir: Path,
    settings: Settings,
    *,
    speed: float = 1.0,
    force: bool = False,
) -> None:
    audio_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings)
    for index, segment in enumerate(segments, start=1):
        key = hashlib.sha1(
            f"{settings.tts_engine}|{settings.kokoro_model}|{settings.voice}|{speed:.4f}|{segment.text}".encode()
        ).hexdigest()[:16]
        out = audio_dir / f"seg_{index:03d}_{key}.wav"
        if out.exists() and not force and out.stat().st_size > 1000:
            segment.audio_path = str(out)
            segment.duration = _wav_duration(out)
            segment.tts_speed = speed
            LOG.info("  [%d/%d] TTS cache hit: %.2fs", index, len(segments), segment.duration)
            continue
        LOG.info("  [%d/%d] Synthesizing: %s", index, len(segments), segment.text[:30])
        duration = engine.synthesize(segment.text, out, speed=speed)
        segment.audio_path = str(out)
        segment.duration = duration
        segment.tts_speed = speed
        LOG.info("         done in %.2fs", duration)
