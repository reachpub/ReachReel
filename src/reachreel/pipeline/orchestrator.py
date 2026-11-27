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
    ffmpeg_full,
    host_platform,
    platform_supported,
    read_json,
    sha1_text,
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
from ..media.audio import bgm_metadata, mix_audio, pick_bgm, prepare_bgm_loop
from ..media.render import output_dimensions, render
from ..media.scenes import (
    detect_scenes,
    extract_frames,
    load_scenes,
    probe_video,
    save_scenes,
)
from ..media.subtitles import build_subtitles, write_ass
from ..prompts import load_content, load_prompt
from .stage_inputs import NARRATION_STAGES, RENDER_STAGES, STAGE_ARTIFACTS, StageInputs

STAGE_ORDER = ["probe", "vlm", "script", "tts", "subtitles", "mix", "render", "qa"]
EDIT_FORMATS = ("mp4", "mov")
MANIFEST_SCHEMA_VERSION = 2

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
        self.shared_cache_root = (
            cache_root(self.settings.cache_dir) if self.settings.cache_enabled else None
        )
        self._video_key: str | None = None

    # ---------- stages ----------

    def load_video(self) -> VideoInfo:
        if self._info is not None:
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

    def write_script(self, *, reuse: bool = True) -> None:
        assert self._scenes is not None
        settings = self.settings
        language = settings.content_language
        options = {
            "duration": self.load_video().duration,
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
             for scene in self._scenes],
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
        segments = generate_script(self._scenes, client=client, **options)
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
            )
        )

    def make_timeline(self) -> None:
        assert self._segments is not None
        assert self._scenes is not None
        place_segments(
            self._segments,
            self._scenes,
            self.load_video().duration,
            self.settings,
        )
        build_narration_track(
            self._segments,
            self.load_video().duration,
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
        bgm_wav = None
        directory = self.settings.resolved_bgm_dir()
        saved = self.bgm_source
        if saved is not None and (
            not saved.is_file() or saved.absolute().parent.resolve() != directory.resolve()
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
                prepare_bgm_loop(chosen, info.duration, self.bgm_path)
            bgm_wav = self.bgm_path
        elif not no_bgm:
            LOG.warning("No BGM found. Put CC0 music in %s to enable automatic mixing.", directory)
        mix_audio(
            video=self.video,
            narration_wav=self.narration_path,
            bgm_wav=bgm_wav,
            out_wav=self.mix_path,
            duration=info.duration,
            has_ambient=info.has_audio,
            settings=self.settings,
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
        )
        return self.burned_subtitles

    def export_edit(self, *, destination: Path | None = None, video_format: str = "mp4") -> Path:
        """Build an editing bundle using the source and existing stage artifacts."""
        from ..media.edit_export import write_edit_bundle

        if video_format not in EDIT_FORMATS:
            raise ValueError(f"Unsupported edit video format: {video_format}")
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
        write_edit_bundle(
            destination,
            source=self.load_video(),
            narration=self.narration_path,
            subtitles=self.subtitles_path,
            mix=self.mix_path if self.mix_path.is_file() else None,
            settings=self.settings,
            video_format=video_format,
        )
        return destination

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
    ) -> None:
        for stage in stages:
            LOG.info("=== Stage: %s ===", stage_label(stage))
            inputs = self.stage_inputs.capture(stage)
            if stage == "probe":
                self.load_video()
            elif stage == "vlm":
                self.prepare_scenes()
                self.understand(force=self.force or explicit)
            elif stage == "script":
                self.load_scenes_data()
                if self._analysis_settings_changed():
                    raise RuntimeError(t("error.analysis_options_changed", command=shlex.join((
                        "reachreel", "run", str(self.video), "--workdir", str(self.workdir),
                        "--only", "vlm,script",
                    ))))
                self.write_script(reuse=not explicit)
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
                explicit=selected is not None,
                review=review_script,
            )
            return self.output_path

        started = time.time()
        explicit = selected is not None
        self._run_stages(
            [stage for stage in stages if stage != "qa"],
            started=started,
            asr_engine=asr_engine,
            bgm_file=bgm_file,
            no_bgm=no_bgm,
            burn=burn,
            explicit=explicit,
        )

        if "script" in stages and (review_script or explicit):
            self.write_script_md()
            LOG.info("Script review ready: %s", self.script_md_path)
            LOG.info("Edit %s, then run: reachreel tts %s", self.script_path, self.workdir)
        if any(stage in stages for stage in ("tts", "subtitles", "mix", "render")):
            self.write_review()

        if (explicit and "qa" in stages) or (not explicit and qa and not review_script):
            from .qa import attach_to_manifest, print_report, run_qa

            self.qa_report = run_qa(
                self.workdir, strict=qa_strict, settings_override=self.settings.qa_values()
            )
            print_report(self.qa_report)
            attach_to_manifest(self.workdir, self.qa_report)
        if explicit or review_script:
            return self.workdir / STAGE_ARTIFACTS[stages[-1]]
        LOG.info("Done: %s (%.1fs)", self.output_path, time.time() - started)
        return self.output_path

    def print_dry_run(
        self,
        selected: list[str] | None,
        *,
        qa: bool = True,
        qa_strict: bool = False,
        burn: bool = True,
        explicit: bool | None = None,
        review: bool = False,
    ) -> None:
        """Describe what `run` would do without touching models or writing files."""
        info = self.load_video()
        if explicit is None:
            explicit = selected is not None
        stages = selected or list(STAGE_ORDER)
        settings = self.settings
        lines = [
            t("dry_run.title"),
            "=" * 60,
            t("dry_run.video", value=self.video),
            t(
                "dry_run.video_meta",
                width=info.width,
                height=info.height,
                fps=info.fps,
                duration=info.duration,
                audio=t("common.audio_present")
                if info.has_audio
                else t("common.audio_missing"),
            ),
            t("dry_run.workdir", value=self.workdir)
            + ("" if self.workdir.exists() else t("dry_run.workdir_new")),
            (
                t("dry_run.mode_review", stages=", ".join(stages))
                if review
                else t("dry_run.mode_selected", stages=", ".join(stages))
                if explicit
                else t("dry_run.mode_full")
            ),
            t(
                "dry_run.models",
                vlm=settings.vlm_model,
                llm=settings.llm_model,
                tts=settings.tts_engine,
                voice=(
                    f"clone:{Path(settings.tts_ref_audio).name}"
                    if settings.tts_engine == "qwen3" and settings.tts_ref_audio.strip()
                    else (settings.resolved_voice() or "custom")
                ),
            ),
            t(
                "dry_run.render",
                height=settings.output_height or t("common.source"),
                fps=settings.output_fps or t("common.source"),
                burned=t("common.on") if burn else t("common.off"),
            ),
            t("dry_run.qa", state=t("common.on") if qa else t("common.off"))
            + (t("common.strict") if qa and qa_strict else ""),
            "",
            t("dry_run.stages"),
        ]
        for stage in STAGE_ORDER:
            if stage not in stages:
                lines.append(f"  [skip] {stage:<10} {stage_label(stage)}")
                continue
            lines.append(
                f"  [run ] {stage:<10} {stage_label(stage)}  {self._stage_note(stage, explicit)}"
            )
        lines += [
            "",
            t("dry_run.dry_only"),
        ]
        print("\n".join(lines))

    def _stage_note(self, stage: str, explicit: bool) -> str:
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
        info = self.load_video()
        segments = self._segments or []
        scenes = self._scenes or []
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
            place_segments(estimates, scenes, info.duration, self.settings)
        lines = [
            f"# {t('script_md.title', name=self.video.stem)}",
            "",
            t("script_md.video", path=self.video),
            t("script_md.language", language=self.settings.content_language),
            t("script_md.scenes", count=len(scenes)),
            t("script_md.segments", count=len(segments)),
            t("script_md.characters", count=sum(len(s.text) for s in segments)),
            t(
                "script_md.estimated",
                seconds=sum(s.duration for s in estimates),
                video_seconds=info.duration,
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
            t("script_md.edit_hint", workdir=self.workdir),
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
        data = {
            **existing,
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "video": asdict(info),
            "workdir": str(self.workdir),
            "output": str(self.output_path),
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
            "narration_segments": len(segments),
            "narration_chars": sum(len(s.text) for s in segments),
            "narration_seconds": round(sum(s.duration for s in segments), 2),
            "timeline": [
                {
                    "id": s.id,
                    "start": s.start,
                    "end": s.end,
                    "chars": len(s.text),
                }
                for s in segments
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
        "asr": "subtitles",
        "qa": "qa",
    }.get(prefix, "")


def _load_segments(data: dict[str, Any]) -> list[NarrationSegment]:
    result = []
    items = data.get("segments") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return result
    for item in items:
        try:
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
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return result


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
    ) -> None:
        nonlocal ok
        if not good and required:
            ok = False
        checks.append(
            {
                "id": check_id,
                "label": label,
                "value": value,
                "status": "ok" if good else ("missing" if required else "optional_missing"),
                "required": required,
                "ok": bool(good),
            }
        )

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
    for tool in ("ffmpeg", "ffprobe"):
        path = shutil.which(tool)
        add(tool, tool, path or t("doctor.missing"), bool(path))
    full = ffmpeg_full()
    add(
        "ffmpeg_full",
        t("doctor.ffmpeg_full"),
        full or t("doctor.ffmpeg_full_missing"),
        bool(full),
    )
    try:
        from ..core.util import ffmpeg_filters

        filters = ffmpeg_filters(full or "ffmpeg")
        for name in ("subtitles", "sidechaincompress", "loudnorm", "blackdetect", "freezedetect"):
            add(
                f"filter.{name}",
                t("doctor.filter", name=name),
                t("doctor.present") if name in filters else t("doctor.missing"),
                name in filters,
            )
    except Exception as exc:  # noqa: BLE001
        add("filter_check", t("doctor.filter_check"), str(exc), False)

    for module in ("mlx_audio", "mlx_whisper"):
        found = importlib.util.find_spec(module) is not None
        add(
            f"module.{module}",
            module,
            t("doctor.installed") if found else t("doctor.missing"),
            found,
        )
    resolved_asr = settings.resolved_asr_language()
    resolved_voice = settings.resolved_voice()
    voice_display = (
        f"clone:{Path(settings.tts_ref_audio).name}"
        if settings.tts_engine == "qwen3" and settings.tts_ref_audio.strip()
        else (resolved_voice or "(default)")
    )
    for axis, value in (
        ("content_language", settings.content_language),
        ("tts_language", settings.tts_language or "(follows content_language)"),
        (
            "asr_language",
            settings.asr_language or f"{resolved_asr} (follows content_language)",
        ),
        ("voice", voice_display),
    ):
        add(f"language.{axis}", axis, str(value), True, required=False)

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
        add("language.tts", "TTS language", detail, good)
    except ValueError as exc:
        add(
            "language.tts",
            "TTS language",
            f"{exc} (validated: {', '.join(supported_tags())})",
            False,
        )

    if profile is not None:
        g2p_ok, g2p_detail = check_tts_dependencies(profile, settings.tts_engine)
        dep_label = (
            f"TTS backend ({settings.tts_engine})"
            if settings.tts_engine != "kokoro"
            else f"G2P ({profile.tag})"
        )
        add(
            "module.misaki",
            dep_label,
            g2p_detail,
            g2p_ok,
        )
    else:
        misaki = importlib.util.find_spec("misaki") is not None
        add(
            "module.misaki",
            "misaki",
            t("doctor.installed") if misaki else t("doctor.misaki_missing"),
            misaki,
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
        print(f"{mark}{check['label']:<24} {check['value']}")
    print("=" * 60)
    print(t("doctor.ready") if report["ok"] else t("doctor.fix"))
    return 0 if report["ok"] else 1
