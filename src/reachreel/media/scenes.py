"""Media probing and scene detection."""
from __future__ import annotations

import json
import logging
import re
import subprocess
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

from ..core.util import atomic_write_json, run
from ..domain.models import Scene, VideoInfo

LOG = logging.getLogger("reachreel.media.scenes")


def display_dimensions(
    coded_width: int,
    coded_height: int,
    rotation: float,
) -> tuple[int, int]:
    """Swap dimensions when 90°/270° rotation metadata is present."""
    if abs(rotation) % 180 == 90:
        return coded_height, coded_width
    return coded_width, coded_height


def probe_video(path: Path) -> VideoInfo:
    from ..core.util import ffprobe_json

    data = ffprobe_json(path)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not video:
        raise RuntimeError(f"No video stream in file: {path}")
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    if duration <= 0:
        raise RuntimeError(f"Cannot read video duration: {path}")
    fps_text = video.get("avg_frame_rate") or video.get("r_frame_rate") or "30/1"
    try:
        num, den = fps_text.split("/")
        fps = float(num) / float(den) if float(den) else 30.0
    except (ValueError, ZeroDivisionError):
        fps = 30.0
    rotation = 0.0
    for side_data in video.get("side_data_list", []) or []:
        if "rotation" in side_data:
            try:
                rotation = float(side_data["rotation"])
            except (TypeError, ValueError):
                rotation = 0.0
            break
    coded_width = int(video.get("width") or 0)
    coded_height = int(video.get("height") or 0)
    width, height = display_dimensions(coded_width, coded_height, rotation)
    return VideoInfo(
        path=str(path),
        duration=duration,
        width=width,
        height=height,
        fps=round(fps, 3),
        has_audio=audio is not None,
        audio_channels=int(audio.get("channels") or 0) if audio else 0,
        audio_sample_rate=int(audio.get("sample_rate") or 0) if audio else 0,
        rotation=rotation,
        coded_width=coded_width,
        coded_height=coded_height,
    )


def _detect_cuts(path: Path, threshold: float) -> list[float]:
    """Use ffmpeg's scene score to find hard cuts. Returns timestamps in seconds."""
    expr = f"select='gt(scene,{threshold})',showinfo"
    proc = run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-i",
            str(path),
            "-filter:v",
            expr,
            "-an",
            "-f",
            "null",
            "-",
        ],
        check=False,
    )
    text = (proc.stderr or "") + "\n" + (proc.stdout or "")
    cuts: list[float] = []
    for match in re.finditer(r"pts_time:([0-9]+(?:\.[0-9]+)?)", text):
        value = float(match.group(1))
        if value > 0.05:
            cuts.append(value)
    return sorted({round(c, 3) for c in cuts})


def _split_long(start: float, end: float, max_len: float) -> Iterable[tuple[float, float]]:
    length = end - start
    if length <= max_len:
        yield (start, end)
        return
    count = max(1, int(length // max_len) + (1 if length % max_len else 0))
    step = length / count
    for i in range(count):
        yield (start + i * step, min(end, start + (i + 1) * step))


def detect_scenes(
    path: Path,
    duration: float,
    *,
    threshold: float = 0.32,
    min_len: float = 1.2,
    max_len: float = 8.0,
    max_scenes: int = 160,
) -> list[Scene]:
    cuts = _detect_cuts(path, threshold)
    LOG.info("Detected %d scene cuts", len(cuts))
    boundaries = [0.0] + [c for c in cuts if 0.2 < c < duration - 0.2] + [duration]
    raw: list[tuple[float, float]] = []
    for start, end in ((boundaries[i], boundaries[i + 1]) for i in range(len(boundaries) - 1)):
        if end - start > 0.12:
            raw.extend(_split_long(start, end, max_len))

    # Merge very short scenes into their neighbour when that does not create an over-long scene.
    merged: list[tuple[float, float]] = []
    for start, end in raw:
        if merged and (end - start) < min_len:
            prev_start, _prev_end = merged[-1]
            if end - prev_start <= max_len * 1.4:
                merged[-1] = (prev_start, end)
                continue
        merged.append((start, end))

    # Final safety: if the detector exploded, group uniformly.
    if len(merged) > max_scenes:
        group = len(merged) / max_scenes
        compact: list[tuple[float, float]] = []
        for i in range(max_scenes):
            chunk = merged[int(i * group): int((i + 1) * group)]
            if chunk:
                compact.append((chunk[0][0], chunk[-1][1]))
        merged = compact

    scenes = [
        Scene(id=i + 1, start=round(start, 3), end=round(end, 3))
        for i, (start, end) in enumerate(merged)
        if end - start > 0.15
    ]
    if not scenes:
        scenes = [Scene(id=1, start=0.0, end=round(duration, 3))]
    LOG.info("Built %d scenes", len(scenes))
    return scenes


def _grab_frame(video: Path, time_s: float, out: Path, width: int) -> bool:
    proc = run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-ss",
            f"{max(0.0, time_s):.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-vf",
            f"scale='min({width},iw)':-2",
            "-q:v",
            "3",
            "-y",
            str(out),
        ],
        check=False,
    )
    return proc.returncode == 0 and out.exists() and out.stat().st_size > 0


def select_representative_frames(
    signatures: Sequence[bytes],
    *,
    threshold: float = 4.0,
) -> list[int]:
    """Return the indices of frames that are not near-duplicates.

    Signatures are small grayscale thumbnails (see ``_frame_signature``). The
    first frame is always kept; a later frame is kept only when its mean
    absolute pixel difference from the last kept frame reaches ``threshold``.
    A static long shot therefore collapses to one frame, while a pan keeps
    several. ``threshold <= 0`` disables the filter and keeps everything.
    """
    if not signatures:
        return []
    if threshold <= 0:
        return list(range(len(signatures)))
    kept = [0]
    for index in range(1, len(signatures)):
        previous = signatures[kept[-1]]
        current = signatures[index]
        if len(previous) != len(current) or not previous:
            kept.append(index)
            continue
        difference = sum(
            abs(left - right) for left, right in zip(previous, current, strict=True)
        ) / len(previous)
        if difference >= threshold:
            kept.append(index)
    return kept


def _frame_signature(path: Path, size: int = 16) -> bytes | None:
    """Downscale a JPEG to a ``size``x``size`` grayscale thumbnail."""
    proc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-vf",
            f"scale={size}:{size},format=gray",
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-",
        ],
        check=False,
        capture_output=True,
        timeout=60,
    )
    if proc.returncode != 0 or len(proc.stdout) != size * size:
        return None
    return proc.stdout


def _drop_duplicate_frames(scene: Scene, threshold: float) -> int:
    """Delete near-duplicate frames from one scene; return how many were dropped."""
    frames = [Path(p) for p in scene.frames]
    if len(frames) < 2:
        return 0
    signatures = [_frame_signature(frame) for frame in frames]
    if any(signature is None for signature in signatures):
        # Without a signature we cannot judge, so keep the frames.
        return 0
    kept = select_representative_frames(
        [signature for signature in signatures if signature is not None],
        threshold=threshold,
    )
    if len(kept) == len(frames):
        return 0
    dropped = 0
    for index, frame in enumerate(frames):
        if index in kept:
            continue
        frame.unlink(missing_ok=True)
        dropped += 1
    scene.frames = [str(frames[index]) for index in kept]
    return dropped


def extract_frames(
    video: Path,
    scenes: list[Scene],
    frames_dir: Path,
    *,
    per_scene: int = 3,
    width: int = 768,
    workers: int = 4,
    dedup: float = 4.0,
) -> None:
    frames_dir.mkdir(parents=True, exist_ok=True)
    jobs: list[tuple[Scene, float, Path]] = []
    for scene in scenes:
        count = max(1, min(per_scene, int(scene.duration // 1.0) + 1))
        if count == 1:
            times = [(scene.start + scene.end) / 2]
        else:
            pad = min(0.25, scene.duration * 0.1)
            lo, hi = scene.start + pad, scene.end - pad
            times = [lo + (hi - lo) * i / (count - 1) for i in range(count)]
        for idx, time_s in enumerate(times):
            out = frames_dir / f"scene_{scene.id:04d}_{idx:02d}.jpg"
            jobs.append((scene, time_s, out))

    LOG.info("Extracting %d keyframes...", len(jobs))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(_grab_frame, video, time_s, out, width): (scene, out)
            for scene, time_s, out in jobs
        }
        for future in as_completed(futures):
            scene, out = futures[future]
            try:
                ok = future.result()
            except Exception as exc:  # noqa: BLE001
                LOG.warning("Frame extraction failed %s: %s", out.name, exc)
                ok = False
            if ok:
                scene.frames.append(str(out))
            else:
                LOG.debug("Frame extraction failed: %s", out)

    for scene in scenes:
        scene.frames.sort()

    if dedup > 0:
        dropped = sum(_drop_duplicate_frames(scene, dedup) for scene in scenes)
        if dropped:
            LOG.info("Dropped %d near-duplicate frames (dedup threshold %.1f)", dropped, dedup)


def save_scenes(path: Path, info: VideoInfo, scenes: list[Scene]) -> None:
    atomic_write_json(
        path,
        {"video": asdict(info), "scenes": [s.to_dict() for s in scenes]},
    )


def load_scenes(path: Path) -> tuple[VideoInfo, list[Scene]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    info = VideoInfo(**data["video"])
    scenes = []
    for item in data["scenes"]:
        item = dict(item)
        item.pop("duration", None)
        scenes.append(Scene(**item))
    return info, scenes
