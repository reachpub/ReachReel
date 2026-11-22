"""Shared domain models."""
from __future__ import annotations

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

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height else 16 / 9


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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Cue:
    start: float
    end: float
    text: str
