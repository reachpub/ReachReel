"""Place narration segments on the video timeline and build the narration track."""
from __future__ import annotations

import logging
import wave
from pathlib import Path

from ..core.config import Settings
from ..core.util import atomic_write_json, clamp, run
from .models import NarrationSegment, Scene

LOG = logging.getLogger("reachreel.domain.timeline")


def _scene_starts(scenes: list[Scene]) -> dict[int, float]:
    return {scene.id: scene.start for scene in scenes}


def _place(
    segments: list[NarrationSegment],
    starts: dict[int, float],
    *,
    gap: float,
    start_offset: float,
) -> None:
    cursor = start_offset
    for segment in segments:
        anchor = min((starts.get(sid, start_offset) for sid in segment.scene_ids), default=start_offset)
        segment.start = round(max(anchor, cursor), 3)
        segment.end = round(segment.start + segment.duration, 3)
        cursor = segment.end + gap


def _apply_atempo(segment: NarrationSegment, factor: float) -> None:
    if factor <= 1.001 or not segment.audio_path:
        return
    src = Path(segment.audio_path)
    dst = src.with_name(src.stem + f"_fit{factor:.3f}.wav")
    if not dst.exists():
        run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(src),
                "-filter:a",
                f"atempo={factor:.5f}",
                "-ar",
                "48000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(dst),
            ]
        )
    segment.audio_path = str(dst)
    segment.duration = _wav_duration(dst)
    segment.tts_speed = round(segment.tts_speed * factor, 4)


def place_segments(
    segments: list[NarrationSegment],
    scenes: list[Scene],
    video_duration: float,
    settings: Settings,
) -> list[NarrationSegment]:
    if not segments:
        return []
    starts = _scene_starts(scenes)
    _place(
        segments,
        starts,
        gap=settings.min_gap_seconds,
        start_offset=settings.start_offset,
    )
    available = max(1.0, video_duration - settings.tail_padding - settings.start_offset)
    needed = segments[-1].end - settings.start_offset
    factor = max(1.0, needed / available)
    if factor > 1.02:
        factor = clamp(factor, 1.0, settings.max_speedup)
        LOG.info(
            "Narration exceeds the target by %.0f%%; applying %.2fx speed-up to fit the video",
            (factor - 1) * 100,
            factor,
        )
        for segment in segments:
            _apply_atempo(segment, factor)
        _place(
            segments,
            starts,
            gap=settings.min_gap_seconds,
            start_offset=settings.start_offset,
        )
    if segments[-1].end > video_duration:
        LOG.warning(
            "Narration ends at %.1fs, %.1fs after the video; shorten the script or raise --max-speedup",
            segments[-1].end,
            video_duration,
        )
    for segment in segments:
        LOG.info(
            "  segment %d: %5.1f-%5.1fs  %s",
            segment.id,
            segment.start,
            segment.end,
            segment.text[:34],
        )
    return segments


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as fh:
        return fh.getnframes() / float(fh.getframerate())


def _read_wav(path: Path) -> tuple[list[int], int, int]:
    with wave.open(str(path), "rb") as fh:
        channels = fh.getnchannels()
        width = fh.getsampwidth()
        rate = fh.getframerate()
        frames = fh.readframes(fh.getnframes())
    if width != 2:
        raise RuntimeError(f"Only 16-bit WAV is supported: {path}")
    import array

    samples = array.array("h")
    samples.frombytes(frames)
    return samples.tolist(), channels, rate


def build_narration_track(
    segments: list[NarrationSegment],
    duration: float,
    out_wav: Path,
    *,
    sample_rate: int = 48000,
) -> None:
    import numpy as np

    total = int((duration + 0.5) * sample_rate)
    track = np.zeros(total, dtype=np.float32)
    for segment in segments:
        if not segment.audio_path:
            continue
        samples, channels, rate = _read_wav(Path(segment.audio_path))
        audio = np.asarray(samples, dtype=np.float32) / 32768.0
        if channels > 1:
            audio = audio.reshape(-1, channels).mean(axis=1)
        if rate != sample_rate:
            # Linear resample; all TTS output is already 48k, this is a safety net.
            new_len = int(len(audio) * sample_rate / rate)
            audio = np.interp(
                np.linspace(0, len(audio) - 1, new_len),
                np.arange(len(audio)),
                audio,
            ).astype(np.float32)
        start = int(segment.start * sample_rate)
        if start >= total:
            continue
        audio = audio[: max(0, total - start)]
        fade = min(int(0.012 * sample_rate), len(audio) // 2)
        if fade > 1:
            audio[:fade] *= np.linspace(0.0, 1.0, fade, dtype=np.float32)
            audio[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
        track[start:start + len(audio)] += audio

    peak = float(np.max(np.abs(track))) if track.size else 0.0
    if peak > 0.98:
        track *= 0.98 / peak
    pcm = (np.clip(track, -1.0, 1.0) * 32767.0).astype("<i2")
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_wav), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(sample_rate)
        fh.writeframes(pcm.tobytes())
    LOG.info("Wrote narration track %s (%.1fs)", out_wav.name, duration)


def save_timeline(path: Path, segments: list[NarrationSegment]) -> None:
    atomic_write_json(
        path,
        {
            "segments": [segment.to_dict() for segment in segments],
            "total_chars": sum(len(segment.text) for segment in segments),
        },
    )
