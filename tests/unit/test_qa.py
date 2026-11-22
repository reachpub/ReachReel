import json
import tempfile
import unittest
from pathlib import Path

from reachreel.cli.main import build_parser, main
from reachreel.core.config import Settings, settings_to_dict
from reachreel.pipeline.orchestrator import (
    STAGE_ORDER,
    _flag_stage,
    parse_only,
    review_stages,
)
from reachreel.pipeline.qa import (
    Check,
    QAReport,
    QAThresholds,
    _parse_astats,
    compare_narration_subtitles,
    parse_srt,
    subtitle_margin_status,
)


class QATests(unittest.TestCase):
    def test_parse_srt(self):
        content = (
            "1\n00:00:01,000 --> 00:00:03,500\n第一行\n第二行\n\n"
            "2\n00:00:04,000 --> 00:00:06,000\n下一句\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.srt"
            path.write_text(content, encoding="utf-8")
            cues = parse_srt(path)
        self.assertEqual(len(cues), 2)
        self.assertAlmostEqual(cues[0]["start"], 1.0)
        self.assertAlmostEqual(cues[0]["end"], 3.5)
        self.assertEqual(cues[0]["lines"], ["第一行", "第二行"])
        self.assertAlmostEqual(cues[1]["start"], 4.0)

    def test_report_exit_code(self):
        report = QAReport(
            workdir="/tmp",
            output="/tmp/final.mp4",
            checks=[Check("x", "pass", "ok")],
            summary={"pass": 1, "warn": 0, "fail": 0, "status": "pass", "exit_code": 0},
        )
        self.assertEqual(report.exit_code(), 0)
        self.assertEqual(report.exit_code(strict=True), 0)

        warn = QAReport(
            workdir="/tmp",
            output="/tmp/final.mp4",
            checks=[Check("x", "warn", "hmm")],
            summary={"pass": 0, "warn": 1, "fail": 0, "status": "warn", "exit_code": 0},
        )
        self.assertEqual(warn.exit_code(), 0)
        self.assertEqual(warn.exit_code(strict=True), 1)

        fail = QAReport(
            workdir="/tmp",
            output="/tmp/final.mp4",
            checks=[Check("x", "fail", "bad")],
            summary={"pass": 0, "warn": 0, "fail": 1, "status": "fail", "exit_code": 1},
        )
        self.assertEqual(fail.exit_code(), 1)

    def test_settings_snapshot_is_json_serializable(self):
        data = settings_to_dict(Settings())
        json.dumps(data)
        self.assertNotIn("bgm_root_resolved", data)
        self.assertEqual(data["vlm_model"], "qwen3-vl:8b-instruct")

    def test_stage_commands_are_registered(self):
        parser = build_parser()
        for command in ("script", "tts", "subtitle", "mix", "render", "qa", "licenses"):
            args = parser.parse_args([command, "/tmp/workdir"])
            self.assertEqual(args.command, command)
        args = parser.parse_args(["run", "/tmp/video.mp4", "--no-qa", "--qa-strict"])
        self.assertTrue(args.no_qa)
        self.assertTrue(args.qa_strict)

    def test_licenses_command_writes_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = {
                "bgm_metadata": {
                    "file": "calm.mp3",
                    "title": "Calm",
                    "license": "CC0-1.0",
                }
            }
            (Path(tmp) / "manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8")
            code = main(["licenses", tmp])
            self.assertEqual(code, 0)
            text = (Path(tmp) / "licenses.md").read_text(encoding="utf-8")
        self.assertIn("Calm", text)
        self.assertIn("CC0-1.0", text)

    def test_cache_commands_are_registered(self):
        parser = build_parser()
        self.assertEqual(parser.parse_args(["cache", "info"]).cache_command, "info")
        clean = parser.parse_args(["cache", "clean", "--dry-run"])
        self.assertEqual(clean.cache_command, "clean")
        self.assertTrue(clean.dry_run)
        args = parser.parse_args(["--no-cache", "run", "/tmp/video.mp4"])
        self.assertTrue(args.no_cache)
        preset = parser.parse_args(
            ["run", "/tmp/video.mp4", "--subtitle-preset", "douyin"]
        )
        self.assertEqual(preset.subtitle_preset, "douyin")
        self.assertTrue(parser.parse_args(["doctor", "--json"]).json)
        review = parser.parse_args(["run", "/tmp/video.mp4", "--review-script"])
        self.assertTrue(review.review_script)
        mix = parser.parse_args(["mix", "/tmp/workdir", "--bgm-fade", "3"])
        self.assertEqual(mix.bgm_fade, 3.0)

    def test_review_stages_stops_at_script(self):
        self.assertEqual(review_stages(STAGE_ORDER), ["probe", "vlm", "script"])
        self.assertEqual(review_stages(["probe", "script", "tts"]), ["probe", "script"])
        with self.assertRaises(ValueError):
            review_stages(["probe", "tts"])

    def test_subtitle_margin_status_uses_preset(self):
        status, details = subtitle_margin_status(
            {"left": 100, "right": 100, "top": 0, "bottom": 1500},
            width=1080,
            height=1920,
            settings={"subtitle_preset": "douyin"},
            thresholds=QAThresholds(),
        )
        self.assertEqual(details["limits"], {"left": 80, "right": 80, "bottom": 280})
        self.assertEqual(details["preset"], "douyin")
        self.assertEqual(status, "pass")

    def test_quality_flag_stage_mapping(self):
        self.assertEqual(_flag_stage("vlm.scene_failed"), "vlm")
        self.assertEqual(_flag_stage("tts.segment_failed"), "tts")
        self.assertEqual(_flag_stage("asr.fallback"), "subtitles")
        self.assertEqual(_flag_stage("qa.warn"), "qa")
        self.assertEqual(_flag_stage("unknown"), "")

    def test_parse_only(self):
        self.assertEqual(parse_only("vlm,script"), ["vlm", "script"])
        self.assertEqual(parse_only("script, vlm"), ["vlm", "script"])
        self.assertEqual(parse_only("subtitle"), ["subtitles"])
        self.assertEqual(parse_only("tts,timeline"), ["tts"])
        self.assertEqual(parse_only("all"), STAGE_ORDER)
        self.assertIsNone(parse_only(None))
        with self.assertRaises(ValueError):
            parse_only("bogus")

    def test_run_dry_run_and_only_flags(self):
        args = build_parser().parse_args(
            ["run", "/tmp/video.mp4", "--dry-run", "--only", "vlm,script"]
        )
        self.assertTrue(args.dry_run)
        self.assertEqual(args.only, "vlm,script")

    def test_qa_config_table(self):
        content = (
            "[qa]\n"
            "freeze_min_seconds = 5.0\n"
            "lufs_warn_delta = 1.5\n"
            "skip_subtitle_pixels = true\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(content, encoding="utf-8")
            settings = Settings.load(path)
        self.assertEqual(settings.qa_freeze_min_seconds, 5.0)
        self.assertEqual(settings.qa_lufs_warn_delta, 1.5)
        self.assertTrue(settings.qa_skip_subtitle_pixels)
        snapshot = settings_to_dict(settings)
        thresholds = QAThresholds.from_settings(snapshot)
        self.assertEqual(thresholds.freeze_min_seconds, 5.0)
        self.assertTrue(thresholds.skip_subtitle_pixels)

    def test_parse_astats(self):
        text = (
            "[Parsed_astats_0] Overall\n"
            "[Parsed_astats_0] Peak level dB: -0.001856\n"
            "[Parsed_astats_0] Flat factor: 0.000000\n"
            "[Parsed_astats_0] Peak count: 2000.000000\n"
            "[Parsed_astats_0] Number of samples: 48000\n"
        )
        stats = _parse_astats(text)
        self.assertAlmostEqual(stats["Peak level dB"], -0.001856)
        self.assertEqual(stats["Peak count"], 2000.0)
        self.assertEqual(stats["Number of samples"], 48000.0)

    def test_subtitle_margin_status(self):
        settings = {
            "subtitle_margin_left": 50,
            "subtitle_margin_right": 50,
            "subtitle_margin_v": 36,
        }
        thresholds = QAThresholds()
        ok = {"left": 100, "top": 10, "right": 900, "bottom": 450}
        status, _ = subtitle_margin_status(
            ok, width=1000, height=500, settings=settings, thresholds=thresholds
        )
        self.assertEqual(status, "pass")
        warn = {"left": 40, "top": 10, "right": 900, "bottom": 450}
        status, details = subtitle_margin_status(
            warn, width=1000, height=500, settings=settings, thresholds=thresholds
        )
        self.assertEqual(status, "warn")
        self.assertIn("left", details["violations"])
        fail = {"left": 0, "top": 10, "right": 1000, "bottom": 500}
        status, _ = subtitle_margin_status(
            fail, width=1000, height=500, settings=settings, thresholds=thresholds
        )
        self.assertEqual(status, "fail")

    def test_compare_narration_subtitles(self):
        segments = [
            {"id": 1, "text": "第一句。第二句。", "start": 0.8, "end": 3.5},
        ]
        cues = [
            {"start": 0.8, "end": 2.0, "text": "第一句。", "lines": ["第一句。"]},
            {"start": 2.0, "end": 3.5, "text": "第二句。", "lines": ["第二句。"]},
        ]
        result = compare_narration_subtitles(segments, cues)
        self.assertEqual(result["text_mismatches"], [])
        self.assertEqual(result["missing_segments"], [])
        self.assertEqual(result["timing_mismatches"], [])

        cues[1]["text"] = "第二句话。"
        result = compare_narration_subtitles(segments, cues)
        self.assertEqual(len(result["text_mismatches"]), 1)


if __name__ == "__main__":
    unittest.main()
