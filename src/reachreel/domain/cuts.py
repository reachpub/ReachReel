"""Lightweight non-destructive source video cut list (hard cuts only)."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from ..core.util import atomic_write_json, read_json
from .models import Scene

CUTS_SCHEMA_VERSION = 1
MIN_CLIP_SECONDS = 0.05

_HMS_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{1,2}(?:\.\d+)?)$")


@dataclass(frozen=True)
class CutClip:
    """One kept source interval in playback order."""

    id: int
    start: float
    end: float
    label: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["start"] = round(self.start, 3)
        data["end"] = round(self.end, 3)
        if not self.label:
            data.pop("label", None)
        return data


@dataclass
class CutList:
    """Ordered kept intervals of the source video."""

    clips: list[CutClip]
    source_duration: float
    schema_version: int = CUTS_SCHEMA_VERSION

    @property
    def edited_duration(self) -> float:
        return round(sum(clip.duration for clip in self.clips), 3)

    def validate(self, source_duration: float | None = None) -> None:
        limit = self.source_duration if source_duration is None else float(source_duration)
        if not self.clips:
            raise ValueError("Cut list must keep at least one video clip.")
        for clip in self.clips:
            if clip.start < -1e-6 or clip.end <= clip.start:
                raise ValueError(
                    f"Invalid cut clip {clip.id}: start={clip.start:.3f}, end={clip.end:.3f}"
                )
            if clip.duration < MIN_CLIP_SECONDS:
                raise ValueError(
                    f"Cut clip {clip.id} is shorter than {MIN_CLIP_SECONDS:.2f}s "
                    f"({clip.duration:.3f}s)."
                )
            if limit > 0 and clip.end > limit + 0.05:
                raise ValueError(
                    f"Cut clip {clip.id} end ({clip.end:.3f}s) exceeds source duration "
                    f"({limit:.3f}s)."
                )

    def source_to_output(self, source_time: float) -> float | None:
        """Map a source timestamp onto the first matching edited-timeline timestamp."""
        cursor = 0.0
        for clip in self.clips:
            if clip.start - 1e-6 <= source_time <= clip.end + 1e-6:
                clamped = min(max(source_time, clip.start), clip.end)
                return round(cursor + (clamped - clip.start), 3)
            cursor += clip.duration
        return None

    def output_to_source(self, output_time: float) -> float | None:
        """Map an edited-timeline timestamp back to its source video timestamp."""
        if output_time < -1e-6:
            return None
        cursor = 0.0
        for clip in self.clips:
            clip_end = cursor + clip.duration
            if output_time <= clip_end + 1e-6:
                offset = min(max(output_time - cursor, 0.0), clip.duration)
                return round(clip.start + offset, 3)
            cursor = clip_end
        return None

    def output_interval_to_source(
        self, start: float, end: float
    ) -> tuple[float, float] | None:
        """Map an edited-timeline interval onto the dominant overlapping source clip."""
        if end <= start:
            return None
        best: tuple[float, float, float] | None = None
        cursor = 0.0
        for clip in self.clips:
            clip_end = cursor + clip.duration
            ov_start = max(start, cursor)
            ov_end = min(end, clip_end)
            overlap = ov_end - ov_start
            if overlap > 0:
                src_start = clip.start + (ov_start - cursor)
                src_end = clip.start + (ov_end - cursor)
                if best is None or overlap > best[0]:
                    best = (overlap, round(src_start, 3), round(src_end, 3))
            cursor = clip_end
        if best is None:
            return None
        return best[1], best[2]

    def project_scenes(self, scenes: list[Scene]) -> tuple[list[Scene], set[int]]:
        """Project source scenes onto the edited timeline.

        Returns ``(projected_scenes, dropped_scene_ids)``. Each kept scene gets
        its earliest start and latest end in edited-timeline coordinates; scenes
        with no overlap with any kept clip are reported as dropped.
        """
        bounds: dict[int, tuple[float, float]] = {}
        cursor = 0.0
        for clip in self.clips:
            for scene in scenes:
                ov_start = max(scene.start, clip.start)
                ov_end = min(scene.end, clip.end)
                if ov_end - ov_start < MIN_CLIP_SECONDS:
                    continue
                out_start = round(cursor + (ov_start - clip.start), 3)
                out_end = round(cursor + (ov_end - clip.start), 3)
                prev = bounds.get(scene.id)
                if prev is None:
                    bounds[scene.id] = (out_start, out_end)
                else:
                    bounds[scene.id] = (min(prev[0], out_start), max(prev[1], out_end))
            cursor += clip.duration

        projected: list[Scene] = []
        dropped: set[int] = set()
        for scene in scenes:
            span = bounds.get(scene.id)
            if span is None:
                dropped.add(scene.id)
                continue
            projected.append(replace(scene, start=span[0], end=span[1]))
        projected.sort(key=lambda item: (item.start, item.id))
        return projected, dropped

    def is_identity(self, tolerance: float = 0.05) -> bool:
        """Return True if the cut list keeps the entire source unchanged."""
        if len(self.clips) != 1:
            return False
        clip = self.clips[0]
        return clip.start <= tolerance and abs(clip.end - self.source_duration) <= tolerance

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_duration": round(self.source_duration, 3),
            "edited_duration": self.edited_duration,
            "clips": [clip.to_dict() for clip in self.clips],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CutList:
        if not isinstance(data, dict):
            raise TypeError("Cut list file must be a JSON object.")
        raw_clips = data.get("clips")
        if not isinstance(raw_clips, list) or not raw_clips:
            raise ValueError("Cut list must contain a non-empty 'clips' list.")
        clips: list[CutClip] = []
        for idx, item in enumerate(raw_clips, start=1):
            if not isinstance(item, dict):
                raise TypeError(f"Cut clip #{idx} must be an object.")
            try:
                clip_id = int(item.get("id") or idx)
                start = float(item["start"])
                end = float(item["end"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"Invalid cut clip #{idx}: {item}") from exc
            label = str(item.get("label") or "").strip()
            clips.append(CutClip(id=clip_id, start=round(start, 3), end=round(end, 3), label=label))
        source_duration = float(data.get("source_duration") or 0.0)
        schema_version = int(data.get("schema_version") or CUTS_SCHEMA_VERSION)
        cut_list = cls(
            clips=clips,
            source_duration=round(source_duration, 3),
            schema_version=schema_version,
        )
        cut_list.validate(source_duration if source_duration > 0 else None)
        return cut_list


def parse_timestamp(value: str) -> float:
    """Parse seconds (``12.5``) or ``[HH:]MM:SS[.ms]`` into float seconds."""
    text = value.strip()
    if not text:
        raise ValueError("Empty timestamp")
    match = _HMS_RE.match(text)
    if match:
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2))
        seconds = float(match.group(3))
        if minutes >= 60 or seconds >= 60:
            raise ValueError(f"Invalid timestamp: {value}")
        return round(hours * 3600 + minutes * 60 + seconds, 3)
    try:
        parsed = float(text)
    except ValueError as exc:
        raise ValueError(f"Invalid timestamp: {value}") from exc
    if parsed < 0:
        raise ValueError(f"Timestamp must be >= 0: {value}")
    return round(parsed, 3)


def parse_ranges(spec: str) -> list[tuple[float, float]]:
    """Parse comma-separated time ranges like ``0-2.5,10-15.2`` or ``00:02-00:10``."""
    parts = [part.strip() for part in (spec or "").split(",") if part.strip()]
    if not parts:
        raise ValueError("Range specification is empty.")
    ranges: list[tuple[float, float]] = []
    for part in parts:
        if "-" not in part:
            raise ValueError(f"Range must use 'start-end' format: {part}")
        start_raw, end_raw = part.split("-", 1)
        start = parse_timestamp(start_raw)
        end = parse_timestamp(end_raw)
        if end <= start:
            raise ValueError(f"Range end must be greater than start: {part}")
        ranges.append((start, end))
    return ranges


def build_keep_cuts(
    keep_ranges: list[tuple[float, float]],
    source_duration: float,
) -> CutList:
    """Create a validated CutList directly from kept source intervals."""
    if source_duration <= 0:
        raise ValueError("Source duration must be positive.")
    clips: list[CutClip] = []
    for idx, (start, end) in enumerate(keep_ranges, start=1):
        clamped_end = min(end, source_duration)
        if clamped_end <= start:
            raise ValueError(
                f"Kept range {start:.3f}-{end:.3f} is outside source duration {source_duration:.3f}s."
            )
        clips.append(CutClip(id=idx, start=round(start, 3), end=round(clamped_end, 3)))
    cut_list = CutList(clips=clips, source_duration=round(source_duration, 3))
    cut_list.validate(source_duration)
    return cut_list


def build_remove_cuts(
    remove_ranges: list[tuple[float, float]],
    source_duration: float,
) -> CutList:
    """Create a CutList by subtracting removed source intervals from ``[0, source_duration]``."""
    if source_duration <= 0:
        raise ValueError("Source duration must be positive.")
    # Normalize and merge overlapping removal ranges.
    clamped: list[tuple[float, float]] = []
    for start, end in remove_ranges:
        s = max(0.0, min(start, source_duration))
        e = max(0.0, min(end, source_duration))
        if e > s:
            clamped.append((s, e))
    clamped.sort()
    merged: list[tuple[float, float]] = []
    for s, e in clamped:
        if merged and s <= merged[-1][1] + 1e-6:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))

    kept: list[CutClip] = []
    cursor = 0.0
    clip_id = 1
    for rm_start, rm_end in merged:
        if rm_start - cursor >= MIN_CLIP_SECONDS:
            kept.append(CutClip(id=clip_id, start=round(cursor, 3), end=round(rm_start, 3)))
            clip_id += 1
        cursor = max(cursor, rm_end)
    if source_duration - cursor >= MIN_CLIP_SECONDS:
        kept.append(CutClip(id=clip_id, start=round(cursor, 3), end=round(source_duration, 3)))

    if not kept:
        raise ValueError("Removed ranges leave no remaining video footage.")
    cut_list = CutList(clips=kept, source_duration=round(source_duration, 3))
    cut_list.validate(source_duration)
    return cut_list


def load_cuts(path: Path) -> CutList | None:
    """Load and validate ``cuts.json`` if it exists; return ``None`` if absent."""
    if not path.is_file():
        return None
    data = read_json(path, None)
    if data is None:
        return None
    return CutList.from_dict(data)


def save_cuts(path: Path, cuts: CutList) -> None:
    """Persist ``cuts.json`` atomically."""
    cuts.validate()
    atomic_write_json(path, cuts.to_dict())
