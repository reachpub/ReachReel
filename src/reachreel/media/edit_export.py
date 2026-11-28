"""Encode a subtitle-free preview and write XML interchange timelines."""
from __future__ import annotations

import shutil
import wave
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from fractions import Fraction
from math import isfinite
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from ..core.config import Settings
from ..core.util import atomic_write_json, ffmpeg_bin, ffprobe_json, run
from ..domain.cuts import CutList
from ..domain.models import NarrationSegment, VideoInfo
from ..i18n import t

NARRATION_MODES = ("track", "segments")


@dataclass(frozen=True)
class _AudioFormat:
    rate: int
    channels: int
    depth: int


@dataclass(frozen=True)
class _AudioStem:
    name: str
    role: str
    path: Path
    frames: int
    duration: Fraction
    fmt: _AudioFormat


@dataclass(frozen=True)
class _SegmentClip:
    id: int
    name: str
    path: Path
    start_frame: int
    frames: int
    duration: Fraction
    fmt: _AudioFormat


@dataclass(frozen=True)
class _VideoClip:
    timeline_start: int
    timeline_end: int
    source_in: int
    source_out: int

    @property
    def frames(self) -> int:
        return max(1, self.timeline_end - self.timeline_start)


@dataclass(frozen=True)
class _Timeline:
    video: Path
    narration: Path
    width: int
    height: int
    fps: Fraction
    video_frames: int
    sequence_frames: int
    audio_frames: int
    audio_duration: Fraction
    narration_audio: _AudioFormat
    embedded_audio: _AudioFormat
    video_clips: tuple[_VideoClip, ...] = ()
    segment_clips: tuple[_SegmentClip, ...] = ()
    extra_stems: tuple[_AudioStem, ...] = ()
    markers: tuple[tuple[int, str], ...] = ()


def _edit_rate(fps: float) -> Fraction:
    if isfinite(fps) and fps >= 1:
        timebase = round(fps)
        if fps == timebase:
            return Fraction(timebase)
        ntsc = Fraction(timebase * 1000, 1001)
        # The source probe reports rates to three decimal places.
        if timebase in (24, 30, 60, 120) and abs(fps - ntsc) <= 0.0005:
            return ntsc
    raise ValueError(t("error.export_frame_rate", fps=fps))


def _encode_video(
    source: VideoInfo,
    audio: Path,
    out: Path,
    settings: Settings,
    fps: Fraction,
    *,
    full_source_length: bool = False,
) -> None:
    height = settings.output_height
    # Both codecs need an even width, and 4:2:0 also needs an even height.
    out_height = str(height + height % 2) if height > 0 else "ceil(ih/2)*2"
    out_width = f"max(2,round(dar*({out_height})/2)*2)"
    video_filter = f"scale='{out_width}':{out_height}:flags=lanczos,setsar=1,fps={fps}"
    if out.suffix == ".mov":
        codec = [
            "-c:v", "prores_ks", "-profile:v", "2", "-pix_fmt", "yuv422p10le",
            "-c:a", "pcm_s16le",
        ]
    else:
        codec = [
            "-c:v", "libx264", "-preset", settings.preset, "-crf", str(settings.crf),
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", settings.audio_bitrate,
            "-movflags", "+faststart",
        ]
    length_args = ["-t", f"{source.duration:.3f}"] if full_source_length else ["-shortest"]
    run(
        [
            ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error", "-i", source.path,
            "-i", audio, "-map", "0:V:0", "-map", "1:a:0", "-map_metadata", "-1",
            "-map_metadata:s", "-1",
            "-map_chapters", "-1", "-vf", video_filter,
            "-fps_mode", "passthrough", "-af", "apad", *length_args, *codec, out,
        ],
        timeout=3600,
    )


def _extract_ambient(source: VideoInfo, out_wav: Path, duration: float) -> bool:
    """Extract the source camera/ambient audio as a clean 48 kHz stereo PCM stem."""
    if not source.has_audio:
        return False
    proc = run(
        [
            ffmpeg_bin(),
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-i",
            source.path,
            "-map",
            "0:a:0",
            "-t",
            f"{max(0.1, duration):.3f}",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(out_wav),
        ],
        check=False,
        timeout=600,
    )
    return proc.returncode == 0 and out_wav.is_file() and out_wav.stat().st_size > 44


def _read_wav_stem(path: Path, final_path: Path, fps: Fraction, *, name: str, role: str) -> _AudioStem | None:
    try:
        with wave.open(str(path), "rb") as audio:
            rate = audio.getframerate()
            duration = Fraction(audio.getnframes(), rate)
            channels = audio.getnchannels()
            depth = audio.getsampwidth() * 8
    except (OSError, wave.Error, EOFError):
        return None
    frames = int(duration * fps)
    if frames < 1:
        return None
    return _AudioStem(
        name=name,
        role=role,
        path=final_path,
        frames=frames,
        duration=duration,
        fmt=_AudioFormat(rate, channels, depth),
    )


def _timeline(
    video: Path,
    narration: Path,
    destination: Path,
    *,
    bgm: Path | None = None,
    ambient: Path | None = None,
    scenes: list[dict[str, Any]] | None = None,
    cuts: CutList | None = None,
    segment_files: list[tuple[NarrationSegment, Path, Path]] | None = None,
) -> _Timeline:
    streams = ffprobe_json(video)["streams"]
    if len(streams) != 2 or not streams[0].get("nb_frames"):
        raise ValueError("The editing timeline needs a video stream with frames and an audio stream.")
    picture, soundtrack = streams
    fps = Fraction(picture["avg_frame_rate"])
    frames = int(picture["nb_frames"])
    try:
        with wave.open(str(narration), "rb") as audio:
            rate = audio.getframerate()
            duration = Fraction(audio.getnframes(), rate)
            channels = audio.getnchannels()
            depth = audio.getsampwidth() * 8
    except (wave.Error, EOFError) as exc:
        raise ValueError(f"Cannot read PCM narration: {narration.name}") from exc
    audio_frames = int(duration * fps)
    if min(frames, audio_frames) < 1:
        raise ValueError("The editing timeline needs at least one frame of video and narration.")
    embedded = _AudioFormat(
        rate=int(soundtrack["sample_rate"]), channels=soundtrack["channels"],
        depth=soundtrack.get("bits_per_sample") or 16,
    )

    video_clips: list[_VideoClip] = []
    if cuts is not None and cuts.clips:
        cursor = 0
        for clip in cuts.clips:
            src_in = max(0, min(frames - 1, round(clip.start * float(fps))))
            src_out = max(src_in + 1, min(frames, round(clip.end * float(fps))))
            span = src_out - src_in
            video_clips.append(
                _VideoClip(
                    timeline_start=cursor,
                    timeline_end=cursor + span,
                    source_in=src_in,
                    source_out=src_out,
                )
            )
            cursor += span
        sequence_frames = max(1, cursor)
    else:
        video_clips = [_VideoClip(0, frames, 0, frames)]
        sequence_frames = frames

    segment_clips: list[_SegmentClip] = []
    for seg, staged_wav, final_wav in segment_files or []:
        stem = _read_wav_stem(staged_wav, final_wav, fps, name=final_wav.name, role="dialogue")
        if stem is None:
            continue
        start_frame = max(0, round(seg.start * float(fps)))
        if start_frame >= sequence_frames:
            continue
        segment_clips.append(
            _SegmentClip(
                id=seg.id,
                name=final_wav.name,
                path=final_wav,
                start_frame=start_frame,
                frames=stem.frames,
                duration=stem.duration,
                fmt=stem.fmt,
            )
        )

    extra_stems: list[_AudioStem] = []
    if bgm is not None and bgm.is_file():
        stem = _read_wav_stem(bgm, destination / bgm.name, fps, name="bgm", role="music")
        if stem is not None:
            extra_stems.append(stem)
    if ambient is not None and ambient.is_file():
        stem = _read_wav_stem(ambient, destination / ambient.name, fps, name="ambient", role="effects")
        if stem is not None:
            extra_stems.append(stem)

    markers: list[tuple[int, str]] = []
    for scene in scenes or []:
        if not isinstance(scene, dict):
            continue
        try:
            scene_id = int(scene.get("id") or 0)
            start_s = float(scene.get("start") or 0.0)
        except (TypeError, ValueError):
            continue
        if cuts is not None:
            mapped = cuts.source_to_output(start_s)
            if mapped is None:
                continue
            start_s = mapped
        frame_pos = round(start_s * float(fps))
        if 0 <= frame_pos < sequence_frames and scene_id > 0:
            summary = str(scene.get("summary") or "").strip()[:60]
            label = f"Scene {scene_id}: {summary}" if summary else f"Scene {scene_id}"
            markers.append((frame_pos, label))

    return _Timeline(
        video=destination / video.name, narration=destination / narration.name,
        width=picture["width"], height=picture["height"], fps=fps, video_frames=frames,
        sequence_frames=sequence_frames,
        audio_frames=audio_frames, audio_duration=duration,
        narration_audio=_AudioFormat(rate, channels, depth), embedded_audio=embedded,
        video_clips=tuple(video_clips),
        segment_clips=tuple(segment_clips),
        extra_stems=tuple(extra_stems),
        markers=tuple(markers),
    )


def _write_xml(path: Path, root: ET.Element) -> None:
    ET.indent(root, space="  ")
    path.write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE {root.tag}>\n'
        + ET.tostring(root, encoding="unicode") + "\n",
        encoding="utf-8",
    )


def _write_xmeml(path: Path, edit: _Timeline) -> None:
    def fields(parent: ET.Element, **values: int | str) -> None:
        for name, value in values.items():
            ET.SubElement(parent, name).text = str(value)

    def rate(parent: ET.Element) -> None:
        fields(ET.SubElement(parent, "rate"), timebase=round(edit.fps),
               ntsc="TRUE" if edit.fps.denominator != 1 else "FALSE")

    def video_format(parent: ET.Element) -> None:
        sample = ET.SubElement(parent, "samplecharacteristics")
        rate(sample)
        fields(sample, width=edit.width, height=edit.height, anamorphic="FALSE",
               pixelaspectratio="square", fielddominance="none")

    def audio_format(parent: ET.Element, audio: _AudioFormat) -> None:
        fields(ET.SubElement(parent, "samplecharacteristics"), depth=audio.depth, samplerate=audio.rate)

    def audio_file(parent: ET.Element, audio: _AudioFormat) -> None:
        stream = ET.SubElement(parent, "audio")
        audio_format(stream, audio)
        fields(stream, channelcount=audio.channels)

    root = ET.Element("xmeml", version="5")
    sequence = ET.SubElement(root, "sequence", id="reachreel-sequence")
    fields(sequence, name="ReachReel", duration=edit.sequence_frames)
    rate(sequence)
    media = ET.SubElement(sequence, "media")

    # Video track
    video_group = ET.SubElement(media, "video")
    video_format(ET.SubElement(video_group, "format"))
    video_track = ET.SubElement(video_group, "track")
    vclips = edit.video_clips or (_VideoClip(0, edit.video_frames, 0, edit.video_frames),)
    for idx, vclip in enumerate(vclips):
        clip_id = "video-clip" if len(vclips) == 1 else f"video-clip-{idx + 1}"
        clip = ET.SubElement(video_track, "clipitem", id=clip_id)
        fields(
            clip,
            name=edit.video.name,
            duration=edit.video_frames,
            start=vclip.timeline_start,
            end=vclip.timeline_end,
            **{"in": vclip.source_in, "out": vclip.source_out},
        )
        rate(clip)
        if idx == 0:
            file = ET.SubElement(clip, "file", id="video-file")
            fields(file, name=edit.video.name, pathurl=edit.video.as_uri(), duration=edit.video_frames)
            rate(file)
            file_media = ET.SubElement(file, "media")
            video_format(ET.SubElement(file_media, "video"))
            audio_file(file_media, edit.embedded_audio)
        else:
            ET.SubElement(clip, "file", id="video-file")
    fields(video_track, enabled="TRUE", locked="FALSE")

    # Audio tracks
    audio_group = ET.SubElement(media, "audio")
    audio_format(ET.SubElement(audio_group, "format"), edit.narration_audio)
    narration_track = ET.SubElement(audio_group, "track")
    if edit.segment_clips:
        for seg in edit.segment_clips:
            seg_end = min(seg.start_frame + seg.frames, edit.sequence_frames)
            seg_out = max(1, seg_end - seg.start_frame)
            clip = ET.SubElement(narration_track, "clipitem", id=f"reachreel-seg-{seg.id}")
            fields(
                clip,
                name=seg.name,
                duration=seg.frames,
                start=seg.start_frame,
                end=seg.start_frame + seg_out,
                **{"in": 0, "out": seg_out},
            )
            rate(clip)
            file = ET.SubElement(clip, "file", id=f"reachreel-seg-file-{seg.id}")
            fields(file, name=seg.name, pathurl=seg.path.as_uri(), duration=seg.frames)
            rate(file)
            audio_file(ET.SubElement(file, "media"), seg.fmt)
            fields(ET.SubElement(clip, "sourcetrack"), mediatype="audio", trackindex=1)
    else:
        clip = ET.SubElement(narration_track, "clipitem", id="audio-clip")
        endpoint = min(edit.audio_frames, edit.sequence_frames)
        fields(
            clip,
            name=edit.narration.name,
            duration=endpoint,
            start=0,
            end=endpoint,
            **{"in": 0, "out": endpoint},
        )
        rate(clip)
        file = ET.SubElement(clip, "file", id="audio-file")
        fields(file, name=edit.narration.name, pathurl=edit.narration.as_uri(), duration=edit.audio_frames)
        rate(file)
        audio_file(ET.SubElement(file, "media"), edit.narration_audio)
        fields(ET.SubElement(clip, "sourcetrack"), mediatype="audio", trackindex=1)
    fields(narration_track, enabled="TRUE", locked="FALSE")

    for stem in edit.extra_stems:
        stem_track = ET.SubElement(audio_group, "track")
        stem_clip = ET.SubElement(stem_track, "clipitem", id=f"{stem.name}-clip")
        stem_end = min(stem.frames, edit.sequence_frames)
        fields(stem_clip, name=stem.path.name, duration=stem_end, start=0, end=stem_end,
               **{"in": 0, "out": stem_end})
        rate(stem_clip)
        stem_file = ET.SubElement(stem_clip, "file", id=f"{stem.name}-file")
        fields(stem_file, name=stem.path.name, pathurl=stem.path.as_uri(), duration=stem.frames)
        rate(stem_file)
        audio_file(ET.SubElement(stem_file, "media"), stem.fmt)
        fields(ET.SubElement(stem_clip, "sourcetrack"), mediatype="audio", trackindex=1)
        fields(stem_track, enabled="TRUE", locked="FALSE")
    for frame_pos, label in edit.markers:
        marker = ET.SubElement(sequence, "marker")
        fields(marker, name=label, comment=label, **{"in": frame_pos, "out": -1})
    _write_xml(path, root)


def _write_fcpxml(path: Path, edit: _Timeline) -> None:
    frame = 1 / edit.fps
    video_duration = f"{edit.video_frames * frame}s"
    seq_duration = f"{edit.sequence_frames * frame}s"
    root = ET.Element("fcpxml", version="1.9")
    resources = ET.SubElement(root, "resources")
    ET.SubElement(resources, "format", id="r1", frameDuration=f"{frame}s",
                  width=str(edit.width), height=str(edit.height))
    video = ET.SubElement(
        resources, "asset", id="r2", name=edit.video.name, start="0s", duration=video_duration,
        hasVideo="1", format="r1", hasAudio="1", audioSources="1",
        audioChannels=str(edit.embedded_audio.channels), audioRate=str(edit.embedded_audio.rate),
    )
    ET.SubElement(video, "media-rep", kind="original-media", src=edit.video.as_uri())
    next_ref = 3
    narration_ref = f"r{next_ref}"
    next_ref += 1
    audio = ET.SubElement(
        resources, "asset", id=narration_ref, name=edit.narration.name, start="0s",
        duration=f"{edit.audio_duration}s", hasAudio="1", audioSources="1",
        audioChannels=str(edit.narration_audio.channels), audioRate=str(edit.narration_audio.rate),
    )
    ET.SubElement(audio, "media-rep", kind="original-media", src=edit.narration.as_uri())

    segment_refs: list[tuple[str, _SegmentClip]] = []
    for seg in edit.segment_clips:
        ref = f"r{next_ref}"
        next_ref += 1
        seg_asset = ET.SubElement(
            resources, "asset", id=ref, name=seg.name, start="0s",
            duration=f"{seg.duration}s", hasAudio="1", audioSources="1",
            audioChannels=str(seg.fmt.channels), audioRate=str(seg.fmt.rate),
        )
        ET.SubElement(seg_asset, "media-rep", kind="original-media", src=seg.path.as_uri())
        segment_refs.append((ref, seg))

    stem_refs: list[tuple[str, _AudioStem]] = []
    for stem in edit.extra_stems:
        ref = f"r{next_ref}"
        next_ref += 1
        stem_asset = ET.SubElement(
            resources, "asset", id=ref, name=stem.path.name, start="0s",
            duration=f"{stem.duration}s", hasAudio="1", audioSources="1",
            audioChannels=str(stem.fmt.channels), audioRate=str(stem.fmt.rate),
        )
        ET.SubElement(stem_asset, "media-rep", kind="original-media", src=stem.path.as_uri())
        stem_refs.append((ref, stem))

    event = ET.SubElement(root, "event", name="ReachReel")
    project = ET.SubElement(event, "project", name="ReachReel")
    sequence = ET.SubElement(project, "sequence", format="r1", duration=seq_duration,
                             tcStart="0s", tcFormat="NDF")
    spine = ET.SubElement(sequence, "spine")
    vclips = edit.video_clips or (_VideoClip(0, edit.video_frames, 0, edit.video_frames),)
    spine_nodes: list[tuple[_VideoClip, ET.Element]] = []
    for vclip in vclips:
        node = ET.SubElement(
            spine,
            "asset-clip",
            ref="r2",
            name=edit.video.name,
            offset=f"{vclip.timeline_start * frame}s",
            start=f"{vclip.source_in * frame}s",
            duration=f"{vclip.frames * frame}s",
            srcEnable="video",
        )
        spine_nodes.append((vclip, node))

    def _anchor_for(timeline_frame: int) -> tuple[_VideoClip, ET.Element]:
        for vclip, node in spine_nodes:
            if vclip.timeline_start <= timeline_frame < vclip.timeline_end:
                return vclip, node
        return spine_nodes[-1]

    if segment_refs:
        for ref, seg in segment_refs:
            parent_clip, parent_node = _anchor_for(seg.start_frame)
            local_offset = parent_clip.source_in + (seg.start_frame - parent_clip.timeline_start)
            dur_frames = min(seg.frames, max(1, edit.sequence_frames - seg.start_frame))
            ET.SubElement(
                parent_node,
                "asset-clip",
                ref=ref,
                name=seg.name,
                lane="-1",
                offset=f"{local_offset * frame}s",
                start="0s",
                duration=f"{dur_frames * frame}s",
                audioRole="dialogue",
            )
    else:
        first_clip, first_node = spine_nodes[0]
        audio_duration = min(edit.audio_frames, edit.sequence_frames) * frame
        ET.SubElement(
            first_node,
            "asset-clip",
            ref=narration_ref,
            name=edit.narration.name,
            lane="-1",
            offset=f"{first_clip.source_in * frame}s",
            start="0s",
            duration=f"{audio_duration}s",
            audioRole="dialogue",
        )

    first_clip, first_node = spine_nodes[0]
    for lane_idx, (ref, stem) in enumerate(stem_refs, start=2):
        stem_dur = min(stem.frames, edit.sequence_frames) * frame
        ET.SubElement(
            first_node,
            "asset-clip",
            ref=ref,
            name=stem.path.name,
            lane=f"-{lane_idx}",
            offset=f"{first_clip.source_in * frame}s",
            start="0s",
            duration=f"{stem_dur}s",
            audioRole=stem.role,
        )
    for frame_pos, label in edit.markers:
        parent_clip, parent_node = _anchor_for(frame_pos)
        local_pos = parent_clip.source_in + (frame_pos - parent_clip.timeline_start)
        ET.SubElement(
            parent_node, "marker", start=f"{local_pos * frame}s", duration=f"{frame}s", value=label
        )
    _write_xml(path, root)


def write_edit_bundle(
    destination: Path,
    *,
    source: VideoInfo,
    narration: Path,
    subtitles: Path,
    mix: Path | None,
    settings: Settings,
    video_format: str,
    bgm: Path | None = None,
    scenes: list[dict[str, Any]] | None = None,
    cuts: CutList | None = None,
    segments: list[NarrationSegment] | None = None,
    narration_mode: str = "track",
    overwrite: bool = False,
) -> None:
    """Publish a complete bundle at a new path after every file is ready."""
    if narration_mode not in NARRATION_MODES:
        raise ValueError(f"Unsupported narration mode: {narration_mode}")
    if not overwrite and (destination.exists() or destination.is_symlink()):
        raise FileExistsError(f"Export directory already exists: {destination}")
    fps = _edit_rate(settings.output_fps or source.fps)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".reachreel-export-", dir=destination.parent) as temp:
        staging = Path(temp) / "bundle"
        staging.mkdir()
        video = staging / f"video.{video_format}"
        for artifact in (narration, subtitles, mix):
            if artifact is not None:
                shutil.copyfile(artifact, staging / artifact.name)
        bgm_staged: Path | None = None
        if bgm is not None and bgm.is_file():
            bgm_staged = staging / "bgm.wav"
            shutil.copyfile(bgm, bgm_staged)
        ambient_staged: Path | None = None
        ambient_candidate = staging / "ambient.wav"
        if source.has_audio and _extract_ambient(source, ambient_candidate, source.duration):
            ambient_staged = ambient_candidate

        segment_files: list[tuple[NarrationSegment, Path, Path]] = []
        if narration_mode == "segments" and segments:
            seg_dir = staging / "segments"
            seg_dir.mkdir(parents=True, exist_ok=True)
            for seg in segments:
                if not seg.enabled or not seg.audio_path:
                    continue
                src_wav = Path(seg.audio_path)
                if not src_wav.is_file():
                    continue
                name = f"seg_{seg.id:03d}.wav"
                staged_wav = seg_dir / name
                shutil.copyfile(src_wav, staged_wav)
                segment_files.append((seg, staged_wav, destination / "segments" / name))

        _encode_video(
            source,
            staging / (mix or narration).name,
            video,
            settings,
            fps,
            full_source_length=cuts is not None and bool(cuts.clips),
        )
        edit = _timeline(
            video,
            staging / narration.name,
            destination,
            bgm=bgm_staged,
            ambient=ambient_staged,
            scenes=scenes,
            cuts=cuts,
            segment_files=segment_files,
        )
        _write_xmeml(staging / "timeline.xml", edit)
        _write_fcpxml(staging / "timeline.fcpxml", edit)
        atomic_write_json(
            staging / "manifest.json",
            {
                "video": video.name,
                "narration": narration.name,
                "narration_mode": narration_mode,
                "segments": [f"segments/{seg.name}" for seg in edit.segment_clips],
                "subtitles": subtitles.name,
                "mix": mix.name if mix else None,
                "bgm": bgm_staged.name if bgm_staged else None,
                "ambient": ambient_staged.name if ambient_staged else None,
                "cuts_applied": bool(cuts is not None and cuts.clips),
                "timelines": {"premiere_resolve": "timeline.xml", "final_cut": "timeline.fcpxml"},
            },
        )
        if overwrite and (destination.exists() or destination.is_symlink()):
            backup = Path(temp) / "previous"
            destination.rename(backup)
            try:
                staging.rename(destination)
            except OSError:
                backup.rename(destination)
                raise
        else:
            try:
                staging.rename(destination)
            except OSError as exc:
                if destination.exists() or destination.is_symlink():
                    raise FileExistsError(f"Export directory already exists: {destination}") from exc
                raise
