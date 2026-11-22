"""BGM selection and final audio mix with side-chain ducking."""
from __future__ import annotations

import logging
import random
from pathlib import Path

from ..core.config import Settings
from ..core.util import run

LOG = logging.getLogger("reachreel.media.audio")

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}


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
            "ffmpeg",
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


def mix_audio(
    *,
    video: Path,
    narration_wav: Path,
    bgm_wav: Path | None,
    out_wav: Path,
    duration: float,
    has_ambient: bool,
    settings: Settings,
) -> None:
    inputs = ["-i", str(video), "-i", str(narration_wav)]
    if bgm_wav:
        inputs += ["-i", str(bgm_wav)]

    filters: list[str] = []
    use_ambient = bool(has_ambient and settings.ambient_volume > 0)
    use_bgm = bgm_wav is not None
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
        filters.append(
            f"[0:a]aformat=sample_rates=48000:channel_layouts=stereo,"
            f"volume={settings.ambient_volume:.3f}[amb]"
        )
        filters.append(
            "[amb][sc_amb]sidechaincompress="
            f"threshold=0.04:ratio={ambient_ratio:.1f}:attack=60:release=500:makeup=1[amb_duck]"
        )
        mix_inputs.append("[amb_duck]")

    if use_bgm:
        b_idx = 2
        filters.append(
            f"[{b_idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
            f"volume={settings.bgm_volume:.3f}[bgm]"
        )
        filters.append(
            "[bgm][sc_bgm]sidechaincompress="
            f"threshold=0.035:ratio={bgm_ratio:.1f}:attack=50:release=450:makeup=1[bgm_duck]"
        )
        mix_inputs.append("[bgm_duck]")

    if len(mix_inputs) == 1:
        filters.append(
            f"[nar]loudnorm=I={settings.target_lufs}:TP={settings.true_peak}:LRA=11,"
            "alimiter=limit=0.95[aout]"
        )
    else:
        filters.append(
            "".join(mix_inputs)
            + f"amix=inputs={len(mix_inputs)}:duration=longest:normalize=0,"
            + f"loudnorm=I={settings.target_lufs}:TP={settings.true_peak}:LRA=11,"
            + "alimiter=limit=0.95[aout]"
        )

    out_wav.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg",
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
