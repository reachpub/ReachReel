"""Command line interface."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import __version__
from ..core.config import STYLE_PRESETS, Settings
from ..core.util import read_json, setup_logging
from ..i18n import negotiate_language, set_language, supported_languages, t
from ..pipeline.orchestrator import EDIT_FORMATS, MANIFEST_SCHEMA_VERSION, Pipeline, doctor


def _scan_option(argv: list[str], name: str) -> str | None:
    """Find ``--name value`` or ``--name=value`` before argparse runs."""
    for index, argument in enumerate(argv):
        if argument == name and index + 1 < len(argv):
            return argv[index + 1]
        if argument.startswith(f"{name}="):
            return argument.split("=", 1)[1]
    return None


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
) -> tuple[Path, Settings, dict]:
    workdir = workdir.resolve()
    manifest = read_json(workdir / "manifest.json", {}) or {}
    source = manifest
    if not manifest:
        # Older analysis-only jobs saved their source in scenes.json.
        source = read_json(workdir / "scenes.json", {}) or {}
    video = Path(source.get("video", {}).get("path", ""))
    if not video.is_file():
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reachreel", description=t("cli.description"))
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

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--config", help=t("cli.config"))
        p.add_argument(
            "--content-language",
            choices=["zh", "en"],
            default=argparse.SUPPRESS,
            help=t("cli.content_language"),
        )
        p.add_argument("--ollama-url", default=None)
        p.add_argument("--vlm-model", default=None)
        p.add_argument("--llm-model", default=None)
        p.add_argument("--voice", default=None, help=t("cli.voice"))
        p.add_argument("--tts", choices=["kokoro", "qwen3", "say"], default=None)
        p.add_argument("--tts-language", default=None, help=t("cli.tts_language"))
        p.add_argument("--whisper-model", default=None)
        p.add_argument("--vlm-workers", type=int, default=None, help=t("cli.vlm_workers"))
        p.add_argument("--vlm-frames", type=int, default=None, help=t("cli.vlm_frames"))
        p.add_argument(
            "--vlm-dedup", type=float, default=None, help=t("cli.vlm_dedup")
        )
        p.add_argument(
            "--scene-threshold",
            type=float,
            default=None,
            help=t("cli.scene_threshold"),
        )

    def tts_options(p: argparse.ArgumentParser) -> None:
        p.add_argument("--qwen3-model", default=None, help=t("cli.qwen3_model"))
        p.add_argument("--tts-speed", type=float, default=None, help=t("cli.tts_speed"))
        p.add_argument("--tts-instruct", default=None, help=t("cli.tts_instruct"))
        p.add_argument("--tts-ref-audio", default=None, help=t("cli.tts_ref_audio"))
        p.add_argument("--tts-ref-text", default=None, help=t("cli.tts_ref_text"))
        p.add_argument("--tts-temperature", type=float, default=None, help=t("cli.tts_temperature"))
        p.add_argument("--tts-top-p", type=float, default=None, help=t("cli.tts_top_p"))
        p.add_argument("--tts-top-k", type=int, default=None, help=t("cli.tts_top_k"))
        p.add_argument(
            "--tts-repetition-penalty",
            type=float,
            default=None,
            help=t("cli.tts_repetition_penalty"),
        )
        p.add_argument("--tts-seed", type=int, default=None, help=t("cli.tts_seed"))
        p.add_argument("--tts-chunk-pause", type=float, default=None, help=t("cli.tts_chunk_pause"))
        p.add_argument("--tts-pitch", type=float, default=None, help=t("cli.tts_pitch"))
        p.add_argument("--tts-volume", type=float, default=None, help=t("cli.tts_volume"))

    def script_options(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--style-preset",
            choices=list(STYLE_PRESETS),
            default=None,
            help=t("cli.style_preset"),
        )
        p.add_argument("--style", default=None, help=t("cli.style"))
        p.add_argument(
            "--oral-polish",
            action=argparse.BooleanOptionalAction,
            default=None,
            help=t("cli.oral_polish"),
        )
        p.add_argument("--brief", default=None, help=t("cli.brief"))
        p.add_argument(
            "--density",
            type=float,
            default=None,
            help=t("cli.density"),
        )
        p.add_argument(
            "--max-speedup",
            type=float,
            default=None,
            help=t("cli.max_speedup"),
        )

    def subtitle_layout_options(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--subtitle-size",
            type=int,
            default=None,
            help=t("cli.subtitle_size"),
        )
        p.add_argument(
            "--subtitle-chars",
            type=int,
            default=None,
            help=t("cli.subtitle_chars"),
        )
        p.add_argument(
            "--subtitle-preset",
            choices=["douyin", "reels", "shorts", "landscape"],
            default=None,
            help=t("cli.subtitle_preset"),
        )
        p.add_argument(
            "--subtitle-render",
            choices=["outline", "box", "none"],
            default=None,
            help=t("cli.subtitle_render"),
        )
        p.add_argument(
            "--subtitle-outline",
            type=float,
            default=None,
            help=t("cli.subtitle_outline"),
        )
        p.add_argument(
            "--subtitle-box-padding",
            type=float,
            default=None,
            help=t("cli.subtitle_box_padding"),
        )
        p.add_argument("--subtitle-margin-v", type=int, default=None, help=t("cli.subtitle_margin_v"))
        p.add_argument(
            "--subtitle-margin-left",
            type=int,
            default=None,
            help=t("cli.subtitle_margin_left"),
        )
        p.add_argument(
            "--subtitle-margin-right",
            type=int,
            default=None,
            help=t("cli.subtitle_margin_right"),
        )

    def subtitle_options(p: argparse.ArgumentParser) -> None:
        p.add_argument("--asr", choices=["mlx-whisper", "none"], default="mlx-whisper")
        subtitle_layout_options(p)

    def bgm_options(p: argparse.ArgumentParser) -> None:
        p.add_argument("--bgm", default=None, help=t("cli.bgm"))
        p.add_argument("--bgm-dir", default=None, help=t("cli.bgm_dir"))
        p.add_argument("--bgm-fade", type=float, default=None, help=t("cli.bgm_fade"))
        p.add_argument("--no-bgm", action="store_true")

    run = sub.add_parser("run", help=t("cli.run"))
    common(run)
    script_options(run)
    tts_options(run)
    subtitle_options(run)
    bgm_options(run)
    run.add_argument("video", type=Path)
    run.add_argument("--workdir", type=Path, default=None)
    run.add_argument("--out", default=None, help=t("cli.out"))
    run.add_argument(
        "--height",
        type=int,
        default=None,
        help=t("cli.height"),
    )
    run.add_argument(
        "--fps",
        type=float,
        default=None,
        help=t("cli.fps"),
    )
    run.add_argument("--no-burn", action="store_true", help=t("cli.no_burn"))
    run.add_argument("--force", action="store_true", help=t("cli.force"))
    run.add_argument("--no-resume", action="store_true", help=t("cli.no_resume"))
    run.add_argument("--no-qa", action="store_true", help=t("cli.no_qa"))
    run.add_argument("--qa-strict", action="store_true", help=t("cli.qa_strict"))
    run.add_argument(
        "--only",
        default=None,
        help=t("cli.only"),
    )
    run.add_argument("--dry-run", action="store_true", help=t("cli.dry_run"))
    run.add_argument(
        "--review-script",
        action="store_true",
        help=t("cli.review_script"),
    )

    analyze = sub.add_parser("analyze", help=t("cli.analyze"))
    common(analyze)
    analyze.add_argument("video", type=Path)
    analyze.add_argument("--workdir", type=Path, default=None)
    analyze.add_argument("--out", default=None)
    analyze.add_argument("--force", action="store_true")

    script = sub.add_parser("script", help=t("cli.script"))
    common(script)
    script_options(script)
    script.add_argument("workdir", type=Path)

    tts = sub.add_parser("tts", help=t("cli.tts"))
    common(tts)
    tts_options(tts)
    tts.add_argument("workdir", type=Path)

    subtitle = sub.add_parser("subtitle", help=t("cli.subtitle"))
    common(subtitle)
    subtitle_options(subtitle)
    subtitle.add_argument("workdir", type=Path)

    mix = sub.add_parser("mix", help=t("cli.mix"))
    mix.add_argument("--config", default=None)
    bgm_options(mix)
    mix.add_argument("workdir", type=Path)

    render = sub.add_parser("render", help=t("cli.render"))
    render.add_argument("workdir", type=Path)
    render.add_argument("--config", default=None)
    render.add_argument("--no-burn", action="store_true")
    render.add_argument("--height", type=int, default=None, help=t("cli.render_height"))
    render.add_argument("--fps", type=float, default=None, help=t("cli.render_fps"))
    render.add_argument("--crf", type=int, default=None, help=t("cli.render_crf"))
    render.add_argument(
        "--subtitle-size",
        type=int,
        default=None,
        help=t("cli.render_subtitle_size"),
    )
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
    render.add_argument(
        "--subtitle-outline",
        type=float,
        default=None,
        help=t("cli.subtitle_outline"),
    )
    render.add_argument(
        "--subtitle-box-padding",
        type=float,
        default=None,
        help=t("cli.subtitle_box_padding"),
    )
    render.add_argument(
        "--subtitle-margin-v",
        type=int,
        default=None,
        help=t("cli.render_margin_v"),
    )
    render.add_argument(
        "--subtitle-margin-left",
        type=int,
        default=None,
        help=t("cli.render_margin_left"),
    )
    render.add_argument(
        "--subtitle-margin-right",
        type=int,
        default=None,
        help=t("cli.render_margin_right"),
    )

    export = sub.add_parser("export", help=t("cli.export"))
    export.add_argument("workdir", type=Path)
    export.add_argument("--format", choices=EDIT_FORMATS, default="mp4", help=t("cli.export_format"))
    export.add_argument("--fps", type=float, help=t("cli.export_fps"))
    export.add_argument("--out", type=Path, help=t("cli.export_out"))

    qa = sub.add_parser("qa", help=t("cli.qa"))
    qa.add_argument("workdir", type=Path)
    qa.add_argument("--config", default=None, help=t("cli.qa_config"))
    qa.add_argument("--strict", action="store_true", help=t("cli.strict"))
    qa.add_argument("--json", action="store_true", help=t("cli.json"))

    doc = sub.add_parser("doctor", help=t("cli.doctor"))
    doc.add_argument("--config", default=None)
    doc.add_argument(
        "--content-language",
        choices=["zh", "en"],
        default=argparse.SUPPRESS,
        help=t("cli.content_language"),
    )
    doc.add_argument("--tts", choices=["kokoro", "qwen3", "say"], default=None)
    doc.add_argument("--tts-language", default=None, help=t("cli.tts_language"))
    doc.add_argument("--voice", default=None, help=t("cli.voice"))
    doc.add_argument("--qwen3-model", default=None, help=t("cli.qwen3_model"))
    doc.add_argument("--tts-ref-audio", default=None, help=t("cli.tts_ref_audio"))
    doc.add_argument("--json", action="store_true", help=t("cli.json"))

    voices = sub.add_parser("voices", help=t("cli.voices"))
    voices.add_argument(
        "--tts",
        choices=["kokoro", "qwen3", "all"],
        default="all",
        help=t("cli.voices_tts"),
    )
    voices.add_argument("--json", action="store_true", help=t("cli.json"))

    licenses = sub.add_parser("licenses", help=t("cli.licenses"))
    licenses.add_argument("workdir", type=Path)
    licenses.add_argument("--out", type=Path, default=None, help=t("cli.licenses_out"))

    cache = sub.add_parser("cache", help=t("cli.cache"))
    cache_sub = cache.add_subparsers(dest="cache_command", required=True)
    cache_info = cache_sub.add_parser("info", help=t("cli.cache_info"))
    cache_info.add_argument("--config", default=None)
    cache_clean = cache_sub.add_parser("clean", help=t("cli.cache_clean"))
    cache_clean.add_argument("--config", default=None)
    cache_clean.add_argument("--yes", action="store_true", help=t("cli.cache_clean_yes"))
    cache_clean.add_argument("--dry-run", action="store_true", help=t("cli.cache_clean_dry"))

    return parser


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
    args = parser.parse_args(argv)
    if args.lang:
        set_language(args.lang)
    setup_logging(args.verbose)

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
        )

        payload = {
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
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        print(t("voices.style_presets", presets=", ".join(payload["style_presets"])))
        for engine_name, groups in payload["voices"].items():
            print(f"\n[{engine_name}]")
            for tag, names in groups.items():
                print(f"  {tag}: {', '.join(names)}")
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
        try:
            out = pipeline.run(
                asr_engine=args.asr,
                bgm_file=Path(args.bgm).expanduser() if args.bgm else None,
                no_bgm=args.no_bgm,
                burn=settings.burn_subtitles,
                qa=not args.no_qa,
                qa_strict=args.qa_strict,
                only=only,
                dry_run=args.dry_run,
                review_script=args.review_script,
            )
        except RuntimeError as exc:
            parser.exit(1, f"{parser.prog}: {exc}\n")
        if args.dry_run:
            return 0
        print(out)
        if pipeline.qa_report is not None:
            return pipeline.qa_report.exit_code(strict=args.qa_strict)
        return 0

    if args.command == "analyze":
        settings, _ = _source_context(args, parser)
        pipeline = Pipeline(args.video, settings, workdir=args.workdir, force=args.force)
        try:
            pipeline.stage_analyze()
        except RuntimeError as exc:
            parser.exit(1, f"{parser.prog}: {exc}\n")
        print(pipeline.scenes_path)
        return 0

    if args.command in {"script", "tts", "subtitle", "mix", "render"}:
        video, settings, manifest = _workdir_context(args.workdir, args, parser)
        pipeline = Pipeline(video, settings, workdir=args.workdir, resume=True, force=False)
        bgm_source = manifest.get("bgm_source") or manifest.get("settings", {}).get("bgm")
        pipeline.bgm_source = Path(bgm_source) if bgm_source else None

        try:
            if args.command == "script":
                pipeline.stage_script(refresh_analysis=any(
                    getattr(args, option) is not None for option in (
                        "ollama_url", "vlm_model", "vlm_workers", "vlm_frames",
                        "vlm_dedup", "scene_threshold",
                    )
                ))
                print(pipeline.script_path)
            elif args.command == "tts":
                pipeline.stage_tts()
                print(pipeline.timeline_path)
            elif args.command == "subtitle":
                pipeline.stage_subtitle(asr_engine=args.asr)
                print(pipeline.subtitles_path)
            elif args.command == "mix":
                pipeline.stage_mix(
                    bgm_file=Path(args.bgm).expanduser() if args.bgm else None,
                    no_bgm=args.no_bgm,
                )
                print(pipeline.mix_path)
            else:
                out = pipeline.run(only="render", burn=settings.burn_subtitles)
                print(f"{out} (burned={pipeline.burned_subtitles})")
        except RuntimeError as exc:
            parser.exit(1, f"{parser.prog}: {exc}\n")
        return 0

    if args.command == "export":
        workdir = args.workdir.expanduser().resolve()
        if not (workdir / "manifest.json").resolve().is_relative_to(workdir):
            parser.error(t("error.export_artifact_escape", name="manifest.json"))
        video, settings, _ = _workdir_context(workdir, args, parser)
        pipeline = Pipeline(video, settings, workdir=workdir, create_dir=False)
        try:
            out = pipeline.export_edit(destination=args.out, video_format=args.format)
        except (OSError, RuntimeError, ValueError) as exc:
            parser.error(str(exc))
        print(out)
        return 0

    if args.command == "cache":
        from ..core.cache import cache_root, cache_stats, clean_cache, format_bytes

        settings = Settings.load(Path(args.config) if args.config else None)
        if getattr(args, "cache_dir", None):
            settings.cache_dir = args.cache_dir
        root = cache_root(settings.cache_dir)
        stats = cache_stats(root)
        if args.cache_command == "info":
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

        workdir = args.workdir.resolve()
        manifest = read_json(workdir / "manifest.json", {}) or {}
        metadata = manifest.get("bgm_metadata")
        entries = [metadata] if isinstance(metadata, dict) and metadata else []
        out = args.out.expanduser() if args.out else workdir / "licenses.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(bgm_license_markdown(entries), encoding="utf-8")
        print(out)
        return 0

    if args.command == "qa":
        from ..pipeline.qa import attach_to_manifest, print_report, run_qa

        workdir = args.workdir.resolve()
        overrides = None
        if args.config:
            manifest = read_json(workdir / "manifest.json", {}) or {}
            overrides = Settings.load(
                Path(args.config), inherited=manifest.get("settings_snapshot")
            ).qa_values()
        report = run_qa(workdir, strict=args.strict, settings_override=overrides)
        attach_to_manifest(workdir, report)
        if args.json:
            print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        else:
            print_report(report)
        return report.exit_code(strict=args.strict)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
