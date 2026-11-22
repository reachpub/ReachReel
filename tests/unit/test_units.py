import tempfile
import unittest
from pathlib import Path

from reachreel.core.config import Settings
from reachreel.core.util import srt_timestamp
from reachreel.domain.models import Cue, NarrationSegment, Scene
from reachreel.domain.timeline import place_segments
from reachreel.generation.script import _extract_json_array
from reachreel.media.render import output_dimensions
from reachreel.media.subtitles import (
    _cue_texts,
    _split_text,
    ass_timestamp,
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


if __name__ == "__main__":
    unittest.main()
