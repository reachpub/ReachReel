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

from ..core.config import Settings
from ..core.util import atomic_write_json, ffprobe_json, run
from ..domain.models import VideoInfo
from ..i18n import t


@dataclass(frozen=True)
class _AudioFormat:
    rate: int
    channels: int
    depth: int


@dataclass(frozen=True)
class _Timeline:
    video: Path
    narration: Path
    width: int
    height: int
    fps: Fraction
    video_frames: int
    audio_frames: int
    audio_duration: Fraction
    narration_audio: _AudioFormat
    embedded_audio: _AudioFormat


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


def _encode_video(source: VideoInfo, audio: Path, out: Path, settings: Settings, fps: Fraction) -> None:
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
    run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-i", source.path,
            "-i", audio, "-map", "0:V:0", "-map", "1:a:0", "-map_metadata", "-1",
            "-map_metadata:s", "-1",
            "-map_chapters", "-1", "-vf", video_filter,
            "-fps_mode", "passthrough", "-af", "apad", "-shortest", *codec, out,
        ],
        timeout=3600,
    )


def _timeline(video: Path, narration: Path, destination: Path) -> _Timeline:
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
    return _Timeline(
        video=destination / video.name, narration=destination / narration.name,
        width=picture["width"], height=picture["height"], fps=fps, video_frames=frames,
        audio_frames=audio_frames, audio_duration=duration,
        narration_audio=_AudioFormat(rate, channels, depth), embedded_audio=embedded,
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
    fields(sequence, name="ReachReel", duration=edit.video_frames)
    rate(sequence)
    media = ET.SubElement(sequence, "media")
    for kind, asset, duration in (
        ("video", edit.video, edit.video_frames),
        ("audio", edit.narration, edit.audio_frames),
    ):
        track_group = ET.SubElement(media, kind)
        if kind == "video":
            video_format(ET.SubElement(track_group, "format"))
        else:
            audio_format(ET.SubElement(track_group, "format"), edit.narration_audio)
        track = ET.SubElement(track_group, "track")
        clip = ET.SubElement(track, "clipitem", id=f"{kind}-clip")
        endpoint = min(duration, edit.video_frames)
        fields(clip, name=asset.name, duration=endpoint, start=0, end=endpoint,
               **{"in": 0, "out": endpoint})
        rate(clip)
        file = ET.SubElement(clip, "file", id=f"{kind}-file")
        fields(file, name=asset.name, pathurl=asset.as_uri(), duration=duration)
        rate(file)
        file_media = ET.SubElement(file, "media")
        if kind == "video":
            video_format(ET.SubElement(file_media, "video"))
            audio_file(file_media, edit.embedded_audio)
        else:
            audio_file(file_media, edit.narration_audio)
            fields(ET.SubElement(clip, "sourcetrack"), mediatype="audio", trackindex=1)
        fields(track, enabled="TRUE", locked="FALSE")
    _write_xml(path, root)


def _write_fcpxml(path: Path, edit: _Timeline) -> None:
    frame = 1 / edit.fps
    duration = f"{edit.video_frames * frame}s"
    root = ET.Element("fcpxml", version="1.9")
    resources = ET.SubElement(root, "resources")
    ET.SubElement(resources, "format", id="r1", frameDuration=f"{frame}s",
                  width=str(edit.width), height=str(edit.height))
    video = ET.SubElement(
        resources, "asset", id="r2", name=edit.video.name, start="0s", duration=duration,
        hasVideo="1", format="r1", hasAudio="1", audioSources="1",
        audioChannels=str(edit.embedded_audio.channels), audioRate=str(edit.embedded_audio.rate),
    )
    ET.SubElement(video, "media-rep", kind="original-media", src=edit.video.as_uri())
    audio = ET.SubElement(
        resources, "asset", id="r3", name=edit.narration.name, start="0s",
        duration=f"{edit.audio_duration}s", hasAudio="1", audioSources="1",
        audioChannels=str(edit.narration_audio.channels), audioRate=str(edit.narration_audio.rate),
    )
    ET.SubElement(audio, "media-rep", kind="original-media", src=edit.narration.as_uri())
    event = ET.SubElement(root, "event", name="ReachReel")
    project = ET.SubElement(event, "project", name="ReachReel")
    sequence = ET.SubElement(project, "sequence", format="r1", duration=duration,
                             tcStart="0s", tcFormat="NDF")
    spine = ET.SubElement(sequence, "spine")
    clip = ET.SubElement(spine, "asset-clip", ref="r2", name=edit.video.name,
                         offset="0s", start="0s", duration=duration, srcEnable="video")
    audio_duration = min(edit.audio_frames, edit.video_frames) * frame
    ET.SubElement(clip, "asset-clip", ref="r3", name=edit.narration.name, lane="-1",
                  offset="0s", start="0s", duration=f"{audio_duration}s", audioRole="dialogue")
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
) -> None:
    """Publish a complete bundle at a new path after every file is ready."""
    if destination.exists() or destination.is_symlink():
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
        _encode_video(source, staging / (mix or narration).name, video, settings, fps)
        edit = _timeline(video, staging / narration.name, destination)
        _write_xmeml(staging / "timeline.xml", edit)
        _write_fcpxml(staging / "timeline.fcpxml", edit)
        atomic_write_json(
            staging / "manifest.json",
            {
                "video": video.name,
                "narration": narration.name,
                "subtitles": subtitles.name,
                "mix": mix.name if mix else None,
                "timelines": {"premiere_resolve": "timeline.xml", "final_cut": "timeline.fcpxml"},
            },
        )
        try:
            staging.rename(destination)
        except OSError as exc:
            if destination.exists() or destination.is_symlink():
                raise FileExistsError(f"Export directory already exists: {destination}") from exc
            raise
