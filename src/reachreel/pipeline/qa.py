"""Post-render quality gate.

The QA gate is intentionally heuristic: it catches the expensive-to-miss problems
(no audio, wrong duration, clipped mix, black/frozen stretches, broken subtitles)
without pretending to be a full broadcast QC system.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from ..core.config import public_ollama_url
from ..core.util import (
    LOG,
    atomic_write_json,
    ffmpeg_filters,
    ffmpeg_full,
    ffprobe_json,
    read_json,
    run,
)
from ..domain.models import QualityFlag, merge_quality_flags
from ..i18n import t
from ..languages import PROFILES, profile_for
from ..media.subtitles import SUBTITLE_PRESETS
from .stage_inputs import RENDER_STAGES, StageInputs

PASS = "pass"
WARN = "warn"
FAIL = "fail"

TIMECODE_RE = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")
PUNCT_RE = re.compile(r"[\s，。！？；：、,.!?;:\"'“”‘’（）()《》〈〉【】…—–\-~～]")


@dataclass(frozen=True)
class QAThresholds:
    """All QA thresholds in one place; filled from `[qa]` config / manifest snapshot."""

    duration_warn_seconds: float = 0.5
    duration_fail_seconds: float = 2.0
    av_sync_warn_seconds: float = 0.2
    av_sync_fail_seconds: float = 0.8
    lufs_warn_delta: float = 2.0
    lufs_fail_delta: float = 4.0
    true_peak_tolerance_db: float = 0.6
    true_peak_ceiling_db: float = 0.0
    black_min_seconds: float = 0.5
    black_pixel_threshold: float = 0.10
    black_longest_fail_seconds: float = 3.0
    black_total_fail_seconds: float = 5.0
    freeze_noise: float = 0.0005
    freeze_min_seconds: float = 3.0
    freeze_fail_seconds: float = 8.0
    freeze_motion_threshold: float = 0.02
    freeze_sample_seconds: float = 20.0
    clip_peak_db: float = -0.1
    clip_sample_ratio: float = 0.0005
    clip_flat_factor: float = 0.5
    subtitle_char_warn_extra: int = 4
    subtitle_char_fail_extra: int = 10
    subtitle_safe_tolerance_px: float = 6.0
    subtitle_overflow_tolerance_px: float = 20.0
    narration_subtitle_tolerance_seconds: float = 0.9
    narration_subtitle_fail_ratio: float = 0.5
    skip_subtitle_pixels: bool = False

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> QAThresholds:
        values: dict[str, Any] = {}
        for item in fields(cls):
            key = f"qa_{item.name}"
            value = settings.get(key)
            if value is None:
                continue
            default = item.default
            try:
                if isinstance(default, bool):
                    values[item.name] = bool(value)
                elif isinstance(default, int):
                    values[item.name] = int(value)
                elif isinstance(default, float):
                    values[item.name] = float(value)
                else:
                    values[item.name] = value
            except (TypeError, ValueError):
                continue
        return cls(**values)


@dataclass
class Check:
    id: str
    status: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "message": self.message,
            "details": self.details,
        }


@dataclass
class QAReport:
    workdir: str
    output: str
    checks: list[Check]
    summary: dict[str, Any]
    thresholds: dict[str, Any] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return self.summary.get("fail", 0) > 0

    @property
    def warned(self) -> bool:
        return self.summary.get("warn", 0) > 0

    def exit_code(self, strict: bool = False) -> int:
        if self.failed:
            return 1
        if strict and self.warned:
            return 1
        return 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "workdir": self.workdir,
            "output": self.output,
            "summary": self.summary,
            "thresholds": self.thresholds,
            "checks": [check.to_dict() for check in self.checks],
        }


def _timecode_to_seconds(text: str) -> float:
    match = TIMECODE_RE.search(text)
    if not match:
        return 0.0
    hours, minutes, seconds, millis = (int(part) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds + millis / 1000.0


def parse_srt(path: Path) -> list[dict[str, Any]]:
    """Small, dependency-free SRT parser for QA."""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return []
    cues: list[dict[str, Any]] = []
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
            {
                "start": _timecode_to_seconds(start_text),
                "end": _timecode_to_seconds(end_text),
                "lines": content,
                "text": "\n".join(content),
            }
        )
    return cues


def _loudness(path: Path) -> dict[str, Any] | None:
    proc = run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-af",
            "loudnorm=I=-14:TP=-1.5:print_format=json",
            "-f",
            "null",
            "-",
        ],
        check=False,
    )
    match = re.search(r'\{\s*"input_i".*?\}', proc.stderr or "", re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except ValueError:
        return None


def _black_intervals(
    path: Path,
    *,
    min_seconds: float = 0.5,
    pixel_threshold: float = 0.10,
) -> list[tuple[float, float]]:
    proc = run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-i",
            str(path),
            "-vf",
            f"blackdetect=d={min_seconds:.2f}:pix_th={pixel_threshold:.3f}",
            "-an",
            "-f",
            "null",
            "-",
        ],
        check=False,
    )
    intervals: list[tuple[float, float]] = []
    for match in re.finditer(
        r"black_start:([0-9.]+)\s+black_end:([0-9.]+)\s+black_duration:([0-9.]+)",
        proc.stderr or "",
    ):
        intervals.append((float(match.group(1)), float(match.group(2))))
    return intervals


def _freeze_intervals(
    path: Path,
    *,
    noise: float = 0.0005,
    min_seconds: float = 3.0,
) -> list[tuple[float, float]]:
    proc = run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-i",
            str(path),
            "-vf",
            f"freezedetect=n={noise:g}:d={min_seconds:g}",
            "-an",
            "-f",
            "null",
            "-",
        ],
        check=False,
    )
    starts = [float(m.group(1)) for m in re.finditer(r"freeze_start:\s*([0-9.]+)", proc.stderr or "")]
    ends = [float(m.group(1)) for m in re.finditer(r"freeze_end:\s*([0-9.]+)", proc.stderr or "")]
    return list(zip(starts, ends or [float("inf")] * len(starts)))


def _motion_score(path: Path, start: float, end: float, *, sample_seconds: float = 20.0) -> float | None:
    """Mean luma difference between consecutive frames (0 = identical, 0-255 scale).

    `freezedetect` only says "the frames are almost identical". A tripod shot with
    sensor/compression noise can also trip it, so we measure the residual motion to
    tell a static shot apart from a genuinely frozen encoder output.
    """
    if end == float("inf"):
        end = start + sample_seconds
    start = max(0.0, start + 0.1)
    length = min(max(0.4, end - start - 0.2), sample_seconds)
    proc = run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-nostdin",
            "-ss",
            f"{start:.3f}",
            "-i",
            str(path),
            "-t",
            f"{length:.3f}",
            "-an",
            "-vf",
            (
                "fps=4,scale=160:-2,tblend=all_mode=difference,"
                "signalstats,metadata=print:key=lavfi.signalstats.YAVG"
            ),
            "-f",
            "null",
            "-",
        ],
        check=False,
    )
    values = [
        float(match.group(1))
        for match in re.finditer(r"lavfi\.signalstats\.YAVG=([0-9.]+)", proc.stderr or "")
    ]
    if not values:
        return None
    return sum(values) / len(values)


def _parse_astats(text: str) -> dict[str, float]:
    """Parse the `Overall` section of ffmpeg's astats output."""
    section = text.split("Overall", 1)[-1]
    result: dict[str, float] = {}
    for label in ("Peak level dB", "Flat factor", "Peak count", "Number of samples"):
        match = re.search(rf"{re.escape(label)}:\s*(-?[0-9.]+)", section)
        if match:
            result[label] = float(match.group(1))
    return result


def _audio_stats(path: Path) -> dict[str, float] | None:
    proc = run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-nostdin",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-af",
            (
                "astats=metadata=0:reset=0:measure_perchannel=none:"
                "measure_overall=Peak_level+Peak_count+Number_of_samples+Flat_factor"
            ),
            "-f",
            "null",
            "-",
        ],
        check=False,
    )
    stats = _parse_astats(proc.stderr or "")
    return stats or None


def _subtitle_style(settings: dict[str, Any]) -> str:
    """Legacy SRT force_style for workdirs rendered before ASS layout."""
    from ..core.config import Settings
    from ..media.render import subtitle_style

    values = Settings()
    values.content_language = str(settings.get("content_language") or "zh")
    values.subtitle_font = str(settings.get("subtitle_font") or "")
    values.subtitle_size = int(settings.get("subtitle_size", 18))
    values.subtitle_margin_v = int(settings.get("subtitle_margin_v", 36))
    values.subtitle_margin_left = int(settings.get("subtitle_margin_left", 50))
    values.subtitle_margin_right = int(settings.get("subtitle_margin_right", 50))
    values.subtitle_render = str(settings.get("subtitle_render") or "outline")
    values.subtitle_outline = float(settings.get("subtitle_outline") or 0.0)
    values.subtitle_box_padding = float(settings.get("subtitle_box_padding") or 0.0)
    return subtitle_style(values)


def _escape_filter_path(path: Path) -> str:
    from ..media.render import _escape_filter_path as escape

    return escape(path)


def subtitle_pixel_bounds(
    srt_path: Path,
    *,
    width: int,
    height: int,
    duration: float,
    settings: dict[str, Any],
    ass_path: Path | None = None,
) -> dict[str, int] | None:
    """Render the subtitle layer on a black canvas and measure its pixel bounds.

    New workdirs have `subtitles.ass` with PlayRes equal to the output size, so
    the measurement is exactly what render burns. Old workdirs fall back to SRT
    plus the legacy `force_style` units.
    """
    ffmpeg = ffmpeg_full()
    if not ffmpeg or "subtitles" not in ffmpeg_filters(ffmpeg):
        return None
    if ass_path is not None and ass_path.exists():
        subtitle_filter = f"subtitles=filename='{_escape_filter_path(ass_path)}'"
    else:
        style = _subtitle_style(settings).replace("'", "\\'")
        subtitle_filter = (
            f"subtitles=filename='{_escape_filter_path(srt_path)}':force_style='{style}'"
        )
    fps = 4 if duration <= 60 else 2
    vf = f"{subtitle_filter},bbox=min_val=20,metadata=print"
    source = f"color=c=black:s={width}x{height}:r={fps}:d={max(1.0, duration):.2f}"
    proc = run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            source,
            "-vf",
            vf,
            "-an",
            "-f",
            "null",
            "-",
        ],
        check=False,
    )
    boxes: list[tuple[int, int, int, int]] = []
    for match in re.finditer(
        r"lavfi\.bbox\.x1=(\d+).*?lavfi\.bbox\.x2=(\d+).*?"
        r"lavfi\.bbox\.y1=(\d+).*?lavfi\.bbox\.y2=(\d+)",
        proc.stderr or "",
        re.DOTALL,
    ):
        x1, x2, y1, y2 = (int(part) for part in match.groups())
        if x2 >= x1 and y2 >= y1:
            boxes.append((x1, y1, x2 + 1, y2 + 1))
    if not boxes:
        return None
    left = min(box[0] for box in boxes)
    top = min(box[1] for box in boxes)
    right = max(box[2] for box in boxes)
    bottom = max(box[3] for box in boxes)
    return {"left": left, "top": top, "right": right, "bottom": bottom}


def subtitle_margin_status(
    bounds: dict[str, int],
    *,
    width: int,
    height: int,
    settings: dict[str, Any],
    thresholds: QAThresholds,
) -> tuple[str, dict[str, Any]]:
    """Compare measured text bounds with the configured subtitle safe margins."""
    preset_name = str(settings.get("subtitle_preset") or "").strip().lower()
    preset = SUBTITLE_PRESETS.get(preset_name)
    if preset:
        limits = {
            "left": int(preset["margin_left"]),
            "right": int(preset["margin_right"]),
            "bottom": int(preset["margin_v"]),
        }
    else:
        limits = {
            "left": int(settings.get("subtitle_margin_left", 50)),
            "right": int(settings.get("subtitle_margin_right", 50)),
            "bottom": int(settings.get("subtitle_margin_v", 36)),
        }
    actual = {
        "left": bounds["left"],
        "right": width - bounds["right"],
        "bottom": height - bounds["bottom"],
    }
    status = PASS
    violations: dict[str, Any] = {}
    for side, limit in limits.items():
        margin = actual[side]
        shortfall = max(0.0, limit - margin)
        if margin <= 1.0 or shortfall > thresholds.subtitle_overflow_tolerance_px:
            status = FAIL
            violations[side] = {"actual": margin, "limit": limit, "shortfall": round(shortfall, 1)}
        elif shortfall > thresholds.subtitle_safe_tolerance_px:
            status = WARN if status == PASS else status
            violations[side] = {"actual": margin, "limit": limit, "shortfall": round(shortfall, 1)}
    details = {
        "bounds": bounds,
        "margins": actual,
        "limits": limits,
        "violations": violations,
        "preset": preset_name or None,
        "frame": {"width": width, "height": height},
    }
    return status, details


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", PUNCT_RE.sub("", text or ""))


def compare_narration_subtitles(
    segments: list[dict[str, Any]],
    cues: list[dict[str, Any]],
    *,
    check_text: bool = True,
    tolerance_seconds: float = 0.9,
) -> dict[str, Any]:
    """Align each narration segment with its cues and report text/timing drift."""
    assigned: dict[int, list[dict[str, Any]]] = {index: [] for index in range(len(segments))}
    orphans = 0
    for cue in cues:
        best_index = None
        best_overlap = 0.0
        for index, segment in enumerate(segments):
            overlap = min(float(cue["end"]), float(segment["end"])) - max(
                float(cue["start"]), float(segment["start"])
            )
            if overlap > best_overlap:
                best_overlap = overlap
                best_index = index
        if best_index is None:
            orphans += 1
        else:
            assigned[best_index].append(cue)

    text_mismatches: list[dict[str, Any]] = []
    timing_mismatches: list[dict[str, Any]] = []
    missing: list[int] = []
    worst_deviation = 0.0
    for index, segment in enumerate(segments):
        segment_cues = sorted(assigned[index], key=lambda cue: float(cue["start"]))
        if not segment_cues:
            missing.append(int(segment.get("id", index + 1)))
            continue
        if check_text:
            cue_text = _normalize_text("".join(str(cue["text"]) for cue in segment_cues))
            script_text = _normalize_text(str(segment.get("text") or ""))
            if cue_text != script_text:
                text_mismatches.append(
                    {
                        "id": segment.get("id"),
                        "script": script_text[:60],
                        "subtitle": cue_text[:60],
                    }
                )
        segment_text_len = len(_normalize_text(str(segment.get("text") or ""))) or 1
        consumed = 0
        segment_start = float(segment["start"])
        segment_end = float(segment["end"])
        deviation = 0.0
        for cue in segment_cues:
            expected_start = segment_start + (segment_end - segment_start) * (consumed / segment_text_len)
            deviation = max(deviation, abs(float(cue["start"]) - expected_start))
            consumed += len(_normalize_text(str(cue["text"])))
        worst_deviation = max(worst_deviation, deviation)
        if deviation > tolerance_seconds:
            timing_mismatches.append(
                {"id": segment.get("id"), "deviation": round(deviation, 2)}
            )

    return {
        "segments": len(segments),
        "cues": len(cues),
        "orphan_cues": orphans,
        "missing_segments": missing,
        "text_mismatches": text_mismatches,
        "timing_mismatches": timing_mismatches,
        "worst_deviation": round(worst_deviation, 2),
    }


def _stream_info(path: Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None, float]:
    data = ffprobe_json(path)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float(data.get("format", {}).get("duration") or 0.0)
    return video, audio, duration


def _add(checks: list[Check], check_id: str, status: str, message: str, **details: Any) -> None:
    checks.append(Check(id=check_id, status=status, message=message, details=details))


def run_qa(
    workdir: Path,
    *,
    strict: bool = False,
    settings_override: dict[str, Any] | None = None,
) -> QAReport:
    workdir = workdir.resolve()
    manifest = read_json(workdir / "manifest.json", {}) or {}
    render_settings = manifest.get("render_settings", {}) or {}
    pipeline_settings = manifest.get("settings_snapshot", {}) or {}
    settings = {**pipeline_settings, **render_settings, **(settings_override or {})}
    thresholds = QAThresholds.from_settings(settings)
    output = Path(manifest.get("output") or workdir / "final.mp4")
    checks: list[Check] = []

    if not output.exists():
        _add(checks, "output_exists", FAIL, t("qa.output_missing", output=output))
        return _finish(workdir, output, checks, strict, thresholds)

    source_path = (manifest.get("video") or {}).get("path")
    inputs = StageInputs(workdir, Path(source_path) if source_path else None)
    try:
        stale = inputs.first_stale(manifest, (*RENDER_STAGES, "render"))
    except (OSError, RuntimeError) as exc:
        _add(checks, "artifact_inputs", FAIL, str(exc))
        return _finish(workdir, output, checks, strict, thresholds)
    if stale:
        _add(checks, "artifact_inputs", FAIL, inputs.stale_message(stale), stage=stale)
        return _finish(workdir, output, checks, strict, thresholds)

    try:
        video_stream, audio_stream, out_duration = _stream_info(output)
    except Exception as exc:  # noqa: BLE001
        _add(checks, "output_probe", FAIL, t("qa.output_probe_failed", error=exc))
        return _finish(workdir, output, checks, strict, thresholds)

    if not video_stream:
        _add(checks, "video_stream", FAIL, t("qa.video_missing"))
    else:
        codec = video_stream.get("codec_name")
        width = video_stream.get("width")
        height = video_stream.get("height")
        fps_text = video_stream.get("avg_frame_rate") or ""
        _add(
            checks,
            "video_stream",
            PASS if codec == "h264" else WARN,
            t("qa.video_stream", codec=codec, width=width, height=height, fps=fps_text),
            codec=codec,
            width=width,
            height=height,
            fps=fps_text,
        )

    if not audio_stream:
        _add(checks, "audio_stream", FAIL, t("qa.audio_missing"))
    else:
        codec = audio_stream.get("codec_name")
        _add(
            checks,
            "audio_stream",
            PASS if codec == "aac" else WARN,
            t("qa.audio_stream", codec=codec, channels=audio_stream.get("channels")),
            codec=codec,
            channels=audio_stream.get("channels"),
        )

    source_duration = float(manifest.get("video", {}).get("duration") or 0.0)
    if source_duration > 0 and out_duration > 0:
        delta = abs(out_duration - source_duration)
        status = (
            PASS
            if delta <= thresholds.duration_warn_seconds
            else WARN
            if delta <= thresholds.duration_fail_seconds
            else FAIL
        )
        _add(
            checks,
            "duration_match",
            status,
            t("qa.duration", output=out_duration, source=source_duration, delta=delta),
            output=round(out_duration, 3),
            source=round(source_duration, 3),
            delta=round(delta, 3),
        )

    if video_stream and audio_stream:
        v_dur = float(video_stream.get("duration") or out_duration or 0)
        a_dur = float(audio_stream.get("duration") or out_duration or 0)
        if v_dur > 0 and a_dur > 0:
            delta = abs(v_dur - a_dur)
            status = (
                PASS
                if delta <= thresholds.av_sync_warn_seconds
                else WARN
                if delta <= thresholds.av_sync_fail_seconds
                else FAIL
            )
            _add(
                checks,
                "av_duration_match",
                status,
                t("qa.av_duration", delta=delta),
                video=round(v_dur, 3),
                audio=round(a_dur, 3),
            )

    if audio_stream:
        loud = _loudness(output)
        target_lufs = float(settings.get("target_lufs", -14.0))
        target_tp = float(settings.get("true_peak", -1.5))
        if loud:
            try:
                integrated = float(loud.get("input_i"))
                peak = float(loud.get("input_tp"))
            except (TypeError, ValueError):
                integrated = peak = float("-inf")
            if integrated == float("-inf"):
                _add(checks, "loudness", FAIL, t("qa.loudness_missing"))
            else:
                delta = abs(integrated - target_lufs)
                status = (
                    PASS
                    if delta <= thresholds.lufs_warn_delta
                    else WARN
                    if delta <= thresholds.lufs_fail_delta
                    else FAIL
                )
                _add(
                    checks,
                    "loudness",
                    status,
                    t("qa.loudness", integrated=integrated, target=target_lufs),
                    integrated=round(integrated, 2),
                    target=target_lufs,
                    delta=round(delta, 2),
                )
            if peak == float("-inf"):
                _add(checks, "true_peak", WARN, t("qa.true_peak_missing"))
            else:
                tolerance = thresholds.true_peak_tolerance_db
                status = (
                    PASS
                    if peak <= target_tp + tolerance
                    else WARN
                    if peak <= thresholds.true_peak_ceiling_db
                    else FAIL
                )
                _add(
                    checks,
                    "true_peak",
                    status,
                    t("qa.true_peak", peak=peak, target=target_tp, tolerance=tolerance),
                    peak=round(peak, 2),
                    limit=target_tp,
                )
        else:
            _add(checks, "loudness", WARN, t("qa.loudness_failed"))

        stats = _audio_stats(output)
        if not stats:
            _add(checks, "audio_clipping", WARN, t("qa.clipping_failed"))
        else:
            peak_db = stats.get("Peak level dB")
            peak_count = stats.get("Peak count", 0.0)
            samples = stats.get("Number of samples", 0.0)
            flat = stats.get("Flat factor", 0.0)
            ratio = peak_count / samples if samples else 0.0
            clipped = (
                peak_db is not None
                and peak_db >= thresholds.clip_peak_db
                and ratio >= thresholds.clip_sample_ratio
            )
            if clipped:
                status = FAIL
            elif (
                peak_db is not None and peak_db >= thresholds.clip_peak_db
            ) or flat >= thresholds.clip_flat_factor:
                status = WARN
            else:
                status = PASS
            peak_text = f"{peak_db:.2f} dBFS" if peak_db is not None else t("qa.peak_unknown")
            _add(
                checks,
                "audio_clipping",
                status,
                t("qa.clipping", peak=peak_text, ratio=ratio * 100, flat=flat),
                peak_db=round(peak_db, 3) if peak_db is not None else None,
                peak_count=int(peak_count),
                samples=int(samples),
                peak_ratio=round(ratio, 6),
                flat_factor=round(flat, 4),
                thresholds={
                    "peak_db": thresholds.clip_peak_db,
                    "sample_ratio": thresholds.clip_sample_ratio,
                    "flat_factor": thresholds.clip_flat_factor,
                },
            )

    black = _black_intervals(
        output,
        min_seconds=thresholds.black_min_seconds,
        pixel_threshold=thresholds.black_pixel_threshold,
    )
    if black:
        longest = max(end - start for start, end in black)
        total = sum(end - start for start, end in black)
        status = (
            FAIL
            if longest >= thresholds.black_longest_fail_seconds
            or total >= thresholds.black_total_fail_seconds
            else WARN
        )
        _add(
            checks,
            "black_frames",
            status,
            t("qa.black", count=len(black), longest=longest, total=total),
            intervals=black,
            longest=round(longest, 2),
            total=round(total, 2),
        )
    else:
        _add(
            checks,
            "black_frames",
            PASS,
            t("qa.black_none", threshold=thresholds.black_min_seconds),
        )

    freeze = _freeze_intervals(
        output,
        noise=thresholds.freeze_noise,
        min_seconds=thresholds.freeze_min_seconds,
    )
    if freeze:
        freeze = [
            (start, out_duration if end == float("inf") else end)
            for start, end in freeze
        ]
        source_text = str(manifest.get("video", {}).get("path") or "")
        source_video = Path(source_text)
        if source_text and not source_video.is_absolute():
            source_video = workdir / source_video
        source_available = source_video.is_file()
        real_freezes: list[dict[str, Any]] = []
        static_shots: list[dict[str, Any]] = []
        for start, end in freeze:
            output_motion = _motion_score(
                output,
                start,
                end,
                sample_seconds=thresholds.freeze_sample_seconds,
            )
            source_motion = (
                _motion_score(
                    source_video,
                    start,
                    end,
                    sample_seconds=thresholds.freeze_sample_seconds,
                )
                if source_available
                else None
            )
            # A static source shot is legitimate content. A real render/encode
            # freeze shows motion in the source but none in the output.
            static_shot = (
                output_motion is not None
                and output_motion >= thresholds.freeze_motion_threshold
            ) or (
                source_motion is not None
                and source_motion < thresholds.freeze_motion_threshold
            )
            record = {
                "start": round(start, 2),
                "end": round(end, 2),
                "output_motion": None if output_motion is None else round(output_motion, 4),
                "source_motion": None if source_motion is None else round(source_motion, 4),
            }
            (static_shots if static_shot else real_freezes).append(record)
        if real_freezes:
            longest = max(item["end"] - item["start"] for item in real_freezes)
            status = FAIL if longest >= thresholds.freeze_fail_seconds else WARN
            message = t("qa.freeze", count=len(real_freezes), longest=longest)
            if static_shots:
                message += t("qa.freeze_excluded", count=len(static_shots))
            _add(
                checks,
                "freeze_frames",
                status,
                message,
                intervals=real_freezes,
                static_shot_intervals=static_shots,
                longest=round(longest, 2),
            )
        else:
            reason = t("qa.freeze_reason_source") if source_available else t("qa.freeze_reason_output")
            _add(
                checks,
                "freeze_frames",
                PASS,
                t("qa.freeze_static", count=len(static_shots), reason=reason),
                static_shot_intervals=static_shots,
            )
    else:
        _add(
            checks,
            "freeze_frames",
            PASS,
            t("qa.freeze_none", threshold=thresholds.freeze_min_seconds),
        )

    srt_path = workdir / "subtitles.srt"
    cues = parse_srt(srt_path)
    if not srt_path.exists():
        _add(checks, "subtitle_file", FAIL, t("qa.subtitle_missing", name=srt_path.name))
    elif not cues:
        _add(checks, "subtitle_cues", FAIL, t("qa.subtitle_no_cues"))
    else:
        configured_chars = int(settings.get("subtitle_max_chars") or 0)
        if configured_chars > 0:
            max_chars_limit = configured_chars
        else:
            profile = profile_for(str(settings.get("content_language") or "zh")) or PROFILES["zh-CN"]
            max_chars_limit = profile.subtitle_max_chars
        max_lines_limit = int(settings.get("subtitle_max_lines", 2))
        max_seconds_limit = float(settings.get("subtitle_max_seconds", 6.0))
        max_line_len = max(len(line) for cue in cues for line in cue["lines"])
        max_line_count = max(len(cue["lines"]) for cue in cues)
        char_status = (
            PASS
            if max_line_len <= max_chars_limit + thresholds.subtitle_char_warn_extra
            else WARN
            if max_line_len <= max_chars_limit + thresholds.subtitle_char_fail_extra
            else FAIL
        )
        line_status = PASS if max_line_count <= max_lines_limit + 1 else WARN
        _add(
            checks,
            "subtitle_layout",
            char_status if char_status != PASS else line_status,
            t("qa.subtitle_layout", count=len(cues), chars=max_line_len, lines=max_line_count),
            max_line_length=max_line_len,
            max_lines=max_line_count,
            limits={"chars": max_chars_limit, "lines": max_lines_limit},
        )
        timing_errors = [
            cue for cue in cues
            if cue["end"] <= cue["start"] or cue["end"] > out_duration + 0.5
        ]
        _add(
            checks,
            "subtitle_timing",
            FAIL if timing_errors else PASS,
            t("qa.subtitle_timing", count=len(timing_errors)),
            bad=timing_errors[:5],
        )
        overlaps = 0
        previous_end = 0.0
        for cue in cues:
            if cue["start"] < previous_end - 0.05:
                overlaps += 1
            previous_end = max(previous_end, cue["end"])
        _add(
            checks,
            "subtitle_overlap",
            WARN if overlaps else PASS,
            t("qa.subtitle_overlap", count=overlaps),
            overlaps=overlaps,
        )
        last_cue = cues[-1]
        _add(
            checks,
            "subtitle_bounds",
            PASS if last_cue["end"] <= out_duration + 0.5 else WARN,
            t("qa.subtitle_bounds", end=last_cue["end"], duration=out_duration),
        )
        long_cues = sum(1 for cue in cues if cue["end"] - cue["start"] > max_seconds_limit + 0.5)
        _add(
            checks,
            "subtitle_duration",
            WARN if long_cues else PASS,
            t("qa.subtitle_duration", count=long_cues, seconds=max_seconds_limit),
            long=long_cues,
        )

        frame_width = int((video_stream or {}).get("width") or 0)
        frame_height = int((video_stream or {}).get("height") or 0)
        burned_mode = bool(manifest.get("settings", {}).get("burned_subtitles", False))
        if thresholds.skip_subtitle_pixels:
            _add(checks, "subtitle_overflow_pixels", PASS, t("qa.subtitle_pixels_disabled"))
        elif not burned_mode:
            _add(checks, "subtitle_overflow_pixels", PASS, t("qa.subtitle_pixels_soft"))
        elif not frame_width or not frame_height:
            _add(checks, "subtitle_overflow_pixels", WARN, t("qa.subtitle_pixels_no_dimensions"))
        else:
            srt_duration = min(out_duration, max(cue["end"] for cue in cues) + 0.2)
            ass_path = workdir / "subtitles.ass"
            # If the ASS is newer than the movie, it was not the overlay that
            # produced this file; fall back to the legacy SRT layout for QA.
            if ass_path.exists() and ass_path.stat().st_mtime > output.stat().st_mtime + 1:
                ass_path = None
            layout = "ASS" if ass_path is not None and ass_path.exists() else "SRT(legacy)"
            bounds = subtitle_pixel_bounds(
                srt_path,
                width=frame_width,
                height=frame_height,
                duration=srt_duration,
                settings=settings,
                ass_path=ass_path,
            )
            if bounds is None:
                _add(
                    checks,
                    "subtitle_overflow_pixels",
                    WARN,
                    t("qa.subtitle_pixels_render_failed"),
                )
            else:
                status, details = subtitle_margin_status(
                    bounds,
                    width=frame_width,
                    height=frame_height,
                    settings=settings,
                    thresholds=thresholds,
                )
                margins = details["margins"]
                limits = details["limits"]
                _add(
                    checks,
                    "subtitle_overflow_pixels",
                    status,
                    t(
                        "qa.subtitle_margins",
                        layout=layout,
                        left=margins["left"],
                        right=margins["right"],
                        bottom=margins["bottom"],
                        safe_left=limits["left"],
                        safe_right=limits["right"],
                        safe_bottom=limits["bottom"],
                    ),
                    **details,
                )

    timeline = read_json(workdir / "timeline.json", {}) or {}
    segments = timeline.get("segments", []) or []
    if segments:
        max_speed = max(float(seg.get("tts_speed") or 1.0) for seg in segments)
        max_allowed = float(settings.get("max_speedup", 1.32)) + 0.02
        status = PASS if max_speed <= max_allowed else WARN if max_speed <= 1.5 else FAIL
        _add(
            checks,
            "narration_speed",
            status,
            t("qa.narration_speed", max=max_speed, limit=max_allowed),
            max_speed=round(max_speed, 3),
            limit=max_allowed,
        )
        last_end = max(float(seg.get("end") or 0.0) for seg in segments)
        duration_limit = out_duration + 0.5
        _add(
            checks,
            "narration_bounds",
            PASS if last_end <= duration_limit else WARN,
            t("qa.narration_bounds", end=last_end, duration=out_duration),
        )
        if cues:
            check_text = str(settings.get("subtitle_text", "script")) == "script"
            comparison = compare_narration_subtitles(
                segments,
                cues,
                check_text=check_text,
                tolerance_seconds=thresholds.narration_subtitle_tolerance_seconds,
            )
            issues = (
                len(comparison["missing_segments"])
                + len(comparison["text_mismatches"])
                + len(comparison["timing_mismatches"])
                + (1 if comparison["orphan_cues"] else 0)
            )
            ratio = issues / max(1, len(segments))
            status = (
                FAIL
                if ratio > thresholds.narration_subtitle_fail_ratio
                else WARN
                if issues
                else PASS
            )
            mode = t("qa.alignment_mode_text") if check_text else t("qa.alignment_mode_timing")
            _add(
                checks,
                "narration_subtitle_alignment",
                status,
                t(
                    "qa.alignment",
                    segments=len(segments),
                    cues=len(cues),
                    mode=mode,
                    missing=len(comparison["missing_segments"]),
                    text=len(comparison["text_mismatches"]),
                    timing=len(comparison["timing_mismatches"]),
                    delta=comparison["worst_deviation"],
                ),
                **comparison,
                fail_ratio=thresholds.narration_subtitle_fail_ratio,
                tolerance_seconds=thresholds.narration_subtitle_tolerance_seconds,
            )
    else:
        _add(checks, "timeline", WARN, t("qa.timeline_missing"))

    bgm = manifest.get("bgm_source") or manifest.get("settings", {}).get("bgm")
    if bgm:
        bgm_path = Path(bgm)
        _add(
            checks,
            "bgm",
            PASS if bgm_path.exists() else WARN,
            t("qa.bgm", name=bgm_path.name)
            + ("" if bgm_path.exists() else t("qa.bgm_missing_file")),
        )
    else:
        _add(checks, "bgm", PASS, t("qa.bgm_none"))

    burned = bool(manifest.get("settings", {}).get("burned_subtitles", False))
    try:
        has_subtitle_stream = any(
            stream.get("codec_type") == "subtitle"
            for stream in ffprobe_json(output).get("streams", [])
        )
    except Exception:  # noqa: BLE001
        has_subtitle_stream = False
    if burned:
        _add(
            checks,
            "subtitle_mode",
            WARN if has_subtitle_stream else PASS,
            t("qa.subtitle_mode_hard")
            + (t("qa.subtitle_mode_hard_track") if has_subtitle_stream else ""),
        )
    else:
        _add(
            checks,
            "subtitle_mode",
            WARN if not has_subtitle_stream else PASS,
            t("qa.subtitle_mode_soft")
            + (t("qa.subtitle_mode_soft_no_track") if not has_subtitle_stream else ""),
        )

    return _finish(workdir, output, checks, strict, thresholds)


def _finish(
    workdir: Path,
    output: Path,
    checks: list[Check],
    strict: bool,
    thresholds: QAThresholds | None = None,
) -> QAReport:
    counts = {PASS: 0, WARN: 0, FAIL: 0}
    for check in checks:
        counts[check.status] = counts.get(check.status, 0) + 1
    status = FAIL if counts[FAIL] else WARN if counts[WARN] else PASS
    summary = {
        **counts,
        "status": status,
        "strict": strict,
        "exit_code": 1 if counts[FAIL] or (strict and counts[WARN]) else 0,
    }
    threshold_dict = asdict(thresholds) if thresholds else {}
    report = QAReport(
        workdir=str(workdir),
        output=str(output),
        checks=checks,
        summary=summary,
        thresholds=threshold_dict,
    )
    atomic_write_json(workdir / "qa.json", report.to_dict())
    return report


def attach_to_manifest(workdir: Path, report: QAReport) -> None:
    """Persist the QA summary into manifest.json for downstream tooling."""
    path = workdir / "manifest.json"
    if not path.exists():
        return
    data = read_json(path, {}) or {}
    snapshot = data.get("settings_snapshot")
    if isinstance(snapshot, dict) and "ollama_url" in snapshot:
        endpoint = public_ollama_url(snapshot.pop("ollama_url"))
        if endpoint is not None:
            snapshot["ollama_url"] = endpoint
    summary = report.summary
    data["qa"] = summary
    kept = [
        flag
        for flag in (data.get("quality_flags") or [])
        if isinstance(flag, dict)
        and not str(flag.get("code") or "").startswith("qa.")
    ]
    new_flags: list[QualityFlag] = []
    if summary["warn"] or summary["fail"]:
        new_flags.append(
            QualityFlag(
                code="qa.fail" if summary["fail"] else "qa.warn",
                params={
                    "pass": summary["pass"],
                    "warn": summary["warn"],
                    "fail": summary["fail"],
                },
                severity="error" if summary["fail"] else "warning",
            )
        )
    data["quality_flags"] = merge_quality_flags(kept, new_flags)
    atomic_write_json(path, data)


def print_report(report: QAReport) -> None:
    labels = {PASS: "OK  ", WARN: "WARN", FAIL: "FAIL"}
    print(t("qa.report_title"))
    print("=" * 60)
    for check in report.checks:
        print(f"[{labels.get(check.status, '?')}] {check.id:<20} {check.message}")
    summary = report.summary
    print("-" * 60)
    print(
        t(
            "qa.result",
            status=summary["status"].upper(),
            pass_count=summary["pass"],
            warn_count=summary["warn"],
            fail_count=summary["fail"],
            exit_code=summary["exit_code"],
        )
    )
    LOG.info(
        "QA: %s (pass=%d warn=%d fail=%d)",
        summary["status"],
        summary["pass"],
        summary["warn"],
        summary["fail"],
    )
