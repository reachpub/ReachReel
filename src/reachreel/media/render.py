"""Final render: burn subtitles, mux mixed audio, encode H.264."""
from __future__ import annotations

import logging
from pathlib import Path

from ..core.config import Settings
from ..core.util import ffmpeg_full, run

LOG = logging.getLogger("reachreel.media.render")


def _escape_filter_path(path: Path) -> str:
    text = str(path.resolve()).replace("\\", "/")
    text = text.replace(":", "\\:").replace("'", "\\'")
    return text


def output_dimensions(width: int, height: int, settings: Settings) -> tuple[int, int]:
    """Resolve the rendered frame size, matching `scale=-2:<height>`."""
    if settings.output_height <= 0 or height <= 0:
        return width, height
    out_height = int(settings.output_height)
    out_width = max(2, round(width * out_height / height))
    if out_width % 2:
        out_width += 1
    return out_width, out_height


def subtitle_style(settings: Settings) -> str:
    """Legacy SRT force_style.

    Kept for QA on workdirs rendered before ASS layout existed. New renders use
    `subtitles.ass`, where `subtitle_size` and margins are real output pixels.
    """
    from .subtitles import subtitle_border, subtitle_margins

    margin_v, margin_l, margin_r = subtitle_margins(settings)
    border_style, outline, shadow = subtitle_border(settings, None)
    return (
        f"FontName={settings.subtitle_font},"
        f"FontSize={settings.subtitle_size},"
        "PrimaryColour=&H00FFFFFF,"
        "BackColour=&H80000000,"
        "OutlineColour=&H00000000,"
        f"BorderStyle={border_style},"
        f"Outline={outline},"
        f"Shadow={shadow},"
        "Alignment=2,"
        f"MarginV={margin_v},"
        f"MarginL={margin_l},"
        f"MarginR={margin_r},"
        "WrapStyle=2"
    )


def render(
    *,
    video: Path,
    audio: Path,
    subtitles: Path,
    out: Path,
    settings: Settings,
    burn: bool = True,
) -> bool:
    """Render final.mp4. Returns True if subtitles were burned in."""
    from ..core.util import ffprobe_duration

    if not video.exists():
        raise RuntimeError(f"Source video not found: {video}")
    out.parent.mkdir(parents=True, exist_ok=True)
    duration = ffprobe_duration(audio)
    ffmpeg = ffmpeg_full() if burn else "ffmpeg"
    burned = bool(burn and ffmpeg and "subtitles" in _filters(ffmpeg))
    if burn and not burned:
        LOG.warning(
            "This ffmpeg build has no subtitles filter, so hard subtitles cannot be burned in. "
            "Install: brew install ffmpeg-full"
        )

    if burned:
        from .scenes import probe_video
        from .subtitles import write_ass_from_srt

        info = probe_video(video)
        out_width, out_height = output_dimensions(info.width, info.height, settings)
        if settings.subtitle_size < 24:
            LOG.warning(
                "subtitle_size=%d is interpreted as output pixels and may be too small; try 40-60",
                settings.subtitle_size,
            )
        ass_path = subtitles.with_suffix(".ass")
        if subtitles.suffix.lower() == ".srt":
            # Always rebuild the ASS overlay so CLI overrides (e.g. --subtitle-size)
            # take effect even when an old ASS file exists.
            write_ass_from_srt(
                subtitles,
                ass_path,
                width=out_width,
                height=out_height,
                settings=settings,
            )
        else:
            ass_path = subtitles
        LOG.info("Hard subtitle layout: %s (%dx%d)", ass_path.name, out_width, out_height)
        vf_parts: list[str] = []
        if settings.output_height:
            vf_parts.append(f"scale=-2:{settings.output_height}:flags=lanczos")
        if settings.output_fps:
            vf_parts.append(f"fps={settings.output_fps:g}")
        vf_parts.append(f"subtitles=filename='{_escape_filter_path(ass_path)}'")
        vf = ",".join(vf_parts)
        cmd = [
            str(ffmpeg or "ffmpeg"),
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video),
            "-i",
            str(audio),
            "-vf",
            vf,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-crf",
            str(settings.crf),
            "-preset",
            settings.preset,
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            settings.audio_bitrate,
            "-t",
            f"{duration:.3f}",
            "-movflags",
            "+faststart",
            str(out),
        ]
    else:
        # Soft-subtitle fallback: keep the SRT next to the movie.
        vf_parts = []
        if settings.output_height:
            vf_parts.append(f"scale=-2:{settings.output_height}:flags=lanczos")
        if settings.output_fps:
            vf_parts.append(f"fps={settings.output_fps:g}")
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video),
            "-i",
            str(audio),
            "-i",
            str(subtitles),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-map",
            "2:s:0",
            "-c:v",
            "libx264",
            "-crf",
            str(settings.crf),
            "-preset",
            settings.preset,
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            settings.audio_bitrate,
            "-c:s",
            "mov_text",
            "-metadata:s:s:0",
            "language=chi",
            "-t",
            f"{duration:.3f}",
            "-movflags",
            "+faststart",
            str(out),
        ]
        if vf_parts:
            cmd[cmd.index("-map"):cmd.index("-map")] = ["-vf", ",".join(vf_parts)]

    LOG.info("Rendered output: %s", out.name)
    run(cmd)
    return burned


def _filters(ffmpeg: str) -> set[str]:
    from ..core.util import ffmpeg_filters

    return ffmpeg_filters(ffmpeg)
