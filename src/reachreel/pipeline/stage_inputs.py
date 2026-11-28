"""Verify saved stage inputs from both the orchestrator and standalone QA."""
from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

from ..core.cache import video_key
from ..core.util import read_json, sha1_file, sha1_text
from ..i18n import t

STAGE_ARTIFACTS = {
    "probe": "manifest.json",
    "vlm": "scenes.json",
    "script": "script.json",
    "tts": "timeline.json",
    "subtitles": "subtitles.srt",
    "mix": "mix.wav",
    "render": "final.mp4",
    "qa": "qa.json",
}

NARRATION_STAGES = ("vlm", "tts")
RENDER_STAGES = (*NARRATION_STAGES, "subtitles", "mix")

_INPUT_FILES = {
    "tts": ("script.json", "scenes.json", "cuts.json"),
    "subtitles": ("narration.wav", "timeline.json"),
    "mix": ("narration.wav", "cuts.json"),
    "render": ("mix.wav", "subtitles.srt", "timeline.json", "cuts.json"),
}
_SOURCE_STAGES = {"vlm", "tts", "mix", "render"}
_CUTS_STAGES = {"tts", "mix", "render"}


class StageInputs:
    """Read current fingerprints and compare them with a job checkpoint."""

    def __init__(self, workdir: Path, source: Path | None) -> None:
        self.workdir = workdir
        self.source = source
        self._keys: dict[Path, tuple[tuple[int, int, int, int], str]] = {}

    def source_key(self) -> str | None:
        if self.source is None:
            return None
        return self._key(self.source, media=True)

    def capture(self, stage: str) -> dict[str, str]:
        keys: dict[str, str] = {}
        for name in _INPUT_FILES.get(stage, ()):
            path = self.workdir / name
            key = self._artifact_key(path)
            if name == "cuts.json":
                keys[name] = key or ""
            elif key is not None:
                keys[name] = key
        if stage in _SOURCE_STAGES and (source := self.source_key()) is not None:
            keys["source"] = source
        return keys

    def rendered_key(self) -> str | None:
        return self._artifact_key(self.workdir / STAGE_ARTIFACTS["render"], media=True)

    def rendered_subtitles_key(self) -> str | None:
        return self._artifact_key(self.workdir / "subtitles.ass")

    def first_stale(self, manifest: dict[str, Any], stages: tuple[str, ...]) -> str | None:
        recorded = manifest.get("artifact_inputs") or {}
        for stage in stages:
            saved = recorded.get(stage)
            if not isinstance(saved, dict) or not saved:
                if self._legacy_inputs_changed(manifest, stage):
                    return stage
                continue
            current = self.capture(stage)
            if stage == "render" and "output" in saved:
                current["output"] = self.rendered_key() or ""
            if stage == "render" and "subtitles.ass" in saved:
                current["subtitles.ass"] = self.rendered_subtitles_key() or ""
            # Older checkpoints record fewer inputs. Check every input they did
            # record and capture the additional fields when the stage runs next.
            if any(current.get(name) != key for name, key in saved.items()):
                return stage
            if stage in _CUTS_STAGES and "cuts.json" not in saved and current.get("cuts.json"):
                return stage
        return None

    def _legacy_inputs_changed(self, manifest: dict[str, Any], stage: str) -> bool:
        if stage == "vlm" and self.source is not None and self.source.is_file():
            scenes = self.workdir / STAGE_ARTIFACTS["vlm"]
            if scenes.is_file():
                if self.source.stat().st_mtime_ns > scenes.stat().st_mtime_ns:
                    return True
                previous = (manifest.get("cache") or {}).get("video_key")
                if previous:
                    return (self.source_key() or "").partition(":")[2] != previous
        if stage == "render":
            recorded_out = manifest.get("output")
            output = Path(recorded_out) if recorded_out else self.workdir / STAGE_ARTIFACTS["render"]
            if not output.is_file():
                fallback_out = self.workdir / STAGE_ARTIFACTS["render"]
                if fallback_out.is_file():
                    output = fallback_out
            if output.is_file():
                rendered_at = output.stat().st_mtime_ns
                return any(
                    path.is_file() and path.stat().st_mtime_ns > rendered_at
                    for name in _INPUT_FILES["render"]
                    if (path := self.workdir / name).exists()
                )
        return False

    def stale_message(self, stage: str) -> str:
        if stage == "vlm" and self.source is not None:
            args = ("analyze", str(self.source), "--workdir", str(self.workdir))
        else:
            command = "subtitle" if stage == "subtitles" else stage
            args = (command, str(self.workdir))
        return t(
            "error.stale_stage_output",
            name=STAGE_ARTIFACTS[stage],
            command=shlex.join(("reachreel", *args)),
        )

    def _artifact_key(self, path: Path, *, media: bool = False) -> str | None:
        if not path.resolve().is_relative_to(self.workdir):
            raise RuntimeError(t("error.stage_input_escape", name=path.name))
        return self._key(path, media=media)

    def _key(self, path: Path, *, media: bool = False) -> str | None:
        if not path.is_file():
            return None
        stat = path.stat()
        signature = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
        cached = self._keys.get(path)
        if cached is not None and cached[0] == signature:
            return cached[1]
        if media:
            key = f"{stat.st_mtime_ns}:{video_key(path)}"
        elif path.name == "scenes.json":
            # Timeline placement depends on the cut boundaries and duration.
            # Rewording a visual description leaves the existing timing valid.
            data = read_json(path)
            try:
                timing = ([s["id"], s["start"], s["end"]] for s in data["scenes"])
                key = sha1_text(json.dumps([data["video"]["duration"], list(timing)]))
            except (KeyError, TypeError):
                key = sha1_file(path)
        else:
            key = sha1_file(path)
        self._keys[path] = (signature, key)
        return key
