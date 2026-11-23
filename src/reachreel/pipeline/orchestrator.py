"""Pipeline orchestration."""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.cache import cache_root, tts_cache_dir, video_key, vlm_cache_path
from ..core.config import Settings, settings_to_dict
from ..core.util import (
    LOG,
    atomic_write_json,
    ffmpeg_full,
    host_platform,
    platform_supported,
    read_json,
)
from ..domain.models import (
    NarrationSegment,
    QualityFlag,
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

STAGE_ORDER = ["probe", "vlm", "script", "tts", "subtitles", "mix", "render", "qa"]

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

STAGE_ARTIFACTS = {
    "probe": "scenes.json",
    "vlm": "vlm_cache.json",
    "script": "script.json",
    "tts": "timeline.json + narration.wav",
    "subtitles": "subtitles.srt",
    "mix": "mix.wav",
    "render": "final.mp4",
    "qa": "qa.json",
}


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
        self.manifest_path = self.workdir / "manifest.json"
        self._info: VideoInfo | None = None
        self._scenes = None
        self._segments: list[NarrationSegment] | None = None
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
        if self.scenes_path.exists() and self.resume and not self.force:
            cached_info, cached_scenes = load_scenes(self.scenes_path)
            if Path(cached_info.path).resolve() != self.video:
                LOG.warning("Scene analysis in the workdir belongs to a different video; re-analyzing")
            else:
                # Always trust a fresh probe: scenes.json written by an older
                # version may lack rotation/coded dimensions.
                if cached_info != info:
                    LOG.info("Refreshing video metadata in scenes.json")
                    save_scenes(self.scenes_path, info, cached_scenes)
                self._info = info
                self._scenes = cached_scenes
                LOG.info("Reusing scene analysis (%d scenes)", len(self._scenes))
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
        save_scenes(self.scenes_path, info, scenes)
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
        save_scenes(self.scenes_path, info, self._scenes)

    def write_script(self, *, reuse: bool = True) -> None:
        assert self._scenes is not None
        if reuse and self.script_path.exists() and self.resume and not self.force:
            data = read_json(self.script_path, {})
            segments = _load_segments(data)
            cached_language = data.get("content_language", "zh")
            if segments and cached_language == self.settings.content_language:
                self._segments = segments
                LOG.info("Reusing narration script (%d segments)", len(segments))
                return
            if segments:
                LOG.info(
                    "Narration language changed (%s -> %s); regenerating script",
                    cached_language,
                    self.settings.content_language,
                )
        client = OllamaClient(self.settings.ollama_url, self.settings.llm_model)
        segments = generate_script(
            self._scenes,
            duration=self.load_video().duration,
            client=client,
            style=self.settings.style,
            style_preset=self.settings.resolved_style_preset(),
            style_guidance=self.settings.resolved_style_guidance(),
            oral_polish=self.settings.script_oral_polish,
            brief=self.settings.brief,
            cps=self.settings.resolved_speech_chars_per_second(),
            density=self.settings.narration_density,
            temperature=self.settings.llm_temperature,
            content_language=self.settings.content_language,
        )
        atomic_write_json(
            self.script_path,
            {
                "content_language": self.settings.content_language,
                "style_preset": self.settings.resolved_style_preset(),
                "style": self.settings.resolved_style(),
                "segments": [s.to_dict() for s in segments],
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
        save_timeline(self.timeline_path, self._segments)
        build_narration_track(
            self._segments,
            self.load_video().duration,
            self.narration_path,
        )

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
        if not no_bgm:
            chosen = bgm_file or self.bgm_source or pick_bgm(self.settings)
            if chosen:
                self.bgm_disabled = False
                self.bgm_source = chosen
                self.bgm_metadata = bgm_metadata(chosen)
                if chosen.resolve() == self.bgm_path.resolve() and self.bgm_path.exists():
                    # Stage commands may hand us the already-looping bed.
                    bgm_wav = self.bgm_path
                else:
                    prepare_bgm_loop(chosen, info.duration, self.bgm_path)
                    bgm_wav = self.bgm_path
            else:
                LOG.warning("No BGM found. Put CC0 music in %s to enable automatic mixing.", self.settings.resolved_bgm_dir())
        else:
            self.bgm_disabled = True
            self.bgm_source = None
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
        burned = render(
            video=self.video,
            audio=self.mix_path,
            subtitles=self.subtitles_path,
            out=self.output_path,
            settings=self.settings,
            burn=burn,
        )
        return burned

    # ---------- stage commands ----------

    def load_scenes_data(self) -> None:
        if self._scenes is not None:
            return
        if not self.scenes_path.exists():
            raise RuntimeError(
                f"Missing {self.scenes_path.name}. Run first: reachreel analyze <video> --workdir {self.workdir}"
            )
        info, scenes = load_scenes(self.scenes_path)
        if Path(info.path).resolve() != self.video:
            raise RuntimeError("scenes.json does not match the current video")
        self._info = info
        self._scenes = scenes

    def load_script(self) -> None:
        if self._segments is not None:
            return
        data = read_json(self.script_path, {})
        segments = _load_segments(data)
        if not segments:
            raise RuntimeError(
                f"Missing {self.script_path.name}. Run first: reachreel script {self.workdir}"
            )
        self._segments = segments

    def ensure_narration(self) -> None:
        if self.narration_path.exists():
            return
        self.load_timeline()
        build_narration_track(
            self._segments or [],
            self.load_video().duration,
            self.narration_path,
        )

    def load_timeline(self) -> None:
        data = read_json(self.timeline_path, {})
        segments = _load_segments(data)
        if segments and any(segment.audio_path for segment in segments):
            self._segments = segments
            return
        # No usable timeline: rebuild it from the script + existing TTS audio.
        self.load_scenes_data()
        self.load_script()
        self.make_voice()
        self.make_timeline()

    def stage_script(self) -> None:
        self.prepare_scenes()
        self.understand()
        self.write_script(reuse=False)
        self.write_manifest(started=time.time(), burned=False, announce=False)
        self.write_script_md()
        LOG.info("Review: %s", self.script_md_path)
        LOG.info("Next: reachreel tts %s", self.workdir)

    def stage_tts(self) -> None:
        self.load_script()
        self.load_scenes_data()
        self.make_voice()
        self.make_timeline()
        self.update_manifest()
        LOG.info(
            "Next: reachreel subtitle %s && reachreel mix %s && reachreel render %s",
            self.workdir,
            self.workdir,
            self.workdir,
        )

    def stage_subtitle(self, *, asr_engine: str = "mlx-whisper") -> None:
        self.load_timeline()
        self.ensure_narration()
        self.make_subtitles(asr_engine=asr_engine)
        self.update_manifest()
        LOG.info("Next: reachreel render %s", self.workdir)

    def stage_mix(self, *, bgm_file: Path | None = None, no_bgm: bool = False) -> None:
        self.load_timeline()
        self.ensure_narration()
        self.make_mix(bgm_file=bgm_file, no_bgm=no_bgm)
        self.update_manifest()
        LOG.info("Next: reachreel render %s", self.workdir)

    def update_manifest(self) -> None:
        existing = read_json(self.manifest_path, {}) or {}
        burned = (
            self.burned_subtitles
            if self.burned_subtitles is not None
            else bool(existing.get("settings", {}).get("burned_subtitles", False))
        )
        self.write_manifest(started=time.time(), burned=burned, announce=False)
        data = read_json(self.manifest_path, {}) or {}
        if existing.get("elapsed_seconds"):
            data["elapsed_seconds"] = existing["elapsed_seconds"]
        data.pop("qa", None)
        if not data.get("bgm_source") and not self.bgm_disabled:
            data["bgm_source"] = existing.get("bgm_source")
        if not data.get("bgm_metadata") and not self.bgm_disabled:
            data["bgm_metadata"] = existing.get("bgm_metadata")
        # Stage commands may not load scenes; keep the existing analysis summary.
        if not self._scenes and existing.get("scenes"):
            data["scenes"] = existing["scenes"]
        if not self._segments:
            for key in ("narration_segments", "narration_chars", "narration_seconds", "timeline"):
                if key in existing:
                    data[key] = existing[key]
        atomic_write_json(self.manifest_path, data)
        self.write_review()

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
        missing = [name for name in ("mix.wav", "subtitles.srt") if not (self.workdir / name).exists()]
        if missing:
            raise RuntimeError(
                f"Render inputs missing: {', '.join(missing)}. Run first: reachreel mix {self.workdir}"
                f" && reachreel subtitle {self.workdir}"
            )
        self.load_existing_timeline()

    def _run_stages(
        self,
        stages: list[str],
        *,
        asr_engine: str,
        bgm_file: Path | None,
        no_bgm: bool,
        burn: bool,
        explicit: bool,
    ) -> bool | None:
        burned: bool | None = None
        for stage in stages:
            LOG.info("=== Stage: %s ===", stage_label(stage))
            if stage == "probe":
                self.load_video()
            elif stage == "vlm":
                self.prepare_scenes()
                self.understand(force=self.force or explicit)
            elif stage == "script":
                if self._scenes is None:
                    self.load_scenes_data()
                self.write_script(reuse=not explicit)
            elif stage == "tts":
                self.load_script()
                self.load_scenes_data()
                self.make_voice()
                self.make_timeline()
            elif stage == "subtitles":
                self.load_timeline()
                self.ensure_narration()
                self.make_subtitles(asr_engine=asr_engine)
            elif stage == "mix":
                self.load_timeline()
                self.ensure_narration()
                self.make_mix(bgm_file=bgm_file, no_bgm=no_bgm)
            elif stage == "render":
                self._require_render_inputs()
                burned = self.render_final(burn=burn)
                self.burned_subtitles = burned
        return burned

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
        burned = self._run_stages(
            [stage for stage in stages if stage != "qa"],
            asr_engine=asr_engine,
            bgm_file=bgm_file,
            no_bgm=no_bgm,
            burn=burn,
            explicit=explicit,
        )

        if review_script:
            self.write_manifest(started=started, burned=False)
            self.write_script_md()
            LOG.info("Script review ready: %s", self.script_md_path)
            LOG.info("Edit %s, then run: reachreel tts %s", self.script_path, self.workdir)
            return self.script_path

        if explicit:
            if any(stage in stages for stage in ("tts", "subtitles", "mix", "render")):
                self.update_manifest()
            if "render" in stages:
                self.write_review()
        else:
            self.write_manifest(started=started, burned=bool(burned))
            self.write_review()

        if (explicit and "qa" in stages) or (not explicit and qa):
            from .qa import attach_to_manifest, print_report, run_qa

            self.qa_report = run_qa(self.workdir, strict=qa_strict)
            print_report(self.qa_report)
            attach_to_manifest(self.workdir, self.qa_report)
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
            "|---|---|---|---|---|",
        ]
        for segment in estimates:
            scene_ids = ", ".join(str(scene_id) for scene_id in segment.scene_ids)
            lines.append(
                f"| {segment.id} | {scene_ids} | {segment.start:.1f} | "
                f"{segment.end:.1f} | {segment.text} |"
            )
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
                f"| {segment.id} | {segment.start:.1f}-{segment.end:.1f}s | {segment.text} |"
            )
        lines += [
            "",
            t("review.section_edit"),
            "",
            t("review.edit_hint"),
            "",
        ]
        (self.workdir / "review.md").write_text("\n".join(lines), encoding="utf-8")

    def write_manifest(self, *, started: float, burned: bool, announce: bool = True) -> None:
        info = self.load_video()
        segments = self._segments or []
        existing = read_json(self.manifest_path, {}) or {}
        kept_flags = [
            flag
            for flag in (existing.get("quality_flags") or [])
            if isinstance(flag, dict)
            and _flag_stage(str(flag.get("code") or "")) not in self.refreshed_stages
        ]
        data = {
            "video": asdict(info),
            "workdir": str(self.workdir),
            "output": str(self.output_path),
            "settings": {
                "vlm_model": self.settings.vlm_model,
                "llm_model": self.settings.llm_model,
                "tts_engine": self.settings.tts_engine,
                "voice": self.settings.resolved_voice(),
                "whisper_model": self.settings.whisper_model,
                "bgm": str(self.bgm_path) if self.bgm_source and self.bgm_path.exists() else None,
                "burned_subtitles": burned,
            },
            "settings_snapshot": settings_to_dict(self.settings),
            "bgm_source": str(self.bgm_source) if self.bgm_source else None,
            "cache": {
                "enabled": self.shared_cache_root is not None,
                "dir": str(self.shared_cache_root) if self.shared_cache_root else None,
                "video_key": self.video_cache_key() if self.shared_cache_root else None,
            },
            "bgm_metadata": self.bgm_metadata
            or (existing.get("bgm_metadata") if not self.bgm_disabled else None),
            "render_settings": {
                "output_height": self.settings.output_height,
                "output_fps": self.settings.output_fps,
                "crf": self.settings.crf,
                "preset": self.settings.preset,
                "audio_bitrate": self.settings.audio_bitrate,
                "subtitle_font": self.settings.resolved_subtitle_font(),
                "subtitle_size": self.settings.subtitle_size,
                "subtitle_preset": self.settings.subtitle_preset,
                "subtitle_margin_v": self.settings.subtitle_margin_v,
                "subtitle_margin_left": self.settings.subtitle_margin_left,
                "subtitle_margin_right": self.settings.subtitle_margin_right,
                "subtitle_render": self.settings.subtitle_render,
                "subtitle_outline": self.settings.subtitle_outline,
                "subtitle_box_padding": self.settings.subtitle_box_padding,
                "subtitle_max_chars": self.settings.resolved_subtitle_max_chars(),
                "subtitle_max_lines": self.settings.subtitle_max_lines,
                "subtitle_max_seconds": self.settings.subtitle_max_seconds,
                "subtitle_size_unit": "px",
                "subtitle_layout": "ass",
                "burn_subtitles": self.settings.burn_subtitles,
            },
            "quality_flags": merge_quality_flags(kept_flags, self.quality_flags),
            "scenes": len(self._scenes or []),
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
            "files": {
                "script": str(self.script_path),
                "subtitles": str(self.subtitles_path),
                "ass": str(self.ass_path),
                "narration": str(self.narration_path),
                "mix": str(self.mix_path),
            },
        }
        atomic_write_json(self.manifest_path, data)
        if announce:
            LOG.info("Done: %s (%.1fs)", self.output_path, time.time() - started)


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
    for item in data.get("segments", []) or []:
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

    try:
        with urllib.request.urlopen(f"{settings.ollama_url.rstrip('/')}/api/tags", timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        names = [m.get("name", "") for m in data.get("models", [])]
        add("ollama", t("doctor.ollama"), settings.ollama_url)
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
            t("doctor.unreachable", url=settings.ollama_url, error=exc),
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
