"""Shared domain models."""
from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class VideoInfo:
    path: str
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    audio_channels: int = 0
    audio_sample_rate: int = 0
    rotation: float = 0.0
    # Encoded (pre-rotation) frame size. ``width``/``height`` are display pixels.
    coded_width: int = 0
    coded_height: int = 0

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height else 16 / 9


@dataclass
class QualityFlag:
    """Structured degradation notice stored in manifest.json.

    ``code`` and ``params`` are the language-neutral contract; the CLI and the
    future panel render a localized message from them.
    """

    code: str
    params: dict[str, Any] = field(default_factory=dict)
    severity: str = "warning"  # warning | error

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def merge_quality_flags(
    *groups: Iterable[QualityFlag | dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Merge flag groups, preserving first-seen order and dropping duplicates."""
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for flag in group or []:
            if isinstance(flag, QualityFlag):
                item = flag.to_dict()
            elif isinstance(flag, dict):
                item = dict(flag)
            else:
                continue
            key = json.dumps(item, sort_keys=True, ensure_ascii=False)
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged


@dataclass
class Scene:
    id: int
    start: float
    end: float
    frames: list[str] = field(default_factory=list)
    summary: str = ""
    details: list[str] = field(default_factory=list)
    mood: str = ""
    place: str = ""
    hint: str = ""
    subjects: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["duration"] = round(self.duration, 3)
        return data


@dataclass
class NarrationSegment:
    id: int
    scene_ids: list[int]
    text: str
    audio_path: str = ""
    duration: float = 0.0
    start: float = 0.0
    end: float = 0.0
    tts_speed: float = 1.0
    instruct: str = ""
    voice: str = ""
    speed: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if not self.instruct:
            data.pop("instruct", None)
        if not self.voice:
            data.pop("voice", None)
        if self.speed <= 0:
            data.pop("speed", None)
        return data


@dataclass
class Cue:
    start: float
    end: float
    text: str
