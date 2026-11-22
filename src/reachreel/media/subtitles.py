"""Subtitles: mlx-whisper word timing, with a deterministic fallback from the TTS timeline."""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

from ..core.config import Settings
from ..core.util import srt_timestamp
from ..domain.models import Cue, NarrationSegment, QualityFlag

LOG = logging.getLogger("reachreel.media.subtitles")

SRT_TIMECODE_RE = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")


def _clean(text: str) -> str:
    text = re.sub(r"\s+", "", text or "")
    return text.strip()


def _split_text(text: str, max_chars: int) -> list[str]:
    """Split Chinese text into subtitle-sized pieces, preferring punctuation."""
    text = _clean(text)
    if not text:
        return []
    pieces = re.split(r"(?<=[。！？；，、,.!?;])", text)
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if not piece:
            continue
        if len(current) + len(piece) <= max_chars:
            current += piece
        else:
            if current:
                chunks.append(current)
            current = piece
            while len(current) > max_chars:
                chunks.append(current[:max_chars])
                current = current[max_chars:]
    if current:
        chunks.append(current)
    # Avoid a one-character orphan at the end of a cue.
    cleaned: list[str] = []
    for chunk in chunks:
        chunk = chunk.strip()
        if len(chunk) == 1 and cleaned:
            cleaned[-1] += chunk
        elif chunk:
            cleaned.append(chunk)
    return cleaned


def _cue_texts(text: str, max_chars: int, max_lines: int) -> list[str]:
    """Wrap text into subtitle cues without ever dropping characters."""
    lines = _split_text(text, max_chars)
    if not lines:
        return []
    cues = []
    for index in range(0, len(lines), max_lines):
        cues.append("\n".join(lines[index:index + max_lines]))
    return cues


def cues_from_timeline(
    segments: list[NarrationSegment],
    *,
    max_chars: int = 18,
    max_lines: int = 2,
    max_seconds: float = 6.0,
    speech_windows: list[tuple[float, float]] | None = None,
) -> list[Cue]:
    cues: list[Cue] = []
    for index, segment in enumerate(segments):
        window_start, window_end = (
            speech_windows[index] if speech_windows and index < len(speech_windows) else (segment.start, segment.end)
        )
        window_start = max(segment.start, window_start)
        window_end = min(segment.end, window_end)
        if window_end - window_start < 0.4:
            window_start, window_end = segment.start, segment.end
        pieces = _cue_texts(segment.text, max_chars, max_lines)
        if not pieces:
            continue
        total_chars = sum(len(piece) for piece in pieces) or 1
        cursor = window_start
        for piece in pieces:
            share = len(piece) / total_chars
            length = max(0.8, min(max_seconds, (window_end - window_start) * share))
            end = min(window_end, cursor + length)
            if end - cursor < 0.35:
                end = min(window_end, cursor + 0.8)
            cues.append(Cue(start=cursor, end=end, text=piece))
            cursor = end
        if cues:
            cues[-1].end = max(cues[-1].end, min(window_end, cues[-1].start + 0.6))
    return _dedupe(cues)


def cues_from_asr(
    result: dict[str, Any],
    *,
    max_chars: int = 18,
    max_lines: int = 2,
    max_seconds: float = 6.0,
) -> list[Cue]:
    cues: list[Cue] = []
    for segment in result.get("segments", []) or []:
        text = _clean(str(segment.get("text") or ""))
        if not text:
            continue
        start = float(segment.get("start") or 0.0)
        end = float(segment.get("end") or start + 1.0)
        duration = max(0.4, end - start)
        pieces = _cue_texts(text, max_chars, max_lines)
        if not pieces:
            continue
        total_chars = sum(len(piece) for piece in pieces) or 1
        cursor = start
        for piece in pieces:
            share = len(piece) / total_chars
            length = max(0.6, min(max_seconds, duration * share))
            piece_end = min(end, cursor + length)
            if piece_end - cursor < 0.3:
                piece_end = min(end, cursor + 0.6)
            cues.append(Cue(start=cursor, end=piece_end, text=piece))
            cursor = piece_end
    return _dedupe(cues)


def _dedupe(cues: list[Cue]) -> list[Cue]:
    result: list[Cue] = []
    for cue in cues:
        if cue.end <= cue.start:
            continue
        if result and cue.start < result[-1].end:
            result[-1].end = max(result[-1].end, cue.start)
        result.append(cue)
    return result


def write_srt(cues: list[Cue], path: Path) -> None:
    lines: list[str] = []
    for index, cue in enumerate(cues, start=1):
        lines.append(str(index))
        lines.append(f"{srt_timestamp(cue.start)} --> {srt_timestamp(cue.end)}")
        lines.append(cue.text)
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _srt_seconds(text: str) -> float:
    match = SRT_TIMECODE_RE.search(text)
    if not match:
        return 0.0
    hours, minutes, seconds, millis = (int(part) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds + millis / 1000.0


def read_srt(path: Path) -> list[Cue]:
    """Minimal SRT reader used to build ASS from an existing subtitle file."""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return []
    cues: list[Cue] = []
    for block in re.split(r"\n\s*\n", text):
        lines = [line.rstrip() for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        timing_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue
        start_text, _, end_text = lines[timing_index].partition("-->")
        content = lines[timing_index + 1:]
        if not content:
            continue
        cues.append(
            Cue(
                start=_srt_seconds(start_text),
                end=_srt_seconds(end_text),
                text="\n".join(content),
            )
        )
    return cues


def ass_timestamp(seconds: float) -> str:
    """ASS uses `H:MM:SS.cc` (centiseconds), not SRT milliseconds."""
    total = max(0, round(seconds * 100))
    hours, rem = divmod(total, 360000)
    minutes, rem = divmod(rem, 6000)
    whole, centis = divmod(rem, 100)
    return f"{hours}:{minutes:02d}:{whole:02d}.{centis:02d}"


def _ass_escape(text: str) -> str:
    # Keep braces from being interpreted as override blocks; `\N` is a hard line break.
    text = text.replace("\\", "\\\\")
    text = text.replace("{", "\\{").replace("}", "\\}")
    return text.replace("\n", "\\N")


def write_ass(
    cues: list[Cue],
    path: Path,
    *,
    width: int,
    height: int,
    settings: Settings,
) -> None:
    """Write an ASS overlay whose PlayRes matches the output pixel resolution.

    `subtitle_size` and the margins are therefore real pixels: libass does not
    apply its default 384x288 scaling.
    """
    width = max(2, int(width))
    height = max(2, int(height))
    size = max(1, int(settings.subtitle_size))
    margin_l = max(0, int(settings.subtitle_margin_left))
    margin_r = max(0, int(settings.subtitle_margin_right))
    margin_v = max(0, int(settings.subtitle_margin_v))
    font = (settings.subtitle_font or "Sans").replace(",", " ")
    outline = max(1.0, round(height / 288.0, 1))
    header = [
        "[Script Info]",
        "; generated by reachreel",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        (
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
            "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
            "MarginL, MarginR, MarginV, Encoding"
        ),
        (
            f"Style: Default,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,"
            f"&H80000000,0,0,0,0,100,100,0,0,3,{outline},0,2,"
            f"{margin_l},{margin_r},{margin_v},1"
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    events = [
        (
            f"Dialogue: 0,{ass_timestamp(cue.start)},{ass_timestamp(cue.end)},"
            f"Default,,0,0,0,,{_ass_escape(cue.text)}"
        )
        for cue in cues
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(header + events) + "\n", encoding="utf-8")


def write_ass_from_srt(
    srt_path: Path,
    ass_path: Path,
    *,
    width: int,
    height: int,
    settings: Settings,
) -> None:
    write_ass(
        read_srt(srt_path),
        ass_path,
        width=width,
        height=height,
        settings=settings,
    )


def transcribe_with_mlx_whisper(
    audio_path: Path,
    *,
    model: str,
    language: str = "zh",
    retries: int = 1,
) -> dict[str, Any] | None:
    try:
        import mlx_whisper
    except ImportError:
        LOG.warning("mlx-whisper is not installed; using the TTS timeline for subtitles")
        return None
    LOG.info("Transcribing narration with mlx-whisper (%s)...", model)
    attempts = max(1, retries + 1)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return mlx_whisper.transcribe(
                str(audio_path),
                path_or_hf_repo=model,
                language=language,
                word_timestamps=True,
                condition_on_previous_text=False,
                verbose=False,
            )
        except Exception as exc:  # noqa: BLE001 - mlx/ffmpeg failures vary
            last_error = exc
            if attempt < attempts:
                wait = min(2**attempt, 5)
                LOG.warning(
                    "mlx-whisper attempt %d/%d failed; retrying in %ss: %s",
                    attempt,
                    attempts,
                    wait,
                    exc,
                )
                time.sleep(wait)
    LOG.warning(
        "mlx-whisper failed after %d attempts; falling back to the TTS timeline: %s",
        attempts,
        last_error,
    )
    return None


def _speech_windows(
    result: dict[str, Any],
    segments: list[NarrationSegment],
) -> list[tuple[float, float]]:
    """Refine each TTS segment's speech bounds with Whisper's detected speech."""
    whisper_segments = result.get("segments", []) or []
    windows: list[tuple[float, float]] = []
    for segment in segments:
        overlaps = [
            item for item in whisper_segments
            if float(item.get("end") or 0) > segment.start - 0.15
            and float(item.get("start") or 0) < segment.end + 0.15
        ]
        if not overlaps:
            windows.append((segment.start, segment.end))
            continue
        start = max(segment.start, min(float(item["start"]) for item in overlaps))
        end = min(segment.end, max(float(item["end"]) for item in overlaps))
        if end - start < 0.4:
            start, end = segment.start, segment.end
        windows.append((start, end))
    return windows


def build_subtitles(
    narration_wav: Path,
    segments: list[NarrationSegment],
    out_srt: Path,
    settings: Settings,
    *,
    asr_engine: str = "mlx-whisper",
    flags: list[QualityFlag] | None = None,
) -> list[Cue]:
    result = None
    if asr_engine != "none":
        result = transcribe_with_mlx_whisper(
            narration_wav,
            model=settings.whisper_model,
            language=settings.asr_language,
        )
        if result is None and flags is not None:
            flags.append(
                QualityFlag(
                    code="asr.fallback",
                    params={"engine": asr_engine, "model": settings.whisper_model},
                    severity="warning",
                )
            )
    if result and settings.subtitle_text == "asr":
        cues = cues_from_asr(
            result,
            max_chars=settings.subtitle_max_chars,
            max_lines=settings.subtitle_max_lines,
            max_seconds=settings.subtitle_max_seconds,
        )
        if cues:
            LOG.info("Generated %d cues from ASR text", len(cues))
            write_srt(cues, out_srt)
            return cues
        if flags is not None:
            flags.append(
                QualityFlag(
                    code="asr.empty",
                    params={"engine": asr_engine},
                    severity="warning",
                )
            )
    if result:
        windows = _speech_windows(result, segments)
        cues = cues_from_timeline(
            segments,
            max_chars=settings.subtitle_max_chars,
            max_lines=settings.subtitle_max_lines,
            max_seconds=settings.subtitle_max_seconds,
            speech_windows=windows,
        )
        if cues:
            LOG.info("Generated %d cues from script text and Whisper timestamps", len(cues))
            write_srt(cues, out_srt)
            return cues
    cues = cues_from_timeline(
        segments,
        max_chars=settings.subtitle_max_chars,
        max_lines=settings.subtitle_max_lines,
        max_seconds=settings.subtitle_max_seconds,
    )
    LOG.info("Generated %d cues from the timeline", len(cues))
    write_srt(cues, out_srt)
    return cues
