import hashlib
import itertools
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
from reachreel.core.util import platform_supported, srt_timestamp
from reachreel.domain.models import (
    Cue,
    NarrationSegment,
    QualityFlag,
    Scene,
    VideoInfo,
    merge_quality_flags,
)
from reachreel.domain.timeline import place_segments
from reachreel.engines.tts import (
    KokoroEngine,
    Qwen3TTSEngine,
    _split_for_tts,
    _synthesize_with_retries,
    _tts_audio_filter_chain,
    make_engine,
    synthesize_segments,
    tts_cache_key,
    validate_ref_audio,
)
from reachreel.engines.vlm import analyze_scenes
from reachreel.engines.vlm import settings_key as vlm_settings_key
from reachreel.generation.script import (
    _clean_text,
    _extract_json_array,
    _normalise_segments,
    generate_script,
)
from reachreel.i18n import set_language
from reachreel.media.audio import (
    _bgm_filter,
    _loudnorm_filter,
    _parse_loudnorm_json,
    bgm_license_markdown,
    bgm_metadata,
    load_bgm_catalog,
    mix_filter_graph,
)
from reachreel.media.render import (
    output_dimensions,
    subtitle_track_language,
    unscaled_source_warning,
)
from reachreel.media.scenes import display_dimensions, select_representative_frames
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
    def test_platform_supported_requires_apple_silicon(self):
        self.assertTrue(platform_supported("Darwin", "arm64"))
        self.assertTrue(platform_supported("Darwin", "aarch64"))
        self.assertTrue(platform_supported("darwin", "ARM64"))
        self.assertFalse(platform_supported("Darwin", "x86_64"))
        self.assertFalse(platform_supported("Linux", "aarch64"))
        self.assertFalse(platform_supported("Windows", "AMD64"))

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

    def test_select_representative_frames(self):
        def flat(value: int) -> bytes:
            return bytes([value] * 256)
        self.assertEqual(select_representative_frames([]), [])
        self.assertEqual(select_representative_frames([flat(0)]), [0])
        # A static shot collapses to its first frame.
        self.assertEqual(select_representative_frames([flat(0), flat(0), flat(1)]), [0])
        # A gradual shift accumulates against the last kept frame: 3 < 4, 6 >= 4.
        self.assertEqual(
            select_representative_frames([flat(0), flat(3), flat(6), flat(9)]),
            [0, 2],
        )
        # threshold 0 disables the filter.
        self.assertEqual(
            select_representative_frames([flat(0), flat(0)], threshold=0.0),
            [0, 1],
        )
        # Mismatched or empty signatures are treated as distinct, not dropped.
        self.assertEqual(
            select_representative_frames([b"\x00" * 4, b"\x00" * 9, b""]),
            [0, 1, 2],
        )

    def test_unscaled_source_warning(self):
        settings = Settings()
        four_k = VideoInfo(
            path="4k.mov", duration=90.0, width=3840, height=2160, fps=60.0, has_audio=True
        )
        portrait = VideoInfo(
            path="p.mp4", duration=80.0, width=1080, height=1920, fps=30.0, has_audio=True
        )
        # 4K with output_height = 0 stays 4K: warn once, with the fix in the text.
        message = unscaled_source_warning(four_k, settings)
        self.assertIsNotNone(message)
        self.assertIn("3840x2160", message)
        self.assertIn("--height 1080", message)
        # Choosing an output height silences it.
        settings.output_height = 1080
        self.assertIsNone(unscaled_source_warning(four_k, settings))
        # 1080p portrait has a height above 1080 but no wasted pixels.
        settings.output_height = 0
        self.assertIsNone(unscaled_source_warning(portrait, settings))

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

    def test_place_segments_empty_list(self):
        self.assertEqual(place_segments([], [], 10.0, Settings()), [])

    def test_place_segments_without_scenes_keeps_order(self):
        segments = [
            NarrationSegment(id=1, scene_ids=[1], text="a", duration=1.0),
            NarrationSegment(id=2, scene_ids=[2], text="b", duration=1.0),
        ]
        settings = Settings()
        place_segments(segments, [], 10.0, settings)
        self.assertAlmostEqual(segments[0].start, settings.start_offset, places=3)
        for previous, current in itertools.pairwise(segments):
            self.assertGreaterEqual(
                current.start, previous.end + settings.min_gap_seconds - 0.001
            )
        self.assertLessEqual(segments[-1].end, 10.0)

    def test_place_segments_unknown_scene_id_falls_back_to_start_offset(self):
        scenes = [Scene(id=1, start=0.0, end=4.0)]
        segments = [NarrationSegment(id=1, scene_ids=[99], text="x", duration=1.0)]
        settings = Settings()
        place_segments(segments, scenes, 10.0, settings)
        self.assertAlmostEqual(segments[0].start, settings.start_offset, places=3)

    def test_place_segments_overlong_narration_is_reported_not_raised(self):
        # Narration far longer than the clip and no audio attached, so no
        # atempo can shorten it: the stage must survive and keep the order.
        segments = [
            NarrationSegment(id=i, scene_ids=[1], text="x", duration=8.0) for i in (1, 2, 3)
        ]
        settings = Settings()
        with self.assertLogs("reachreel.domain.timeline", level="WARNING"):
            result = place_segments(segments, [Scene(id=1, start=0, end=4)], 5.0, settings)
        self.assertIs(result, segments)
        self.assertEqual([segment.id for segment in segments], [1, 2, 3])
        for previous, current in itertools.pairwise(segments):
            self.assertGreater(current.start, previous.start)
        self.assertGreater(segments[-1].end, 5.0)

    def test_place_segments_shorter_than_tail_padding(self):
        segments = [NarrationSegment(id=1, scene_ids=[1], text="x", duration=0.5)]
        settings = Settings()
        place_segments(segments, [Scene(id=1, start=0.0, end=0.3)], 0.4, settings)
        self.assertAlmostEqual(segments[0].start, settings.start_offset, places=3)
        self.assertAlmostEqual(segments[0].end, 1.2, places=3)

    def test_place_segments_fits_without_speedup(self):
        segments = [NarrationSegment(id=1, scene_ids=[1], text="x", duration=2.0)]
        settings = Settings()
        place_segments(segments, [Scene(id=1, start=0.0, end=9.0)], 10.0, settings)
        self.assertEqual(segments[0].duration, 2.0)
        self.assertEqual(segments[0].tts_speed, 1.0)

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

    def test_write_ass_defaults_to_white_text_with_black_outline(self):
        settings = Settings()
        settings.subtitle_size = 50
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.ass"
            write_ass([Cue(0.0, 1.0, "x")], path, width=1080, height=1920, settings=settings)
            text = path.read_text(encoding="utf-8")
        style = next(line for line in text.splitlines() if line.startswith("Style:"))
        fields = style.split(",")
        self.assertEqual(fields[3], "&H00FFFFFF")  # white fill
        self.assertEqual(fields[5], "&H00000000")  # black outline colour
        self.assertEqual(fields[15], "1")  # BorderStyle=1 -> outline, no box
        self.assertEqual(fields[16], "3.0")  # 50 * 0.06
        self.assertEqual(fields[17], "2")  # soft shadow

    def test_write_ass_box_mode_keeps_the_old_look(self):
        settings = Settings()
        settings.subtitle_render = "box"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.ass"
            write_ass([Cue(0.0, 1.0, "x")], path, width=1080, height=1920, settings=settings)
            text = path.read_text(encoding="utf-8")
        fields = next(line for line in text.splitlines() if line.startswith("Style:")).split(",")
        self.assertEqual(fields[15], "3")  # BorderStyle=3 -> opaque box
        self.assertEqual(fields[16], "6.7")  # padding scales with 1920 height
        self.assertEqual(fields[17], "0")

    def test_write_ass_outline_override_and_normalization(self):
        settings = Settings()
        settings.subtitle_outline = 5.0
        settings.subtitle_render = "stroke"  # alias for outline
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.ass"
            write_ass([Cue(0.0, 1.0, "x")], path, width=1920, height=1080, settings=settings)
            text = path.read_text(encoding="utf-8")
        fields = next(line for line in text.splitlines() if line.startswith("Style:")).split(",")
        self.assertEqual(fields[15], "1")
        self.assertEqual(fields[16], "5.0")

    def test_write_ass_none_mode_is_plain_text(self):
        settings = Settings()
        settings.subtitle_render = "none"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.ass"
            write_ass([Cue(0.0, 1.0, "x")], path, width=1080, height=1920, settings=settings)
            text = path.read_text(encoding="utf-8")
        fields = next(line for line in text.splitlines() if line.startswith("Style:")).split(",")
        self.assertEqual(fields[15], "1")  # BorderStyle=1 (no box)
        self.assertEqual(fields[16], "0.0")  # no stroke
        self.assertEqual(fields[17], "0")  # no shadow

    def test_write_ass_box_padding_override(self):
        settings = Settings()
        settings.subtitle_render = "box"
        settings.subtitle_box_padding = 3.0
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.ass"
            write_ass([Cue(0.0, 1.0, "x")], path, width=1080, height=1920, settings=settings)
            text = path.read_text(encoding="utf-8")
        fields = next(line for line in text.splitlines() if line.startswith("Style:")).split(",")
        self.assertEqual(fields[15], "3")
        self.assertEqual(fields[16], "3.0")  # explicit padding wins over height/288

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
        scene_settings = {
            "content_language": "zh",
            "model": "qwen3-vl:8b-instruct",
            "max_width": 768,
            "frame_dedup": 0.0,
            "context": "",
        }
        settings_key = vlm_settings_key(**scene_settings)
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
                model=scene_settings["model"],
                cache_path=local,
                shared_cache_path=shared,
                max_width=scene_settings["max_width"],
                context=scene_settings["context"],
                content_language=scene_settings["content_language"],
                frame_dedup=scene_settings["frame_dedup"],
            )
        self.assertEqual(scenes[0].summary, "cached summary")
        self.assertEqual(flags, [])

    def test_vlm_settings_key_includes_dedup(self):
        base = {
            "content_language": "zh",
            "model": "qwen3-vl:8b-instruct",
            "max_width": 768,
            "context": "ski trip",
        }
        self.assertEqual(
            vlm_settings_key(frame_dedup=4.0, **base),
            vlm_settings_key(frame_dedup=4.0, **base),
        )
        # A different duplicate-frame threshold changes which frames are sent,
        # so it must invalidate the VLM cache.
        self.assertNotEqual(
            vlm_settings_key(frame_dedup=0.0, **base),
            vlm_settings_key(frame_dedup=4.0, **base),
        )

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

    def test_split_text_english_prefers_sentences_and_balances_lines(self):
        text = "Mist drifts over the gorge. Sunlight breaks across the stone wall."
        pieces = _split_text(text, 42)
        self.assertEqual(
            pieces,
            ["Mist drifts over the gorge.", "Sunlight breaks across the stone wall."],
        )
        # Single-letter words like "a" or "I" must never be glued without a space.
        short_word_text = "Morning light climbs above a ridge as I walk."
        short_pieces = _split_text(short_word_text, 28)
        self.assertTrue(all(len(piece) <= 28 for piece in short_pieces))
        self.assertEqual(" ".join(short_pieces).split(), short_word_text.split())

    def test_split_for_tts_preserves_english_words(self):
        paragraph = (
            "Morning mist lifts from the river as sunlight breaks over the canyon walls. "
            "At the edge of the gorge, the water drops with a steady roar that you feel "
            "before you hear. Mist drifts across the stone railing and catches the late "
            "afternoon light before the wind carries it downstream."
        )
        chunks = _split_for_tts(paragraph, primary_language="en")
        self.assertGreaterEqual(len(chunks), 2)
        self.assertTrue(all(len(chunk) <= 250 for chunk in chunks))
        self.assertEqual(" ".join(chunks).split(), paragraph.split())

    def test_write_ass_english_uses_latin_font_hint(self):
        settings = Settings(content_language="en")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "en.ass"
            write_ass([Cue(0.0, 2.0, "Hello world.")], path, width=1920, height=1080, settings=settings)
            text = path.read_text(encoding="utf-8")
        self.assertIn("Default,Helvetica Neue,48,", text)

    def test_subtitle_track_language_follows_content_language(self):
        self.assertEqual(subtitle_track_language(Settings(content_language="zh")), "chi")
        self.assertEqual(subtitle_track_language(Settings(content_language="en")), "eng")

    def test_clean_text_english_preserves_closing_quotes(self):
        self.assertEqual(_clean_text('He said, "Hold on."', "en"), 'He said, "Hold on."')
        self.assertEqual(_clean_text("Mist rises over the water", "en"), "Mist rises over the water.")

    def test_kokoro_validates_voice_before_segment_retries(self):
        settings = Settings(content_language="en", voice="zf_xiaoxiao")
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError) as ctx:
            synthesize_segments(
                [NarrationSegment(id=1, scene_ids=[1], text="Hello.")],
                Path(tmp),
                settings,
                retries=2,
            )
        self.assertIn("zf_xiaoxiao", str(ctx.exception))

    def test_kokoro_reports_missing_english_g2p_dependencies(self):
        engine = KokoroEngine(Settings(content_language="en"))
        profile = engine.language()
        with patch(
            "reachreel.engines.tts.check_tts_dependencies",
            return_value=(False, "missing en_core_web_sm"),
        ), self.assertRaises(RuntimeError) as ctx:
            engine._ensure_g2p(profile)
        self.assertIn("en_core_web_sm", str(ctx.exception))

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

    def test_mix_filter_graph_narration_only(self):
        settings = Settings()
        graph = mix_filter_graph(
            duration=10.0, has_ambient=False, has_bgm=False, settings=settings
        )
        self.assertEqual(len(graph), 2)
        self.assertIn("channel_layouts=stereo[nar]", graph[0])
        self.assertEqual(graph[-1], "[nar]anull[premix]")
        self.assertNotIn("sidechaincompress", ";".join(graph))

    def test_mix_filter_graph_ambient_only(self):
        settings = Settings()
        graph = mix_filter_graph(
            duration=10.0, has_ambient=True, has_bgm=False, settings=settings
        )
        joined = ";".join(graph)
        self.assertIn("asplit=2[nar][sc_amb]", joined)
        self.assertIn("volume=0.160[amb]", joined)
        self.assertIn("[amb][sc_amb]sidechaincompress=", joined)
        self.assertNotIn("sc_bgm", joined)
        self.assertIn("[nar][amb_duck]amix=inputs=2:duration=longest:normalize=0[premix]", joined)

    def test_mix_filter_graph_bgm_only(self):
        settings = Settings()
        settings.bgm_volume = 0.2
        graph = mix_filter_graph(
            duration=10.0, has_ambient=False, has_bgm=True, settings=settings
        )
        joined = ";".join(graph)
        self.assertIn("asplit=2[nar][sc_bgm]", joined)
        self.assertIn("volume=0.200", joined)
        self.assertIn("[2:a]aformat=", joined)
        self.assertNotIn("sc_amb", joined)
        self.assertIn("[nar][bgm_duck]amix=inputs=2", joined)

    def test_mix_filter_graph_ambient_and_bgm(self):
        settings = Settings()
        settings.ambient_duck_db = -18.0
        settings.bgm_duck_db = -24.0
        graph = mix_filter_graph(
            duration=10.0, has_ambient=True, has_bgm=True, settings=settings
        )
        joined = ";".join(graph)
        self.assertIn("asplit=3[nar][sc_amb][sc_bgm]", joined)
        self.assertIn("ratio=9.0", joined)  # ambient 18 dB / 2 db-per-ratio
        self.assertIn("ratio=12.0", joined)  # bgm 24 dB / 2 db-per-ratio
        self.assertIn("amix=inputs=3:duration=longest:normalize=0[premix]", joined)
        self.assertTrue(graph[-1].endswith("[premix]"))

    def test_mix_filter_graph_skips_zero_volume_ambient(self):
        settings = Settings()
        settings.ambient_volume = 0.0
        graph = mix_filter_graph(
            duration=10.0, has_ambient=True, has_bgm=False, settings=settings
        )
        self.assertNotIn("sidechaincompress", ";".join(graph))
        self.assertEqual(graph[-1], "[nar]anull[premix]")

    def test_mix_filter_graph_clamps_duck_ratios(self):
        settings = Settings()
        settings.ambient_duck_db = -1.0  # below the 2.0 floor
        settings.bgm_duck_db = -400.0  # above the 20.0 ceiling
        joined = ";".join(
            mix_filter_graph(duration=5.0, has_ambient=True, has_bgm=True, settings=settings)
        )
        self.assertIn("ratio=2.0", joined)
        self.assertIn("ratio=20.0", joined)

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

    def test_style_presets_custom_override_and_oral_polish(self):
        self.assertEqual(Settings.load(overrides={"style_preset": "spoken"}).style_preset, "oral")
        self.assertEqual(Settings.load(overrides={"style_preset": "written"}).style_preset, "literary")
        self.assertEqual(Settings.load(overrides={"style_preset": "unknown"}).style_preset, "oral")

        custom = Settings(style_preset="oral", style="东北老铁唠嗑风格")
        self.assertEqual(custom.resolved_style(), "东北老铁唠嗑风格")
        self.assertIn("指定风格", custom.resolved_style_guidance())

        scenes = [Scene(id=1, start=0.0, end=8.0, summary="小孩在雪地里滑行")]

        class FakeClient:
            def __init__(self):
                self.prompts: list[str] = []

            def chat(self, messages, **_kwargs):
                self.prompts.append(messages[0]["content"])
                if len(self.prompts) == 1:
                    return '[{"scene_ids":[1],"text":"少年踩雪而行，如画中人。"}]'
                return '[{"scene_ids":[1],"text":"小朋友刚踏上雪道，脚底下还有点打滑。"}]'

        # Default oral preset runs the spoken polish pass.
        oral_client = FakeClient()
        segs = generate_script(
            scenes,
            duration=15.0,
            client=oral_client,
            style="",
            style_preset="oral",
            oral_polish=True,
        )
        self.assertEqual(len(oral_client.prompts), 2)
        self.assertIn("口语", oral_client.prompts[0])
        self.assertEqual(segs[0].text, "小朋友刚踏上雪道，脚底下还有点打滑。")

        # Literary preset skips the oral polish pass.
        lit_client = FakeClient()
        segs_lit = generate_script(
            scenes,
            duration=15.0,
            client=lit_client,
            style="",
            style_preset="literary",
            oral_polish=True,
        )
        self.assertEqual(len(lit_client.prompts), 1)
        self.assertEqual(segs_lit[0].text, "少年踩雪而行，如画中人。")

        # Custom style also skips automatic oral rewriting.
        custom_client = FakeClient()
        generate_script(
            scenes,
            duration=15.0,
            client=custom_client,
            style="文言游记",
            style_preset="oral",
            oral_polish=True,
        )
        self.assertEqual(len(custom_client.prompts), 1)
        self.assertIn("文言游记", custom_client.prompts[0])

    def test_normalise_segments_preserves_optional_tts_overrides(self):
        scenes = [Scene(id=1, start=0.0, end=5.0)]
        segments = _normalise_segments(
            [
                {
                    "scene_ids": [1],
                    "text": "这一下太刺激了！",
                    "voice": "Vivian",
                    "instruct": "语气兴奋一点",
                    "speed": 1.1,
                }
            ],
            scenes,
            "zh",
        )
        self.assertEqual(segments[0].voice, "Vivian")
        self.assertEqual(segments[0].instruct, "语气兴奋一点")
        self.assertAlmostEqual(segments[0].speed, 1.1)

    def test_tts_table_in_config_and_ref_audio_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.toml"
            ref = Path(tmp) / "voices" / "me.wav"
            ref.parent.mkdir(parents=True)
            ref.write_bytes(b"RIFF" + b"\x00" * 40)
            cfg.write_text(
                '[tts]\n'
                'engine = "qwen3"\n'
                "speed = 0.92\n"
                'instruct = "轻松聊天感"\n'
                'ref_audio = "voices/me.wav"\n'
                'ref_text = "你好，这是我的声音。"\n'
                "temperature = 0.75\n"
                "top_p = 0.9\n"
                "top_k = 40\n"
                "repetition_penalty = 1.1\n"
                "seed = 42\n"
                "chunk_pause = 0.2\n"
                "pitch = -1.5\n"
                "volume = 1.15\n",
                encoding="utf-8",
            )
            settings = Settings.load(cfg)
        self.assertEqual(settings.tts_engine, "qwen3")
        self.assertAlmostEqual(settings.tts_speed, 0.92)
        self.assertEqual(settings.tts_instruct, "轻松聊天感")
        self.assertEqual(Path(settings.tts_ref_audio).resolve(), ref.resolve())
        self.assertEqual(settings.tts_ref_text, "你好，这是我的声音。")
        self.assertAlmostEqual(settings.tts_temperature, 0.75)
        self.assertAlmostEqual(settings.tts_top_p, 0.9)
        self.assertEqual(settings.tts_top_k, 40)
        self.assertAlmostEqual(settings.tts_repetition_penalty, 1.1)
        self.assertEqual(settings.tts_seed, 42)
        self.assertAlmostEqual(settings.tts_chunk_pause_seconds, 0.2)
        self.assertAlmostEqual(settings.tts_pitch, -1.5)
        self.assertAlmostEqual(settings.tts_volume, 1.15)
        self.assertIn("Base", settings.resolved_qwen3_model())
        self.assertEqual(settings.resolved_voice(), "")

    def test_tts_audio_filter_chain_and_cache_key(self):
        self.assertIsNone(_tts_audio_filter_chain())
        chain = _tts_audio_filter_chain(speed=1.1, pitch=-2.0, volume=1.2)
        self.assertIn("aresample=24000", chain)
        self.assertIn("asetrate=", chain)
        self.assertIn("atempo=", chain)
        self.assertIn("volume=1.2000", chain)

        base = Settings(tts_engine="qwen3", tts_instruct="自然放松")
        k1 = tts_cache_key(base, "你好", speed=1.0, voice="Serena", instruct="自然放松")
        k2 = tts_cache_key(base, "你好", speed=1.0, voice="Serena", instruct="特别兴奋")
        base_pitch = Settings(tts_engine="qwen3", tts_instruct="自然放松", tts_pitch=-1.0)
        k3 = tts_cache_key(base_pitch, "你好", speed=1.0, voice="Serena", instruct="自然放松")
        self.assertNotEqual(k1, k2)
        self.assertNotEqual(k1, k3)

    def test_qwen3_engine_routing_and_validation(self):
        self.assertIsInstance(make_engine(Settings(tts_engine="qwen3")), Qwen3TTSEngine)

        with self.assertRaises(ValueError):
            validate_ref_audio("bad\x00path.wav")
        with self.assertRaises(FileNotFoundError):
            validate_ref_audio("/nonexistent/sample.wav")
        with tempfile.TemporaryDirectory() as tmp:
            bad_ext = Path(tmp) / "note.txt"
            bad_ext.write_text("hi", encoding="utf-8")
            with self.assertRaises(ValueError):
                validate_ref_audio(str(bad_ext))

        # Mocked model generation for custom_voice, voice_design, and base cloning.
        import types

        import numpy as np

        calls: list[tuple[str, dict]] = []

        class FakeConfig:
            def __init__(self, model_type):
                self.tts_model_type = model_type

        class FakeModel:
            def __init__(self, model_type):
                self.config = FakeConfig(model_type)
                self.supported_speakers = ["Serena", "Vivian", "Ryan"]

            def generate(self, text, **kwargs):
                calls.append((text, kwargs))
                yield types.SimpleNamespace(
                    audio=np.zeros(2400, dtype=np.float32),
                    sample_rate=24000,
                )

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.wav"
            settings = Settings(
                tts_engine="qwen3",
                voice="Serena",
                tts_instruct="像朋友聊天",
                tts_temperature=0.7,
                tts_top_p=0.95,
                tts_top_k=30,
                tts_repetition_penalty=1.05,
            )
            engine = Qwen3TTSEngine(settings)
            engine._model = FakeModel("custom_voice")
            with patch("reachreel.engines.tts._finalize_tts_wav", return_value=0.5):
                dur = engine.synthesize("测试一下。", out, speed=1.05)
            self.assertEqual(dur, 0.5)
            self.assertEqual(calls[-1][1]["voice"], "Serena")
            self.assertEqual(calls[-1][1]["instruct"], "像朋友聊天")
            self.assertEqual(calls[-1][1]["lang_code"], "Chinese")
            self.assertAlmostEqual(calls[-1][1]["temperature"], 0.7)

            # VoiceDesign requires instruct.
            engine._model = FakeModel("voice_design")
            with self.assertRaises(RuntimeError):
                engine.synthesize("测试。", out, instruct="")

            # Base cloning requires ref_audio.
            engine._model = FakeModel("base")
            with self.assertRaises(RuntimeError):
                engine.synthesize("测试。", out)

            ref_wav = Path(tmp) / "ref.wav"
            ref_wav.write_bytes(b"RIFF" + b"\x00" * 40)
            engine.settings.tts_ref_audio = str(ref_wav)
            engine.settings.tts_ref_text = "参考文本"
            with patch("reachreel.engines.tts._finalize_tts_wav", return_value=0.5):
                engine.synthesize("克隆测试。", out)
            self.assertEqual(calls[-1][1]["ref_audio"], str(ref_wav.resolve()))
            self.assertEqual(calls[-1][1]["ref_text"], "参考文本")
            self.assertNotIn("voice", calls[-1][1])

    def test_synthesize_segments_forwards_per_segment_overrides(self):
        recorded: list[tuple[str, float, str | None, str | None]] = []

        class OverrideEngine:
            supports_segment_overrides = True

            def synthesize(self, text, out, *, speed=1.0, voice=None, instruct=None):
                recorded.append((text, speed, voice, instruct))
                out.write_bytes(b"0" * 2048)
                return 1.2

        segments = [
            NarrationSegment(
                id=1,
                scene_ids=[1],
                text="第一段",
                voice="Vivian",
                instruct="开心一点",
                speed=1.15,
            )
        ]
        with tempfile.TemporaryDirectory() as tmp, patch(
            "reachreel.engines.tts.make_engine", return_value=OverrideEngine()
        ), patch("reachreel.engines.tts._wav_duration", return_value=1.2):
            flags = synthesize_segments(
                segments,
                Path(tmp),
                Settings(tts_engine="qwen3", voice="Serena"),
                retries=0,
            )
        self.assertEqual(flags, [])
        self.assertEqual(recorded, [("第一段", 1.15, "Vivian", "开心一点")])

    def test_cli_parses_style_and_tts_options_and_voices_command(self):
        import io
        from contextlib import redirect_stdout

        from reachreel.cli.main import _overrides, build_parser, main

        parser = build_parser()
        args = parser.parse_args(
            [
                "run",
                "clip.mp4",
                "--style-preset",
                "casual",
                "--style",
                "朋友聊天",
                "--no-oral-polish",
                "--tts",
                "qwen3",
                "--voice",
                "Serena",
                "--tts-speed",
                "0.95",
                "--tts-instruct",
                "自然放松",
                "--tts-pitch",
                "-1.0",
                "--tts-seed",
                "7",
            ]
        )
        ov = _overrides(args)
        self.assertEqual(ov["style_preset"], "casual")
        self.assertEqual(ov["style"], "朋友聊天")
        self.assertFalse(ov["script_oral_polish"])
        self.assertEqual(ov["tts_engine"], "qwen3")
        self.assertEqual(ov["voice"], "Serena")
        self.assertAlmostEqual(ov["tts_speed"], 0.95)
        self.assertEqual(ov["tts_instruct"], "自然放松")
        self.assertAlmostEqual(ov["tts_pitch"], -1.0)
        self.assertEqual(ov["tts_seed"], 7)

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["voices", "--json"])
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertIn("oral", payload["style_presets"])
        self.assertIn("Serena", payload["voices"]["qwen3"]["zh-CN"])
        self.assertIn("zf_xiaoxiao", payload["voices"]["kokoro"]["zh-CN"])

        # BooleanOptionalAction supports re-enabling oral polish via CLI too.
        args_on = parser.parse_args(["script", "work", "--oral-polish"])
        self.assertTrue(_overrides(args_on)["script_oral_polish"])

    def test_segment_to_dict_omits_unset_overrides_and_workdir_context_resets_stale_fields(self):
        from reachreel.cli.main import _workdir_context, build_parser

        plain = NarrationSegment(id=1, scene_ids=[1], text="hi").to_dict()
        self.assertNotIn("speed", plain)
        self.assertNotIn("voice", plain)
        self.assertNotIn("instruct", plain)
        custom = NarrationSegment(
            id=1, scene_ids=[1], text="hi", voice="Vivian", instruct="warm", speed=1.1
        ).to_dict()
        self.assertEqual(custom["voice"], "Vivian")
        self.assertEqual(custom["instruct"], "warm")
        self.assertAlmostEqual(custom["speed"], 1.1)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "clip.mp4"
            video.write_bytes(b"0")
            workdir = root / "work"
            workdir.mkdir()
            (workdir / "manifest.json").write_text(
                json.dumps(
                    {
                        "video": {"path": str(video)},
                        "settings": {"tts_engine": "kokoro", "voice": "zf_xiaoxiao"},
                        "settings_snapshot": {
                            "tts_engine": "kokoro",
                            "voice": "zf_xiaoxiao",
                            "style": "旧的默认散文风格",
                        },
                    }
                ),
                encoding="utf-8",
            )
            parser = build_parser()
            # Switching engine to qwen3 without --voice clears stale Kokoro voice.
            tts_args = parser.parse_args(["tts", str(workdir), "--tts", "qwen3"])
            _, tts_settings, _ = _workdir_context(workdir, tts_args, parser)
            self.assertEqual(tts_settings.tts_engine, "qwen3")
            self.assertEqual(tts_settings.voice, "")
            self.assertEqual(tts_settings.resolved_voice(), "Serena")

            # Passing --style-preset without --style clears stale custom style from manifest.
            script_args = parser.parse_args(["script", str(workdir), "--style-preset", "oral"])
            _, script_settings, _ = _workdir_context(workdir, script_args, parser)
            self.assertEqual(script_settings.style_preset, "oral")
            self.assertEqual(script_settings.style, "")


if __name__ == "__main__":
    unittest.main()
