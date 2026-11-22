import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reachreel.core.config import Settings
from reachreel.core.util import srt_timestamp
from reachreel.domain.models import (
    Cue,
    NarrationSegment,
    QualityFlag,
    Scene,
    VideoInfo,
    merge_quality_flags,
)
from reachreel.domain.timeline import place_segments
from reachreel.engines.tts import _synthesize_with_retries, synthesize_segments
from reachreel.generation.script import _extract_json_array
from reachreel.i18n import set_language
from reachreel.media.audio import bgm_license_markdown, bgm_metadata, load_bgm_catalog
from reachreel.media.render import output_dimensions
from reachreel.media.scenes import display_dimensions
from reachreel.media.subtitles import (
    _cue_texts,
    _split_text,
    ass_timestamp,
    build_subtitles,
    cues_from_timeline,
    read_srt,
    write_ass,
)


class UnitTests(unittest.TestCase):
    def test_srt_timestamp(self):
        self.assertEqual(srt_timestamp(0), "00:00:00,000")
        self.assertEqual(srt_timestamp(3661.234), "01:01:01,234")

    def test_split_text(self):
        text = "清晨的雾气还没散去，山脊像一条沉睡的龙。我们沿着小路往上走，脚下的碎石发出细碎的响声。"
        pieces = _split_text(text, 18)
        self.assertTrue(all(len(piece) <= 22 for piece in pieces))
        self.assertEqual("".join(pieces), text)

    def test_extract_json_array(self):
        raw = '```json\n[{"scene_ids":[1],"text":"你好。"}]\n```'
        self.assertEqual(len(_extract_json_array(raw)), 1)

    def test_place_segments(self):
        scenes = [Scene(id=1, start=0, end=4), Scene(id=2, start=4, end=8), Scene(id=3, start=8, end=12)]
        segments = [
            NarrationSegment(id=1, scene_ids=[1], text="第一段。", duration=1.5),
            NarrationSegment(id=2, scene_ids=[2], text="第二段。", duration=1.5),
        ]
        settings = Settings()
        place_segments(segments, scenes, 12.0, settings)
        self.assertGreaterEqual(segments[0].start, 0.7)
        self.assertGreater(segments[1].start, segments[0].end)
        self.assertLessEqual(segments[-1].end, 12.0)

    def test_cues_do_not_drop_text(self):
        text = "第一次滑，小脸绷得像块冰。蓝黄小身影在紫色灯光下，像只试探雪地的企鹅。"
        cues = _cue_texts(text, 18, 2)
        self.assertGreaterEqual(len(cues), 2)
        self.assertEqual("".join(cue.replace("\n", "") for cue in cues), text)

    def test_cues_from_timeline(self):
        segments = [NarrationSegment(id=1, scene_ids=[1], text="第一句。第二句。", start=1.0, end=4.0, duration=3.0)]
        cues = cues_from_timeline(segments, max_chars=18, max_lines=2)
        self.assertEqual(len(cues), 1)
        self.assertAlmostEqual(cues[0].start, 1.0, places=2)

    def test_ass_timestamp(self):
        self.assertEqual(ass_timestamp(0), "0:00:00.00")
        self.assertEqual(ass_timestamp(3661.234), "1:01:01.23")

    def test_output_dimensions(self):
        settings = Settings()
        self.assertEqual(output_dimensions(1080, 1920, settings), (1080, 1920))
        settings.output_height = 1080
        self.assertEqual(output_dimensions(1080, 1920, settings), (608, 1080))
        settings.output_height = 720
        self.assertEqual(output_dimensions(1920, 1080, settings), (1280, 720))

    def test_write_ass_uses_output_pixels(self):
        settings = Settings()
        settings.subtitle_size = 52
        settings.subtitle_margin_v = 60
        cues = [Cue(0.0, 2.5, "第一句。\n第二句。")]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.ass"
            write_ass(cues, path, width=1080, height=1920, settings=settings)
            text = path.read_text(encoding="utf-8")
        self.assertIn("PlayResX: 1080", text)
        self.assertIn("PlayResY: 1920", text)
        self.assertIn("Default,PingFang SC,52,", text)
        self.assertIn(",2,50,50,60,1", text)
        self.assertIn("Dialogue: 0,0:00:00.00,0:00:02.50,", text)
        self.assertIn(r"第一句。\N第二句。", text)

    def test_read_srt_roundtrip(self):
        content = "1\n00:00:01,000 --> 00:00:03,500\n第一行\n第二行\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.srt"
            path.write_text(content, encoding="utf-8")
            cues = read_srt(path)
        self.assertEqual(len(cues), 1)
        self.assertAlmostEqual(cues[0].start, 1.0)
        self.assertAlmostEqual(cues[0].end, 3.5)
        self.assertEqual(cues[0].text, "第一行\n第二行")

    def test_display_dimensions_rotation(self):
        self.assertEqual(display_dimensions(1920, 1080, 0), (1920, 1080))
        self.assertEqual(display_dimensions(1920, 1080, 180), (1920, 1080))
        self.assertEqual(display_dimensions(1920, 1080, 90), (1080, 1920))
        self.assertEqual(display_dimensions(1920, 1080, -90), (1080, 1920))
        self.assertEqual(display_dimensions(1920, 1080, 270), (1080, 1920))
        self.assertEqual(display_dimensions(1080, 1080, 90), (1080, 1080))

    def test_video_info_accepts_legacy_scenes_json(self):
        info = VideoInfo(
            path="clip.mp4",
            duration=1.0,
            width=1920,
            height=1080,
            fps=24.0,
            has_audio=False,
        )
        self.assertEqual((info.coded_width, info.coded_height), (0, 0))

    def test_merge_quality_flags_deduplicates(self):
        flags = merge_quality_flags(
            [
                {"code": "vlm.scene_failed", "params": {"scene": 1}, "severity": "warning"},
                "corrupt",
            ],
            [
                QualityFlag("vlm.scene_failed", {"scene": 1}),
                QualityFlag("tts.segment_failed", {"id": 2}, "error"),
            ],
        )
        self.assertEqual(len(flags), 2)
        self.assertEqual(flags[1]["code"], "tts.segment_failed")

    def test_bgm_catalog_and_license_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "manifest.json").write_text(
                json.dumps(
                    {
                        "tracks": [
                            {
                                "file": "calm.mp3",
                                "title": "Calm",
                                "author": "A",
                                "source": "https://example.test/calm",
                                "license": "CC0-1.0",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            catalog = load_bgm_catalog(directory)
            self.assertEqual(catalog["calm.mp3"]["license"], "CC0-1.0")
            metadata = bgm_metadata(directory / "calm.mp3")
            self.assertEqual(metadata["title"], "Calm")
            set_language("en")
            report = bgm_license_markdown([metadata])
            self.assertIn("Media licenses", report)
            self.assertIn("Calm", report)
            self.assertIn("CC0-1.0", report)
            self.assertIn("No BGM metadata", bgm_license_markdown([]))

    def test_tts_retries_before_success(self):
        class FlakyEngine:
            def __init__(self):
                self.calls = 0

            def synthesize(self, text, out, *, speed=1.0):
                self.calls += 1
                if self.calls < 3:
                    raise RuntimeError("boom")
                out.write_bytes(b"0" * 2048)
                return 1.0

        engine = FlakyEngine()
        with tempfile.TemporaryDirectory() as tmp, patch(
            "reachreel.engines.tts.time.sleep"
        ) as sleep:
            duration = _synthesize_with_retries(
                engine,
                NarrationSegment(id=1, scene_ids=[1], text="hello"),
                Path(tmp) / "seg.wav",
                speed=1.0,
                retries=2,
            )
        self.assertEqual(duration, 1.0)
        self.assertEqual(engine.calls, 3)
        sleep.assert_called()

    def test_tts_isolates_failed_segment(self):
        class SelectiveEngine:
            def synthesize(self, text, out, *, speed=1.0):
                if text == "bad":
                    raise RuntimeError("boom")
                out.write_bytes(b"0" * 2048)
                return 1.0

        segments = [
            NarrationSegment(id=1, scene_ids=[1], text="bad"),
            NarrationSegment(id=2, scene_ids=[1], text="good"),
        ]
        with tempfile.TemporaryDirectory() as tmp, patch(
            "reachreel.engines.tts.make_engine", return_value=SelectiveEngine()
        ), patch("reachreel.engines.tts._wav_duration", return_value=1.0):
            flags = synthesize_segments(
                segments,
                Path(tmp),
                Settings(tts_engine="say"),
                retries=0,
            )
        self.assertEqual(segments[0].audio_path, "")
        self.assertTrue(segments[1].audio_path)
        self.assertEqual([flag.code for flag in flags], ["tts.segment_failed"])
        self.assertEqual(flags[0].params["id"], 1)

    def test_tts_all_segments_failing_raises(self):
        class FailingEngine:
            def synthesize(self, text, out, *, speed=1.0):
                raise RuntimeError("boom")

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("reachreel.engines.tts.make_engine", return_value=FailingEngine()),
            self.assertRaises(RuntimeError),
        ):
            synthesize_segments(
                [NarrationSegment(id=1, scene_ids=[1], text="a")],
                Path(tmp),
                Settings(tts_engine="say"),
                retries=0,
            )

    def test_asr_fallback_records_quality_flag(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "reachreel.media.subtitles.transcribe_with_mlx_whisper",
            return_value=None,
        ):
            flags = []
            cues = build_subtitles(
                Path(tmp) / "missing.wav",
                [
                    NarrationSegment(
                        id=1,
                        scene_ids=[1],
                        text="第一句。",
                        duration=1.0,
                        start=0.0,
                        end=1.0,
                    )
                ],
                Path(tmp) / "sub.srt",
                Settings(),
                flags=flags,
            )
        self.assertTrue(cues)
        self.assertEqual([flag.code for flag in flags], ["asr.fallback"])


if __name__ == "__main__":
    unittest.main()
