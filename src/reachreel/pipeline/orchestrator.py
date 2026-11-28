"""Pipeline orchestration."""
from __future__ import annotations

import json
import shlex
import shutil
import time
from contextlib import suppress
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.cache import cache_root, tts_cache_dir, video_key, vlm_cache_path
from ..core.config import Settings, public_ollama_url, settings_to_dict
from ..core.util import (
    LOG,
    atomic_write_json,
    ffmpeg_bin,
    ffmpeg_full,
    find_ffmpeg,
    find_ffprobe,
    host_platform,
    pad_display,
    platform_supported,
    read_json,
    sha1_text,
)
from ..domain.cuts import (
    CutList,
    build_keep_cuts,
    build_remove_cuts,
    load_cuts,
    save_cuts,
)
from ..domain.models import (
    NarrationSegment,
    QualityFlag,
    Scene,
    VideoInfo,
    merge_quality_flags,
)
from ..domain.timeline import build_narration_track, place_segments, save_timeline
from ..engines.tts import synthesize_segments
from ..engines.vlm import OllamaClient, analyze_scenes
from ..generation.script import generate_script
from ..i18n import t
from ..media.audio import (
    AUDIO_EXTS,
    bgm_metadata,
    mix_audio,
    normalize_audio_wav,
    pick_bgm,
    prepare_bgm_loop,
)
from ..media.edit_export import NARRATION_MODES
from ..media.render import output_dimensions, render
from ..media.scenes import (
    detect_scenes,
    extract_frames,
    load_scenes,
    probe_video,
    save_scenes,
)
from ..media.subtitles import build_subtitles, validate_srt, write_ass, write_srt
from ..prompts import load_content, load_prompt
from .stage_inputs import NARRATION_STAGES, RENDER_STAGES, STAGE_ARTIFACTS, StageInputs

STAGE_ORDER = ["probe", "vlm", "script", "tts", "subtitles", "mix", "render", "qa"]
EDIT_FORMATS = ("mp4", "mov")
MANIFEST_SCHEMA_VERSION = 2
MAX_HISTORY_SNAPSHOTS = 20

_ANALYSIS_OPTIONS = (
    "vlm_model", "content_language", "brief", "scene_threshold", "min_scene_seconds",
    "max_scene_seconds", "max_scenes", "vlm_frames_per_scene", "vlm_max_width", "vlm_frame_dedup",
)

# User-facing names accepted by `run --only`. `tts` includes the timeline stage;
# `analyze` is kept as an alias of the VLM stage.
STAGE_ALIASES = {
    "probe": "probe",
    "scenes": "probe",
    "analyze": "vlm",
    "vlm": "vlm",
    "script": "script",
    "tts": "tts",
    "timeline": "tts",
    "subtitle": "subtitles",
    "subtitles": "subtitles",
    "mix": "mix",
    "render": "render",
    "qa": "qa",
}

def stage_label(stage: str) -> str:
    """Localized stage label for dry-run output and progress logs."""
    return t(f"stage.{stage}")

def review_stages(stages: list[str]) -> list[str]:
    """Truncate a stage list at ``script`` for the human review gate."""
    if "script" not in stages:
        raise ValueError(t("error.review_script_needs_script"))
    return stages[: stages.index("script") + 1]


def parse_only(value: str | list[str] | tuple[str, ...] | None) -> list[str] | None:
    """Parse `--only vlm,script` into canonical stages, preserving pipeline order."""
    if value is None:
        return None
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",")]
    else:
        items = [str(item).strip() for item in value]
    items = [item for item in items if item]
    if not items:
        return None
    if "all" in items:
        return list(STAGE_ORDER)
    chosen: set[str] = set()
    for item in items:
        stage = STAGE_ALIASES.get(item.lower())
        if stage is None:
            supported = ", ".join(sorted(STAGE_ALIASES))
            raise ValueError(t("error.unknown_stage", item=item, available=supported))
        chosen.add(stage)
    return [stage for stage in STAGE_ORDER if stage in chosen]


class Pipeline:
    def __init__(
        self,
        video: Path,
        settings: Settings,
        *,
        workdir: Path | None = None,
        resume: bool = True,
        force: bool = False,
        create_dir: bool = True,
    ) -> None:
        self.video = video.resolve()
        self.settings = settings
        self.force = force
        self.resume = resume
        if workdir is None:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            workdir = Path(settings.work_root) / f"{self.video.stem}_{stamp}"
        self.workdir = workdir.resolve()
        if create_dir:
            self.workdir.mkdir(parents=True, exist_ok=True)
        self.frames_dir = self.workdir / "frames"
        self.tts_dir = self.workdir / "tts"
        self.scenes_path = self.workdir / "scenes.json"
        self.vlm_cache_path = self.workdir / "vlm_cache.json"
        self.script_path = self.workdir / "script.json"
        self.script_md_path = self.workdir / "script.md"
        self.timeline_path = self.workdir / "timeline.json"
        self.narration_path = self.workdir / "narration.wav"
        self.subtitles_path = self.workdir / "subtitles.srt"
        self.ass_path = self.workdir / "subtitles.ass"
        self.bgm_path = self.workdir / "bgm_loop.wav"
        self.mix_path = self.workdir / "mix.wav"
        self.output_path = self.workdir / "final.mp4"
        self.edit_export_path = self.workdir / "edit"
        self.cuts_path = self.workdir / "cuts.json"
        self.imports_dir = self.workdir / "imports"
        self.history_dir = self.workdir / ".history"
        self.manifest_path = self.workdir / "manifest.json"
        self._info: VideoInfo | None = None
        self._scenes: list[Scene] | None = None
        self._scene_inputs_key: str | None = None
        self._segments: list[NarrationSegment] | None = None
        self.stage_inputs = StageInputs(self.workdir, self.video)
        self.bgm_source: Path | None = None
        self.bgm_metadata: dict[str, Any] | None = None
        self.bgm_disabled = False
        self.qa_report = None
        self.burned_subtitles: bool | None = None
        self.quality_flags: list[QualityFlag] = []
        # Stages re-executed in this process; their old quality flags are dropped.
        self.refreshed_stages: set[str] = set()
        self.executed_stages: list[str] = []
        self.shared_cache_root = (
            cache_root(self.settings.cache_dir) if self.settings.cache_enabled else None
        )
        self._video_key: str | None = None

    # ---------- stages ----------

    def load_video(self) -> VideoInfo:
        if self._info is not None:
            return self._info
        if not self.video.is_file():
            saved = (
                (read_json(self.manifest_path, {}) or {}).get("video")
                or (read_json(self.scenes_path, {}) or {}).get("video")
            )
            if isinstance(saved, dict) and saved.get("duration"):
                self._info = VideoInfo(
                    path=str(saved.get("path") or self.video),
                    duration=float(saved["duration"]),
                    width=int(saved.get("width") or 0),
                    height=int(saved.get("height") or 0),
                    fps=float(saved.get("fps") or 0.0),
                    has_audio=bool(saved.get("has_audio", False)),
                    audio_channels=int(saved.get("audio_channels") or 0),
                    audio_sample_rate=int(saved.get("audio_sample_rate") or 0),
                    rotation=float(saved.get("rotation") or 0.0),
                    coded_width=int(saved.get("coded_width") or 0),
                    coded_height=int(saved.get("coded_height") or 0),
                )
                return self._info
        LOG.info("Reading video: %s", self.video.name)
        self._info = probe_video(self.video)
        LOG.info(
            "  %dx%d  %.1f fps  %.1fs  %s",
            self._info.width,
            self._info.height,
            self._info.fps,
            self._info.duration,
            "audio present" if self._info.has_audio else "no audio",
        )
        return self._info

    def prepare_scenes(self) -> None:
        info = self.load_video()
        settings = self.settings
        self._scene_inputs_key = _data_key((
            self.video_cache_key(), self.video.stat().st_mtime_ns, info.duration,
            settings.scene_threshold, settings.min_scene_seconds, settings.max_scene_seconds,
            settings.max_scenes, settings.vlm_frames_per_scene, settings.vlm_max_width,
            settings.vlm_frame_dedup,
        ))
        if self.scenes_path.exists() and self.resume and not self.force:
            cached = read_json(self.scenes_path, {}) or {}
            if isinstance(cached, dict) and cached.get("inputs_key") == self._scene_inputs_key:
                with suppress(OSError, ValueError, KeyError, TypeError):
                    cached_info, cached_scenes = load_scenes(self.scenes_path)
                    if Path(cached_info.path).resolve() == self.video and cached_scenes and all(
                        scene.frames and all(Path(frame).is_file() for frame in scene.frames)
                        for scene in cached_scenes
                    ):
                        if cached_info != info:
                            save_scenes(
                                self.scenes_path, info, cached_scenes, inputs_key=self._scene_inputs_key
                            )
                        self._scenes = cached_scenes
                        LOG.info("Reusing scene analysis (%d scenes)", len(cached_scenes))
                        return
        LOG.info("Detecting scenes...")
        scenes = detect_scenes(
            self.video,
            info.duration,
            threshold=self.settings.scene_threshold,
            min_len=self.settings.min_scene_seconds,
            max_len=self.settings.max_scene_seconds,
            max_scenes=self.settings.max_scenes,
        )
        if self.frames_dir.exists() and self.force:
            shutil.rmtree(self.frames_dir)
        extract_frames(
            self.video,
            scenes,
            self.frames_dir,
            per_scene=self.settings.vlm_frames_per_scene,
            width=self.settings.vlm_max_width,
            dedup=self.settings.vlm_frame_dedup,
        )
        save_scenes(self.scenes_path, info, scenes, inputs_key=self._scene_inputs_key)
        self._scenes = scenes

    def video_cache_key(self) -> str:
        if self._video_key is None:
            self._video_key = video_key(self.video)
        return self._video_key

    def understand(self, *, force: bool | None = None) -> None:
        assert self._scenes is not None
        self.refreshed_stages.add("vlm")
        force = self.force if force is None else force
        if force:
            self.vlm_cache_path.unlink(missing_ok=True)
        shared_cache_path = (
            vlm_cache_path(self.shared_cache_root, self.video_cache_key())
            if self.shared_cache_root is not None
            else None
        )
        self.quality_flags.extend(
            analyze_scenes(
                self._scenes,
                base_url=self.settings.ollama_url,
                model=self.settings.vlm_model,
                workers=self.settings.vlm_workers,
                cache_path=self.vlm_cache_path,
                shared_cache_path=shared_cache_path,
                max_width=self.settings.vlm_max_width,
                force=force,
                context=self.settings.brief,
                content_language=self.settings.content_language,
                frame_dedup=self.settings.vlm_frame_dedup,
            )
        )
        info = self.load_video()
        save_scenes(self.scenes_path, info, self._scenes, inputs_key=self._scene_inputs_key)

    def _saved_video_info(self) -> VideoInfo:
        """Return saved video metadata from manifest/scenes without running ffprobe if available."""
        if self._info is not None:
            return self._info
        saved = (
            (_read_dict_json(self.manifest_path)).get("video")
            or (_read_dict_json(self.scenes_path)).get("video")
        )
        if isinstance(saved, dict) and float(saved.get("duration") or 0.0) > 0:
            self._info = VideoInfo(
                path=str(saved.get("path") or self.video),
                duration=float(saved["duration"]),
                width=int(saved.get("width") or 0),
                height=int(saved.get("height") or 0),
                fps=float(saved.get("fps") or 0.0),
                has_audio=bool(saved.get("has_audio", False)),
                audio_channels=int(saved.get("audio_channels") or 0),
                audio_sample_rate=int(saved.get("audio_sample_rate") or 0),
                rotation=float(saved.get("rotation") or 0.0),
                coded_width=int(saved.get("coded_width") or 0),
                coded_height=int(saved.get("coded_height") or 0),
            )
            return self._info
        return self.load_video()

    def load_cuts(self) -> CutList | None:
        return load_cuts(self.cuts_path)

    def effective_duration(self) -> float:
        cuts = self.load_cuts()
        if cuts is not None:
            return cuts.edited_duration
        return self.load_video().duration

    def effective_scenes(self) -> list[Scene]:
        assert self._scenes is not None
        cuts = self.load_cuts()
        if cuts is None:
            return self._scenes
        projected, _ = cuts.project_scenes(self._scenes)
        return projected or self._scenes

    def write_script(self, *, reuse: bool = True) -> None:
        assert self._scenes is not None
        settings = self.settings
        language = settings.content_language
        active_scenes = self.effective_scenes()
        duration = self.effective_duration()
        options = {
            "duration": duration,
            "style": settings.style,
            "style_preset": settings.resolved_style_preset(),
            "style_guidance": settings.resolved_style_guidance(),
            "oral_polish": settings.script_oral_polish,
            "brief": settings.brief,
            "cps": settings.resolved_speech_chars_per_second(),
            "density": settings.narration_density,
            "temperature": settings.llm_temperature,
            "content_language": language,
        }
        inputs_key = _data_key((
            options, settings.ollama_url, settings.llm_model,
            load_prompt(language, "script"), load_content(language),
            [{key: value for key, value in scene.to_dict().items() if key != "frames"}
             for scene in active_scenes],
        ))
        if reuse and self.script_path.exists() and self.resume and not self.force:
            data = read_json(self.script_path, {})
            if not isinstance(data, dict):
                data = {}
            segments = _load_segments(data)
            cached_language = data.get("content_language", "zh")
            generation = data.get("generation")
            if not isinstance(generation, dict):
                generation = {}
            edited = bool(generation) and generation.get("segments_key") != _data_key(
                [segment.to_dict() for segment in segments]
            )
            if segments and edited and cached_language != language:
                raise self._script_language_mismatch(cached_language)
            if segments and cached_language == language and (
                edited or not generation or generation.get("inputs_key") == inputs_key
            ):
                self._segments = segments
                LOG.info(
                    "Reusing %s narration script (%d segments)",
                    "edited" if edited else "existing", len(segments),
                )
                return
            if segments:
                LOG.info("Narration inputs changed; regenerating script")
        client = OllamaClient(settings.ollama_url, settings.llm_model)
        segments = generate_script(active_scenes, client=client, **options)
        serialized = [segment.to_dict() for segment in segments]
        atomic_write_json(
            self.script_path,
            {
                "content_language": language,
                "style_preset": settings.resolved_style_preset(),
                "style": settings.resolved_style(),
                "segments": serialized,
                "generation": {"inputs_key": inputs_key, "segments_key": _data_key(serialized)},
            },
        )
        self._segments = segments

    def make_voice(self) -> None:
        assert self._segments is not None
        self.refreshed_stages.add("tts")
        self.quality_flags.extend(
            synthesize_segments(
                self._segments,
                self.tts_dir,
                self.settings,
                speed=self.settings.tts_speed,
                force=self.force,
                shared_dir=(
                    tts_cache_dir(self.shared_cache_root)
                    if self.shared_cache_root is not None
                    else None
                ),
                workdir=self.workdir,
            )
        )

    def make_timeline(self) -> None:
        assert self._segments is not None
        assert self._scenes is not None
        duration = self.effective_duration()
        place_segments(
            self._segments,
            self.effective_scenes(),
            duration,
            self.settings,
            flags=self.quality_flags,
        )
        build_narration_track(
            self._segments,
            duration,
            self.narration_path,
        )
        save_timeline(self.timeline_path, self._segments)

    def make_subtitles(self, *, asr_engine: str = "mlx-whisper") -> None:
        assert self._segments is not None
        self.refreshed_stages.add("subtitles")
        cues = build_subtitles(
            self.narration_path,
            self._segments,
            self.subtitles_path,
            self.settings,
            asr_engine=asr_engine,
            flags=self.quality_flags,
        )
        info = self.load_video()
        out_width, out_height = output_dimensions(info.width, info.height, self.settings)
        write_ass(
            cues,
            self.ass_path,
            width=out_width,
            height=out_height,
            settings=self.settings,
        )

    def make_mix(self, *, bgm_file: Path | None, no_bgm: bool) -> None:
        info = self.load_video()
        cuts = self.load_cuts()
        duration = cuts.edited_duration if cuts is not None else info.duration
        bgm_wav = None
        directory = self.settings.resolved_bgm_dir()
        saved = self.bgm_source
        if saved is not None and (
            not saved.is_file()
            or (
                not saved.resolve().is_relative_to(self.workdir)
                and saved.absolute().parent.resolve() != directory.resolve()
            )
        ):
            saved = None
        chosen = None
        if not no_bgm:
            chosen = bgm_file or (
                pick_bgm(self.settings) if self.settings.bgm_file else saved or pick_bgm(self.settings)
            )
        self.bgm_disabled = chosen is None
        self.bgm_source = chosen
        self.bgm_metadata = bgm_metadata(chosen) if chosen else None
        if chosen:
            if chosen.resolve() != self.bgm_path.resolve():
                prepare_bgm_loop(chosen, duration, self.bgm_path)
            bgm_wav = self.bgm_path
        elif not no_bgm:
            LOG.warning("No BGM found. Put CC0 music in %s to enable automatic mixing.", directory)
        mix_audio(
            video=self.video,
            narration_wav=self.narration_path,
            bgm_wav=bgm_wav,
            out_wav=self.mix_path,
            duration=duration,
            has_ambient=info.has_audio,
            settings=self.settings,
            cuts=cuts,
        )

    def render_final(self, *, burn: bool) -> bool:
        self._require_render_inputs()
        self.burned_subtitles = render(
            video=self.video,
            audio=self.mix_path,
            subtitles=self.subtitles_path,
            out=self.output_path,
            settings=self.settings,
            burn=burn,
            cuts=self.load_cuts(),
        )
        return self.burned_subtitles

    def export_edit(
        self,
        *,
        destination: Path | None = None,
        video_format: str = "mp4",
        narration_mode: str = "track",
        overwrite: bool = False,
    ) -> Path:
        """Build an editing bundle using the source and existing stage artifacts."""
        from ..media.edit_export import write_edit_bundle

        if video_format not in EDIT_FORMATS:
            raise ValueError(f"Unsupported edit video format: {video_format}")
        if narration_mode not in NARRATION_MODES:
            raise ValueError(f"Unsupported narration mode: {narration_mode}")
        for path, command in (
            (self.narration_path, "tts"),
            (self.subtitles_path, "subtitle"),
            (self.mix_path, None),
        ):
            if not path.resolve().is_relative_to(self.workdir):
                raise ValueError(t("error.export_artifact_escape", name=path.name))
            if command and not path.is_file():
                raise ValueError(t("error.export_missing", name=path.name, command=command))
        stages = RENDER_STAGES if self.mix_path.is_file() else (*NARRATION_STAGES, "subtitles")
        self._require_current(stages)
        destination = destination.expanduser() if destination is not None else self.edit_export_path
        destination = destination.parent.resolve() / destination.name
        if self.workdir.resolve().is_relative_to(destination.resolve(strict=False)):
            raise ValueError(t("error.export_contains_workdir", path=destination))
        scenes_payload = read_json(self.scenes_path, {}) or {}
        scenes_list = (
            scenes_payload.get("scenes")
            if isinstance(scenes_payload, dict) and isinstance(scenes_payload.get("scenes"), list)
            else None
        )
        timeline_segments: list[NarrationSegment] | None = None
        if narration_mode == "segments" and self.timeline_path.is_file():
            timeline_segments = _load_segments(read_json(self.timeline_path, {}) or {})
        write_edit_bundle(
            destination,
            source=self.load_video(),
            narration=self.narration_path,
            subtitles=self.subtitles_path,
            mix=self.mix_path if self.mix_path.is_file() else None,
            settings=self.settings,
            video_format=video_format,
            bgm=self.bgm_path if self.bgm_path.is_file() else None,
            scenes=scenes_list,
            cuts=self.load_cuts(),
            segments=timeline_segments,
            narration_mode=narration_mode,
            overwrite=overwrite,
        )
        return destination

    # ---------- fine-tuning & import (shared by CLI and future UI) ----------

    def _save_history_snapshot(self, action: str) -> Path:
        """Save a lightweight JSON snapshot of editable job state before mutation."""
        self.history_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        safe_action = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in action) or "edit"
        snap_path = self.history_dir / f"{stamp}_{safe_action}.json"
        payload = {
            "action": action,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "script": read_json(self.script_path, None) if self.script_path.is_file() else None,
            "cuts": read_json(self.cuts_path, None) if self.cuts_path.is_file() else None,
        }
        atomic_write_json(snap_path, payload)
        snapshots = sorted(self.history_dir.glob("*.json"))
        for old in snapshots[:-MAX_HISTORY_SNAPSHOTS]:
            with suppress(OSError):
                old.unlink()
        return snap_path

    def undo_last_edit(self, *, dry_run: bool = False) -> dict[str, Any]:
        """Restore the most recent ``.history/`` snapshot of ``script.json`` and ``cuts.json``."""
        snapshots = sorted(self.history_dir.glob("*.json")) if self.history_dir.is_dir() else []
        if not snapshots:
            raise ValueError(t("error.undo_empty", workdir=self.workdir))
        latest = snapshots[-1]
        snap = read_json(latest, {}) or {}
        if not isinstance(snap, dict):
            raise TypeError(f"Corrupted history snapshot: {latest.name}")
        restored: list[str] = []
        if not dry_run:
            if snap.get("script") is not None:
                atomic_write_json(self.script_path, snap["script"])
                restored.append("script.json")
            if snap.get("cuts") is not None:
                atomic_write_json(self.cuts_path, snap["cuts"])
                restored.append("cuts.json")
            elif self.cuts_path.is_file():
                self.cuts_path.unlink()
                restored.append("cuts.json (removed)")
            latest.unlink(missing_ok=True)
            self._sync_manifest_cuts()
        else:
            if snap.get("script") is not None:
                restored.append("script.json")
            if snap.get("cuts") is not None:
                restored.append("cuts.json")
            elif self.cuts_path.is_file():
                restored.append("cuts.json (removed)")
        status = workdir_status(self.workdir)
        return {
            "ok": True,
            "dry_run": dry_run,
            "action": "undo",
            "snapshot": latest.name,
            "undone_action": snap.get("action"),
            "restored": restored,
            "workdir": str(self.workdir),
            "affected_stages": status["pending_stages"],
            "next_stage": status["next_stage"],
            "next_command": status["next_command"],
            "resume_command": status["resume_command"],
        }

    def _write_script_data(
        self,
        segments: list[NarrationSegment],
        base_data: dict[str, Any] | None = None,
    ) -> None:
        existing = base_data if isinstance(base_data, dict) else _read_dict_json(self.script_path)
        payload = dict(existing)
        payload.setdefault("content_language", self.settings.content_language)
        payload["segments"] = [segment.to_dict() for segment in segments]
        atomic_write_json(self.script_path, payload)
        self._segments = segments

    def _sync_manifest_cuts(self) -> None:
        if not self.manifest_path.is_file():
            return
        manifest = _read_dict_json(self.manifest_path)
        cuts = self.load_cuts()
        manifest["cuts"] = cuts.to_dict() if cuts is not None else None
        files = manifest.get("files")
        if isinstance(files, dict):
            if cuts is not None:
                files["cuts"] = str(self.cuts_path)
            else:
                files.pop("cuts", None)
        atomic_write_json(self.manifest_path, manifest)

    def _record_import_audit(self, entries: list[dict[str, Any]]) -> None:
        if not entries:
            return
        manifest = _read_dict_json(self.manifest_path)
        history = manifest.get("imports")
        if not isinstance(history, list):
            history = []
        now = datetime.now().isoformat(timespec="seconds")
        for entry in entries:
            history.append({"imported_at": now, **entry})
        manifest["imports"] = history[-30:]
        atomic_write_json(self.manifest_path, manifest)

    def _disable_dropped_scene_segments(
        self,
        cuts: CutList,
        segments: list[NarrationSegment],
    ) -> list[int]:
        """Disable segments whose linked scenes are completely removed by ``cuts``."""
        if not self.scenes_path.is_file() or not segments:
            return []
        try:
            _, raw_scenes = load_scenes(self.scenes_path)
        except (OSError, ValueError, KeyError, TypeError):
            return []
        _, dropped_scenes = cuts.project_scenes(raw_scenes)
        if not dropped_scenes:
            return []
        disabled_ids: list[int] = []
        for seg in segments:
            if seg.enabled and seg.scene_ids and all(sid in dropped_scenes for sid in seg.scene_ids):
                seg.enabled = False
                disabled_ids.append(seg.id)
        return disabled_ids

    def import_artifacts(
        self,
        *,
        script_file: Path | None = None,
        subtitles_file: Path | None = None,
        narration_file: Path | None = None,
        segment_audio: list[tuple[int, Path]] | None = None,
        bgm_file: Path | None = None,
        cuts_file: Path | None = None,
        timeline_file: Path | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Validate and import external artifacts or NLE XML timelines into the workdir."""
        from ..media.edit_import import parse_timeline_xml

        segment_audio = list(segment_audio or [])
        if not any(
            (script_file, subtitles_file, narration_file, segment_audio, bgm_file, cuts_file, timeline_file)
        ):
            raise ValueError(t("error.import_nothing"))

        info = self._saved_video_info()
        existing_script_data = _read_dict_json(self.script_path)
        segments = _load_segments(existing_script_data)

        warnings: list[str] = []
        imported_items: list[dict[str, Any]] = []
        script_mutated = False
        cuts_to_write: CutList | None = None
        cuts_remove = False
        parsed_cues = None

        # 1. Validate all inputs before writing anything.
        if script_file is not None:
            src_script = script_file.expanduser().resolve()
            if not src_script.is_file():
                raise ValueError(f"Script file not found: {src_script}")
            if src_script.suffix.lower() == ".md":
                if not segments:
                    raise ValueError(
                        "Cannot import a script.md table before script.json exists in the workdir."
                    )
                updated_texts = _parse_script_md_updates(src_script)
                if not updated_texts:
                    raise ValueError(f"No valid segment rows found in {src_script.name}")
                by_id = {seg.id: seg for seg in segments}
                unknown = sorted(sid for sid in updated_texts if sid not in by_id)
                for sid in unknown:
                    warnings.append(f"Ignored unknown segment {sid} in {src_script.name}.")
                updated_count = 0
                for sid, new_text in updated_texts.items():
                    if sid in by_id and by_id[sid].text != new_text:
                        by_id[sid].text = new_text
                        updated_count += 1
                script_mutated = True
                imported_items.append(
                    {"kind": "script", "source": src_script.name, "segments_updated": updated_count}
                )
            else:
                raw_script = read_json(src_script, None)
                if not isinstance(raw_script, dict):
                    raise ValueError(f"Script file must be a JSON object: {src_script}")
                new_segments = _load_segments(raw_script)
                if not new_segments:
                    raise ValueError(f"Script JSON has no valid 'segments' entries: {src_script}")
                segments = new_segments
                existing_script_data = {**existing_script_data, **raw_script}
                script_mutated = True
                imported_items.append(
                    {"kind": "script", "source": src_script.name, "segments": len(segments)}
                )

        if subtitles_file is not None:
            src_srt = subtitles_file.expanduser().resolve()
            parsed_cues, srt_warnings = validate_srt(src_srt)
            warnings.extend(srt_warnings)
            imported_items.append(
                {"kind": "subtitles", "source": src_srt.name, "cues": len(parsed_cues)}
            )

        if narration_file is not None:
            src_nar = narration_file.expanduser().resolve()
            if not src_nar.is_file():
                raise ValueError(f"Narration audio file not found: {src_nar}")
            imported_items.append({"kind": "narration", "source": src_nar.name})

        if segment_audio:
            if not segments:
                raise ValueError("Cannot import per-segment audio before script.json exists.")
            by_id = {seg.id: seg for seg in segments}
            for seg_id, path in segment_audio:
                resolved = path.expanduser().resolve()
                if seg_id not in by_id:
                    raise ValueError(f"Segment {seg_id} does not exist in script.json.")
                if not resolved.is_file():
                    raise ValueError(f"Segment {seg_id} audio file not found: {resolved}")
                imported_items.append(
                    {"kind": "segment_audio", "segment_id": seg_id, "source": resolved.name}
                )

        if bgm_file is not None:
            src_bgm = bgm_file.expanduser().resolve()
            if not src_bgm.is_file():
                raise ValueError(f"BGM file not found: {src_bgm}")
            if src_bgm.suffix.lower() not in AUDIO_EXTS:
                raise ValueError(
                    f"Unsupported BGM extension {src_bgm.suffix!r} (supported: {', '.join(sorted(AUDIO_EXTS))})"
                )
            imported_items.append({"kind": "bgm", "source": src_bgm.name})

        if cuts_file is not None:
            src_cuts = cuts_file.expanduser().resolve()
            if not src_cuts.is_file():
                raise ValueError(f"Cuts file not found: {src_cuts}")
            raw_cuts = read_json(src_cuts, None)
            if not isinstance(raw_cuts, dict):
                raise ValueError(f"Cuts file must be a JSON object: {src_cuts}")
            if not raw_cuts.get("source_duration") and info.duration > 0:
                raw_cuts = {**raw_cuts, "source_duration": info.duration}
            cuts_to_write = CutList.from_dict(raw_cuts)
            cuts_to_write.validate(info.duration if info.duration > 0 else None)
            imported_items.append(
                {
                    "kind": "cuts",
                    "source": src_cuts.name,
                    "clips": len(cuts_to_write.clips),
                    "edited_duration": cuts_to_write.edited_duration,
                }
            )

        timeline_plan = None
        if timeline_file is not None:
            src_xml = timeline_file.expanduser().resolve()
            timeline_plan = parse_timeline_xml(
                src_xml,
                source_duration=info.duration,
                existing_segments=segments,
            )
            warnings.extend(timeline_plan.warnings)
            if timeline_plan.cuts_changed and timeline_plan.cuts is not None:
                cuts_to_write = timeline_plan.cuts
            elif self.cuts_path.is_file() and timeline_plan.cuts is None:
                cuts_remove = True
            if segments:
                by_id = {seg.id: seg for seg in segments}
                for seg_id, start_s in timeline_plan.segment_starts.items():
                    if seg_id in by_id:
                        by_id[seg_id].start_override = start_s
                        by_id[seg_id].enabled = True
                        script_mutated = True
                for seg_id, gain_db in timeline_plan.segment_gains.items():
                    if seg_id in by_id:
                        by_id[seg_id].gain_db = gain_db
                        script_mutated = True
                for seg_id in timeline_plan.disabled_segment_ids:
                    if seg_id in by_id:
                        by_id[seg_id].enabled = False
                        script_mutated = True
            imported_items.append(
                {
                    "kind": "timeline",
                    "format": timeline_plan.source_format,
                    "source": src_xml.name,
                    "cuts_changed": timeline_plan.cuts_changed,
                    "segments_updated": len(timeline_plan.matched_segment_ids),
                    "segments_disabled": len(timeline_plan.disabled_segment_ids),
                }
            )

        if cuts_to_write is not None and segments:
            dropped_disabled = self._disable_dropped_scene_segments(cuts_to_write, segments)
            if dropped_disabled:
                script_mutated = True
                warnings.append(
                    "Auto-disabled narration segment(s) whose linked scenes were cut: "
                    + ", ".join(str(sid) for sid in dropped_disabled)
                )

        # Predict affected stages for dry_run.
        predicted_stages = _predict_import_stages(
            has_script=script_mutated,
            has_segment_audio=bool(segment_audio),
            has_narration=narration_file is not None,
            has_subtitles=subtitles_file is not None,
            has_bgm=bgm_file is not None,
            has_cuts=(cuts_to_write is not None or cuts_remove),
        )

        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "workdir": str(self.workdir),
                "imported": imported_items,
                "warnings": warnings,
                "affected_stages": predicted_stages,
                "timeline": timeline_plan.to_dict() if timeline_plan is not None else None,
            }

        # 2. Persist changes atomically.
        if script_mutated or cuts_to_write is not None or cuts_remove or segment_audio:
            self._save_history_snapshot("import")

        if segment_audio:
            self.imports_dir.mkdir(parents=True, exist_ok=True)
            by_id = {seg.id: seg for seg in segments}
            for seg_id, path in segment_audio:
                dst_wav = self.imports_dir / f"seg_{seg_id:03d}.wav"
                duration = normalize_audio_wav(path.expanduser().resolve(), dst_wav, channels=1, sample_rate=48000)
                by_id[seg_id].audio_override = f"imports/{dst_wav.name}"
                by_id[seg_id].duration = duration
                by_id[seg_id].enabled = True
                script_mutated = True

        if script_mutated:
            self._write_script_data(segments, existing_script_data)

        if cuts_to_write is not None:
            save_cuts(self.cuts_path, cuts_to_write)
            self._sync_manifest_cuts()
        elif cuts_remove and self.cuts_path.is_file():
            self.cuts_path.unlink()
            self._sync_manifest_cuts()

        if narration_file is not None:
            nar_duration = normalize_audio_wav(
                narration_file.expanduser().resolve(),
                self.narration_path,
                channels=1,
                sample_rate=48000,
            )
            if not self.timeline_path.is_file():
                active = [seg for seg in segments if seg.enabled]
                if active:
                    total_chars = max(1, sum(max(1, len(seg.text)) for seg in active))
                    cursor = 0.0
                    fallback_segs: list[NarrationSegment] = []
                    for seg in active:
                        share = nar_duration * (max(1, len(seg.text)) / total_chars)
                        seg_end = round(min(nar_duration, cursor + share), 3)
                        fallback_segs.append(
                            replace(
                                seg,
                                audio_path=str(self.narration_path),
                                start=round(cursor, 3),
                                end=seg_end,
                                duration=round(max(0.0, seg_end - cursor), 3),
                            )
                        )
                        cursor = seg_end
                else:
                    fallback_segs = [
                        NarrationSegment(
                            id=1,
                            scene_ids=[],
                            text="",
                            audio_path=str(self.narration_path),
                            duration=nar_duration,
                            start=0.0,
                            end=nar_duration,
                        )
                    ]
                save_timeline(self.timeline_path, fallback_segs)
            # Mark TTS checkpoint up-to-date so resume does not overwrite imported narration.wav,
            # while subtitles/mix/render detect the new narration.wav hash as stale.
            manifest = _read_dict_json(self.manifest_path)
            manifest["narration_source"] = "imported"
            artifact_inputs = manifest.get("artifact_inputs")
            if not isinstance(artifact_inputs, dict):
                artifact_inputs = {}
            artifact_inputs["tts"] = self.stage_inputs.capture("tts")
            manifest["artifact_inputs"] = artifact_inputs
            atomic_write_json(self.manifest_path, manifest)

        if parsed_cues is not None:
            write_srt(parsed_cues, self.subtitles_path)
            # Keep subtitles stage current so resume does not overwrite imported subtitles.srt.
            manifest = _read_dict_json(self.manifest_path)
            artifact_inputs = manifest.get("artifact_inputs")
            if not isinstance(artifact_inputs, dict):
                artifact_inputs = {}
            artifact_inputs["subtitles"] = self.stage_inputs.capture("subtitles")
            manifest["artifact_inputs"] = artifact_inputs
            atomic_write_json(self.manifest_path, manifest)

        if bgm_file is not None:
            self.imports_dir.mkdir(parents=True, exist_ok=True)
            src_bgm = bgm_file.expanduser().resolve()
            safe_stem = "".join(ch if ch.isalnum() or ch in {"_", "-", "."} else "_" for ch in src_bgm.name)
            dst_bgm = self.imports_dir / f"bgm_{safe_stem}"
            shutil.copyfile(src_bgm, dst_bgm)
            self.bgm_source = dst_bgm
            self.bgm_metadata = bgm_metadata(src_bgm)
            manifest = _read_dict_json(self.manifest_path)
            manifest["bgm_source"] = str(dst_bgm)
            manifest["bgm_metadata"] = self.bgm_metadata
            artifact_inputs = manifest.get("artifact_inputs")
            if not isinstance(artifact_inputs, dict):
                artifact_inputs = {}
            # Mark mix stale without deleting existing mix.wav.
            artifact_inputs["mix"] = {
                **(artifact_inputs.get("mix") or {}),
                "bgm_import": _data_key(str(dst_bgm) + str(dst_bgm.stat().st_mtime_ns)),
            }
            manifest["artifact_inputs"] = artifact_inputs
            atomic_write_json(self.manifest_path, manifest)

        self._record_import_audit(imported_items)
        status = workdir_status(self.workdir)
        return {
            "ok": True,
            "dry_run": False,
            "workdir": str(self.workdir),
            "imported": imported_items,
            "warnings": warnings,
            "affected_stages": status["pending_stages"],
            "next_stage": status["next_stage"],
            "next_command": status["next_command"],
            "resume_command": status["resume_command"],
            "timeline": timeline_plan.to_dict() if timeline_plan is not None else None,
        }

    def tune(
        self,
        *,
        segment_id: int | None = None,
        text: str | None = None,
        voice: str | None = None,
        instruct: str | None = None,
        speed: float | None = None,
        start: float | str | None = None,
        gain_db: float | None = None,
        enabled: bool | None = None,
        clear_audio_override: bool = False,
        cut_ranges: list[tuple[float, float]] | None = None,
        keep_ranges: list[tuple[float, float]] | None = None,
        reset_cuts: bool = False,
        undo: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Apply structured local fine-tuning edits (per-segment or Cut List)."""
        if undo:
            return self.undo_last_edit(dry_run=dry_run)

        has_seg_edit = any(
            value is not None
            for value in (text, voice, instruct, speed, start, gain_db, enabled)
        ) or clear_audio_override
        has_cut_edit = bool(cut_ranges or keep_ranges or reset_cuts)
        if not has_seg_edit and not has_cut_edit:
            raise ValueError(t("error.tune_nothing"))
        if has_seg_edit and segment_id is None:
            raise ValueError(t("error.tune_needs_segment"))
        if sum(bool(x) for x in (cut_ranges, keep_ranges, reset_cuts)) > 1:
            raise ValueError(t("error.tune_cuts_conflict"))

        info = self._saved_video_info()
        existing_script_data = _read_dict_json(self.script_path)
        segments = _load_segments(existing_script_data)
        changes: list[str] = []
        warnings: list[str] = []
        script_mutated = False

        if has_seg_edit:
            assert segment_id is not None
            if not segments:
                raise self._missing_input(self.script_path, "script", str(self.workdir))
            target = next((seg for seg in segments if seg.id == segment_id), None)
            if target is None:
                available = ", ".join(str(seg.id) for seg in segments)
                raise ValueError(
                    f"Segment {segment_id} not found in script.json (available: {available})."
                )
            if text is not None:
                cleaned = text.strip()
                if not cleaned:
                    raise ValueError("Segment --text cannot be empty.")
                target.text = cleaned
                changes.append(f"segment {segment_id}: updated text")
            if voice is not None:
                target.voice = "" if voice.strip().lower() in {"default", "auto"} else voice.strip()
                changes.append(f"segment {segment_id}: voice={target.voice or 'default'}")
            if instruct is not None:
                target.instruct = "" if instruct.strip().lower() == "none" else instruct.strip()
                changes.append(f"segment {segment_id}: instruct={target.instruct or 'default'}")
            if speed is not None:
                if speed < 0 or (speed > 0 and not (0.5 <= speed <= 2.0)):
                    raise ValueError("Segment --speed must be 0 (default) or between 0.5 and 2.0.")
                target.speed = round(float(speed), 3)
                changes.append(f"segment {segment_id}: speed={target.speed or 'default'}")
            if start is not None:
                if isinstance(start, str) and start.strip().lower() == "auto":
                    target.start_override = None
                    changes.append(f"segment {segment_id}: start=auto")
                else:
                    start_val = float(start)
                    if start_val < 0:
                        raise ValueError("Segment --start must be >= 0 or 'auto'.")
                    target.start_override = round(start_val, 3)
                    changes.append(f"segment {segment_id}: start_override={target.start_override:.3f}s")
            if gain_db is not None:
                if not (-24.0 <= gain_db <= 12.0):
                    raise ValueError("Segment --gain-db must be between -24.0 and +12.0 dB.")
                target.gain_db = round(float(gain_db), 2)
                changes.append(f"segment {segment_id}: gain_db={target.gain_db:+.2f}dB")
            if enabled is not None:
                target.enabled = bool(enabled)
                changes.append(f"segment {segment_id}: enabled={target.enabled}")
            if clear_audio_override:
                target.audio_override = ""
                changes.append(f"segment {segment_id}: cleared audio_override")
            script_mutated = True

        new_cuts: CutList | None = None
        if cut_ranges:
            new_cuts = build_remove_cuts(cut_ranges, info.duration)
            changes.append(
                f"cuts: removed {len(cut_ranges)} range(s), kept {len(new_cuts.clips)} clip(s) "
                f"({new_cuts.edited_duration:.2f}s / {info.duration:.2f}s)"
            )
        elif keep_ranges:
            new_cuts = build_keep_cuts(keep_ranges, info.duration)
            changes.append(
                f"cuts: kept {len(new_cuts.clips)} clip(s) "
                f"({new_cuts.edited_duration:.2f}s / {info.duration:.2f}s)"
            )
        elif reset_cuts:
            changes.append("cuts: reset to full source video")

        if new_cuts is not None and segments:
            dropped_disabled = self._disable_dropped_scene_segments(new_cuts, segments)
            if dropped_disabled:
                script_mutated = True
                warnings.append(
                    "Auto-disabled narration segment(s) whose linked scenes were cut: "
                    + ", ".join(str(sid) for sid in dropped_disabled)
                )

        if dry_run:
            predicted = _predict_import_stages(
                has_script=script_mutated,
                has_segment_audio=False,
                has_narration=False,
                has_subtitles=False,
                has_bgm=False,
                has_cuts=has_cut_edit,
            )
            return {
                "ok": True,
                "dry_run": True,
                "workdir": str(self.workdir),
                "changes": changes,
                "warnings": warnings,
                "affected_stages": predicted,
            }

        self._save_history_snapshot("tune")
        if script_mutated:
            self._write_script_data(segments, existing_script_data)
        if new_cuts is not None:
            save_cuts(self.cuts_path, new_cuts)
            self._sync_manifest_cuts()
        elif reset_cuts and self.cuts_path.is_file():
            self.cuts_path.unlink()
            self._sync_manifest_cuts()

        status = workdir_status(self.workdir)
        return {
            "ok": True,
            "dry_run": False,
            "workdir": str(self.workdir),
            "changes": changes,
            "warnings": warnings,
            "affected_stages": status["pending_stages"],
            "next_stage": status["next_stage"],
            "next_command": status["next_command"],
            "resume_command": status["resume_command"],
        }

    # ---------- stage commands ----------

    def _missing_input(self, path: Path, *next_args: str) -> RuntimeError:
        return RuntimeError(t(
            "error.missing_stage_input",
            name=path.name,
            command=shlex.join(("reachreel", *next_args)),
        ))

    def _script_language_mismatch(self, original: str) -> RuntimeError:
        language = self.settings.content_language
        return RuntimeError(t(
            "error.saved_script_language",
            original=original,
            requested=language,
            command=shlex.join((
                "reachreel", "script", str(self.workdir), "--content-language", language,
            )),
        ))

    def load_scenes_data(self) -> None:
        self._require_current(("vlm",))
        if self._scenes is not None:
            return
        try:
            info, scenes = load_scenes(self.scenes_path)
            if not scenes or Path(info.path).resolve() != self.video:
                raise ValueError("Scene analysis does not match the current video")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise self._missing_input(
                self.scenes_path, "analyze", str(self.video), "--workdir", str(self.workdir)
            ) from exc
        self._info = info
        self._scenes = scenes

    def load_script(self) -> None:
        if self._segments is not None:
            return
        data = read_json(self.script_path, {})
        segments = _load_segments(data)
        if not segments:
            raise self._missing_input(self.script_path, "script", str(self.workdir))
        language = data.get("content_language")
        if language and language != self.settings.content_language:
            raise self._script_language_mismatch(language)
        self._segments = segments

    def _load_narration_inputs(self) -> None:
        self._require_current(NARRATION_STAGES)
        self.load_timeline()
        if not self.narration_path.is_file():
            raise self._missing_input(self.narration_path, "tts", str(self.workdir))

    def load_timeline(self) -> None:
        data = read_json(self.timeline_path, {})
        segments = _load_segments(data)
        if segments and any(segment.audio_path for segment in segments):
            self._segments = segments
            return
        raise self._missing_input(self.timeline_path, "tts", str(self.workdir))

    def stage_analyze(self) -> None:
        started = time.time()
        inputs = self.stage_inputs.capture("vlm")
        self.prepare_scenes()
        self.understand()
        self.write_manifest(
            started=started, burned=None, announce=False, artifact_inputs={"vlm": inputs}
        )

    def stage_script(self, *, refresh_analysis: bool = False) -> None:
        if refresh_analysis or self._analysis_settings_changed():
            self.stage_analyze()
        self.run(only="script")

    def _analysis_settings_changed(self) -> bool:
        manifest = read_json(self.manifest_path, {}) or {}
        recorded = manifest.get("analysis_settings_key")
        if recorded:
            return recorded != self._analysis_settings_key()
        saved = {**(manifest.get("settings") or {}), **(manifest.get("settings_snapshot") or {})}
        return any(key in saved and saved[key] != getattr(self.settings, key)
                   for key in _ANALYSIS_OPTIONS)

    def _analysis_settings_key(self) -> str:
        return _data_key((
            public_ollama_url(self.settings.ollama_url),
            [getattr(self.settings, key) for key in _ANALYSIS_OPTIONS],
        ))

    def stage_tts(self) -> None:
        self.run(only="tts")
        LOG.info(
            "Next: reachreel subtitle %s && reachreel mix %s && reachreel render %s",
            self.workdir,
            self.workdir,
            self.workdir,
        )

    def stage_subtitle(self, *, asr_engine: str = "mlx-whisper") -> None:
        self.run(only="subtitles", asr_engine=asr_engine)
        LOG.info("Next: reachreel render %s", self.workdir)

    def stage_mix(self, *, bgm_file: Path | None = None, no_bgm: bool = False) -> None:
        self.run(only="mix", bgm_file=bgm_file, no_bgm=no_bgm)
        LOG.info("Next: reachreel render %s", self.workdir)

    # ---------- full run ----------

    def load_existing_timeline(self) -> None:
        """Load timeline data without rebuilding it (used by render-only runs)."""
        if self._segments is not None:
            return
        data = read_json(self.timeline_path, {}) or {}
        segments = _load_segments(data)
        if segments:
            self._segments = segments

    def _require_render_inputs(self) -> None:
        self._require_current(NARRATION_STAGES)
        for path, command in ((self.subtitles_path, "subtitle"), (self.mix_path, "mix")):
            if not path.is_file():
                raise self._missing_input(path, command, str(self.workdir))
        self._require_current(("subtitles", "mix"))
        self.load_existing_timeline()

    def _require_current(self, stages: tuple[str, ...]) -> None:
        manifest = read_json(self.manifest_path, {}) or {}
        if stale := self.stage_inputs.first_stale(manifest, stages):
            raise RuntimeError(self.stage_inputs.stale_message(stale))

    def _run_stages(
        self,
        stages: list[str],
        *,
        started: float,
        asr_engine: str,
        bgm_file: Path | None,
        no_bgm: bool,
        burn: bool,
        explicit: bool,
        force_selected: bool = True,
    ) -> None:
        force_stage = self.force or (explicit and force_selected)
        for stage in stages:
            LOG.info("=== Stage: %s ===", stage_label(stage))
            inputs = self.stage_inputs.capture(stage)
            if stage == "probe":
                self.load_video()
            elif stage == "vlm":
                self.prepare_scenes()
                self.understand(force=force_stage)
            elif stage == "script":
                self.load_scenes_data()
                if self._analysis_settings_changed():
                    raise RuntimeError(t("error.analysis_options_changed", command=shlex.join((
                        "reachreel", "run", str(self.video), "--workdir", str(self.workdir),
                        "--only", "vlm,script",
                    ))))
                self.write_script(reuse=not force_stage)
            elif stage == "tts":
                self.load_script()
                self.load_scenes_data()
                self.make_voice()
                self.make_timeline()
            elif stage == "subtitles":
                self._load_narration_inputs()
                self.make_subtitles(asr_engine=asr_engine)
            elif stage == "mix":
                self._load_narration_inputs()
                self.make_mix(bgm_file=bgm_file, no_bgm=no_bgm)
            elif stage == "render":
                self.render_final(burn=burn)
                if output_key := self.stage_inputs.rendered_key():
                    inputs["output"] = output_key
                if self.burned_subtitles and (ass_key := self.stage_inputs.rendered_subtitles_key()):
                    inputs["subtitles.ass"] = ass_key
            self.refreshed_stages.add(stage)
            self.write_manifest(
                started=started,
                burned=self.burned_subtitles,
                announce=False,
                artifact_inputs={stage: inputs} if inputs else None,
            )

    def run(
        self,
        *,
        asr_engine: str = "mlx-whisper",
        bgm_file: Path | None = None,
        no_bgm: bool = False,
        burn: bool = True,
        qa: bool = True,
        qa_strict: bool = False,
        only: str | list[str] | tuple[str, ...] | None = None,
        dry_run: bool = False,
        review_script: bool = False,
        print_qa: bool = True,
        force_selected: bool = True,
    ) -> Path:
        selected = parse_only(only)
        stages = selected or list(STAGE_ORDER)
        if review_script:
            stages = review_stages(stages)
        if dry_run:
            self.print_dry_run(
                stages if (review_script or selected is not None) else None,
                qa=qa,
                qa_strict=qa_strict,
                burn=burn,
                explicit=(selected is not None) and force_selected,
                review=review_script,
                asr_engine=asr_engine,
                bgm_file=bgm_file,
                no_bgm=no_bgm,
            )
            return self.output_path

        started = time.time()
        explicit = selected is not None
        non_qa_stages = [stage for stage in stages if stage != "qa"]
        self.executed_stages = []
        self._run_stages(
            non_qa_stages,
            started=started,
            asr_engine=asr_engine,
            bgm_file=bgm_file,
            no_bgm=no_bgm,
            burn=burn,
            explicit=explicit,
            force_selected=force_selected,
        )
        self.executed_stages.extend(non_qa_stages)

        if "script" in stages and (review_script or explicit):
            self.write_script_md()
            LOG.info("Script review ready: %s", self.script_md_path)
            LOG.info("Edit %s, then run: reachreel resume %s", self.script_path, self.workdir)
        if any(stage in stages for stage in ("tts", "subtitles", "mix", "render")):
            self.write_review()

        if (explicit and "qa" in stages) or (not explicit and qa and not review_script):
            from .qa import attach_to_manifest, print_report, run_qa

            self.qa_report = run_qa(
                self.workdir, strict=qa_strict, settings_override=self.settings.qa_values()
            )
            self.executed_stages.append("qa")
            if print_qa:
                print_report(self.qa_report)
            attach_to_manifest(self.workdir, self.qa_report)
        if explicit or review_script:
            if "render" in stages and not review_script:
                return self.output_path
            return self.workdir / STAGE_ARTIFACTS[stages[-1]]
        LOG.info("Done: %s (%.1fs)", self.output_path, time.time() - started)
        return self.output_path

    def dry_run_plan(
        self,
        selected: list[str] | None,
        *,
        qa: bool = True,
        qa_strict: bool = False,
        burn: bool = True,
        explicit: bool | None = None,
        review: bool = False,
        asr_engine: str = "mlx-whisper",
        bgm_file: Path | None = None,
        no_bgm: bool = False,
    ) -> dict[str, Any]:
        """Build a structured execution plan without calling models or writing files."""
        info = self.load_video()
        if explicit is None:
            explicit = selected is not None
        if selected is not None:
            stages = list(selected)
        else:
            stages = list(STAGE_ORDER) if qa else [s for s in STAGE_ORDER if s != "qa"]
        settings = self.settings
        voice = (
            f"clone:{Path(settings.tts_ref_audio).name}"
            if settings.tts_engine == "qwen3" and settings.tts_ref_audio.strip()
            else (settings.resolved_voice() or "custom")
        )
        mode = "review_script" if review else ("selected" if explicit else "full")
        stage_entries = []
        for stage in STAGE_ORDER:
            if stage in stages:
                stage_entries.append(
                    {
                        "stage": stage,
                        "action": "run",
                        "label": stage_label(stage),
                        "note": self._stage_note(stage, explicit, asr_engine=asr_engine),
                    }
                )
            else:
                stage_entries.append(
                    {
                        "stage": stage,
                        "action": "skip",
                        "label": stage_label(stage),
                        "note": "",
                    }
                )
        return {
            "dry_run": True,
            "mode": mode,
            "video": {
                "path": str(self.video),
                "width": info.width,
                "height": info.height,
                "fps": info.fps,
                "duration": info.duration,
                "has_audio": info.has_audio,
            },
            "workdir": str(self.workdir),
            "workdir_exists": self.workdir.exists(),
            "language": {
                "content": settings.content_language,
                "tts": settings.tts_language or None,
                "asr": settings.resolved_asr_language(),
            },
            "style": {
                "preset": settings.resolved_style_preset(),
                "custom": bool(settings.style.strip()),
                "brief": bool(settings.brief.strip()),
            },
            "models": {
                "vlm": settings.vlm_model,
                "llm": settings.llm_model,
                "tts": settings.tts_engine,
                "voice": voice,
                "asr": asr_engine,
            },
            "subtitles": {
                "preset": settings.subtitle_preset or None,
                "size": settings.subtitle_size,
                "render": settings.subtitle_render,
                "burn": burn,
            },
            "bgm": {
                "enabled": not no_bgm,
                "file": str(bgm_file) if bgm_file else (settings.bgm_file or None),
                "dir": str(settings.resolved_bgm_dir()),
            },
            "cache": {
                "enabled": self.shared_cache_root is not None,
                "dir": str(self.shared_cache_root) if self.shared_cache_root else None,
            },
            "render": {
                "height": settings.output_height,
                "fps": settings.output_fps,
                "burn_subtitles": burn,
            },
            "qa": {
                "enabled": qa if not explicit else ("qa" in stages),
                "strict": qa_strict,
            },
            "stages": stage_entries,
        }

    def print_dry_run(
        self,
        selected: list[str] | None,
        *,
        qa: bool = True,
        qa_strict: bool = False,
        burn: bool = True,
        explicit: bool | None = None,
        review: bool = False,
        asr_engine: str = "mlx-whisper",
        bgm_file: Path | None = None,
        no_bgm: bool = False,
    ) -> None:
        """Describe what `run` would do without touching models or writing files."""
        plan = self.dry_run_plan(
            selected,
            qa=qa,
            qa_strict=qa_strict,
            burn=burn,
            explicit=explicit,
            review=review,
            asr_engine=asr_engine,
            bgm_file=bgm_file,
            no_bgm=no_bgm,
        )
        video = plan["video"]
        models = plan["models"]
        render_info = plan["render"]
        run_stages = [entry["stage"] for entry in plan["stages"] if entry["action"] == "run"]
        lines = [
            t("dry_run.title"),
            "=" * 60,
            t("dry_run.video", value=video["path"]),
            t(
                "dry_run.video_meta",
                width=video["width"],
                height=video["height"],
                fps=video["fps"],
                duration=video["duration"],
                audio=t("common.audio_present")
                if video["has_audio"]
                else t("common.audio_missing"),
            ),
            t("dry_run.workdir", value=plan["workdir"])
            + ("" if plan["workdir_exists"] else t("dry_run.workdir_new")),
            (
                t("dry_run.mode_review", stages=", ".join(run_stages))
                if plan["mode"] == "review_script"
                else t("dry_run.mode_selected", stages=", ".join(run_stages))
                if plan["mode"] == "selected"
                else t("dry_run.mode_full")
            ),
            t(
                "dry_run.models",
                vlm=models["vlm"],
                llm=models["llm"],
                tts=models["tts"],
                voice=models["voice"],
            ),
            t(
                "dry_run.render",
                height=render_info["height"] or t("common.source"),
                fps=render_info["fps"] or t("common.source"),
                burned=t("common.on") if render_info["burn_subtitles"] else t("common.off"),
            ),
            t("dry_run.qa", state=t("common.on") if plan["qa"]["enabled"] else t("common.off"))
            + (t("common.strict") if plan["qa"]["enabled"] and plan["qa"]["strict"] else ""),
            "",
            t("dry_run.stages"),
        ]
        for entry in plan["stages"]:
            stage = entry["stage"]
            if entry["action"] == "skip":
                lines.append(f"  [skip] {stage:<10} {entry['label']}")
            else:
                lines.append(f"  [run ] {stage:<10} {entry['label']}  {entry['note']}")
        lines += [
            "",
            t("dry_run.dry_only"),
        ]
        print("\n".join(lines))

    def run_summary(
        self,
        output: Path,
        *,
        review_script: bool = False,
        qa_strict: bool = False,
    ) -> dict[str, Any]:
        """Build a machine-readable summary for a completed ``run`` invocation."""
        status = workdir_status(self.workdir)
        exit_code = (
            self.qa_report.exit_code(strict=qa_strict)
            if self.qa_report is not None
            else 0
        )
        qa_payload = None
        if self.qa_report is not None:
            qa_payload = {
                **self.qa_report.to_dict(),
                "exit_code": exit_code,
            }
        last_artifact = (
            str(self.workdir / STAGE_ARTIFACTS[self.executed_stages[-1]])
            if self.executed_stages
            else str(output)
        )
        return {
            "ok": exit_code == 0,
            "exit_code": exit_code,
            "dry_run": False,
            "workdir": str(self.workdir),
            "video": str(self.video),
            "output": str(output),
            "last_artifact": last_artifact,
            "stages_executed": list(self.executed_stages),
            "review_script": review_script,
            "burned_subtitles": self.burned_subtitles,
            "artifacts": status["artifacts"],
            "quality_flags": status["quality_flags"],
            "qa": qa_payload,
            "next_stage": status["next_stage"],
            "next_command": status["next_command"],
            "resume_command": status["resume_command"],
        }

    def _stage_note(
        self,
        stage: str,
        explicit: bool,
        *,
        asr_engine: str = "mlx-whisper",
    ) -> str:
        def exists(name: str) -> bool:
            return (self.workdir / name).exists()

        if stage == "probe":
            data = read_json(self.scenes_path, {}) or {}
            count = len(data.get("scenes", []) or [])
            return (
                t("stage_note.probe_reuse", count=count)
                if count
                else t("stage_note.probe_new")
            )
        if stage == "vlm":
            data = read_json(self.scenes_path, {}) or {}
            count = len(data.get("scenes", []) or [])
            cache = read_json(self.vlm_cache_path, {}) or {}
            cached = len(cache.get("scenes", {}) or {})
            prefix = (
                t("stage_note.vlm_prefix", count=count)
                if count
                else t("stage_note.vlm_required")
            )
            if explicit:
                return t("stage_note.vlm_force", prefix=prefix)
            return t("stage_note.vlm_cache", prefix=prefix, cached=cached)
        if stage == "script":
            if explicit:
                return t("stage_note.script_rewrite")
            return (
                t("stage_note.script_reuse")
                if exists("script.json")
                else t("stage_note.script_new")
            )
        if stage == "tts":
            audio_count = 0
            if self.tts_dir.exists():
                audio_count = sum(1 for _ in self.tts_dir.glob("*.wav"))
            return t("stage_note.tts", count=audio_count)
        if stage == "subtitles":
            if asr_engine == "none":
                return t("stage_note.subtitles_timeline")
            return t("stage_note.subtitles")
        if stage == "mix":
            return t("stage_note.mix")
        if stage == "render":
            return (
                t("stage_note.render_hard")
                if self.settings.burn_subtitles
                else t("stage_note.render_soft")
            )
        if stage == "qa":
            return t("stage_note.qa")
        return ""

    def write_script_md(self) -> Path:
        """Write a human-readable script preview with estimated placement."""
        duration = self.effective_duration()
        segments = self._segments or []
        scenes = self.effective_scenes() if self._scenes is not None else []
        cps = max(0.1, self.settings.resolved_speech_chars_per_second())
        estimates = [
            replace(
                segment,
                audio_path="",
                duration=max(1.0, len(segment.text) / cps),
                start=0.0,
                end=0.0,
            )
            for segment in segments
        ]
        if scenes:
            place_segments(estimates, scenes, duration, self.settings, estimate_only=True)
        lines = [
            f"# {t('script_md.title', name=self.video.stem)}",
            "",
            t("script_md.video", path=self.video),
            t("script_md.language", language=self.settings.content_language),
            t("script_md.scenes", count=len(scenes)),
            t("script_md.segments", count=len(segments)),
            t("script_md.characters", count=sum(len(s.text) for s in segments if s.enabled)),
            t(
                "script_md.estimated",
                seconds=sum(s.duration for s in estimates if s.enabled),
                video_seconds=duration,
            ),
            "",
            t("script_md.section"),
            "",
            t("script_md.table_header"),
            "|---|---|---|---|---|---|",
        ]
        manifest = read_json(self.manifest_path, {}) or {}
        degraded = {
            flag.get("params", {}).get("scene")
            for flag in manifest.get("quality_flags", [])
            if str(flag.get("code", "")).startswith("vlm.")
        }
        by_id = {scene.id: scene for scene in scenes}
        for segment in estimates:
            scene_ids = ", ".join(str(scene_id) for scene_id in segment.scene_ids)
            evidence = []
            for scene_id in segment.scene_ids:
                scene = by_id.get(scene_id)
                summary = "" if scene is None or scene_id in degraded else " ".join(
                    part for part in (scene.summary, *scene.actions, *scene.details) if part
                )
                evidence.append(f"{scene_id}: {summary or t('script_md.missing_evidence')}")
            lines.append(
                f"| {segment.id} | {scene_ids} | {segment.start:.1f} | "
                f"{segment.end:.1f} | {_markdown_cell(segment.text)} | "
                f"{_markdown_cell(' / '.join(evidence) or t('script_md.missing_evidence'))} |"
            )
        if self.settings.brief.strip():
            lines += ["", t("script_md.brief", brief=_markdown_cell(self.settings.brief))]
        lines += [
            "",
            t("script_md.section_edit"),
            "",
            t("script_md.edit_hint", workdir=self.workdir, video=self.video),
            "",
        ]
        self.script_md_path.write_text("\n".join(lines), encoding="utf-8")
        return self.script_md_path

    def write_review(self) -> None:
        info = self.load_video()
        segments = self._segments or []
        lines = [
            f"# {self.video.stem}",
            "",
            t(
                "review.output",
                name=self.output_path.name,
                width=info.width,
                height=info.height,
                duration=info.duration,
            ),
            t("review.subtitles"),
            t(
                "review.narration",
                segments=len(segments),
                characters=sum(len(s.text) for s in segments),
            ),
            t("review.bgm", path=self.bgm_path)
            if self.bgm_source and self.bgm_path.exists()
            else t("review.bgm_none"),
            "",
            t("review.section_narration"),
            "",
            t("review.table_header"),
            "|---|---|---|",
        ]
        for segment in segments:
            lines.append(
                f"| {segment.id} | {segment.start:.1f}-{segment.end:.1f}s | "
                f"{_markdown_cell(segment.text)} |"
            )
        scenes = self._scenes
        if scenes is None:
            with suppress(OSError, ValueError, KeyError, TypeError):
                cached_info, cached_scenes = load_scenes(self.scenes_path)
                if Path(cached_info.path).resolve() == self.video:
                    scenes = cached_scenes
        shot_ends = {scene.id: scene.end for scene in scenes or []}
        overflow = []
        for segment in segments:
            ends = [shot_ends[scene_id] for scene_id in segment.scene_ids if scene_id in shot_ends]
            if segment.audio_path and ends and segment.end - max(ends) > 0.05:
                overflow.append(t("review.shot_overflow", id=segment.id, seconds=segment.end - max(ends)))
        if overflow:
            lines += ["", t("review.section_shot_budget"), "", *overflow]
        lines += [
            "",
            t("review.section_edit"),
            "",
            t("review.edit_hint"),
            "",
        ]
        (self.workdir / "review.md").write_text("\n".join(lines), encoding="utf-8")

    def write_manifest(
        self,
        *,
        started: float,
        burned: bool | None,
        announce: bool = True,
        artifact_inputs: dict[str, dict[str, str]] | None = None,
    ) -> None:
        info = self.load_video()
        segments = self._segments or []
        existing = read_json(self.manifest_path, {}) or {}
        previous = existing.get("settings") or {}
        invalidated = set(self.refreshed_stages)
        if invalidated - {"probe"}:
            invalidated.add("qa")
        if burned is None:
            burned = bool(previous.get("burned_subtitles", False))
        kept_flags = [
            flag
            for flag in (existing.get("quality_flags") or [])
            if isinstance(flag, dict)
            and _flag_stage(str(flag.get("code") or "")) not in invalidated
        ]
        cuts = self.load_cuts()
        active_segments = [s for s in segments if s.enabled]
        data = {
            **existing,
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "video": asdict(info),
            "workdir": str(self.workdir),
            "output": str(self.output_path),
            "cuts": cuts.to_dict() if cuts is not None else None,
            "settings": {
                "vlm_model": self.settings.vlm_model,
                "llm_model": self.settings.llm_model,
                "tts_engine": self.settings.tts_engine,
                "voice": self.settings.resolved_voice(),
                "whisper_model": self.settings.whisper_model,
                "bgm": (
                    str(self.bgm_path) if self.bgm_source and self.bgm_path.exists()
                    else None if self.bgm_disabled else previous.get("bgm")
                ),
                "burned_subtitles": burned,
            },
            "settings_snapshot": settings_to_dict(self.settings),
            "bgm_source": (
                str(self.bgm_source) if self.bgm_source
                else None if self.bgm_disabled else existing.get("bgm_source")
            ),
            "cache": {
                "enabled": self.shared_cache_root is not None,
                "dir": str(self.shared_cache_root) if self.shared_cache_root else None,
                "video_key": self.video_cache_key() if self.shared_cache_root else None,
            },
            "bgm_metadata": self.bgm_metadata
            or (existing.get("bgm_metadata") if not self.bgm_disabled else None),
            "quality_flags": merge_quality_flags(kept_flags, self.quality_flags),
            "scenes": len(self._scenes) if self._scenes is not None else existing.get("scenes", 0),
            "narration_segments": len(active_segments),
            "narration_chars": sum(len(s.text) for s in active_segments),
            "narration_seconds": round(sum(s.duration for s in active_segments), 2),
            "timeline": [
                {
                    "id": s.id,
                    "start": s.start,
                    "end": s.end,
                    "chars": len(s.text),
                }
                for s in active_segments
            ],
            "elapsed_seconds": round(time.time() - started, 1),
            "artifact_inputs": {
                **(existing.get("artifact_inputs") or {}),
                **(artifact_inputs or {}),
            },
            "files": {
                "script": str(self.script_path),
                "subtitles": str(self.subtitles_path),
                "ass": str(self.ass_path),
                "narration": str(self.narration_path),
                "mix": str(self.mix_path),
                "edit_export": str(self.edit_export_path),
                **({"cuts": str(self.cuts_path)} if cuts is not None else {}),
            },
        }
        if artifact_inputs and "vlm" in artifact_inputs:
            data["analysis_settings_key"] = self._analysis_settings_key()
        if artifact_inputs and "render" in artifact_inputs:
            settings = self.settings
            data["render_settings"] = {
                "output_height": settings.output_height,
                "output_fps": settings.output_fps,
                "crf": settings.crf,
                "preset": settings.preset,
                "audio_bitrate": settings.audio_bitrate,
                "subtitle_font": settings.resolved_subtitle_font(),
                "subtitle_size": settings.subtitle_size,
                "subtitle_preset": settings.subtitle_preset,
                "subtitle_margin_v": settings.subtitle_margin_v,
                "subtitle_margin_left": settings.subtitle_margin_left,
                "subtitle_margin_right": settings.subtitle_margin_right,
                "subtitle_render": settings.subtitle_render,
                "subtitle_outline": settings.subtitle_outline,
                "subtitle_box_padding": settings.subtitle_box_padding,
                "subtitle_max_chars": settings.resolved_subtitle_max_chars(),
                "subtitle_max_lines": settings.subtitle_max_lines,
                "subtitle_max_seconds": settings.subtitle_max_seconds,
                "subtitle_size_unit": "px",
                "subtitle_layout": "ass",
                "burn_subtitles": settings.burn_subtitles,
                "content_language": settings.content_language,
                "subtitle_text": settings.subtitle_text,
                "max_speedup": settings.max_speedup,
                "target_lufs": settings.target_lufs,
            }
        if self._segments is None:
            for key in ("narration_segments", "narration_chars", "narration_seconds", "timeline"):
                if key in existing:
                    data[key] = existing[key]
        if "qa" in invalidated:
            data.pop("qa", None)
        atomic_write_json(self.manifest_path, data)
        if announce:
            LOG.info("Done: %s (%.1fs)", self.output_path, time.time() - started)


def _data_key(value: Any) -> str:
    return sha1_text(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _markdown_cell(value: str) -> str:
    return " ".join(value.splitlines()).replace("|", "\\|")


def _flag_stage(code: str) -> str:
    """Map a quality-flag code to the stage that owns it."""
    prefix = code.split(".", 1)[0]
    return {
        "vlm": "vlm",
        "tts": "tts",
        "timeline": "tts",
        "asr": "subtitles",
        "qa": "qa",
    }.get(prefix, "")


def _parse_script_md_updates(path: Path) -> dict[int, str]:
    """Extract ``segment_id -> text`` rows from a ``script.md`` review table."""
    updates: dict[int, str] = {}
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            continue
        cells = [cell.strip() for cell in stripped[1:-1].split("|")]
        if len(cells) < 5:
            continue
        if not cells[0].isdigit():
            continue
        seg_id = int(cells[0])
        text = cells[4].replace("\\|", "|").strip()
        if text:
            updates[seg_id] = text
    return updates


def _predict_import_stages(
    *,
    has_script: bool,
    has_segment_audio: bool,
    has_narration: bool,
    has_subtitles: bool,
    has_bgm: bool,
    has_cuts: bool,
) -> list[str]:
    affected: set[str] = set()
    if has_script or has_segment_audio or has_cuts:
        affected.update(("tts", "subtitles", "mix", "render", "qa"))
    if has_narration:
        affected.update(("subtitles", "mix", "render", "qa"))
    if has_bgm:
        affected.update(("mix", "render", "qa"))
    if has_subtitles:
        affected.update(("render", "qa"))
    return [stage for stage in STAGE_ORDER if stage in affected]


def _load_segments(data: dict[str, Any]) -> list[NarrationSegment]:
    result = []
    items = data.get("segments") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return result
    for item in items:
        try:
            raw_start_override = item.get("start_override")
            start_override = (
                float(raw_start_override)
                if raw_start_override is not None and raw_start_override != ""
                else None
            )
            result.append(
                NarrationSegment(
                    id=int(item["id"]),
                    scene_ids=[int(x) for x in item.get("scene_ids", [])],
                    text=str(item["text"]),
                    audio_path=str(item.get("audio_path") or ""),
                    duration=float(item.get("duration") or 0),
                    start=float(item.get("start") or 0),
                    end=float(item.get("end") or 0),
                    tts_speed=float(item.get("tts_speed") or 1.0),
                    instruct=str(item.get("instruct") or "").strip(),
                    voice=str(item.get("voice") or "").strip(),
                    speed=float(item.get("speed") or 0.0),
                    start_override=start_override,
                    gain_db=float(item.get("gain_db") or 0.0),
                    audio_override=str(item.get("audio_override") or "").strip(),
                    enabled=bool(item.get("enabled", True)),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return result


def _read_dict_json(path: Path) -> dict[str, Any]:
    data = read_json(path, {})
    return data if isinstance(data, dict) else {}


def workdir_status(workdir: Path) -> dict[str, Any]:
    """Inspect a work directory's stages, artifacts, staleness, and next step."""
    workdir = workdir.expanduser().resolve()
    manifest = _read_dict_json(workdir / "manifest.json")
    scenes_data = _read_dict_json(workdir / "scenes.json")
    script_data = _read_dict_json(workdir / "script.json")
    timeline_data = _read_dict_json(workdir / "timeline.json")
    qa_data = _read_dict_json(workdir / "qa.json")

    raw_video = manifest.get("video") or scenes_data.get("video") or {}
    video_info = raw_video if isinstance(raw_video, dict) else {}
    video_path_str = str(video_info.get("path") or "")
    video_path = Path(video_path_str) if video_path_str else None
    source_exists = bool(video_path and video_path.is_file())

    stage_inputs = StageInputs(workdir, video_path if source_exists else None)
    artifact_inputs = manifest.get("artifact_inputs")
    if not isinstance(artifact_inputs, dict):
        artifact_inputs = {}

    scenes_path = workdir / "scenes.json"
    scenes_list = scenes_data.get("scenes") if isinstance(scenes_data.get("scenes"), list) else []
    vlm_done = (
        scenes_path.is_file()
        and bool(scenes_list)
        and (
            "vlm" in artifact_inputs
            or (workdir / "vlm_cache.json").is_file()
            or any(isinstance(scene, dict) and scene.get("summary") for scene in scenes_list)
        )
    )

    script_segments = _load_segments(script_data)
    generation = script_data.get("generation")
    script_edited = bool(
        script_segments
        and isinstance(generation, dict)
        and generation
        and generation.get("segments_key")
        != _data_key([segment.to_dict() for segment in script_segments])
    )

    timeline_segments = _load_segments(timeline_data)
    tts_done = (
        (workdir / "timeline.json").is_file()
        and (workdir / "narration.wav").is_file()
        and bool(timeline_segments)
        and any(segment.audio_path for segment in timeline_segments)
    )

    stage_statuses: dict[str, str] = {}
    stage_entries: list[dict[str, Any]] = []
    for stage in STAGE_ORDER:
        artifact_path = workdir / STAGE_ARTIFACTS[stage]
        if stage == "probe":
            exists = bool(video_path_str)
            status = "ok" if exists else "missing"
            if not artifact_path.is_file() and scenes_path.is_file():
                artifact_path = scenes_path
        elif stage == "vlm":
            exists = vlm_done
            if not exists:
                status = "missing"
            elif stage_inputs.first_stale(manifest, ("vlm",)) == "vlm":
                status = "stale"
            else:
                status = "ok"
        elif stage == "script":
            exists = bool(script_segments)
            status = "ok" if exists else "missing"
        elif stage == "tts":
            exists = tts_done
            if not exists:
                status = "missing"
            elif stage_inputs.first_stale(manifest, ("tts",)) == "tts":
                status = "stale"
            else:
                status = "ok"
        elif stage in {"subtitles", "mix", "render"}:
            exists = artifact_path.is_file()
            if not exists:
                status = "missing"
            elif stage_inputs.first_stale(manifest, (stage,)) == stage:
                status = "stale"
            else:
                status = "ok"
        else:  # qa
            exists = artifact_path.is_file()
            if not exists:
                status = "missing"
            elif not isinstance(manifest.get("qa"), dict) or stage_statuses.get("render") != "ok":
                status = "stale"
            else:
                status = "ok"

        stage_statuses[stage] = status
        stage_entries.append(
            {
                "stage": stage,
                "status": status,
                "artifact": str(artifact_path),
                "exists": exists,
            }
        )

    qa_info: dict[str, Any] | None = None
    qa_summary = qa_data.get("summary")
    if (workdir / "qa.json").is_file() and isinstance(qa_summary, dict):
        qa_state = (
            "fail"
            if qa_summary.get("fail", 0) > 0
            else ("warn" if qa_summary.get("warn", 0) > 0 else "pass")
        )
        qa_info = {
            "status": qa_state,
            "summary": qa_summary,
            "stale": stage_statuses["qa"] == "stale",
        }

    next_stage: str | None = None
    next_command: str | None = None
    for stage in STAGE_ORDER:
        if stage_statuses[stage] != "ok":
            next_stage = stage
            if stage == "probe":
                next_command = None
            elif stage == "vlm":
                if source_exists and video_path is not None:
                    next_command = shlex.join(
                        ("reachreel", "analyze", str(video_path), "--workdir", str(workdir))
                    )
            else:
                command = "subtitle" if stage == "subtitles" else stage
                next_command = shlex.join(("reachreel", command, str(workdir)))
            break

    downstream_of = {
        "probe": ("vlm", "script", "tts", "subtitles", "mix", "render", "qa"),
        "vlm": ("script", "tts", "subtitles", "mix", "render", "qa"),
        "script": ("tts", "subtitles", "mix", "render", "qa"),
        "tts": ("subtitles", "mix", "render", "qa"),
        "subtitles": ("render", "qa"),
        "mix": ("render", "qa"),
        "render": ("qa",),
        "qa": (),
    }
    pending_set: set[str] = set()
    for stage in STAGE_ORDER:
        if stage_statuses[stage] != "ok":
            pending_set.add(stage)
            pending_set.update(downstream_of.get(stage, ()))
    pending_stages = [stage for stage in STAGE_ORDER if stage in pending_set]
    resume_command = (
        shlex.join(("reachreel", "resume", str(workdir)))
        if pending_stages and stage_statuses["probe"] == "ok"
        else None
    )

    artifact_candidates = {
        "manifest": workdir / "manifest.json",
        "scenes": workdir / "scenes.json",
        "cuts": workdir / "cuts.json",
        "script": workdir / "script.json",
        "script_md": workdir / "script.md",
        "timeline": workdir / "timeline.json",
        "narration": workdir / "narration.wav",
        "subtitles": workdir / "subtitles.srt",
        "ass": workdir / "subtitles.ass",
        "mix": workdir / "mix.wav",
        "output": workdir / "final.mp4",
        "qa": workdir / "qa.json",
        "review": workdir / "review.md",
        "edit_export": workdir / "edit",
    }
    artifacts = {
        name: str(path) for name, path in artifact_candidates.items() if path.exists()
    }
    cuts_info = None
    with suppress(ValueError, OSError):
        loaded_cuts = load_cuts(workdir / "cuts.json")
        if loaded_cuts is not None:
            cuts_info = loaded_cuts.to_dict()

    settings_block = manifest.get("settings")
    if not isinstance(settings_block, dict):
        settings_block = {}
    snapshot = manifest.get("settings_snapshot")
    if not isinstance(snapshot, dict):
        snapshot = {}
    summary_settings = {
        "content_language": snapshot.get("content_language")
        or script_data.get("content_language"),
        "vlm_model": settings_block.get("vlm_model"),
        "llm_model": settings_block.get("llm_model"),
        "tts_engine": settings_block.get("tts_engine"),
        "voice": settings_block.get("voice"),
        "whisper_model": settings_block.get("whisper_model"),
        "burned_subtitles": settings_block.get("burned_subtitles"),
    }

    flags = manifest.get("quality_flags")
    quality_flags = flags if isinstance(flags, list) else []

    return {
        "workdir": str(workdir),
        "schema_version": manifest.get("schema_version"),
        "complete": all(status == "ok" for status in stage_statuses.values()),
        "video": {
            "path": video_path_str or None,
            "exists": source_exists,
            "duration": video_info.get("duration"),
            "width": video_info.get("width"),
            "height": video_info.get("height"),
            "fps": video_info.get("fps"),
        },
        "settings": summary_settings,
        "cuts": cuts_info,
        "script_edited": script_edited,
        "stages": stage_entries,
        "artifacts": artifacts,
        "quality_flags": quality_flags,
        "qa": qa_info,
        "next_stage": next_stage,
        "next_command": next_command,
        "pending_stages": pending_stages,
        "resume_command": resume_command,
    }


def print_status(report: dict[str, Any]) -> None:
    """Print a human-readable summary of ``workdir_status``."""
    video = report["video"]
    if not video["path"]:
        video_line = t("status.video_unknown")
    elif video["exists"]:
        video_line = t("status.video", value=video["path"])
    else:
        video_line = t("status.video_missing", value=video["path"])

    lines = [
        t("status.title"),
        "=" * 60,
        t("status.workdir", value=report["workdir"]),
        video_line,
        t("status.complete") if report["complete"] else t("status.incomplete"),
    ]
    if report["script_edited"]:
        lines.append(t("status.script_edited"))
    lines += ["", t("status.stages")]
    labels = {
        "ok": t("status.state_ok"),
        "stale": t("status.state_stale"),
        "missing": t("status.state_missing"),
    }
    badge_width = max(5, *(len(lbl) for lbl in labels.values()))
    for entry in report["stages"]:
        stage = entry["stage"]
        mark = pad_display(labels.get(entry["status"], entry["status"]), badge_width)
        lines.append(f"  [{mark}] {stage:<10} {stage_label(stage)}")
    lines.append("")
    qa_info = report.get("qa")
    if qa_info is None:
        lines.append(t("status.qa_none"))
    elif qa_info.get("stale"):
        lines.append(t("status.qa_stale", status=qa_info["status"]))
    else:
        summary = qa_info["summary"]
        lines.append(
            t(
                "status.qa",
                status=qa_info["status"],
                pass_count=summary.get("pass", 0),
                warn_count=summary.get("warn", 0),
                fail_count=summary.get("fail", 0),
            )
        )
    if report["quality_flags"]:
        lines.append(t("status.quality_flags", count=len(report["quality_flags"])))
    if report.get("resume_command") and len(report.get("pending_stages") or []) > 1:
        lines.append(t("status.resume_command", command=report["resume_command"]))
    if report["next_command"]:
        lines.append(t("status.next_command", command=report["next_command"]))
    print("\n".join(lines))


def doctor_report(settings: Settings) -> dict[str, Any]:
    """Collect the environment availability matrix used by doctor and tools."""
    import importlib.util
    import sys
    import urllib.request

    checks: list[dict[str, Any]] = []
    ok = True

    def add(
        check_id: str,
        label: str,
        value: str,
        good: bool = True,
        *,
        required: bool = True,
        hint: str = "",
    ) -> None:
        nonlocal ok
        if not good and required:
            ok = False
        entry: dict[str, Any] = {
            "id": check_id,
            "label": label,
            "value": value,
            "status": "ok" if good else ("missing" if required else "optional_missing"),
            "required": required,
            "ok": bool(good),
        }
        if hint and not good:
            entry["hint"] = hint
        checks.append(entry)

    add("python", t("doctor.python"), sys.version.split()[0])
    host = host_platform()
    supported_host = platform_supported()
    add(
        "platform",
        t("doctor.platform"),
        t("doctor.platform_ok", host=host)
        if supported_host
        else t("doctor.platform_unsupported", host=host),
        supported_host,
    )
    ffmpeg_path = find_ffmpeg()
    add(
        "ffmpeg",
        "ffmpeg",
        ffmpeg_path or t("doctor.missing"),
        bool(ffmpeg_path),
        hint="brew install ffmpeg-full",
    )
    ffprobe_path = find_ffprobe()
    add(
        "ffprobe",
        "ffprobe",
        ffprobe_path or t("doctor.missing"),
        bool(ffprobe_path),
        hint="brew install ffmpeg-full",
    )
    full = ffmpeg_full()
    add(
        "ffmpeg_full",
        t("doctor.ffmpeg_full"),
        full or t("doctor.ffmpeg_full_missing"),
        bool(full),
        hint="brew install ffmpeg-full",
    )
    try:
        from ..core.util import ffmpeg_filters

        filters = ffmpeg_filters(full or ffmpeg_bin())
        for name in ("subtitles", "sidechaincompress", "loudnorm", "blackdetect", "freezedetect"):
            add(
                f"filter.{name}",
                t("doctor.filter", name=name),
                t("doctor.present") if name in filters else t("doctor.missing"),
                name in filters,
                hint="brew install ffmpeg-full",
            )
    except Exception as exc:  # noqa: BLE001
        add("filter_check", t("doctor.filter_check"), str(exc), False)

    for module in ("mlx_audio", "mlx_whisper"):
        found = importlib.util.find_spec(module) is not None
        pkg = module.replace("_", "-")
        add(
            f"module.{module}",
            module,
            t("doctor.installed") if found else t("doctor.missing"),
            found,
            hint=f"pip install {pkg}",
        )
    resolved_asr = settings.resolved_asr_language()
    resolved_voice = settings.resolved_voice()
    voice_display = (
        f"clone:{Path(settings.tts_ref_audio).name}"
        if settings.tts_engine == "qwen3" and settings.tts_ref_audio.strip()
        else (resolved_voice or t("doctor.default_value"))
    )
    follows = t("doctor.follows_content")
    for axis, label, value in (
        ("content_language", t("doctor.content_language"), settings.content_language),
        ("tts_language", t("doctor.tts_language"), settings.tts_language or follows),
        (
            "asr_language",
            t("doctor.asr_language"),
            settings.asr_language or f"{resolved_asr} {follows}",
        ),
        ("voice", t("doctor.voice"), voice_display),
    ):
        add(f"language.{axis}", label, str(value), True, required=False)

    profile = None
    try:
        from ..languages import (
            check_tts_dependencies,
            require_voice_match,
            resolve_tts_language,
            supported_tags,
            voice_language,
        )

        profile = resolve_tts_language(
            tts_language=settings.tts_language,
            content_language=settings.content_language,
            voice=resolved_voice,
        )
        code = profile.tts.get(settings.tts_engine)
        detail = f"{profile.tag} -> {settings.tts_engine}"
        detail += f" {code}" if code else " (no code)"
        good = profile.status == "supported"
        if not good:
            detail += " (planned, not validated)"
        elif settings.tts_engine == "kokoro":
            try:
                require_voice_match(profile, resolved_voice)
            except ValueError as exc:
                detail = str(exc)
                good = False
        elif settings.tts_engine == "qwen3" and resolved_voice and voice_language(resolved_voice) is not None:
            detail = f"voice {resolved_voice!r} is a Kokoro voice; choose a Qwen3 speaker or leave voice empty"
            good = False
        add("language.tts", t("doctor.tts_match"), detail, good, hint="reachreel voices")
    except ValueError as exc:
        add(
            "language.tts",
            t("doctor.tts_match"),
            f"{exc} (validated: {', '.join(supported_tags())})",
            False,
        )

    if profile is not None:
        g2p_ok, g2p_detail = check_tts_dependencies(profile, settings.tts_engine)
        dep_label = (
            t("doctor.tts_backend", engine=settings.tts_engine)
            if settings.tts_engine != "kokoro"
            else t("doctor.g2p", tag=profile.tag)
        )
        g2p_hint = (
            "pip install -e '.[tts-en]' && python -m spacy download en_core_web_sm"
            if settings.tts_engine == "kokoro" and profile.primary == "en"
            else "pip install 'misaki[zh]'"
            if settings.tts_engine == "kokoro"
            else "pip install 'mlx-audio>=0.6.7'"
        )
        add(
            "module.misaki",
            dep_label,
            g2p_detail,
            g2p_ok,
            hint=g2p_hint,
        )
    else:
        misaki = importlib.util.find_spec("misaki") is not None
        add(
            "module.misaki",
            "misaki",
            t("doctor.installed") if misaki else t("doctor.misaki_missing"),
            misaki,
            hint="pip install 'misaki[zh]'",
        )

    if settings.tts_engine == "qwen3" and settings.tts_ref_audio.strip():
        from ..engines.tts import validate_ref_audio

        try:
            ref_path = validate_ref_audio(settings.tts_ref_audio)
            add("tts.ref_audio", "TTS ref_audio", str(ref_path), True)
        except Exception as exc:  # noqa: BLE001
            add("tts.ref_audio", "TTS ref_audio", str(exc), False)

    endpoint = public_ollama_url(settings.ollama_url) or t("doctor.missing")
    try:
        with urllib.request.urlopen(f"{settings.ollama_url.rstrip('/')}/api/tags", timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        names = [m.get("name", "") for m in data.get("models", [])]
        add("ollama", t("doctor.ollama"), endpoint)
        for model in (settings.vlm_model, settings.llm_model):
            present = any(
                name == model or name.startswith(model.split(":")[0] + ":") for name in names
            )
            add(
                f"model.{model}",
                t("doctor.model", name=model),
                t("doctor.present") if present else t("doctor.model_missing"),
                present,
                hint=f"ollama pull {model}",
            )
    except Exception as exc:  # noqa: BLE001
        add(
            "ollama",
            t("doctor.ollama"),
            t(
                "doctor.unreachable", url=endpoint,
                error=str(exc) if endpoint == settings.ollama_url else type(exc).__name__,
            ),
            False,
            hint="ollama serve",
        )

    bgm = settings.resolved_bgm_dir()
    tracks = []
    if bgm.exists():
        from ..media.audio import AUDIO_EXTS

        tracks = [p.name for p in bgm.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS]
    add(
        "bgm",
        t("doctor.bgm_folder"),
        t("doctor.bgm_tracks", path=bgm, count=len(tracks))
        if tracks
        else t("doctor.bgm_empty", path=bgm),
        bool(tracks),
        required=False,
    )
    return {"ok": ok, "checks": checks}


def doctor(settings: Settings, *, as_json: bool = False) -> int:
    """Print a local environment report. Returns 0 when the essentials are present."""
    report = doctor_report(settings)
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1
    print(t("doctor.title") + "\n" + "=" * 60)
    for check in report["checks"]:
        if check["ok"]:
            mark = "OK "
        elif check["required"]:
            mark = "!! "
        else:
            mark = "-- "
        print(f"{mark}{pad_display(check['label'], 26)} {check['value']}")
        if not check["ok"] and check.get("hint"):
            print(f"   {' ' * 26} -> {check['hint']}")
    print("=" * 60)
    print(t("doctor.ready") if report["ok"] else t("doctor.fix"))
    return 0 if report["ok"] else 1
