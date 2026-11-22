import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reachreel.core.cache import (
    cache_root,
    cache_stats,
    clean_cache,
    format_bytes,
    video_key,
)
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
from reachreel.engines.vlm import PROMPT_VERSION, analyze_scenes
from reachreel.generation.script import _extract_json_array
from reachreel.i18n import set_language
from reachreel.media.audio import (
    _bgm_filter,
    _loudnorm_filter,
    _parse_loudnorm_json,
    bgm_license_markdown,
    bgm_metadata,
    load_bgm_catalog,
)
from reachreel.media.render import output_dimensions
from reachreel.media.scenes import display_dimensions
from reachreel.media.subtitles import (
    SUBTITLE_PRESETS,
    _cue_texts,
    _split_text,
    ass_timestamp,
    build_subtitles,
    cues_from_timeline,
    read_srt,
    subtitle_margins,
    write_ass,
)
from reachreel.pipeline.orchestrator import Pipeline


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

    def test_shared_cache_helpers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "cache"
            (root / "vlm").mkdir(parents=True)
            (root / "vlm" / "a.json").write_text("{}", encoding="utf-8")
            (root / "tts").mkdir()
            (root / "tts" / "b.wav").write_bytes(b"x" * 100)
            self.assertEqual(cache_stats(root), {"files": 2, "bytes": 102})
            self.assertEqual(clean_cache(root), {"files": 2, "bytes": 102})
            self.assertFalse(root.exists())
        self.assertEqual(format_bytes(0), "0 B")
        self.assertEqual(format_bytes(1536), "1.5 KiB")

    def test_cache_root_prefers_platform_dir_and_legacy_fallback(self):
        self.assertEqual(cache_root("~/custom-cache").name, "custom-cache")
        with tempfile.TemporaryDirectory() as tmp:
            new = Path(tmp) / "new"
            legacy = Path(tmp) / "legacy"
            with patch(
                "reachreel.core.cache.default_cache_root", return_value=new
            ), patch("reachreel.core.cache.legacy_cache_root", return_value=legacy):
                legacy.mkdir()
                self.assertEqual(cache_root(None), legacy)
                new.mkdir()
                self.assertEqual(cache_root(None), new)

    def test_video_key_is_stable_and_content_sensitive(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "a.mp4"
            second = Path(tmp) / "b.mp4"
            first.write_bytes(b"a" * 3_000_000)
            second.write_bytes(b"b" * 3_000_000)
            self.assertEqual(video_key(first), video_key(first))
            self.assertNotEqual(video_key(first), video_key(second))
            self.assertEqual(len(video_key(first)), 20)

    def test_vlm_shared_cache_reuse(self):
        scenes = [Scene(id=1, start=0.0, end=2.0)]
        settings_key = f"zh|qwen3-vl:8b-instruct|{PROMPT_VERSION}|768|"
        with tempfile.TemporaryDirectory() as tmp:
            shared = Path(tmp) / "shared.json"
            local = Path(tmp) / "vlm_cache.json"
            shared.write_text(
                json.dumps(
                    {
                        "settings": settings_key,
                        "scenes": {"1:0.000:2.000": {"summary": "cached summary"}},
                    }
                ),
                encoding="utf-8",
            )
            flags = analyze_scenes(
                scenes,
                base_url="http://127.0.0.1:1",
                model="qwen3-vl:8b-instruct",
                cache_path=local,
                shared_cache_path=shared,
                max_width=768,
                context="",
                content_language="zh",
            )
        self.assertEqual(scenes[0].summary, "cached summary")
        self.assertEqual(flags, [])

    def test_tts_shared_cache_reuse(self):
        settings = Settings(tts_engine="say", voice="Tingting", kokoro_model="m")
        text = "hello"
        key = hashlib.sha1(
            f"say|m|Tingting|1.0000|{text}".encode()
        ).hexdigest()[:16]

        class NeverEngine:
            def synthesize(self, text, out, *, speed=1.0):
                raise AssertionError("shared cache should avoid synthesis")

        segment = NarrationSegment(id=1, scene_ids=[1], text=text)
        with tempfile.TemporaryDirectory() as tmp:
            shared_dir = Path(tmp) / "shared"
            shared_dir.mkdir()
            (shared_dir / f"{key}.wav").write_bytes(b"0" * 2048)
            with patch(
                "reachreel.engines.tts.make_engine", return_value=NeverEngine()
            ), patch("reachreel.engines.tts._wav_duration", return_value=1.5):
                flags = synthesize_segments(
                    [segment],
                    Path(tmp) / "tts",
                    settings,
                    shared_dir=shared_dir,
                )
            self.assertTrue(Path(segment.audio_path).exists())
        self.assertEqual(segment.duration, 1.5)
        self.assertEqual(flags, [])

    def test_subtitle_presets_and_margins(self):
        settings = Settings()
        self.assertEqual(subtitle_margins(settings), (36, 50, 50))
        self.assertEqual(
            set(SUBTITLE_PRESETS), {"douyin", "reels", "shorts", "landscape"}
        )
        for name, preset in SUBTITLE_PRESETS.items():
            settings.subtitle_preset = name
            self.assertEqual(
                subtitle_margins(settings),
                (preset["margin_v"], preset["margin_left"], preset["margin_right"]),
            )
        settings.subtitle_preset = "bogus"
        self.assertEqual(subtitle_margins(settings), (36, 50, 50))

    def test_split_text_keeps_english_words(self):
        text = "The wind crosses the valley and the light fades."
        pieces = _split_text(text, 18)
        self.assertTrue(pieces)
        self.assertTrue(all(len(piece) <= 18 for piece in pieces))
        self.assertEqual(" ".join(pieces).split(), text.split())

    def test_cue_timing_favours_sentence_pauses(self):
        segments = [
            NarrationSegment(
                id=1,
                scene_ids=[1],
                text="短。很长的第二句话。",
                start=0.0,
                end=4.0,
                duration=4.0,
            )
        ]
        cues = cues_from_timeline(segments, max_chars=8, max_lines=1)
        self.assertEqual(len(cues), 2)
        self.assertLess(cues[0].end - cues[0].start, cues[1].end - cues[1].start)

    def test_write_ass_uses_preset_margins(self):
        settings = Settings()
        settings.subtitle_preset = "douyin"
        settings.subtitle_size = 52
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.ass"
            write_ass([Cue(0.0, 1.0, "x")], path, width=1080, height=1920, settings=settings)
            text = path.read_text(encoding="utf-8")
        self.assertIn(",2,80,80,280,1", text)

    def test_loudnorm_helpers(self):
        settings = Settings()
        single = _loudnorm_filter(settings)
        self.assertIn("loudnorm=I=-14.0", single)
        self.assertNotIn("linear=true", single)
        measured = {
            "input_i": "-20.1",
            "input_tp": "-3.2",
            "input_lra": "5.1",
            "input_thresh": "-30.2",
            "target_offset": "0.1",
        }
        two = _loudnorm_filter(settings, measured)
        self.assertIn("measured_I=-20.1", two)
        self.assertIn("linear=true", two)
        stderr = json.dumps(measured)
        self.assertEqual(_parse_loudnorm_json(stderr)["input_i"], "-20.1")
        self.assertIsNone(_parse_loudnorm_json('{"input_i": "-inf"}'))
        self.assertIsNone(_parse_loudnorm_json("no json"))

    def test_bgm_fade_filter(self):
        settings = Settings()
        settings.bgm_fade_seconds = 2.0
        self.assertIn("afade=t=out:st=8.000:d=2.000", _bgm_filter(settings, 10.0))
        settings.bgm_fade_seconds = 0.0
        self.assertNotIn("afade", _bgm_filter(settings, 10.0))

    def test_write_script_md(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp) / "work"
            pipeline = Pipeline(Path(tmp) / "clip.mp4", Settings(), workdir=workdir)
            pipeline._info = VideoInfo(
                path=str(Path(tmp) / "clip.mp4"),
                duration=60.0,
                width=1920,
                height=1080,
                fps=30.0,
                has_audio=True,
            )
            pipeline._scenes = [Scene(id=1, start=0.0, end=10.0), Scene(id=2, start=10.0, end=20.0)]
            pipeline._segments = [
                NarrationSegment(id=1, scene_ids=[1], text="第一段。"),
                NarrationSegment(id=2, scene_ids=[2], text="第二段。"),
            ]
            path = pipeline.write_script_md()
            text = path.read_text(encoding="utf-8")
        self.assertIn("第一段。", text)
        self.assertIn("第二段。", text)
        self.assertIn("| 1 | 1 |", text)

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
