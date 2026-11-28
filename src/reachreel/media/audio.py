"""BGM selection and final audio mix with side-chain ducking."""
from __future__ import annotations

import json
import logging
import random
import re
from pathlib import Path
from typing import Any

from ..core.config import Settings
from ..core.util import ffmpeg_bin, run
from ..domain.cuts import CutList
from ..i18n import t

LOG = logging.getLogger("reachreel.media.audio")

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
BGM_CATALOG = "manifest.json"


def load_bgm_catalog(directory: Path) -> dict[str, dict[str, Any]]:
    """Load optional per-track metadata from ``<bgm_dir>/manifest.json``."""
    path = directory / BGM_CATALOG
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        LOG.warning("Could not read the BGM catalog %s: %s", path, exc)
        return {}
    tracks = data.get("tracks") if isinstance(data, dict) else None
    if not isinstance(tracks, list):
        return {}
    catalog: dict[str, dict[str, Any]] = {}
    for item in tracks:
        if not isinstance(item, dict):
            continue
        name = str(item.get("file") or "").strip()
        if name:
            catalog[name] = dict(item)
    return catalog


def bgm_metadata(path: Path) -> dict[str, Any]:
    """Return catalog metadata for one track, falling back to the file name."""
    path = path.expanduser().resolve()
    entry = dict(load_bgm_catalog(path.parent).get(path.name, {}))
    entry.setdefault("file", path.name)
    return entry


def bgm_license_markdown(entries: list[dict[str, Any]]) -> str:
    """Render a Markdown license table for the tracks used by a project."""
    header = [
        t("licenses.file"),
        t("licenses.title"),
        t("licenses.author"),
        t("licenses.source"),
        t("licenses.license"),
    ]
    lines = [f"# {t('licenses.heading')}", "", "| " + " | ".join(header) + " |"]
    lines.append("|" + "---|" * len(header))
    if not entries:
        lines += ["", t("licenses.no_metadata")]
        return "\n".join(lines) + "\n"
    for entry in entries:
        cells = [str(entry.get(key) or t("licenses.unknown")) for key in ("file", "title", "author", "source", "license")]
        lines.append("| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |")
    return "\n".join(lines) + "\n"


def pick_bgm(settings: Settings) -> Path | None:
    if settings.bgm_file:
        path = Path(settings.bgm_file).expanduser()
        if not path.exists():
            raise RuntimeError(f"BGM file not found: {path}")
        return path
    directory = settings.resolved_bgm_dir()
    directory.mkdir(parents=True, exist_ok=True)
    tracks = sorted(
        path for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in AUDIO_EXTS
    )
    if not tracks:
        return None
    chosen = random.choice(tracks)
    LOG.info("BGM: %s", chosen.name)
    return chosen


def prepare_bgm_loop(bgm: Path, duration: float, out_wav: Path) -> None:
    run(
        [
            ffmpeg_bin(),
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-stream_loop",
            "-1",
            "-i",
            str(bgm),
            "-t",
            f"{duration:.3f}",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(out_wav),
        ]
    )


def _loudnorm_filter(settings: Settings, measured: dict[str, Any] | None = None) -> str:
    result = f"loudnorm=I={settings.target_lufs}:TP={settings.true_peak}:LRA=11"
    if measured:
        result += (
            f":measured_I={measured['input_i']}"
            f":measured_TP={measured['input_tp']}"
            f":measured_LRA={measured['input_lra']}"
            f":measured_thresh={measured['input_thresh']}"
            f":offset={measured['target_offset']}"
            ":linear=true"
        )
    return result


def _parse_loudnorm_json(stderr: str) -> dict[str, Any] | None:
    for match in re.finditer(r"\{.*?\}", stderr or "", re.DOTALL):
        try:
            data = json.loads(match.group(0))
        except ValueError:
            continue
        if not isinstance(data, dict) or "input_i" not in data:
            continue
        if str(data.get("input_i") or "").strip().lower() in {"-inf", "inf", "nan", ""}:
            return None
        return data
    return None


def _measure_loudness(
    inputs: list[str],
    filters: list[str],
    duration: float,
    settings: Settings,
) -> dict[str, Any] | None:
    probe_filters = filters + [
        f"[premix]{_loudnorm_filter(settings)}:print_format=json[measure]"
    ]
    proc = run(
        [
            ffmpeg_bin(),
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "info",
            "-y",
            *inputs,
            "-filter_complex",
            ";".join(probe_filters),
            "-map",
            "[measure]",
            "-t",
            f"{duration:.3f}",
            "-f",
            "null",
            "-",
        ],
        check=False,
    )
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-3:])
        LOG.warning("Two-pass loudness measurement failed; using single-pass: %s", tail)
        return None
    measured = _parse_loudnorm_json(proc.stderr or "")
    if measured is None:
        LOG.warning("Could not parse the loudnorm measurement; using single-pass")
    return measured


def _bgm_filter(settings: Settings, duration: float, input_index: int = 2) -> str:
    fade = max(0.0, min(float(settings.bgm_fade_seconds), duration))
    fade_filter = ""
    if fade > 0.1:
        fade_filter = f",afade=t=out:st={max(0.0, duration - fade):.3f}:d={fade:.3f}"
    return (
        f"[{input_index}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
        f"volume={settings.bgm_volume:.3f}{fade_filter}[bgm]"
    )


def normalize_audio_wav(
    src: Path,
    dst: Path,
    *,
    channels: int = 1,
    sample_rate: int = 48000,
) -> float:
    """Decode any FFmpeg-supported audio file into 16-bit PCM WAV and return its duration."""
    import wave
    from tempfile import TemporaryDirectory

    if not src.is_file():
        raise ValueError(f"Audio file not found: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".reachreel-audio-", dir=dst.parent) as tmp:
        staged = Path(tmp) / dst.name
        run(
            [
                ffmpeg_bin(),
                "-hide_banner",
                "-nostdin",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(src),
                "-vn",
                "-ar",
                str(sample_rate),
                "-ac",
                str(channels),
                "-c:a",
                "pcm_s16le",
                str(staged),
            ],
            timeout=300,
        )
        try:
            with wave.open(str(staged), "rb") as fh:
                frames = fh.getnframes()
                rate = fh.getframerate()
                duration = frames / float(rate) if rate else 0.0
        except (OSError, wave.Error, EOFError) as exc:
            raise ValueError(f"Normalized audio is not a valid WAV file: {src}") from exc
        if duration <= 0.01:
            raise ValueError(f"Imported audio is empty or too short: {src}")
        staged.replace(dst)
    return round(duration, 3)


def _ambient_cut_filters(cuts: CutList) -> list[str]:
    """Build FFmpeg audio trim/concat filters mapping ``[0:a]`` to ``[amb_src]``."""
    if len(cuts.clips) == 1:
        clip = cuts.clips[0]
        return [
            f"[0:a]atrim=start={clip.start:.3f}:end={clip.end:.3f},asetpts=PTS-STARTPTS[amb_src]"
        ]
    count = len(cuts.clips)
    splits = "".join(f"[amb_s{idx}]" for idx in range(count))
    parts = [f"[0:a]asplit={count}{splits}"]
    concat_inputs: list[str] = []
    for idx, clip in enumerate(cuts.clips):
        parts.append(
            f"[amb_s{idx}]atrim=start={clip.start:.3f}:end={clip.end:.3f},"
            f"asetpts=PTS-STARTPTS[amb_c{idx}]"
        )
        concat_inputs.append(f"[amb_c{idx}]")
    parts.append(f"{''.join(concat_inputs)}concat=n={count}:v=0:a=1[amb_src]")
    return parts


def mix_filter_graph(
    *,
    duration: float,
    has_ambient: bool,
    has_bgm: bool,
    settings: Settings,
    cuts: CutList | None = None,
) -> list[str]:
    """Return the ``filter_complex`` parts that produce ``[premix]``.

    Pure string construction so the ducking graph can be asserted without
    running FFmpeg. ``mix_audio`` appends the loudnorm/limiter pair on top.
    """
    filters: list[str] = []
    use_ambient = bool(has_ambient and settings.ambient_volume > 0)
    use_bgm = bool(has_bgm)
    # Narration to stereo + copies for the final mix and for each side-chain.
    sidechain_labels = (["[sc_amb]"] if use_ambient else []) + (["[sc_bgm]"] if use_bgm else [])
    if sidechain_labels:
        split_outputs = "".join(["[nar]"] + sidechain_labels)
        filters.append(
            "[1:a]aformat=sample_rates=48000:channel_layouts=stereo,"
            f"asplit={1 + len(sidechain_labels)}{split_outputs}"
        )
    else:
        filters.append("[1:a]aformat=sample_rates=48000:channel_layouts=stereo[nar]")

    mix_inputs: list[str] = ["[nar]"]
    bgm_ratio = max(2.0, min(20.0, abs(settings.bgm_duck_db) / 2.0))
    ambient_ratio = max(2.0, min(20.0, abs(settings.ambient_duck_db) / 2.0))
    if use_ambient:
        amb_in = "[0:a]"
        if cuts is not None and cuts.clips:
            filters.extend(_ambient_cut_filters(cuts))
            amb_in = "[amb_src]"
        filters.append(
            f"{amb_in}aformat=sample_rates=48000:channel_layouts=stereo,"
            f"volume={settings.ambient_volume:.3f}[amb]"
        )
        filters.append(
            "[amb][sc_amb]sidechaincompress="
            f"threshold=0.04:ratio={ambient_ratio:.1f}:attack=60:release=500:makeup=1[amb_duck]"
        )
        mix_inputs.append("[amb_duck]")

    if use_bgm:
        filters.append(_bgm_filter(settings, duration, 2))
        filters.append(
            "[bgm][sc_bgm]sidechaincompress="
            f"threshold=0.035:ratio={bgm_ratio:.1f}:attack=50:release=450:makeup=1[bgm_duck]"
        )
        mix_inputs.append("[bgm_duck]")

    if len(mix_inputs) == 1:
        filters.append("[nar]anull[premix]")
    else:
        filters.append(
            "".join(mix_inputs)
            + f"amix=inputs={len(mix_inputs)}:duration=longest:normalize=0[premix]"
        )
    return filters


def mix_audio(
    *,
    video: Path,
    narration_wav: Path,
    bgm_wav: Path | None,
    out_wav: Path,
    duration: float,
    has_ambient: bool,
    settings: Settings,
    cuts: CutList | None = None,
) -> None:
    inputs = ["-i", str(video), "-i", str(narration_wav)]
    if bgm_wav:
        inputs += ["-i", str(bgm_wav)]

    filters = mix_filter_graph(
        duration=duration,
        has_ambient=has_ambient,
        has_bgm=bgm_wav is not None,
        settings=settings,
        cuts=cuts,
    )

    measured = None
    if settings.two_pass_loudnorm:
        measured = _measure_loudness(inputs, filters, duration, settings)
    filters.append(
        f"[premix]{_loudnorm_filter(settings, measured)},"
        "alimiter=limit=0.95:level=false[aout]"
    )

    out_wav.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            ffmpeg_bin(),
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            *inputs,
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[aout]",
            "-t",
            f"{duration:.3f}",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(out_wav),
        ]
    )
    LOG.info("Mix complete: %s", out_wav.name)
