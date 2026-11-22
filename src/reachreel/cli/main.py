"""Command line interface."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..core.config import Settings
from ..core.util import read_json, setup_logging
from ..i18n import negotiate_language, set_language, supported_languages, t
from ..pipeline.orchestrator import Pipeline, doctor


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
        "scene_threshold": getattr(args, "scene_threshold", None),
        "voice": getattr(args, "voice", None),
        "tts_engine": getattr(args, "tts", None),
        "whisper_model": getattr(args, "whisper_model", None),
        "style": getattr(args, "style", None),
        "brief": getattr(args, "brief", None),
        "narration_density": getattr(args, "density", None),
        "max_speedup": getattr(args, "max_speedup", None),
        "output_height": getattr(args, "height", None),
        "output_fps": getattr(args, "fps", None),
        "subtitle_size": getattr(args, "subtitle_size", None),
        "subtitle_max_chars": getattr(args, "subtitle_chars", None),
        "subtitle_margin_v": getattr(args, "subtitle_margin_v", None),
        "subtitle_margin_left": getattr(args, "subtitle_margin_left", None),
        "subtitle_margin_right": getattr(args, "subtitle_margin_right", None),
        "bgm_file": getattr(args, "bgm", None),
        "bgm_dir": getattr(args, "bgm_dir", None),
        "work_root": getattr(args, "out", None),
        "burn_subtitles": not getattr(args, "no_burn", False),
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
    video = Path(manifest.get("video", {}).get("path", ""))
    if not video.exists():
        parser.error(t("error.workdir_no_video", workdir=workdir))

    config = Path(args.config) if getattr(args, "config", None) else None
    settings = Settings.load(config)

    # Reuse the full settings snapshot when available; fall back to the old partial fields.
    for key, value in (manifest.get("settings_snapshot") or {}).items():
        if hasattr(settings, key) and value is not None:
            setattr(settings, key, value)
    for key in ("vlm_model", "llm_model", "tts_engine", "voice", "whisper_model"):
        value = manifest.get("settings", {}).get(key)
        if value is not None:
            setattr(settings, key, value)
    for key, value in (manifest.get("render_settings") or {}).items():
        if hasattr(settings, key) and value is not None:
            setattr(settings, key, value)

    # CLI wins over config/manifest; env is applied by Settings.load before this point.
    for key, value in _overrides(args).items():
        if value is not None and hasattr(settings, key):
            setattr(settings, key, value)

    return video, settings, manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reachreel", description=t("cli.description"))
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

    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--config", help=t("cli.config"))
        p.add_argument("--ollama-url", default=None)
        p.add_argument("--vlm-model", default=None)
        p.add_argument("--llm-model", default=None)
        p.add_argument("--voice", default=None, help=t("cli.voice"))
        p.add_argument("--tts", choices=["kokoro", "say"], default=None)
        p.add_argument("--whisper-model", default=None)
        p.add_argument("--vlm-workers", type=int, default=None, help=t("cli.vlm_workers"))
        p.add_argument("--vlm-frames", type=int, default=None, help=t("cli.vlm_frames"))
        p.add_argument(
            "--scene-threshold",
            type=float,
            default=None,
            help=t("cli.scene_threshold"),
        )

    def script_options(p: argparse.ArgumentParser) -> None:
        p.add_argument("--style", default=None, help=t("cli.style"))
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
        p.add_argument("--no-bgm", action="store_true")

    run = sub.add_parser("run", help=t("cli.run"))
    common(run)
    script_options(run)
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

    qa = sub.add_parser("qa", help=t("cli.qa"))
    qa.add_argument("workdir", type=Path)
    qa.add_argument("--config", default=None, help=t("cli.qa_config"))
    qa.add_argument("--strict", action="store_true", help=t("cli.strict"))
    qa.add_argument("--json", action="store_true", help=t("cli.json"))

    doc = sub.add_parser("doctor", help=t("cli.doctor"))
    doc.add_argument("--config", default=None)

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
        settings = Settings.load(Path(args.config) if args.config else None)
        return doctor(settings)

    if args.command == "run":
        if not args.video.exists():
            parser.error(t("error.video_not_found", video=args.video))
        from ..pipeline.orchestrator import parse_only

        try:
            only = parse_only(args.only)
        except ValueError as exc:
            parser.error(str(exc))
        video = args.video
        manifest: dict = {}
        # Reuse the settings snapshot when running into an existing workdir,
        # so `run --only ...` behaves like the stage commands.
        if args.workdir and (assumed := read_json(args.workdir / "manifest.json", {})):
            if assumed.get("video", {}).get("path"):
                video, settings, manifest = _workdir_context(args.workdir, args, parser)
                if video.resolve() != args.video.resolve():
                    parser.error(
                        t(
                            "error.workdir_other_video",
                            video=video,
                            current=args.video,
                        )
                    )
            else:
                settings = _settings(args)
        else:
            settings = _settings(args)
        pipeline = Pipeline(
            video,
            settings,
            workdir=args.workdir,
            resume=not args.no_resume,
            force=args.force,
            create_dir=not args.dry_run,
        )
        bgm_source = manifest.get("bgm_source") or manifest.get("settings", {}).get("bgm")
        if bgm_source:
            pipeline.bgm_source = Path(bgm_source)
        out = pipeline.run(
            asr_engine=args.asr,
            bgm_file=Path(args.bgm).expanduser() if args.bgm else None,
            no_bgm=args.no_bgm,
            burn=not args.no_burn,
            qa=not args.no_qa,
            qa_strict=args.qa_strict,
            only=only,
            dry_run=args.dry_run,
        )
        if args.dry_run:
            return 0
        print(out)
        if pipeline.qa_report is not None:
            return pipeline.qa_report.exit_code(strict=args.qa_strict)
        return 0

    if args.command == "analyze":
        if not args.video.exists():
            parser.error(t("error.video_not_found", video=args.video))
        settings = _settings(args)
        pipeline = Pipeline(args.video, settings, workdir=args.workdir, force=args.force)
        pipeline.prepare_scenes()
        pipeline.understand()
        print(pipeline.scenes_path)
        return 0

    if args.command in {"script", "tts", "subtitle", "mix"}:
        video, settings, manifest = _workdir_context(args.workdir, args, parser)
        pipeline = Pipeline(video, settings, workdir=args.workdir, resume=True, force=False)
        bgm_source = manifest.get("bgm_source") or manifest.get("settings", {}).get("bgm")
        pipeline.bgm_source = Path(bgm_source) if bgm_source else None

        if args.command == "script":
            pipeline.stage_script()
            print(pipeline.script_path)
        elif args.command == "tts":
            pipeline.stage_tts()
            print(pipeline.timeline_path)
        elif args.command == "subtitle":
            pipeline.stage_subtitle(asr_engine=args.asr)
            print(pipeline.subtitles_path)
        else:
            pipeline.stage_mix(
                bgm_file=Path(args.bgm).expanduser() if args.bgm else None,
                no_bgm=args.no_bgm,
            )
            print(pipeline.mix_path)
        return 0

    if args.command == "render":
        workdir = args.workdir.resolve()
        for name in ("mix.wav", "subtitles.srt"):
            if not (workdir / name).exists():
                parser.error(t("error.missing_artifact", name=name))
        video, settings, _manifest = _workdir_context(workdir, args, parser)
        from ..media.render import render

        if args.height is not None:
            settings.output_height = args.height
        if args.fps is not None:
            settings.output_fps = args.fps
        if args.crf is not None:
            settings.crf = args.crf
        out = workdir / "final.mp4"
        burned = render(
            video=video,
            audio=workdir / "mix.wav",
            subtitles=workdir / "subtitles.srt",
            out=out,
            settings=settings,
            burn=not args.no_burn,
        )
        # Keep QA / downstream tooling in sync with the actual render settings.
        from ..core.util import atomic_write_json

        manifest = read_json(workdir / "manifest.json", {}) or {}
        manifest.setdefault("settings", {})["burned_subtitles"] = burned
        manifest.setdefault("render_settings", {}).update(
            {
                "output_height": settings.output_height,
                "output_fps": settings.output_fps,
                "crf": settings.crf,
                "subtitle_font": settings.subtitle_font,
                "subtitle_size": settings.subtitle_size,
                "subtitle_margin_v": settings.subtitle_margin_v,
                "subtitle_margin_left": settings.subtitle_margin_left,
                "subtitle_margin_right": settings.subtitle_margin_right,
                "subtitle_size_unit": "px",
                "subtitle_layout": "ass",
            }
        )
        atomic_write_json(workdir / "manifest.json", manifest)
        print(f"{out} (burned={burned})")
        return 0

    if args.command == "qa":
        from ..pipeline.qa import attach_to_manifest, print_report, run_qa

        overrides = None
        if args.config:
            from ..core.config import settings_to_dict

            overrides = settings_to_dict(Settings.load(Path(args.config)))
        report = run_qa(args.workdir.resolve(), strict=args.strict, settings_override=overrides)
        attach_to_manifest(args.workdir.resolve(), report)
        if args.json:
            print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        else:
            print_report(report)
        return report.exit_code(strict=args.strict)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
