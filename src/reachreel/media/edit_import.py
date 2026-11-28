"""Parse FCP7 XML (xmeml) and FCPXML timelines back into ReachReel fine-tuning edits."""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

from ..domain.cuts import CutClip, CutList
from ..domain.models import NarrationSegment

_SEG_ID_RE = re.compile(r"(?:reachreel-seg-|seg_0*)(\d+)\b", re.IGNORECASE)
_FCP_TIME_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)(?:/(\d+))?s\s*$")
_DB_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*dB", re.IGNORECASE)


@dataclass
class TimelineImportPlan:
    """Structured edits extracted from an external NLE XML or FCPXML file."""

    source_format: str  # "xmeml" | "fcpxml"
    cuts: CutList | None = None
    cuts_changed: bool = False
    segment_starts: dict[int, float] = field(default_factory=dict)
    segment_gains: dict[int, float] = field(default_factory=dict)
    disabled_segment_ids: list[int] = field(default_factory=list)
    matched_segment_ids: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_format": self.source_format,
            "cuts_changed": self.cuts_changed,
            "cuts": self.cuts.to_dict() if self.cuts is not None else None,
            "segment_starts": {str(k): v for k, v in sorted(self.segment_starts.items())},
            "segment_gains": {str(k): v for k, v in sorted(self.segment_gains.items())},
            "disabled_segment_ids": list(self.disabled_segment_ids),
            "matched_segment_ids": list(self.matched_segment_ids),
            "warnings": list(self.warnings),
        }


def _match_segment_id(*candidates: str | None) -> int | None:
    for text in candidates:
        if not text:
            continue
        match = _SEG_ID_RE.search(text)
        if match:
            return int(match.group(1))
    return None


def _parse_fcp_time(raw: str | None) -> Fraction:
    """Parse FCPXML time values like ``0s``, ``150/30s``, or ``14.5s`` into seconds."""
    if not raw:
        return Fraction(0)
    match = _FCP_TIME_RE.match(raw)
    if not match:
        raise ValueError(f"Unsupported FCPXML time value: {raw!r}")
    num_str, den_str = match.group(1), match.group(2)
    if den_str:
        den = int(den_str)
        if den <= 0:
            raise ValueError(f"Invalid FCPXML time denominator: {raw!r}")
        return Fraction(int(num_str), den)
    return Fraction(num_str)


def _xmeml_fps(elem: ET.Element | None, fallback: Fraction = Fraction(30)) -> Fraction:
    if elem is None:
        return fallback
    rate = elem.find("rate")
    if rate is None:
        return fallback
    tb_text = (rate.findtext("timebase") or "").strip()
    if not tb_text:
        return fallback
    try:
        timebase = int(tb_text)
    except ValueError:
        return fallback
    if timebase <= 0:
        return fallback
    ntsc = (rate.findtext("ntsc") or "").strip().upper() == "TRUE"
    if ntsc:
        return Fraction(timebase * 1000, 1001)
    return Fraction(timebase)


def _xmeml_int(elem: ET.Element, tag: str) -> int | None:
    raw = (elem.findtext(tag) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _xmeml_gain_db(clip: ET.Element) -> float | None:
    for filt in clip.findall("filter"):
        effect = filt.find("effect")
        if effect is None:
            continue
        effect_id = (effect.findtext("effectid") or "").strip().lower()
        if effect_id != "audiolevels":
            continue
        for param in effect.findall("parameter"):
            param_id = (param.findtext("parameterid") or "").strip().lower()
            if param_id != "level":
                continue
            raw = (param.findtext("value") or "").strip()
            try:
                linear = float(raw)
            except ValueError:
                continue
            if linear > 0:
                db = 20.0 * math.log10(linear)
                return round(max(-24.0, min(12.0, db)), 2)
    return None


def _parse_xmeml(
    root: ET.Element,
    *,
    source_duration: float,
    existing_segments: list[NarrationSegment],
) -> TimelineImportPlan:
    sequence = root.find("sequence")
    if sequence is None:
        sequence = root.find(".//sequence")
    if sequence is None:
        raise ValueError("FCP7 XML does not contain a <sequence> element.")

    seq_fps = _xmeml_fps(sequence)
    warnings: list[str] = []

    if sequence.findall(".//transitionitem"):
        warnings.append(
            "Detected NLE transitions (<transitionitem>); ReachReel imports hard cuts only."
        )

    media = sequence.find("media")
    video_clips: list[CutClip] = []
    if media is not None:
        video_tracks = media.findall("./video/track")
        non_empty_vtracks = [tr for tr in video_tracks if tr.findall("clipitem")]
        if len(non_empty_vtracks) > 1:
            warnings.append(
                f"Detected {len(non_empty_vtracks)} video tracks; importing primary track 1 only."
            )
        if non_empty_vtracks:
            primary_track = non_empty_vtracks[0]
            raw_items: list[tuple[int, int, int, Fraction, str]] = []
            for clip in primary_track.findall("clipitem"):
                if (clip.findtext("enabled") or "TRUE").strip().upper() == "FALSE":
                    continue
                c_fps = _xmeml_fps(clip, seq_fps)
                tl_start = _xmeml_int(clip, "start")
                src_in = _xmeml_int(clip, "in")
                src_out = _xmeml_int(clip, "out")
                if src_in is None or src_out is None or src_out <= src_in:
                    continue
                if any(
                    (f.findtext("./effect/effectid") or "").strip().lower() == "timeremap"
                    for f in clip.findall("filter")
                ):
                    warnings.append(
                        f"Video clip '{clip.findtext('name') or ''}' has speed/time-remap effects "
                        "which are not imported."
                    )
                order = tl_start if tl_start is not None and tl_start >= 0 else len(raw_items)
                raw_items.append((order, src_in, src_out, c_fps, (clip.findtext("name") or "").strip()))
            raw_items.sort(key=lambda item: item[0])
            for idx, (_, src_in, src_out, clip_fps, label) in enumerate(raw_items, start=1):
                start_s = round(float(Fraction(src_in) / clip_fps), 3)
                end_s = round(float(Fraction(src_out) / clip_fps), 3)
                if source_duration > 0:
                    end_s = min(end_s, round(source_duration, 3))
                if end_s > start_s:
                    video_clips.append(CutClip(id=idx, start=start_s, end=end_s, label=label))

    segment_starts: dict[int, float] = {}
    segment_gains: dict[int, float] = {}
    if media is not None:
        for track in media.findall("./audio/track"):
            if (track.findtext("enabled") or "TRUE").strip().upper() == "FALSE":
                continue
            for clip in track.findall("clipitem"):
                if (clip.findtext("enabled") or "TRUE").strip().upper() == "FALSE":
                    continue
                clip_id_attr = clip.get("id")
                clip_name = clip.findtext("name")
                file_el = clip.find("file")
                file_name = file_el.findtext("name") if file_el is not None else None
                seg_id = _match_segment_id(clip_id_attr, clip_name, file_name)
                if seg_id is None:
                    continue
                tl_start = _xmeml_int(clip, "start")
                if tl_start is None or tl_start < 0:
                    continue
                c_fps = _xmeml_fps(clip, seq_fps)
                segment_starts[seg_id] = round(float(Fraction(tl_start) / c_fps), 3)
                gain = _xmeml_gain_db(clip)
                if gain is not None and abs(gain) > 0.05:
                    segment_gains[seg_id] = gain

    return _finalize_plan(
        source_format="xmeml",
        video_clips=video_clips,
        source_duration=source_duration,
        existing_segments=existing_segments,
        segment_starts=segment_starts,
        segment_gains=segment_gains,
        warnings=warnings,
    )


def _fcpxml_gain_db(clip: ET.Element) -> float | None:
    vol = clip.find("adjust-volume")
    if vol is None:
        return None
    amount = vol.get("amount") or ""
    match = _DB_RE.search(amount)
    if not match:
        return None
    db = float(match.group(1))
    return round(max(-24.0, min(12.0, db)), 2)


def _parse_fcpxml(
    root: ET.Element,
    *,
    source_duration: float,
    existing_segments: list[NarrationSegment],
) -> TimelineImportPlan:
    spine = root.find(".//sequence/spine")
    if spine is None:
        raise ValueError("FCPXML does not contain a <sequence>/<spine> element.")

    assets: dict[str, str] = {}
    for asset in root.findall("./resources/asset"):
        aid = asset.get("id")
        if aid:
            assets[aid] = asset.get("name") or ""

    warnings: list[str] = []
    if spine.findall(".//transition"):
        warnings.append(
            "Detected FCPXML transitions (<transition>); ReachReel imports hard cuts only."
        )
    if spine.findall(".//timeMap"):
        warnings.append(
            "Detected FCPXML speed/timeMap retiming; ReachReel does not import speed ramps."
        )

    video_clips: list[CutClip] = []
    segment_starts: dict[int, float] = {}
    segment_gains: dict[int, float] = {}

    spine_cursor = Fraction(0)
    clip_idx = 0
    for child in list(spine):
        tag = child.tag
        if tag == "transition":
            continue
        if tag == "gap":
            gap_dur = _parse_fcp_time(child.get("duration"))
            warnings.append(
                f"Detected a {float(gap_dur):.2f}s <gap> in FCPXML spine; gaps are not imported."
            )
            spine_cursor += gap_dur
            continue
        if tag not in {"asset-clip", "clip", "mc-clip", "ref-clip"}:
            continue

        offset = _parse_fcp_time(child.get("offset")) if child.get("offset") else spine_cursor
        start = _parse_fcp_time(child.get("start"))
        dur = _parse_fcp_time(child.get("duration"))
        spine_cursor = offset + dur

        if child.get("enabled", "1") != "0" and dur > 0:
            clip_idx += 1
            start_s = round(float(start), 3)
            end_s = round(float(start + dur), 3)
            if source_duration > 0:
                end_s = min(end_s, round(source_duration, 3))
            if end_s > start_s:
                video_clips.append(
                    CutClip(
                        id=clip_idx,
                        start=start_s,
                        end=end_s,
                        label=(child.get("name") or "").strip(),
                    )
                )

        # Inspect connected audio clips attached to this spine clip.
        for attached in child.findall("asset-clip"):
            if attached.get("enabled", "1") == "0":
                continue
            ref_name = assets.get(attached.get("ref") or "", "")
            seg_id = _match_segment_id(attached.get("name"), ref_name)
            if seg_id is None:
                continue
            local_offset = _parse_fcp_time(attached.get("offset"))
            # FCPXML connected clip offset is in parent clip's media time.
            timeline_pos = offset + (local_offset - start)
            segment_starts[seg_id] = round(max(0.0, float(timeline_pos)), 3)
            gain = _fcpxml_gain_db(attached)
            if gain is not None and abs(gain) > 0.05:
                segment_gains[seg_id] = gain

    return _finalize_plan(
        source_format="fcpxml",
        video_clips=video_clips,
        source_duration=source_duration,
        existing_segments=existing_segments,
        segment_starts=segment_starts,
        segment_gains=segment_gains,
        warnings=warnings,
    )


def _finalize_plan(
    *,
    source_format: str,
    video_clips: list[CutClip],
    source_duration: float,
    existing_segments: list[NarrationSegment],
    segment_starts: dict[int, float],
    segment_gains: dict[int, float],
    warnings: list[str],
) -> TimelineImportPlan:
    cuts: CutList | None = None
    cuts_changed = False
    if video_clips:
        effective_source_dur = (
            source_duration
            if source_duration > 0
            else max(clip.end for clip in video_clips)
        )
        candidate = CutList(clips=video_clips, source_duration=round(effective_source_dur, 3))
        candidate.validate(effective_source_dur)
        if not candidate.is_identity():
            cuts = candidate
            cuts_changed = True

    existing_ids = {seg.id for seg in existing_segments}
    matched_ids = sorted(sid for sid in segment_starts if sid in existing_ids)
    unknown_ids = sorted(sid for sid in segment_starts if sid not in existing_ids)
    for sid in unknown_ids:
        warnings.append(f"Timeline references segment {sid}, which does not exist in script.json.")
        segment_starts.pop(sid, None)
        segment_gains.pop(sid, None)

    disabled_ids: list[int] = []
    if matched_ids:
        # Only infer deletions when segment-level clips were present in the timeline.
        matched_set = set(matched_ids)
        disabled_ids = sorted(
            seg.id
            for seg in existing_segments
            if seg.enabled and seg.id not in matched_set
        )

    return TimelineImportPlan(
        source_format=source_format,
        cuts=cuts,
        cuts_changed=cuts_changed,
        segment_starts=segment_starts,
        segment_gains=segment_gains,
        disabled_segment_ids=disabled_ids,
        matched_segment_ids=matched_ids,
        warnings=warnings,
    )


def parse_timeline_xml(
    path: Path,
    *,
    source_duration: float,
    existing_segments: list[NarrationSegment],
) -> TimelineImportPlan:
    """Parse an FCP7 XML (``xmeml``) or FCPXML file into a ``TimelineImportPlan``."""
    if not path.is_file():
        raise ValueError(f"Timeline file not found: {path}")
    try:
        tree = ET.parse(path)
    except (ET.ParseError, OSError) as exc:
        raise ValueError(f"Cannot parse XML timeline {path.name}: {exc}") from exc
    root = tree.getroot()
    tag = root.tag.lower()
    if tag == "xmeml":
        return _parse_xmeml(
            root,
            source_duration=source_duration,
            existing_segments=existing_segments,
        )
    if tag == "fcpxml":
        return _parse_fcpxml(
            root,
            source_duration=source_duration,
            existing_segments=existing_segments,
        )
    raise ValueError(
        f"Unsupported XML root <{root.tag}> in {path.name}; expected <xmeml> (FCP7 XML) or <fcpxml>."
    )
