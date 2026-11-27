from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reachreel.cli.main import _workdir_context, build_parser
from reachreel.core.config import Settings
from reachreel.core.util import atomic_write_json, read_json
from reachreel.domain.models import NarrationSegment, Scene, VideoInfo
from reachreel.i18n import t
from reachreel.pipeline.orchestrator import Pipeline


class GeneratedScriptTest(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        video = root / "source.mp4"
        video.write_bytes(b"test video")
        self.pipeline = Pipeline(video, Settings(cache_enabled=False), workdir=root / "job")
        self.pipeline._info = VideoInfo(str(video), 10.0, 160, 90, 24.0, False)
        self.pipeline._scenes = [Scene(1, 0.0, 4.0, summary="The subject opens a door.")]
        self.generate = self.enterContext(patch("reachreel.pipeline.orchestrator.generate_script"))
        self.generate.return_value = [NarrationSegment(1, [1], "Original script.")]

    def test_generated_script_is_reused_and_regenerated_with_changed_inputs(self):
        self.pipeline.write_script()
        self.pipeline.write_script()
        self.assertEqual(self.generate.call_count, 1)
        self.pipeline.settings.brief = "New background information."
        self.pipeline.settings.style_preset = "literary"
        self.pipeline.write_script()
        self.assertEqual(self.generate.call_count, 2)
        self.assertEqual(self.generate.call_args.kwargs["style_preset"], "literary")
        self.pipeline._scenes[0].summary = "The subject closes a door."
        self.pipeline.write_script()
        self.assertEqual(self.generate.call_count, 3)

    def test_manual_edits_survive_resume_until_explicit_regeneration(self):
        self.pipeline.write_script()
        saved = read_json(self.pipeline.script_path)
        saved["segments"][0]["text"] = "Edited script."
        atomic_write_json(self.pipeline.script_path, saved)
        self.pipeline.settings.brief = "New background information."
        self.pipeline.write_script()
        self.assertEqual(self.generate.call_count, 1)
        self.assertEqual(self.pipeline._segments[0].text, "Edited script.")
        self.pipeline.write_script(reuse=False)
        self.assertEqual(self.generate.call_count, 2)

    def test_language_change_preserves_edited_script_and_prompts_for_regeneration(self):
        self.pipeline.write_script()
        edited = read_json(self.pipeline.script_path)
        edited["segments"][0]["text"] = "Manually edited script."
        atomic_write_json(self.pipeline.script_path, edited)
        self.pipeline.settings.content_language = "en"
        with self.assertRaisesRegex(RuntimeError, "reachreel script.*--content-language en"):
            self.pipeline.write_script()
        self.pipeline._segments = None
        with self.assertRaisesRegex(RuntimeError, "reachreel script.*--content-language en"):
            self.pipeline.load_script()
        self.assertEqual(read_json(self.pipeline.script_path), edited)
        self.assertEqual(self.generate.call_count, 1)

    def test_review_shows_evidence_and_measured_overflow(self):
        self.pipeline._segments = [
            NarrationSegment(1, [1], "Grounded segment.", audio_path="voice.wav", end=6.0,
                             start=1.0, duration=5.0),
            NarrationSegment(2, [2], "Segment without evidence."),
        ]
        preview = self.pipeline.write_script_md().read_text(encoding="utf-8")
        self.assertIn("The subject opens a door.", preview)
        self.assertIn(t("script_md.missing_evidence"), preview)
        self.pipeline.write_review()
        review = (self.pipeline.workdir / "review.md").read_text(encoding="utf-8")
        self.assertIn(t("review.shot_overflow", id=1, seconds=2.0), review)


class WorkdirSettingsTest(unittest.TestCase):
    def test_explicit_config_and_environment_override_only_inherited_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "source.mp4"
            video.write_bytes(b"test video")
            atomic_write_json(root / "manifest.json", {
                "video": {"path": str(video)},
                "settings_snapshot": {
                    "bgm_volume": 0.20, "ambient_volume": 0.42, "llm_model": "previous",
                    "voice": "zf_xiaoxiao", "tts_engine": "kokoro", "style": "Previous style",
                },
            })
            cfg = root / "config.toml"
            cfg.write_text('bgm_volume = 0.15\nllm_model = "config"\nstyle_preset = "literary"\n'
                           '[tts]\nengine = "QWEN3"\n', encoding="utf-8")
            parser = build_parser()
            args = parser.parse_args(["tts", str(root), "--config", str(cfg)])
            with patch.dict(os.environ, {"REACHREEL_LLM_MODEL": "environment"}, clear=True):
                _, settings, _ = _workdir_context(root, args, parser)
            self.assertEqual(settings.bgm_volume, 0.15)
            self.assertEqual(settings.ambient_volume, 0.42)
            self.assertEqual(settings.llm_model, "environment")
            self.assertEqual(settings.tts_engine, "qwen3")
            self.assertEqual(settings.resolved_voice(), "Serena")
            self.assertEqual(settings.style_preset, "literary")
            self.assertEqual(settings.style, "")
