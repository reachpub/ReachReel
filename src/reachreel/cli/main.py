"""Command line interface."""
from __future__ import annotations

import argparse
import contextlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .. import __version__
from ..core.config import STYLE_PRESETS, Settings
from ..core.util import read_json, setup_logging
from ..domain.cuts import parse_ranges, parse_timestamp
from ..i18n import current_language, negotiate_language, set_language, supported_languages, t
from ..pipeline.orchestrator import (
    EDIT_FORMATS,
    MANIFEST_SCHEMA_VERSION,
    NARRATION_MODES,
    STAGE_ORDER,
    Pipeline,
    doctor,
    print_status,
    review_stages,
    workdir_status,
)


def _scan_option(argv: list[str], name: str) -> str | None:
    """Find ``--name value`` or ``--name=value`` before argparse runs."""
    for index, argument in enumerate(argv):
        if argument == name and index + 1 < len(argv):
            return argv[index + 1]
        if argument.startswith(f"{name}="):
            return argument.split("=", 1)[1]
    return None


@contextlib.contextmanager
def _guard_json_stdout(enabled: bool) -> Iterator[None]:
    """Keep stdout pure JSON by routing third-party prints to stderr."""
    if not enabled:
        yield
        return
    with contextlib.redirect_stdout(sys.stderr):
        yield


def _emit_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _overrides(args: argparse.Namespace) -> dict:
    return {
        "content_language": getattr(args, "content_language", None),
        "ollama_url": getattr(args, "ollama_url", None),
        "vlm_model": getattr(args, "vlm_model", None),
        "llm_model": getattr(args, "llm_model", None),
        "vlm_workers": getattr(args, "vlm_workers", None),
        "vlm_frames_per_scene": getattr(args, "vlm_frames", None),
        "vlm_frame_dedup": getattr(args, "vlm_dedup", None),
        "scene_threshold": getattr(args, "scene_threshold", None),
        "voice": getattr(args, "voice", None),
        "tts_engine": getattr(args, "tts", None),
        "tts_language": getattr(args, "tts_language", None),
        "qwen3_model": getattr(args, "qwen3_model", None),
        "tts_speed": getattr(args, "tts_speed", None),
        "tts_instruct": getattr(args, "tts_instruct", None),
        "tts_ref_audio": getattr(args, "tts_ref_audio", None),
        "tts_ref_text": getattr(args, "tts_ref_text", None),
        "tts_temperature": getattr(args, "tts_temperature", None),
        "tts_top_p": getattr(args, "tts_top_p", None),
        "tts_top_k": getattr(args, "tts_top_k", None),
        "tts_repetition_penalty": getattr(args, "tts_repetition_penalty", None),
        "tts_seed": getattr(args, "tts_seed", None),
        "tts_chunk_pause_seconds": getattr(args, "tts_chunk_pause", None),
        "tts_pitch": getattr(args, "tts_pitch", None),
        "tts_volume": getattr(args, "tts_volume", None),
        "whisper_model": getattr(args, "whisper_model", None),
        "style_preset": getattr(args, "style_preset", None),
        "style": getattr(args, "style", None),
        "script_oral_polish": getattr(args, "oral_polish", None),
        "brief": getattr(args, "brief", None),
        "narration_density": getattr(args, "density", None),
        "max_speedup": getattr(args, "max_speedup", None),
        "output_height": getattr(args, "height", None),
        "output_fps": getattr(args, "fps", None),
        "crf": getattr(args, "crf", None),
        "subtitle_size": getattr(args, "subtitle_size", None),
        "subtitle_max_chars": getattr(args, "subtitle_chars", None),
        "subtitle_preset": getattr(args, "subtitle_preset", None),
        "subtitle_render": getattr(args, "subtitle_render", None),
        "subtitle_outline": getattr(args, "subtitle_outline", None),
        "subtitle_box_padding": getattr(args, "subtitle_box_padding", None),
        "subtitle_margin_v": getattr(args, "subtitle_margin_v", None),
        "subtitle_margin_left": getattr(args, "subtitle_margin_left", None),
        "subtitle_margin_right": getattr(args, "subtitle_margin_right", None),
        "bgm_file": getattr(args, "bgm", None),
        "bgm_dir": getattr(args, "bgm_dir", None),
        "bgm_fade_seconds": getattr(args, "bgm_fade", None),
        "bgm_volume": getattr(args, "bgm_volume", None),
        "bgm_duck_db": getattr(args, "bgm_duck_db", None),
        "ambient_volume": getattr(args, "ambient_volume", None),
        "ambient_duck_db": getattr(args, "ambient_duck_db", None),
        "target_lufs": getattr(args, "target_lufs", None),
        "work_root": getattr(args, "out", None),
        "burn_subtitles": False if getattr(args, "no_burn", False) else None,
        "cache_dir": getattr(args, "cache_dir", None),
        "cache_enabled": False if getattr(args, "no_cache", None) else None,
    }


def _settings(args: argparse.Namespace) -> Settings:
    config = Path(args.config) if getattr(args, "config", None) else None
    return Settings.load(config, _overrides(args))


def _workdir_context(
    workdir: Path,
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    *,
    require_source: bool = True,
) -> tuple[Path, Settings, dict]:
    workdir = workdir.resolve()
    manifest = read_json(workdir / "manifest.json", {}) or {}
    source = manifest
    if not manifest:
        # Older analysis-only jobs saved their source in scenes.json.
        source = read_json(workdir / "scenes.json", {}) or {}
    video_raw = str(source.get("video", {}).get("path", "") or "").strip()
    video = Path(video_raw) if video_raw else Path()
    if not video_raw or (require_source and not video.is_file()):
        parser.error(t("error.workdir_no_video", workdir=workdir))

    config = Path(args.config) if getattr(args, "config", None) else None
    old_settings = manifest.get("settings") or {}
    snapshot = manifest.get("settings_snapshot") or {}
    render_settings = manifest.get("render_settings") or {}
    modern = manifest.get("schema_version") == MANIFEST_SCHEMA_VERSION
    saved = {**render_settings, **snapshot} if modern else {**snapshot, **render_settings}
    inherited = {
        **{key: old_settings.get(key) for key in (
            "vlm_model", "llm_model", "tts_engine", "voice", "whisper_model"
        )},
        **saved,
    }
    # The render record stores resolved defaults for QA. Keep automatic values
    # automatic when a new language is selected.
    for key in ("subtitle_font", "subtitle_max_chars"):
        if key in snapshot and not snapshot[key]:
            inherited[key] = snapshot[key]
    settings = Settings.load(config, _overrides(args), inherited=inherited)
    return video, settings, manifest


def _source_context(args: argparse.Namespace, parser: argparse.ArgumentParser) -> tuple[Settings, dict]:
    if not args.video.is_file():
        parser.error(t("error.video_not_found", video=args.video))
    if args.workdir and any(
        (args.workdir / name).is_file() for name in ("manifest.json", "scenes.json")
    ):
        video, settings, manifest = _workdir_context(args.workdir, args, parser)
        if video.resolve() != args.video.resolve():
            parser.error(t("error.workdir_other_video", video=video, current=args.video))
        return settings, manifest
    return _settings(args), {}


def _add_global_flags(p: argparse.ArgumentParser) -> None:
    """Allow global flags after subcommands without overriding pre-subcommand values."""
    p.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS, help=t("cli.verbose"))
    p.add_argument(
        "--lang",
        choices=list(supported_languages()),
        default=argparse.SUPPRESS,
        help=t("cli.lang"),
    )
    p.add_argument("--cache-dir", default=argparse.SUPPRESS, help=t("cli.cache_dir"))
    p.add_argument(
        "--no-cache",
        action="store_true",
        default=argparse.SUPPRESS,
        help=t("cli.no_cache"),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reachreel",
        description=t("cli.description"),
        epilog=t("cli.quickstart"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"reachreel {__version__}")
    parser.add_argument("--verbose", action="store_true", help=t("cli.verbose"))
    parser.add_argument(
        "--lang",
        choices=list(supported_languages()),
        default=None,
        help=t("cli.lang"),
    )
    parser.add_argument(
        "--content-language",
        choices=["zh", "en"],
        default=None,
        help=t("cli.content_language"),
    )
    parser.add_argument("--cache-dir", default=None, help=t("cli.cache_dir"))
    parser.add_argument("--no-cache", action="store_true", default=None, help=t("cli.no_cache"))

    sub = parser.add_subparsers(dest="command", required=True)

    def add_common_models(target: Any, *, include_config: bool = True) -> None:
        if include_config:
            target.add_argument("--config", help=t("cli.config"))
        target.add_argument(
            "--content-language",
            choices=["zh", "en"],
            default=argparse.SUPPRESS,
            help=t("cli.content_language"),
        )
        target.add_argument("--ollama-url", default=None, help=t("cli.ollama_url"))
        target.add_argument("--vlm-model", default=None, help=t("cli.vlm_model"))
        target.add_argument("--llm-model", default=None, help=t("cli.llm_model"))
        target.add_argument("--voice", default=None, help=t("cli.voice"))
        target.add_argument("--tts", choices=["kokoro", "qwen3", "say"], default=None, help=t("cli.tts_engine"))
        target.add_argument("--tts-language", default=None, help=t("cli.tts_language"))
        target.add_argument("--whisper-model", default=None, help=t("cli.whisper_model"))
        target.add_argument("--vlm-workers", type=int, default=None, help=t("cli.vlm_workers"))
        target.add_argument("--vlm-frames", type=int, default=None, help=t("cli.vlm_frames"))
        target.add_argument("--vlm-dedup", type=float, default=None, help=t("cli.vlm_dedup"))
        target.add_argument("--scene-threshold", type=float, default=None, help=t("cli.scene_threshold"))

    def add_tts_options(target: Any, *, include_max_speedup: bool = False) -> None:
        target.add_argument("--qwen3-model", default=None, help=t("cli.qwen3_model"))
        target.add_argument("--tts-speed", type=float, default=None, help=t("cli.tts_speed"))
        if include_max_speedup:
            target.add_argument("--max-speedup", type=float, default=None, help=t("cli.max_speedup"))
        target.add_argument("--tts-instruct", default=None, help=t("cli.tts_instruct"))
        target.add_argument("--tts-ref-audio", default=None, help=t("cli.tts_ref_audio"))
        target.add_argument("--tts-ref-text", default=None, help=t("cli.tts_ref_text"))
        target.add_argument("--tts-temperature", type=float, default=None, help=t("cli.tts_temperature"))
        target.add_argument("--tts-top-p", type=float, default=None, help=t("cli.tts_top_p"))
        target.add_argument("--tts-top-k", type=int, default=None, help=t("cli.tts_top_k"))
        target.add_argument(
            "--tts-repetition-penalty",
            type=float,
            default=None,
            help=t("cli.tts_repetition_penalty"),
        )
        target.add_argument("--tts-seed", type=int, default=None, help=t("cli.tts_seed"))
        target.add_argument("--tts-chunk-pause", type=float, default=None, help=t("cli.tts_chunk_pause"))
        target.add_argument("--tts-pitch", type=float, default=None, help=t("cli.tts_pitch"))
        target.add_argument("--tts-volume", type=float, default=None, help=t("cli.tts_volume"))

    def add_script_options(target: Any, *, include_max_speedup: bool = True) -> None:
        target.add_argument(
            "--style-preset",
            choices=list(STYLE_PRESETS),
            default=None,
            help=t("cli.style_preset"),
        )
        target.add_argument("--style", default=None, help=t("cli.style"))
        target.add_argument(
            "--oral-polish",
            action=argparse.BooleanOptionalAction,
            default=None,
            help=t("cli.oral_polish"),
        )
        target.add_argument("--brief", default=None, help=t("cli.brief"))
        target.add_argument("--density", type=float, default=None, help=t("cli.density"))
        if include_max_speedup:
            target.add_argument("--max-speedup", type=float, default=None, help=t("cli.max_speedup"))

    def add_subtitle_layout_options(target: Any) -> None:
        target.add_argument("--subtitle-size", type=int, default=None, help=t("cli.subtitle_size"))
        target.add_argument("--subtitle-chars", type=int, default=None, help=t("cli.subtitle_chars"))
        target.add_argument(
            "--subtitle-preset",
            choices=["douyin", "reels", "shorts", "landscape"],
            default=None,
            help=t("cli.subtitle_preset"),
        )
        target.add_argument(
            "--subtitle-render",
            choices=["outline", "box", "none"],
            default=None,
            help=t("cli.subtitle_render"),
        )
        target.add_argument("--subtitle-outline", type=float, default=None, help=t("cli.subtitle_outline"))
        target.add_argument(
            "--subtitle-box-padding",
            type=float,
            default=None,
            help=t("cli.subtitle_box_padding"),
        )
        target.add_argument("--subtitle-margin-v", type=int, default=None, help=t("cli.subtitle_margin_v"))
        target.add_argument(
            "--subtitle-margin-left",
            type=int,
            default=None,
            help=t("cli.subtitle_margin_left"),
        )
        target.add_argument(
            "--subtitle-margin-right",
            type=int,
            default=None,
            help=t("cli.subtitle_margin_right"),
        )

    def add_subtitle_options(target: Any) -> None:
        target.add_argument("--asr", choices=["mlx-whisper", "none"], default="mlx-whisper", help=t("cli.asr"))
        add_subtitle_layout_options(target)

    def add_bgm_options(target: Any) -> None:
        target.add_argument("--bgm", default=None, help=t("cli.bgm"))
        target.add_argument("--bgm-dir", default=None, help=t("cli.bgm_dir"))
        target.add_argument("--bgm-fade", type=float, default=None, help=t("cli.bgm_fade"))
        target.add_argument("--no-bgm", action="store_true", help=t("cli.no_bgm"))
        target.add_argument("--bgm-volume", type=float, default=None, help=t("cli.bgm_volume"))
        target.add_argument("--bgm-duck-db", type=float, default=None, help=t("cli.bgm_duck_db"))
        target.add_argument("--ambient-volume", type=float, default=None, help=t("cli.ambient_volume"))
        target.add_argument("--ambient-duck-db", type=float, default=None, help=t("cli.ambient_duck_db"))
        target.add_argument("--target-lufs", type=float, default=None, help=t("cli.target_lufs"))

    # ---- run ----
    run = sub.add_parser("run", help=t("cli.run"))
    run.add_argument("video", type=Path, help=t("cli.video"))

    run_general = run.add_argument_group(t("cli.group_general"))
    run_general.add_argument("--config", help=t("cli.config"))
    run_general.add_argument("--workdir", type=Path, default=None, help=t("cli.workdir"))
    run_general.add_argument("--out", default=None, help=t("cli.out"))
    run_general.add_argument(
        "--content-language",
        choices=["zh", "en"],
        default=argparse.SUPPRESS,
        help=t("cli.content_language"),
    )
    run_general.add_argument("--height", type=int, default=None, help=t("cli.height"))
    run_general.add_argument("--fps", type=float, default=None, help=t("cli.fps"))
    run_general.add_argument("--crf", type=int, default=None, help=t("cli.render_crf"))
    run_general.add_argument("--no-burn", action="store_true", help=t("cli.no_burn"))
    _add_global_flags(run_general)

    run_script = run.add_argument_group(t("cli.group_script"))
    add_script_options(run_script)

    run_tts = run.add_argument_group(t("cli.group_tts"))
    run_tts.add_argument("--tts", choices=["kokoro", "qwen3", "say"], default=None, help=t("cli.tts_engine"))
    run_tts.add_argument("--voice", default=None, help=t("cli.voice"))
    run_tts.add_argument("--tts-language", default=None, help=t("cli.tts_language"))
    add_tts_options(run_tts)

    run_sub = run.add_argument_group(t("cli.group_subtitles"))
    run_sub.add_argument("--whisper-model", default=None, help=t("cli.whisper_model"))
    add_subtitle_options(run_sub)

    run_bgm = run.add_argument_group(t("cli.group_audio"))
    add_bgm_options(run_bgm)

    run_vision = run.add_argument_group(t("cli.group_vision"))
    run_vision.add_argument("--ollama-url", default=None, help=t("cli.ollama_url"))
    run_vision.add_argument("--vlm-model", default=None, help=t("cli.vlm_model"))
    run_vision.add_argument("--llm-model", default=None, help=t("cli.llm_model"))
    run_vision.add_argument("--vlm-workers", type=int, default=None, help=t("cli.vlm_workers"))
    run_vision.add_argument("--vlm-frames", type=int, default=None, help=t("cli.vlm_frames"))
    run_vision.add_argument("--vlm-dedup", type=float, default=None, help=t("cli.vlm_dedup"))
    run_vision.add_argument("--scene-threshold", type=float, default=None, help=t("cli.scene_threshold"))

    run_exec = run.add_argument_group(t("cli.group_execution"))
    run_exec.add_argument("--review-script", action="store_true", help=t("cli.review_script"))
    run_exec.add_argument("--only", default=None, help=t("cli.only"))
    run_exec.add_argument("--dry-run", action="store_true", help=t("cli.dry_run"))
    run_exec.add_argument("--force", action="store_true", help=t("cli.force"))
    run_exec.add_argument("--no-resume", action="store_true", help=t("cli.no_resume"))
    run_exec.add_argument("--no-qa", action="store_true", help=t("cli.no_qa"))
    run_exec.add_argument("--qa-strict", action="store_true", help=t("cli.qa_strict"))
    run_exec.add_argument("--json", action="store_true", help=t("cli.json"))

    # ---- resume ----
    res = sub.add_parser("resume", help=t("cli.resume"))
    res.add_argument("workdir", type=Path, help=t("cli.workdir_pos"))
    res.add_argument("--config", help=t("cli.config"))
    res.add_argument("--asr", choices=["mlx-whisper", "none"], default="mlx-whisper", help=t("cli.asr"))
    add_subtitle_layout_options(res)
    add_bgm_options(res)
    res.add_argument("--height", type=int, default=None, help=t("cli.height"))
    res.add_argument("--fps", type=float, default=None, help=t("cli.fps"))
    res.add_argument("--crf", type=int, default=None, help=t("cli.render_crf"))
    res.add_argument("--no-burn", action="store_true", help=t("cli.no_burn"))
    res.add_argument("--no-qa", action="store_true", help=t("cli.no_qa"))
    res.add_argument("--qa-strict", action="store_true", help=t("cli.qa_strict"))
    res.add_argument("--dry-run", action="store_true", help=t("cli.dry_run"))
    res.add_argument("--json", action="store_true", help=t("cli.json"))
    _add_global_flags(res)

    # ---- analyze ----
    analyze = sub.add_parser("analyze", help=t("cli.analyze"))
    add_common_models(analyze)
    analyze.add_argument("video", type=Path, help=t("cli.video"))
    analyze.add_argument("--workdir", type=Path, default=None, help=t("cli.workdir"))
    analyze.add_argument("--out", default=None, help=t("cli.out"))
    analyze.add_argument("--force", action="store_true", help=t("cli.force"))
    analyze.add_argument("--json", action="store_true", help=t("cli.json"))
    _add_global_flags(analyze)

    # ---- script ----
    script = sub.add_parser("script", help=t("cli.script"))
    add_common_models(script)
    add_script_options(script)
    script.add_argument("workdir", type=Path, help=t("cli.workdir_pos"))
    script.add_argument("--json", action="store_true", help=t("cli.json"))
    _add_global_flags(script)

    # ---- tts ----
    tts = sub.add_parser("tts", help=t("cli.tts"))
    add_common_models(tts)
    add_tts_options(tts, include_max_speedup=True)
    tts.add_argument("workdir", type=Path, help=t("cli.workdir_pos"))
    tts.add_argument("--json", action="store_true", help=t("cli.json"))
    _add_global_flags(tts)

    # ---- subtitle ----
    subtitle = sub.add_parser("subtitle", help=t("cli.subtitle"))
    add_common_models(subtitle)
    add_subtitle_options(subtitle)
    subtitle.add_argument("workdir", type=Path, help=t("cli.workdir_pos"))
    subtitle.add_argument("--json", action="store_true", help=t("cli.json"))
    _add_global_flags(subtitle)

    # ---- mix ----
    mix = sub.add_parser("mix", help=t("cli.mix"))
    mix.add_argument("--config", default=None, help=t("cli.config"))
    add_bgm_options(mix)
    mix.add_argument("workdir", type=Path, help=t("cli.workdir_pos"))
    mix.add_argument("--json", action="store_true", help=t("cli.json"))
    _add_global_flags(mix)

    # ---- render ----
    render = sub.add_parser("render", help=t("cli.render"))
    render.add_argument("workdir", type=Path, help=t("cli.workdir_pos"))
    render.add_argument("--config", default=None, help=t("cli.config"))
    render.add_argument("--no-burn", action="store_true", help=t("cli.no_burn"))
    render.add_argument("--height", type=int, default=None, help=t("cli.render_height"))
    render.add_argument("--fps", type=float, default=None, help=t("cli.render_fps"))
    render.add_argument("--crf", type=int, default=None, help=t("cli.render_crf"))
    render.add_argument("--subtitle-size", type=int, default=None, help=t("cli.render_subtitle_size"))
    render.add_argument(
        "--subtitle-preset",
        choices=["douyin", "reels", "shorts", "landscape"],
        default=None,
        help=t("cli.subtitle_preset"),
    )
    render.add_argument(
        "--subtitle-render",
        choices=["outline", "box", "none"],
        default=None,
        help=t("cli.subtitle_render"),
    )
    render.add_argument("--subtitle-outline", type=float, default=None, help=t("cli.subtitle_outline"))
    render.add_argument(
        "--subtitle-box-padding",
        type=float,
        default=None,
        help=t("cli.subtitle_box_padding"),
    )
    render.add_argument("--subtitle-margin-v", type=int, default=None, help=t("cli.render_margin_v"))
    render.add_argument("--subtitle-margin-left", type=int, default=None, help=t("cli.render_margin_left"))
    render.add_argument("--subtitle-margin-right", type=int, default=None, help=t("cli.render_margin_right"))
    render.add_argument("--json", action="store_true", help=t("cli.json"))
    _add_global_flags(render)

    # ---- export ----
    export = sub.add_parser("export", help=t("cli.export"))
    export.add_argument("workdir", type=Path, help=t("cli.workdir_pos"))
    export.add_argument("--format", choices=EDIT_FORMATS, default="mp4", help=t("cli.export_format"))
    export.add_argument(
        "--narration-mode",
        choices=NARRATION_MODES,
        default="track",
        help=t("cli.export_narration_mode"),
    )
    export.add_argument("--fps", type=float, help=t("cli.export_fps"))
    export.add_argument("--out", type=Path, help=t("cli.export_out"))
    export.add_argument("--force", action="store_true", help=t("cli.export_force"))
    export.add_argument("--json", action="store_true", help=t("cli.json"))
    _add_global_flags(export)

    # ---- import ----
    imp = sub.add_parser("import", help=t("cli.import_cmd"))
    imp.add_argument("workdir", type=Path, help=t("cli.workdir_pos"))
    imp.add_argument("--config", default=None, help=t("cli.config"))
    imp.add_argument("--script", type=Path, default=None, help=t("cli.import_script"))
    imp.add_argument("--subtitles", type=Path, default=None, help=t("cli.import_subtitles"))
    imp.add_argument("--narration", type=Path, default=None, help=t("cli.import_narration"))
    imp.add_argument(
        "--segment-audio",
        action="append",
        default=[],
        metavar="ID=PATH",
        help=t("cli.import_segment_audio"),
    )
    imp.add_argument("--bgm", type=Path, default=None, help=t("cli.import_bgm"))
    imp.add_argument("--cuts", type=Path, default=None, help=t("cli.import_cuts"))
    imp.add_argument("--timeline", type=Path, default=None, help=t("cli.import_timeline"))
    imp.add_argument("--resume", action="store_true", help=t("cli.import_resume"))
    imp.add_argument("--asr", choices=["mlx-whisper", "none"], default="mlx-whisper", help=t("cli.asr"))
    imp.add_argument("--no-qa", action="store_true", help=t("cli.no_qa"))
    imp.add_argument("--qa-strict", action="store_true", help=t("cli.qa_strict"))
    imp.add_argument("--dry-run", action="store_true", help=t("cli.dry_run"))
    imp.add_argument("--json", action="store_true", help=t("cli.json"))
    _add_global_flags(imp)

    # ---- tune ----
    tune = sub.add_parser("tune", help=t("cli.tune"))
    tune.add_argument("workdir", type=Path, help=t("cli.workdir_pos"))
    tune.add_argument("--config", default=None, help=t("cli.config"))

    tune_seg = tune.add_argument_group(t("cli.tune_group_segment"))
    tune_seg.add_argument("--segment", type=int, default=None, metavar="ID", help=t("cli.tune_segment"))
    tune_seg.add_argument("--text", default=None, help=t("cli.tune_text"))
    tune_seg.add_argument("--voice", default=None, help=t("cli.tune_voice"))
    tune_seg.add_argument("--instruct", default=None, help=t("cli.tune_instruct"))
    tune_seg.add_argument("--speed", type=float, default=None, help=t("cli.tune_speed"))
    tune_seg.add_argument("--start", default=None, metavar="SECONDS|auto", help=t("cli.tune_start"))
    tune_seg.add_argument("--gain-db", type=float, default=None, help=t("cli.tune_gain_db"))
    tune_seg_state = tune_seg.add_mutually_exclusive_group()
    tune_seg_state.add_argument("--enable", action="store_true", help=t("cli.tune_enable"))
    tune_seg_state.add_argument("--disable", action="store_true", help=t("cli.tune_disable"))
    tune_seg.add_argument("--clear-audio-override", action="store_true", help=t("cli.tune_clear_audio"))

    tune_cuts = tune.add_argument_group(t("cli.tune_group_cuts"))
    tune_cuts.add_argument("--cut", default=None, metavar="RANGES", help=t("cli.tune_cut"))
    tune_cuts.add_argument("--keep", default=None, metavar="RANGES", help=t("cli.tune_keep"))
    tune_cuts.add_argument("--reset-cuts", action="store_true", help=t("cli.tune_reset_cuts"))

    tune_exec = tune.add_argument_group(t("cli.tune_group_exec"))
    tune_exec.add_argument("--undo", action="store_true", help=t("cli.tune_undo"))
    tune_exec.add_argument("--apply", action="store_true", help=t("cli.tune_apply"))
    tune_exec.add_argument("--asr", choices=["mlx-whisper", "none"], default="mlx-whisper", help=t("cli.asr"))
    tune_exec.add_argument("--no-qa", action="store_true", help=t("cli.no_qa"))
    tune_exec.add_argument("--qa-strict", action="store_true", help=t("cli.qa_strict"))
    tune_exec.add_argument("--dry-run", action="store_true", help=t("cli.dry_run"))
    tune_exec.add_argument("--json", action="store_true", help=t("cli.json"))
    _add_global_flags(tune_exec)

    # ---- serve ----
    srv = sub.add_parser("serve", help=t("cli.serve"))
    srv.add_argument("workdir", nargs="?", type=Path, default=None, help=t("cli.serve_workdir"))
    srv.add_argument("--work-root", type=Path, default=None, help=t("cli.serve_work_root"))
    srv.add_argument("--host", default="127.0.0.1", help=t("cli.serve_host"))
    srv.add_argument("--port", type=int, default=8760, help=t("cli.serve_port"))
    srv.add_argument("--open", action="store_true", help=t("cli.serve_open"))
    srv.add_argument("--config", default=None, help=t("cli.config"))
    _add_global_flags(srv)

    # ---- qa ----
    qa = sub.add_parser("qa", help=t("cli.qa"))
    qa.add_argument("workdir", type=Path, help=t("cli.workdir_pos"))
    qa.add_argument("--config", default=None, help=t("cli.qa_config"))
    qa.add_argument("--strict", action="store_true", help=t("cli.strict"))
    qa.add_argument("--json", action="store_true", help=t("cli.json"))
    _add_global_flags(qa)

    # ---- status ----
    st = sub.add_parser("status", help=t("cli.status"))
    st.add_argument("workdir", type=Path, help=t("cli.workdir_pos"))
    st.add_argument("--json", action="store_true", help=t("cli.json"))
    _add_global_flags(st)

    # ---- doctor ----
    doc = sub.add_parser("doctor", help=t("cli.doctor"))
    doc.add_argument("--config", default=None, help=t("cli.config"))
    doc.add_argument(
        "--content-language",
        choices=["zh", "en"],
        default=argparse.SUPPRESS,
        help=t("cli.content_language"),
    )
    doc.add_argument("--tts", choices=["kokoro", "qwen3", "say"], default=None, help=t("cli.tts_engine"))
    doc.add_argument("--tts-language", default=None, help=t("cli.tts_language"))
    doc.add_argument("--voice", default=None, help=t("cli.voice"))
    doc.add_argument("--qwen3-model", default=None, help=t("cli.qwen3_model"))
    doc.add_argument("--tts-ref-audio", default=None, help=t("cli.tts_ref_audio"))
    doc.add_argument("--json", action="store_true", help=t("cli.json"))
    _add_global_flags(doc)

    # ---- voices ----
    voices = sub.add_parser("voices", help=t("cli.voices"))
    voices.add_argument(
        "--tts",
        choices=["kokoro", "qwen3", "all"],
        default="all",
        help=t("cli.voices_tts"),
    )
    voices.add_argument("--preview", default=None, metavar="VOICE", help=t("cli.voices_preview"))
    voices.add_argument("--text", default=None, help=t("cli.voices_text"))
    voices.add_argument("--out", type=Path, default=None, help=t("cli.voices_out"))
    voices.add_argument("--no-play", action="store_true", help=t("cli.voices_no_play"))
    voices.add_argument("--config", default=None, help=t("cli.config"))
    voices.add_argument("--tts-speed", type=float, default=None, help=t("cli.tts_speed"))
    voices.add_argument("--tts-instruct", default=None, help=t("cli.tts_instruct"))
    voices.add_argument("--json", action="store_true", help=t("cli.json"))
    _add_global_flags(voices)

    # ---- licenses ----
    licenses = sub.add_parser("licenses", help=t("cli.licenses"))
    licenses.add_argument("workdir", type=Path, help=t("cli.workdir_pos"))
    licenses.add_argument("--out", type=Path, default=None, help=t("cli.licenses_out"))
    licenses.add_argument("--json", action="store_true", help=t("cli.json"))
    _add_global_flags(licenses)

    # ---- cache ----
    cache = sub.add_parser("cache", help=t("cli.cache"))
    cache_sub = cache.add_subparsers(dest="cache_command", required=True)
    cache_info = cache_sub.add_parser("info", help=t("cli.cache_info"))
    cache_info.add_argument("--config", default=None, help=t("cli.config"))
    cache_info.add_argument("--json", action="store_true", help=t("cli.json"))
    _add_global_flags(cache_info)
    cache_clean = cache_sub.add_parser("clean", help=t("cli.cache_clean"))
    cache_clean.add_argument("--config", default=None, help=t("cli.config"))
    cache_clean.add_argument("--yes", action="store_true", help=t("cli.cache_clean_yes"))
    cache_clean.add_argument("--dry-run", action="store_true", help=t("cli.cache_clean_dry"))
    cache_clean.add_argument("--json", action="store_true", help=t("cli.json"))
    _add_global_flags(cache_clean)

    return parser


def _parse_segment_audio_args(raw_items: list[str]) -> list[tuple[int, Path]]:
    parsed: list[tuple[int, Path]] = []
    for raw in raw_items:
        if "=" not in raw:
            raise ValueError(t("error.import_segment_audio_format", value=raw))
        id_part, path_part = raw.split("=", 1)
        id_part = id_part.strip()
        path_part = path_part.strip()
        if not id_part.isdigit() or not path_part:
            raise ValueError(t("error.import_segment_audio_format", value=raw))
        parsed.append((int(id_part), Path(path_part)))
    return parsed


def _apply_pending_stages(
    workdir: Path,
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> tuple[dict[str, Any], int]:
    """Run all pending or stale stages after ``import --resume`` or ``tune --apply``."""
    report = workdir_status(workdir)
    pending = list(report.get("pending_stages") or [])
    if args.no_qa:
        pending = [stage for stage in pending if stage != "qa"]
    if not pending:
        return {
            "executed": [],
            "output": report["artifacts"].get("output"),
            "exit_code": 0,
        }, 0
    needs_source = any(stage in {"probe", "vlm", "mix", "render"} for stage in pending)
    base_args = argparse.Namespace(config=getattr(args, "config", None))
    video, settings, manifest = _workdir_context(
        workdir, base_args, parser, require_source=needs_source
    )
    pipeline = Pipeline(
        video,
        settings,
        workdir=workdir,
        resume=True,
        force=False,
        create_dir=True,
    )
    bgm_source = manifest.get("bgm_source") or manifest.get("settings", {}).get("bgm")
    if bgm_source:
        pipeline.bgm_source = Path(bgm_source)
    with _guard_json_stdout(args.json):
        out = pipeline.run(
            asr_engine=args.asr,
            burn=settings.burn_subtitles,
            qa=not args.no_qa,
            qa_strict=args.qa_strict,
            only=pending,
            print_qa=not args.json,
            force_selected=False,
        )
    summary = pipeline.run_summary(out, qa_strict=args.qa_strict)
    return summary, int(summary["exit_code"])


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    config_path = _scan_option(argv, "--config")
    config_language = None
    if config_path:
        try:
            config_language = Settings.load(Path(config_path)).ui_language
        except Exception:  # noqa: BLE001 - config errors are reported later
            config_language = None
    set_language(
        negotiate_language(
            explicit=_scan_option(argv, "--lang"),
            config_language=config_language,
        )
    )
    parser = build_parser()
    if not argv:
        parser.print_help()
        return 2
    args = parser.parse_args(argv)
    if getattr(args, "lang", None):
        set_language(args.lang)
    setup_logging(bool(getattr(args, "verbose", False)))

    if args.command == "doctor":
        settings = _settings(args)
        return doctor(settings, as_json=args.json)

    if args.command == "voices":
        from ..engines.tts import (
            KOKORO_EN_GB_VOICES,
            KOKORO_EN_US_VOICES,
            KOKORO_ZH_VOICES,
            QWEN3_EN_VOICES,
            QWEN3_ZH_VOICES,
            preview_voice,
            voice_metadata,
        )

        if args.preview:
            engine_override = None if args.tts == "all" else args.tts
            settings = Settings.load(
                Path(args.config) if args.config else None,
                {
                    "tts_engine": engine_override,
                    "tts_speed": args.tts_speed,
                    "tts_instruct": args.tts_instruct,
                },
            )
            safe_voice = re.sub(r"[^A-Za-z0-9_.-]+", "_", args.preview).strip("._") or "voice"
            out_wav = (
                args.out.expanduser().resolve()
                if args.out
                else Path(tempfile.gettempdir()) / f"reachreel_preview_{safe_voice}.wav"
            )
            try:
                with _guard_json_stdout(args.json):
                    duration = preview_voice(
                        args.preview,
                        settings,
                        text=args.text,
                        out_wav=out_wav,
                        explicit_engine=engine_override is not None,
                    )
            except (RuntimeError, ValueError, OSError) as exc:
                if args.json:
                    _emit_json({"ok": False, "voice": args.preview, "error": str(exc)})
                    return 1
                parser.exit(1, f"{parser.prog}: {exc}\n")
            played = False
            if not args.no_play and not args.json and shutil.which("afplay"):
                subprocess.run(["afplay", str(out_wav)], check=False)
                played = True
            if args.json:
                _emit_json(
                    {
                        "ok": True,
                        "voice": args.preview,
                        "output": str(out_wav),
                        "duration": round(duration, 2),
                        "played": played,
                    }
                )
            else:
                print(t("voices.preview_done", voice=args.preview, path=out_wav, seconds=duration))
            return 0

        payload: dict[str, Any] = {
            "style_presets": list(STYLE_PRESETS),
            "voices": {
                "kokoro": {
                    "zh-CN": KOKORO_ZH_VOICES,
                    "en-US": KOKORO_EN_US_VOICES,
                    "en-GB": KOKORO_EN_GB_VOICES,
                },
                "qwen3": {
                    "zh-CN": QWEN3_ZH_VOICES,
                    "en-US": QWEN3_EN_VOICES,
                    "en-GB": QWEN3_EN_VOICES,
                },
            },
        }
        if args.tts != "all":
            payload["voices"] = {args.tts: payload["voices"][args.tts]}
        details = {
            name: voice_metadata(name)
            for groups in payload["voices"].values()
            for names in groups.values()
            for name in names
        }
        payload["details"] = details
        if args.json:
            _emit_json(payload)
            return 0
        print(t("voices.style_presets", presets=", ".join(payload["style_presets"])))
        for engine_name, groups in payload["voices"].items():
            print(f"\n[{engine_name}]")
            for tag, names in groups.items():
                print(f"  {tag}:")
                for name in names:
                    meta = details.get(name, {})
                    gender = meta.get("gender", "")
                    tone = meta.get("tone", "")
                    note = f"({gender}, {tone})" if gender and tone else ""
                    print(f"    {name:<16} {note}".rstrip())
        print("\n" + t("voices.preview_hint"))
        return 0

    if args.command == "status":
        workdir = args.workdir.expanduser().resolve()
        if not workdir.is_dir():
            parser.error(t("error.workdir_not_found", workdir=workdir))
        try:
            report = workdir_status(workdir)
        except RuntimeError as exc:
            parser.exit(1, f"{parser.prog}: {exc}\n")
        if args.json:
            _emit_json(report)
        else:
            print_status(report)
        return 0

    if args.command == "resume":
        workdir = args.workdir.expanduser().resolve()
        if not workdir.is_dir():
            parser.error(t("error.workdir_not_found", workdir=workdir))
        report = workdir_status(workdir)
        pending = list(report.get("pending_stages") or [])
        if args.no_qa:
            pending = [stage for stage in pending if stage != "qa"]
        if not pending:
            if args.json:
                _emit_json(
                    {
                        "ok": True,
                        "exit_code": 0,
                        "complete": True,
                        "workdir": str(workdir),
                        "output": report["artifacts"].get("output"),
                        "stages_executed": [],
                        "next_stage": None,
                        "next_command": None,
                    }
                )
            else:
                print(t("resume.already_complete", workdir=workdir))
            return 0
        needs_source = any(stage in {"probe", "vlm", "mix", "render"} for stage in pending)
        video, settings, manifest = _workdir_context(
            workdir, args, parser, require_source=needs_source
        )
        pipeline = Pipeline(
            video,
            settings,
            workdir=workdir,
            resume=True,
            force=False,
            create_dir=not args.dry_run,
        )
        bgm_source = manifest.get("bgm_source") or manifest.get("settings", {}).get("bgm")
        if bgm_source:
            pipeline.bgm_source = Path(bgm_source)
        bgm_arg = Path(args.bgm).expanduser() if args.bgm else None
        if args.dry_run and args.json:
            with _guard_json_stdout(True):
                plan = pipeline.dry_run_plan(
                    pending,
                    qa=not args.no_qa,
                    qa_strict=args.qa_strict,
                    burn=settings.burn_subtitles,
                    explicit=False,
                    review=False,
                    asr_engine=args.asr,
                    bgm_file=bgm_arg,
                    no_bgm=args.no_bgm,
                )
            _emit_json(plan)
            return 0
        try:
            with _guard_json_stdout(args.json):
                out = pipeline.run(
                    asr_engine=args.asr,
                    bgm_file=bgm_arg,
                    no_bgm=args.no_bgm,
                    burn=settings.burn_subtitles,
                    qa=not args.no_qa,
                    qa_strict=args.qa_strict,
                    only=pending,
                    dry_run=args.dry_run,
                    print_qa=not args.json,
                    force_selected=False,
                )
        except RuntimeError as exc:
            if args.json:
                _emit_json(
                    {
                        "ok": False,
                        "exit_code": 1,
                        "workdir": str(pipeline.workdir),
                        "video": str(pipeline.video),
                        "error": str(exc),
                    }
                )
                return 1
            parser.exit(1, f"{parser.prog}: {exc}\n")
        if args.dry_run:
            return 0
        if args.json:
            summary = pipeline.run_summary(out, qa_strict=args.qa_strict)
            _emit_json(summary)
            return int(summary["exit_code"])
        print(out)
        if pipeline.qa_report is not None:
            return pipeline.qa_report.exit_code(strict=args.qa_strict)
        return 0

    if args.command == "run":
        settings, manifest = _source_context(args, parser)
        from ..pipeline.orchestrator import parse_only

        try:
            only = parse_only(args.only)
        except ValueError as exc:
            parser.error(str(exc))
        if args.review_script and only is not None and "script" not in only:
            parser.error(t("error.review_script_needs_script"))
        pipeline = Pipeline(
            args.video,
            settings,
            workdir=args.workdir,
            resume=not args.no_resume,
            force=args.force,
            create_dir=not args.dry_run,
        )
        bgm_source = manifest.get("bgm_source") or manifest.get("settings", {}).get("bgm")
        if bgm_source:
            pipeline.bgm_source = Path(bgm_source)
        bgm_arg = Path(args.bgm).expanduser() if args.bgm else None
        if args.dry_run and args.json:
            selected = (
                review_stages(only or list(STAGE_ORDER))
                if args.review_script
                else only
            )
            try:
                with _guard_json_stdout(True):
                    plan = pipeline.dry_run_plan(
                        selected,
                        qa=not args.no_qa,
                        qa_strict=args.qa_strict,
                        burn=settings.burn_subtitles,
                        explicit=only is not None,
                        review=args.review_script,
                        asr_engine=args.asr,
                        bgm_file=bgm_arg,
                        no_bgm=args.no_bgm,
                    )
            except RuntimeError as exc:
                _emit_json(
                    {
                        "ok": False,
                        "exit_code": 1,
                        "dry_run": True,
                        "workdir": str(pipeline.workdir),
                        "video": str(pipeline.video),
                        "error": str(exc),
                    }
                )
                return 1
            _emit_json(plan)
            return 0
        try:
            with _guard_json_stdout(args.json):
                out = pipeline.run(
                    asr_engine=args.asr,
                    bgm_file=bgm_arg,
                    no_bgm=args.no_bgm,
                    burn=settings.burn_subtitles,
                    qa=not args.no_qa,
                    qa_strict=args.qa_strict,
                    only=only,
                    dry_run=args.dry_run,
                    review_script=args.review_script,
                    print_qa=not args.json,
                )
        except RuntimeError as exc:
            if args.json:
                _emit_json(
                    {
                        "ok": False,
                        "exit_code": 1,
                        "dry_run": False,
                        "workdir": str(pipeline.workdir),
                        "video": str(pipeline.video),
                        "error": str(exc),
                    }
                )
                return 1
            parser.exit(1, f"{parser.prog}: {exc}\n")
        if args.dry_run:
            return 0
        if args.json:
            summary = pipeline.run_summary(
                out,
                review_script=args.review_script,
                qa_strict=args.qa_strict,
            )
            _emit_json(summary)
            return int(summary["exit_code"])
        print(out)
        if pipeline.qa_report is not None:
            return pipeline.qa_report.exit_code(strict=args.qa_strict)
        return 0

    if args.command == "analyze":
        settings, _ = _source_context(args, parser)
        pipeline = Pipeline(args.video, settings, workdir=args.workdir, force=args.force)
        try:
            with _guard_json_stdout(args.json):
                pipeline.stage_analyze()
        except RuntimeError as exc:
            if args.json:
                _emit_json({"ok": False, "workdir": str(pipeline.workdir), "error": str(exc)})
                return 1
            parser.exit(1, f"{parser.prog}: {exc}\n")
        if args.json:
            _emit_json(
                {
                    "ok": True,
                    "stage": "vlm",
                    "workdir": str(pipeline.workdir),
                    "output": str(pipeline.scenes_path),
                }
            )
        else:
            print(pipeline.scenes_path)
        return 0

    if args.command in {"script", "tts", "subtitle", "mix", "render"}:
        require_source = args.command in {"mix", "render"}
        video, settings, manifest = _workdir_context(
            args.workdir, args, parser, require_source=require_source
        )
        pipeline = Pipeline(video, settings, workdir=args.workdir, resume=True, force=False)
        bgm_source = manifest.get("bgm_source") or manifest.get("settings", {}).get("bgm")
        pipeline.bgm_source = Path(bgm_source) if bgm_source else None

        try:
            with _guard_json_stdout(args.json):
                if args.command == "script":
                    pipeline.stage_script(
                        refresh_analysis=any(
                            getattr(args, option) is not None
                            for option in (
                                "ollama_url", "vlm_model", "vlm_workers", "vlm_frames",
                                "vlm_dedup", "scene_threshold",
                            )
                        )
                    )
                    out_path = pipeline.script_path
                elif args.command == "tts":
                    pipeline.stage_tts()
                    out_path = pipeline.timeline_path
                elif args.command == "subtitle":
                    pipeline.stage_subtitle(asr_engine=args.asr)
                    out_path = pipeline.subtitles_path
                elif args.command == "mix":
                    pipeline.stage_mix(
                        bgm_file=Path(args.bgm).expanduser() if args.bgm else None,
                        no_bgm=args.no_bgm,
                    )
                    out_path = pipeline.mix_path
                else:
                    out_path = pipeline.run(only="render", burn=settings.burn_subtitles)
        except RuntimeError as exc:
            if args.json:
                _emit_json(
                    {
                        "ok": False,
                        "stage": args.command,
                        "workdir": str(pipeline.workdir),
                        "error": str(exc),
                    }
                )
                return 1
            parser.exit(1, f"{parser.prog}: {exc}\n")
        if args.json:
            payload = {
                "ok": True,
                "stage": args.command,
                "workdir": str(pipeline.workdir),
                "output": str(out_path),
            }
            if args.command == "render":
                payload["burned_subtitles"] = pipeline.burned_subtitles
            _emit_json(payload)
        elif args.command == "render":
            print(f"{out_path} (burned={pipeline.burned_subtitles})")
        else:
            print(out_path)
        return 0

    if args.command == "export":
        workdir = args.workdir.expanduser().resolve()
        if not (workdir / "manifest.json").resolve().is_relative_to(workdir):
            parser.error(t("error.export_artifact_escape", name="manifest.json"))
        video, settings, _ = _workdir_context(workdir, args, parser)
        pipeline = Pipeline(video, settings, workdir=workdir, create_dir=False)
        try:
            with _guard_json_stdout(args.json):
                out = pipeline.export_edit(
                    destination=args.out,
                    video_format=args.format,
                    narration_mode=args.narration_mode,
                    overwrite=args.force,
                )
        except (OSError, RuntimeError, ValueError) as exc:
            if args.json:
                _emit_json({"ok": False, "workdir": str(workdir), "error": str(exc)})
                return 1
            parser.error(str(exc))
        if args.json:
            _emit_json(
                {
                    "ok": True,
                    "workdir": str(workdir),
                    "output": str(out),
                    "format": args.format,
                    "narration_mode": args.narration_mode,
                }
            )
        else:
            print(out)
        return 0

    if args.command == "import":
        workdir = args.workdir.expanduser().resolve()
        if not workdir.is_dir():
            parser.error(t("error.workdir_not_found", workdir=workdir))
        base_args = argparse.Namespace(config=args.config)
        video, settings, manifest = _workdir_context(
            workdir, base_args, parser, require_source=False
        )
        pipeline = Pipeline(
            video, settings, workdir=workdir, resume=True, force=False, create_dir=False
        )
        bgm_source = manifest.get("bgm_source") or manifest.get("settings", {}).get("bgm")
        if bgm_source:
            pipeline.bgm_source = Path(bgm_source)
        try:
            seg_audio = _parse_segment_audio_args(args.segment_audio)
            with _guard_json_stdout(args.json):
                result = pipeline.import_artifacts(
                    script_file=args.script,
                    subtitles_file=args.subtitles,
                    narration_file=args.narration,
                    segment_audio=seg_audio,
                    bgm_file=args.bgm,
                    cuts_file=args.cuts,
                    timeline_file=args.timeline,
                    dry_run=args.dry_run,
                )
            exit_code = 0
            if args.resume and not args.dry_run:
                run_result, exit_code = _apply_pending_stages(workdir, args, parser)
                result["resume"] = run_result
                result["exit_code"] = exit_code
                result["ok"] = exit_code == 0
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            if args.json:
                _emit_json({"ok": False, "workdir": str(workdir), "error": str(exc)})
                return 1
            parser.exit(1, f"{parser.prog}: {exc}\n")
        if args.json:
            _emit_json(result)
            return exit_code
        if args.dry_run:
            print(t("import_cmd.dry_run_done", workdir=workdir, count=len(result["imported"])))
        else:
            print(t("import_cmd.done", workdir=workdir, count=len(result["imported"])))
        for warn in result.get("warnings") or []:
            print(f"  warning: {warn}")
        if result.get("affected_stages") and not args.resume:
            print(t("import_cmd.affected", stages=", ".join(result["affected_stages"])))
            if result.get("resume_command"):
                print(t("import_cmd.next_hint", command=result["resume_command"]))
        elif args.resume and not args.dry_run and result.get("resume", {}).get("output"):
            print(result["resume"]["output"])
        return exit_code

    if args.command == "tune":
        workdir = args.workdir.expanduser().resolve()
        if not workdir.is_dir():
            parser.error(t("error.workdir_not_found", workdir=workdir))
        base_args = argparse.Namespace(config=args.config)
        video, settings, manifest = _workdir_context(
            workdir, base_args, parser, require_source=False
        )
        pipeline = Pipeline(
            video, settings, workdir=workdir, resume=True, force=False, create_dir=False
        )
        bgm_source = manifest.get("bgm_source") or manifest.get("settings", {}).get("bgm")
        if bgm_source:
            pipeline.bgm_source = Path(bgm_source)
        try:
            start_val: float | str | None = None
            if args.start is not None:
                start_val = (
                    "auto"
                    if args.start.strip().lower() == "auto"
                    else parse_timestamp(args.start)
                )
            enabled_val: bool | None = (
                True if args.enable else False if args.disable else None
            )
            cut_ranges = parse_ranges(args.cut) if args.cut else None
            keep_ranges = parse_ranges(args.keep) if args.keep else None
            with _guard_json_stdout(args.json):
                result = pipeline.tune(
                    segment_id=args.segment,
                    text=args.text,
                    voice=args.voice,
                    instruct=args.instruct,
                    speed=args.speed,
                    start=start_val,
                    gain_db=args.gain_db,
                    enabled=enabled_val,
                    clear_audio_override=args.clear_audio_override,
                    cut_ranges=cut_ranges,
                    keep_ranges=keep_ranges,
                    reset_cuts=args.reset_cuts,
                    undo=args.undo,
                    dry_run=args.dry_run,
                )
            exit_code = 0
            if args.apply and not args.dry_run:
                run_result, exit_code = _apply_pending_stages(workdir, args, parser)
                result["apply"] = run_result
                result["exit_code"] = exit_code
                result["ok"] = exit_code == 0
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            if args.json:
                _emit_json({"ok": False, "workdir": str(workdir), "error": str(exc)})
                return 1
            parser.exit(1, f"{parser.prog}: {exc}\n")
        if args.json:
            _emit_json(result)
            return exit_code
        if args.undo:
            print(t("tune.undone", snapshot=result.get("snapshot", ""), workdir=workdir))
        elif args.dry_run:
            print(t("tune.dry_run_done", workdir=workdir, count=len(result.get("changes") or [])))
        else:
            print(t("tune.done", workdir=workdir, count=len(result.get("changes") or [])))
        for item in result.get("changes") or []:
            print(f"  - {item}")
        for warn in result.get("warnings") or []:
            print(f"  warning: {warn}")
        if result.get("affected_stages") and not args.apply:
            print(t("tune.affected", stages=", ".join(result["affected_stages"])))
            if result.get("resume_command"):
                print(t("tune.next_hint", command=result["resume_command"]))
        elif args.apply and not args.dry_run and result.get("apply", {}).get("output"):
            print(result["apply"]["output"])
        return exit_code

    if args.command == "serve":
        from ..server import serve_studio

        settings = Settings.load(Path(args.config) if args.config else None)
        pinned = args.workdir.expanduser().resolve() if args.workdir else None
        if pinned is not None and not pinned.is_dir():
            parser.error(t("error.workdir_not_found", workdir=pinned))
        if args.work_root is not None:
            work_root = args.work_root.expanduser().resolve()
        elif pinned is not None:
            work_root = pinned.parent
        else:
            work_root = Path(settings.work_root).expanduser().resolve()
        return serve_studio(
            work_root=work_root,
            pinned_workdir=pinned,
            config_path=Path(args.config) if args.config else None,
            host=args.host,
            port=args.port,
            open_browser=args.open,
            default_lang=current_language(),
        )

    if args.command == "cache":
        from ..core.cache import cache_root, cache_stats, clean_cache, format_bytes

        settings = Settings.load(Path(args.config) if args.config else None)
        if getattr(args, "cache_dir", None):
            settings.cache_dir = args.cache_dir
        root = cache_root(settings.cache_dir)
        stats = cache_stats(root)
        if args.cache_command == "info":
            if args.json:
                _emit_json({"dir": str(root), "files": stats["files"], "bytes": stats["bytes"]})
            else:
                print(
                    t(
                        "cache.info",
                        dir=root,
                        files=stats["files"],
                        size=format_bytes(stats["bytes"]),
                    )
                )
            return 0
        if not args.yes and not args.dry_run:
            parser.error(t("cache.confirm", dir=root))
        if args.dry_run:
            if args.json:
                _emit_json(
                    {
                        "dry_run": True,
                        "dir": str(root),
                        "files": stats["files"],
                        "bytes": stats["bytes"],
                    }
                )
            else:
                print(
                    t(
                        "cache.dry_run",
                        dir=root,
                        files=stats["files"],
                        size=format_bytes(stats["bytes"]),
                    )
                )
            return 0
        clean_cache(root)
        if args.json:
            _emit_json(
                {
                    "dry_run": False,
                    "cleaned": True,
                    "dir": str(root),
                    "files": stats["files"],
                    "bytes": stats["bytes"],
                }
            )
        else:
            print(
                t(
                    "cache.cleaned",
                    dir=root,
                    files=stats["files"],
                    size=format_bytes(stats["bytes"]),
                )
            )
        return 0

    if args.command == "licenses":
        from ..media.audio import bgm_license_markdown

        workdir = args.workdir.expanduser().resolve()
        if not workdir.is_dir():
            parser.error(t("error.workdir_not_found", workdir=workdir))
        manifest = read_json(workdir / "manifest.json", {}) or {}
        metadata = manifest.get("bgm_metadata")
        entries = [metadata] if isinstance(metadata, dict) and metadata else []
        out = args.out.expanduser() if args.out else workdir / "licenses.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(bgm_license_markdown(entries), encoding="utf-8")
        if args.json:
            _emit_json({"ok": True, "workdir": str(workdir), "output": str(out), "tracks": entries})
        else:
            print(out)
        return 0

    if args.command == "qa":
        from ..pipeline.qa import attach_to_manifest, print_report, run_qa

        workdir = args.workdir.expanduser().resolve()
        if not workdir.is_dir():
            parser.error(t("error.workdir_not_found", workdir=workdir))
        overrides = None
        if args.config:
            manifest = read_json(workdir / "manifest.json", {}) or {}
            overrides = Settings.load(
                Path(args.config), inherited=manifest.get("settings_snapshot")
            ).qa_values()
        with _guard_json_stdout(args.json):
            report = run_qa(workdir, strict=args.strict, settings_override=overrides)
            attach_to_manifest(workdir, report)
        if args.json:
            _emit_json(report.to_dict())
        else:
            print_report(report)
        return report.exit_code(strict=args.strict)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
