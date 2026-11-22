"""Small shared helpers: subprocess, ffprobe, JSON, logging."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Iterable, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..i18n import t

LOG = logging.getLogger("reachreel")


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-5s  %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )


def run(
    cmd: Sequence[str | os.PathLike[str]],
    *,
    cwd: Path | None = None,
    check: bool = True,
    capture: bool = True,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command, logging it at debug level and raising a readable error."""
    args = [str(c) for c in cmd]
    LOG.debug("$ %s", " ".join(args))
    try:
        proc = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=capture,
            timeout=timeout,
            env={**os.environ, **env} if env else None,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(t("error.command_not_found", command=args[0])) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(t("error.command_timeout", command=" ".join(args))) from exc
    if check and proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        tail = "\n".join(stderr.splitlines()[-25:])
        raise RuntimeError(
            t(
                "error.command_failed",
                code=proc.returncode,
                command=" ".join(args),
                tail=tail,
            )
        )
    return proc


def ffprobe_json(path: Path) -> dict[str, Any]:
    proc = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
    )
    return json.loads(proc.stdout)


def ffmpeg_filters(ffmpeg: str = "ffmpeg") -> set[str]:
    try:
        proc = run([ffmpeg, "-hide_banner", "-filters"], capture=True)
    except RuntimeError:
        return set()
    names: set[str] = set()
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and not line.startswith("Filters:"):
            names.add(parts[1])
    return names


@lru_cache(maxsize=1)
def ffmpeg_full() -> str | None:
    """Return an ffmpeg binary that can burn subtitles, if one is installed."""
    candidates = [
        os.environ.get("FFMPEG_FULL"),
        "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
        "/usr/local/opt/ffmpeg-full/bin/ffmpeg",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and "subtitles" in ffmpeg_filters(candidate):
            return candidate
    if "subtitles" in ffmpeg_filters("ffmpeg"):
        return "ffmpeg"
    return None


def ffprobe_duration(path: Path) -> float:
    data = ffprobe_json(path)
    duration = data.get("format", {}).get("duration")
    if duration:
        return float(duration)
    for stream in data.get("streams", []):
        if stream.get("duration"):
            return float(stream["duration"])
    raise RuntimeError(t("error.cannot_read_duration", path=path))


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def sha1_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def srt_timestamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    whole = int(seconds)
    ms = round((seconds - whole) * 1000)
    if ms >= 1000:
        whole += 1
        ms -= 1000
    hh, rem = divmod(whole, 3600)
    mm, ss = divmod(rem, 60)
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"


def chunked(items: Iterable[Any], size: int) -> Iterable[list[Any]]:
    bucket: list[Any] = []
    for item in items:
        bucket.append(item)
        if len(bucket) >= size:
            yield bucket
            bucket = []
    if bucket:
        yield bucket


def platform_supported(
    system: str | None = None,
    machine: str | None = None,
) -> bool:
    """ReachReel targets Apple silicon macOS only.

    ``mlx-audio`` and ``mlx-whisper`` have no x86_64 macOS build, and the same
    pipeline on Intel hardware is both slow and lower quality. The arguments
    exist so the check stays testable without the host being arm64.
    """
    current_system = (system or platform.system()).lower()
    current_machine = (machine or platform.machine()).lower()
    return current_system == "darwin" and current_machine in {"arm64", "aarch64"}


def host_platform() -> str:
    """Return a short ``system/machine`` label for reports and error messages."""
    return f"{platform.system()}/{platform.machine()}"


def ensure_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(
            t("error.missing_tool", name=name)
        )


def rel(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path)
