"""ReachReel: fully local video narration pipeline."""


from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _get_version

try:
    __version__ = _get_version("reachreel")
except PackageNotFoundError:
    __version__ = "unknown"
