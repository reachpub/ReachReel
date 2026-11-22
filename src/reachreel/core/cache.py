"""Cross-workdir cache keys and paths.

The shared cache is optional and lives in the platform cache directory by
default (``~/Library/Caches/reachreel`` on macOS). It lets the same footage
reuse VLM analysis and TTS audio across project directories without copying
the work directory.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from platformdirs import user_cache_dir

APP_NAME = "reachreel"
SAMPLE_BYTES = 1 << 20


def default_cache_root() -> Path:
    """Platform cache dir: ``~/Library/Caches/reachreel`` on macOS."""
    return Path(user_cache_dir(APP_NAME))


def legacy_cache_root() -> Path:
    return Path.home() / ".cache" / APP_NAME


def cache_root(configured: str | None = None) -> Path:
    """Resolve the shared cache root; empty config means the platform cache dir.

    ``~/.cache/reachreel`` is still used when it exists and the new platform
    directory has not been created yet, so existing caches keep working.
    """
    if configured:
        return Path(configured).expanduser()
    default = default_cache_root()
    legacy = legacy_cache_root()
    if legacy.exists() and not default.exists():
        return legacy
    return default


def video_key(path: Path, sample_bytes: int = SAMPLE_BYTES) -> str:
    """A fast content fingerprint: size plus the first and last sampled MiB.

    Hashing multi-gigabyte footage on every stage command would dominate the
    runtime. The sampled digest still separates different clips reliably.
    """
    size = path.stat().st_size
    digest = hashlib.sha1()
    digest.update(str(size).encode("utf-8"))
    with path.open("rb") as fh:
        digest.update(fh.read(sample_bytes))
        if size > sample_bytes * 2:
            fh.seek(-sample_bytes, 2)
            digest.update(fh.read(sample_bytes))
    return digest.hexdigest()[:20]


def vlm_cache_path(root: Path, key: str) -> Path:
    return root / "vlm" / f"{key}.json"


def tts_cache_dir(root: Path) -> Path:
    return root / "tts"


def tts_cache_path(root: Path, key: str) -> Path:
    return tts_cache_dir(root) / f"{key}.wav"


def cache_stats(root: Path) -> dict[str, int]:
    if not root.exists():
        return {"files": 0, "bytes": 0}
    files = [path for path in root.rglob("*") if path.is_file()]
    total = 0
    for path in files:
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return {"files": len(files), "bytes": total}


def clean_cache(root: Path) -> dict[str, int]:
    stats = cache_stats(root)
    if root.is_file():
        root.unlink()
    elif root.exists():
        shutil.rmtree(root)
    return stats


def format_bytes(size: int) -> str:
    value = float(max(0, size))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"
