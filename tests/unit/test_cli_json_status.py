from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from reachreel.cli.main import build_parser, main
from reachreel.core.util import atomic_write_json
from reachreel.domain.models import NarrationSegment, Scene, VideoInfo
from reachreel.media.scenes import save_scenes
from reachreel.pipeline.orchestrator import Pipeline, workdir_status
from reachreel.pipeline.qa import WARN, Check, QAReport
from reachreel.pipeline.stage_inputs import StageInputs


def _seed_complete_workdir(root: Path) -> tuple[Path, Path]:
    root = root.resolve()
    video = root / "clip.mp4"
    video.write_bytes(b"source-video-bytes")
    workdir = root / "job"
    workdir.mkdir(parents=True, exist_ok=True)
    info = VideoInfo(str(video), 12.0, 1080, 1920, 30.0, True)
    scene = Scene(1, 0.0, 12.0, summary="A calm harbor at sunset.")
    save_scenes(workdir / "scenes.json", info, [scene])

    seg = NarrationSegment(
        id=1,
        scene_ids=[1],
        text="A quiet evening by the water.",
        audio_path=str(workdir / "tts/001.wav"),
        duration=3.0,
        start=0.5,
        end=3.5,
    )
    atomic_write_json(
        workdir / "script.json",
        {
            "content_language": "en",
            "segments": [seg.to_dict()],
            "generation": {"inputs_key": "k1", "segments_key": "orig"},
        },
    )
    atomic_write_json(workdir / "timeline.json", {"segments": [seg.to_dict()]})
    (workdir / "narration.wav").write_bytes(b"wav-narration")
    (workdir / "subtitles.srt").write_text(
        "1\n00:00:00,500 --> 00:00:03,500\nA quiet evening by the water.\n",
        encoding="utf-8",
    )
    (workdir / "subtitles.ass").write_text("[Script Info]\n", encoding="utf-8")
    (workdir / "mix.wav").write_bytes(b"wav-mix")
    (workdir / "final.mp4").write_bytes(b"rendered-final-mp4")
    atomic_write_json(
        workdir / "qa.json",
        {
            "workdir": str(workdir),
            "output": str(workdir / "final.mp4"),
            "summary": {"pass": 10, "warn": 0, "fail": 0},
            "checks": [],
        },
    )

    stage_inputs = StageInputs(workdir, video)
    render_inputs = stage_inputs.capture("render")
    render_inputs["output"] = stage_inputs.rendered_key() or ""
    render_inputs["subtitles.ass"] = stage_inputs.rendered_subtitles_key() or ""
    atomic_write_json(
        workdir / "manifest.json",
        {
            "schema_version": 2,
            "video": {
                "path": str(video),
                "duration": 12.0,
                "width": 1080,
                "height": 1920,
                "fps": 30.0,
                "has_audio": True,
            },
            "workdir": str(workdir),
            "output": str(workdir / "final.mp4"),
            "settings": {
                "vlm_model": "qwen2.5vl:7b",
                "llm_model": "qwen2.5:14b",
                "tts_engine": "kokoro",
                "voice": "af_heart",
                "whisper_model": "mlx-community/whisper-large-v3-turbo",
                "burned_subtitles": True,
            },
            "settings_snapshot": {"content_language": "en"},
            "quality_flags": [],
            "artifact_inputs": {
                "vlm": stage_inputs.capture("vlm"),
                "tts": stage_inputs.capture("tts"),
                "subtitles": stage_inputs.capture("subtitles"),
                "mix": stage_inputs.capture("mix"),
                "render": render_inputs,
            },
            "qa": {"pass": 10, "warn": 0, "fail": 0},
        },
    )
    return video, workdir


class WorkdirStatusTests(unittest.TestCase):
    def test_status_reports_complete_workdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, workdir = _seed_complete_workdir(Path(tmp))
            report = workdir_status(workdir)
            self.assertTrue(report["complete"])
            self.assertIsNone(report["next_stage"])
            self.assertIsNone(report["next_command"])
            self.assertTrue(report["video"]["exists"])
            self.assertEqual(report["qa"]["status"], "pass")
            self.assertFalse(report["qa"]["stale"])
            self.assertIn("output", report["artifacts"])
            for entry in report["stages"]:
                self.assertEqual(entry["status"], "ok")

    def test_status_detects_edited_script_and_stale_tts(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, workdir = _seed_complete_workdir(Path(tmp))
            script = json.loads((workdir / "script.json").read_text(encoding="utf-8"))
            script["segments"][0]["text"] = "Updated narration line."
            atomic_write_json(workdir / "script.json", script)

            report = workdir_status(workdir)
            self.assertFalse(report["complete"])
            self.assertTrue(report["script_edited"])
            by_stage = {entry["stage"]: entry["status"] for entry in report["stages"]}
            self.assertEqual(by_stage["script"], "ok")
            self.assertEqual(by_stage["tts"], "stale")
            self.assertEqual(report["next_stage"], "tts")
            self.assertEqual(report["next_command"], f"reachreel tts {workdir.resolve()}")

    def test_status_detects_stale_qa_when_manifest_qa_cleared(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, workdir = _seed_complete_workdir(Path(tmp))
            manifest = json.loads((workdir / "manifest.json").read_text(encoding="utf-8"))
            manifest.pop("qa", None)
            atomic_write_json(workdir / "manifest.json", manifest)

            report = workdir_status(workdir)
            by_stage = {entry["stage"]: entry["status"] for entry in report["stages"]}
            self.assertEqual(by_stage["qa"], "stale")
            self.assertTrue(report["qa"]["stale"])
            self.assertEqual(report["next_stage"], "qa")
            self.assertEqual(report["next_command"], f"reachreel qa {workdir.resolve()}")

    def test_status_cli_json_and_human_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, workdir = _seed_complete_workdir(Path(tmp))
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["status", str(workdir), "--json"])
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertTrue(payload["complete"])
            self.assertEqual(payload["workdir"], str(workdir.resolve()))

            text_buf = io.StringIO()
            with redirect_stdout(text_buf):
                rc_text = main(["--lang", "en", "status", str(workdir)])
            self.assertEqual(rc_text, 0)
            self.assertIn("ReachReel job status", text_buf.getvalue())
            self.assertIn("State: complete", text_buf.getvalue())

    def test_status_cli_rejects_missing_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does-not-exist"
            with self.assertRaises(SystemExit) as ctx:
                main(["status", str(missing)])
            self.assertEqual(ctx.exception.code, 2)


class RunJsonTests(unittest.TestCase):
    def test_parser_accepts_run_json_and_status_json(self):
        parser = build_parser()
        run_args = parser.parse_args(["run", "/tmp/video.mp4", "--dry-run", "--json"])
        self.assertTrue(run_args.json)
        self.assertTrue(run_args.dry_run)
        status_args = parser.parse_args(["status", "/tmp/job", "--json"])
        self.assertTrue(status_args.json)

    def test_run_dry_run_json_outputs_pure_plan_without_creating_workdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "clip.mp4"
            video.write_bytes(b"video")
            workdir = root / "planned_job"
            info = VideoInfo(str(video), 15.0, 1080, 1920, 30.0, True)

            buf = io.StringIO()
            with (
                patch("reachreel.pipeline.orchestrator.probe_video", return_value=info),
                redirect_stdout(buf),
            ):
                rc = main([
                    "run", str(video), "--workdir", str(workdir),
                    "--dry-run", "--no-qa", "--json",
                ])
            self.assertEqual(rc, 0)
            self.assertFalse(workdir.exists())
            plan = json.loads(buf.getvalue())
            self.assertTrue(plan["dry_run"])
            self.assertEqual(plan["mode"], "full")
            self.assertEqual(plan["workdir"], str(workdir.resolve()))
            self.assertFalse(plan["qa"]["enabled"])
            actions = {item["stage"]: item["action"] for item in plan["stages"]}
            self.assertEqual(actions["render"], "run")
            self.assertEqual(actions["qa"], "skip")

    def test_run_json_emits_summary_and_suppresses_human_qa_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            video, workdir = _seed_complete_workdir(Path(tmp))
            report = QAReport(
                workdir=str(workdir),
                output=str(workdir / "final.mp4"),
                checks=[Check("duration", WARN, "Slightly off")],
                summary={"pass": 9, "warn": 1, "fail": 0},
            )

            seen_print_qa: list[bool] = []

            def fake_run(self: Pipeline, **kwargs):
                self.executed_stages = ["probe", "vlm", "script", "tts", "subtitles", "mix", "render", "qa"]
                self.burned_subtitles = True
                self.qa_report = report
                seen_print_qa.append(bool(kwargs.get("print_qa", True)))
                return self.output_path

            buf = io.StringIO()
            with (
                patch.object(Pipeline, "run", fake_run),
                redirect_stdout(buf),
            ):
                rc = main([
                    "run", str(video), "--workdir", str(workdir),
                    "--qa-strict", "--json",
                ])
            self.assertEqual(rc, 1)
            self.assertEqual(seen_print_qa, [False])
            payload = json.loads(buf.getvalue())
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["exit_code"], 1)
            self.assertEqual(payload["output"], str((workdir / "final.mp4").resolve()))
            self.assertEqual(payload["qa"]["summary"]["warn"], 1)
            self.assertEqual(payload["stages_executed"][-1], "qa")

    def test_run_json_runtime_error_returns_structured_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "clip.mp4"
            video.write_bytes(b"video")
            workdir = root / "job"

            buf = io.StringIO()
            with (
                patch.object(Pipeline, "run", side_effect=RuntimeError("Ollama unreachable")),
                redirect_stdout(buf),
            ):
                rc = main(["run", str(video), "--workdir", str(workdir), "--json"])
            self.assertEqual(rc, 1)
            payload = json.loads(buf.getvalue())
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["exit_code"], 1)
            self.assertIn("Ollama unreachable", payload["error"])

    def test_run_review_script_json_includes_next_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "clip.mp4"
            video.write_bytes(b"video")
            workdir = root / "job"
            workdir.mkdir()
            info = VideoInfo(str(video), 10.0, 1080, 1920, 30.0, False)
            scene = Scene(1, 0.0, 10.0, summary="A mountain trail.")

            def fake_run(self: Pipeline, **kwargs):
                save_scenes(self.scenes_path, info, [scene])
                atomic_write_json(
                    self.script_path,
                    {
                        "content_language": "en",
                        "segments": [NarrationSegment(1, [1], "Walking up the trail.").to_dict()],
                    },
                )
                self.script_md_path.write_text("# Script\n", encoding="utf-8")
                atomic_write_json(
                    self.manifest_path,
                    {
                        "schema_version": 2,
                        "video": {"path": str(video), "duration": 10.0},
                        "artifact_inputs": {"vlm": StageInputs(self.workdir, video).capture("vlm")},
                    },
                )
                self.executed_stages = ["probe", "vlm", "script"]
                return self.script_path

            buf = io.StringIO()
            with (
                patch.object(Pipeline, "run", fake_run),
                redirect_stdout(buf),
            ):
                rc = main([
                    "run", str(video), "--workdir", str(workdir),
                    "--review-script", "--json",
                ])
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["review_script"])
            self.assertEqual(payload["output"], str((workdir / "script.json").resolve()))
            self.assertIn("script_md", payload["artifacts"])
            self.assertEqual(payload["next_stage"], "tts")
            self.assertEqual(payload["next_command"], f"reachreel tts {workdir.resolve()}")


if __name__ == "__main__":
    unittest.main()
