"""Exercise stage handoffs with generated media and deterministic model responses."""
from __future__ import annotations

import io
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from reachreel.cli.main import main
from reachreel.core.util import atomic_write_json, ffprobe_json, read_json, run, sha1_file
from reachreel.domain.models import NarrationSegment
from reachreel.i18n import current_language, set_language


@unittest.skipUnless(os.environ.get("REACHREEL_INTEGRATION") == "1", "set REACHREEL_INTEGRATION=1")
@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
class ResumeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        tmpdir = tempfile.TemporaryDirectory(prefix="reachreel-resume-")
        cls.addClassCleanup(tmpdir.cleanup)
        cls.root = Path(tmpdir.name).resolve()
        cls.source = cls.root / "source clip.mp4"
        cls.voice = cls.root / "voice.wav"
        cls.revised_voice = cls.root / "revised.wav"
        run(
            [
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                "testsrc2=size=320x180:rate=24:duration=6",
                "-c:v", "libx264", "-preset", "ultrafast", cls.source,
            ],
            timeout=30,
        )
        run(
            [
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                "sine=frequency=500:sample_rate=48000:duration=3", "-c:a", "pcm_s16le",
                cls.revised_voice,
            ],
            timeout=30,
        )
        run(
            [
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                "sine=sample_rate=48000:duration=3", "-c:a", "pcm_s16le", cls.voice,
            ],
            timeout=30,
        )

    def setUp(self) -> None:
        tmpdir = tempfile.TemporaryDirectory(dir=self.root)
        self.addCleanup(tmpdir.cleanup)
        self.job = Path(tmpdir.name) / "new job"
        self.addCleanup(set_language, current_language())

        def generate_script(*args, **kwargs) -> list[NarrationSegment]:
            return [NarrationSegment(1, [1], "A pattern moves across the screen.")]

        def synthesize(_engine, text: str, out_wav: Path, *, speed: float) -> float:
            wav = self.revised_voice if text == "Manually revised narration." else self.voice
            shutil.copyfile(wav, out_wav)
            return 3.0

        synthesize_patch = patch(
            "reachreel.engines.tts.SayEngine.synthesize", autospec=True, side_effect=synthesize,
        )
        self.addCleanup(synthesize_patch.stop)
        self.synthesize = synthesize_patch.start()
        analysis_patch = patch(
            "reachreel.engines.vlm.analyze_scene",
            return_value={"summary": "A moving test pattern."},
        )
        self.addCleanup(analysis_patch.stop)
        self.analyze_scene = analysis_patch.start()
        mocks = (
            patch("reachreel.cli.main.setup_logging"),
            patch("reachreel.pipeline.orchestrator.generate_script", side_effect=generate_script),
            patch("reachreel.engines.tts.SayEngine.language"),
        )
        for mock in mocks:
            self.addCleanup(mock.stop)
            mock.start()

    def cli(self, *args: str | Path, expected: int = 0) -> str:
        output = io.StringIO()
        errors = io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            try:
                exit_code = main(["--lang", "en", "--no-cache", *[str(arg) for arg in args]])
            except SystemExit as exc:
                exit_code = exc.code
        self.assertEqual(
            exit_code,
            expected,
            f"reachreel {args}\n{output.getvalue()}\n{errors.getvalue()}",
        )
        return output.getvalue().strip() if expected == 0 else errors.getvalue().strip()

    def start_script(self, *stage_options: str | Path) -> str:
        return self.cli(
            "run", self.source, "--workdir", self.job,
            "--content-language", "en", "--tts", "say", *stage_options,
        )

    def _render_job(self) -> Path:
        self.start_script("--only", "vlm,script")
        self.cli("tts", self.job)
        self.cli("subtitle", self.job, "--asr", "none")
        self.cli("mix", self.job, "--no-bgm")
        self.cli("render", self.job, "--no-burn")
        return self.job / "final.mp4"

    def test_qa_rejects_old_video_after_upstream_artifacts_change(self) -> None:
        final = self._render_job()
        old_final = sha1_file(final)
        self.cli("qa", self.job, "--strict")
        script = self.job / "script.json"
        data = read_json(script)
        data["segments"][0]["text"] = "Manually revised narration."
        atomic_write_json(script, data)
        self.cli("tts", self.job)
        self.cli("subtitle", self.job, "--asr", "none")
        self.cli("mix", self.job, "--no-bgm")
        self.assertEqual(sha1_file(final), old_final)

        for command in (
            ("qa", self.job, "--strict"),
            ("run", self.source, "--workdir", self.job, "--only", "qa", "--qa-strict"),
        ):
            with self.subTest(command=command[0]):
                self.cli(*command, expected=1)
                report = read_json(self.job / "qa.json")
                self.assertEqual(report["summary"]["status"], "fail")
                self.assertTrue(any("reachreel render" in check["message"] for check in report["checks"]))
        self.cli("render", self.job, "--no-burn")
        self.cli("qa", self.job, "--strict")

    def test_failed_rerender_preserves_the_previous_movie_and_qa(self) -> None:
        final = self._render_job()
        self.cli("qa", self.job, "--strict")
        movie_digest = sha1_file(final)
        manifest_digest = sha1_file(self.job / "manifest.json")
        self.cli("render", self.job, "--height", "119", "--no-burn", expected=1)
        self.assertEqual(sha1_file(final), movie_digest)
        self.assertEqual(sha1_file(self.job / "manifest.json"), manifest_digest)

    def test_legacy_render_can_pass_qa_until_inputs_change(self) -> None:
        self._render_job()
        manifest = self.job / "manifest.json"
        data = read_json(manifest)
        data.pop("artifact_inputs")
        data.pop("schema_version")
        atomic_write_json(manifest, data)
        self.cli("qa", self.job, "--strict")
        subtitles = self.job / "subtitles.srt"
        subtitles.write_text(subtitles.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        self.cli("qa", self.job, "--strict", expected=1)
        self.assertEqual(read_json(self.job / "qa.json")["checks"][0]["details"]["stage"], "render")

    def test_probe_updates_requested_settings_without_relabeling_a_render(self) -> None:
        self._render_job()
        self.cli("run", self.source, "--workdir", self.job, "--only", "probe", "--height", "120")
        manifest = read_json(self.job / "manifest.json")
        self.assertEqual(manifest["settings_snapshot"]["output_height"], 120)
        self.assertEqual(manifest["render_settings"]["output_height"], 0)
        self.cli("render", self.job)
        self.assertEqual(ffprobe_json(self.job / "final.mp4")["streams"][0]["height"], 120)

    def test_reanalysis_marks_the_tts_timeline_stale(self) -> None:
        first = self.job.parent / "first.toml"
        second = self.job.parent / "second.toml"
        first.write_text("max_scene_seconds = 3.0\n", encoding="utf-8")
        second.write_text("max_scene_seconds = 2.0\n", encoding="utf-8")
        self.start_script("--only", "vlm,script", "--config", first)
        script = self.job / "script.json"
        data = read_json(script)
        data["segments"][0]["scene_ids"] = [2]
        atomic_write_json(script, data)
        self.cli("tts", self.job)
        self.cli("subtitle", self.job, "--asr", "none")
        self.cli("mix", self.job, "--no-bgm")
        self.assertEqual(read_json(self.job / "timeline.json")["segments"][0]["start"], 3.0)
        self.cli("analyze", self.source, "--workdir", self.job, "--config", second)

        self.assertIn("reachreel tts", self.cli("render", self.job, "--no-burn", expected=1))
        self.cli("tts", self.job)
        self.assertEqual(read_json(self.job / "timeline.json")["segments"][0]["start"], 2.0)

    def test_replacing_the_source_blocks_stale_export_and_tts(self) -> None:
        private_source = self.job.parent / "replaceable.mp4"
        shutil.copy2(self.source, private_source)
        self.source = private_source
        self._render_job()
        run(
            [
                "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                "testsrc2=size=320x180:rate=24:duration=1", "-c:v", "libx264", self.source,
            ],
            timeout=30,
        )
        self.assertIn("reachreel analyze", self.cli("export", self.job, expected=2))
        self.assertFalse((self.job / "edit").exists())
        self.assertIn("reachreel analyze", self.cli("tts", self.job, expected=1))

    def test_mix_reselects_after_bgm_directory_changes(self) -> None:
        self.start_script("--only", "vlm,script")
        self.cli("tts", self.job)
        first, second = (self.job.parent / "old bgm", self.job.parent / "new bgm")
        for directory in (first, second):
            directory.mkdir()
            shutil.copy2(self.voice, directory / "track.wav")
        self.cli("mix", self.job, "--bgm-dir", first)
        self.cli("mix", self.job, "--bgm-dir", second)
        manifest = read_json(self.job / "manifest.json")
        self.assertEqual(Path(manifest["bgm_source"]), second / "track.wav")

    def test_script_applies_an_explicit_vlm_model_change(self) -> None:
        self.cli("analyze", self.source, "--workdir", self.job, "--vlm-model", "initial")
        self.analyze_scene.reset_mock()
        self.cli("script", self.job, "--vlm-model", "changed")
        self.assertTrue(self.analyze_scene.called)
        self.assertTrue(all(call.args[0].model == "changed" for call in self.analyze_scene.call_args_list))

    def test_resume_from_script_to_quality_check(self) -> None:
        self.assertEqual(Path(self.start_script("--only", "vlm,script")), self.job / "script.json")
        self.assertTrue((self.job / "script.json").is_file())
        self.assertTrue((self.job / "script.md").is_file())
        manifest = read_json(self.job / "manifest.json")
        self.assertEqual(Path(manifest["video"]["path"]), self.source)
        final = self.job / "final.mp4"
        self.assertFalse(final.exists())

        self.cli("tts", self.job)
        self.cli("subtitle", self.job, "--asr", "none")
        self.cli("mix", self.job, "--no-bgm")
        self.assertEqual(self.cli("render", self.job, "--no-burn"), f"{final} (burned=False)")
        self.cli("qa", self.job)

        qa = self.job / "qa.json"
        self.assertTrue(final.is_file())
        self.assertEqual(read_json(qa)["summary"]["fail"], 0)
        self.assertIn("qa", read_json(self.job / "manifest.json"))

        result = self.cli(
            "run", self.source, "--workdir", self.job, "--only", "render",
            "--height", "120", "--no-burn",
        )
        self.assertEqual(Path(result), final)
        manifest = read_json(self.job / "manifest.json")
        self.assertEqual(manifest["render_settings"]["output_height"], 120)
        self.assertNotIn("qa", manifest)

        result = self.cli("run", self.source, "--workdir", self.job, "--only", "qa")
        self.assertEqual(Path(result.splitlines()[-1]), qa)
        self.assertEqual(read_json(qa)["summary"]["fail"], 0)

    def test_analyze_hands_off_to_script(self) -> None:
        self.assertEqual(
            Path(self.cli("analyze", self.source, "--workdir", self.job)),
            self.job / "scenes.json",
        )
        manifest = read_json(self.job / "manifest.json")
        self.assertEqual(Path(manifest["video"]["path"]), self.source)
        self.assertEqual(Path(self.cli("script", self.job)), self.job / "script.json")
        self.assertTrue((self.job / "script.json").is_file())

    def test_changed_narration_requires_new_subtitles_and_mix(self) -> None:
        self.start_script("--only", "vlm,script")
        self.cli("tts", self.job)
        self.cli("subtitle", self.job, "--asr", "none")
        self.cli("mix", self.job, "--no-bgm")

        # Manual caption changes are a supported input to render.
        subtitles = self.job / "subtitles.srt"
        subtitles.write_text(subtitles.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        self.cli("render", self.job, "--no-burn")

        script = self.job / "script.json"
        data = read_json(script)
        data["segments"][0]["text"] = "Manually revised narration."
        atomic_write_json(script, data)
        self.assertIn("reachreel tts", self.cli("render", self.job, "--no-burn", expected=1))
        self.cli("tts", self.job)
        self.assertIn("reachreel subtitle", self.cli("render", self.job, "--no-burn", expected=1))
        self.cli("subtitle", self.job, "--asr", "none")
        self.assertIn("reachreel mix", self.cli("render", self.job, "--no-burn", expected=1))
        self.cli("mix", self.job, "--no-bgm")
        self.cli("render", self.job, "--no-burn")

    def test_subtitle_and_mix_require_explicit_tts(self) -> None:
        self.start_script("--review-script")
        timeline = self.job / "timeline.json"
        narration = self.job / "narration.wav"
        for dependency in (timeline, narration):
            if dependency == narration:
                self.cli("tts", self.job)
            for command in (
                ("subtitle", self.job, "--asr", "none"),
                ("mix", self.job, "--no-bgm"),
                ("run", self.source, "--workdir", self.job, "--only", "subtitles,mix",
                 "--asr", "none", "--no-bgm"),
            ):
                with self.subTest(dependency=dependency.name, command=command[0]):
                    dependency.unlink(missing_ok=True)
                    self.synthesize.reset_mock()
                    self.assertIn("reachreel tts", self.cli(*command, expected=1))
                    self.synthesize.assert_not_called()
                    self.assertFalse(dependency.exists())

    def test_resuming_uses_explicit_config_and_preserves_other_saved_settings(self) -> None:
        initial = self.root / "initial.toml"
        changed = self.root / "changed.toml"
        initial.write_text('bgm_volume = 0.20\npreset = "ultrafast"\n', encoding="utf-8")
        changed.write_text(
            'bgm_volume = 0.15\noutput_height = 120\nwhisper_model = "config-model"\n',
            encoding="utf-8",
        )
        self.start_script("--review-script", "--config", initial)
        self.cli("tts", self.job)
        self.cli("subtitle", self.job, "--asr", "none")
        with patch.dict(os.environ, {"REACHREEL_WHISPER_MODEL": "env-model"}):
            self.cli("mix", self.job, "--no-bgm", "--config", changed)

        manifest = read_json(self.job / "manifest.json")
        self.assertEqual(manifest["settings_snapshot"]["bgm_volume"], 0.15)
        self.assertEqual(manifest["settings_snapshot"]["preset"], "ultrafast")
        self.assertEqual(manifest["settings_snapshot"]["whisper_model"], "env-model")
        self.cli("render", self.job, "--config", changed, "--height", "90", "--no-burn")
        stream = ffprobe_json(self.job / "final.mp4")["streams"][0]
        self.assertEqual(stream["height"], 90)
