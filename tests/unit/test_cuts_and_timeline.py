"""Unit tests for lightweight CutList, segment fine-tuning, and FFmpeg cut filter graphs."""
from __future__ import annotations

import array
import tempfile
import unittest
import wave
from pathlib import Path

from reachreel.core.config import Settings
from reachreel.domain.cuts import (
    CutClip,
    CutList,
    build_keep_cuts,
    build_remove_cuts,
    load_cuts,
    parse_ranges,
    parse_timestamp,
    save_cuts,
)
from reachreel.domain.models import NarrationSegment, QualityFlag, Scene
from reachreel.domain.timeline import build_narration_track, place_segments
from reachreel.engines.tts import synthesize_segments
from reachreel.media.audio import mix_filter_graph
from reachreel.media.render import video_cut_filter_graph


def _write_tone_wav(path: Path, seconds: float = 0.5, amplitude: int = 8000, rate: int = 48000) -> None:
    count = int(seconds * rate)
    samples = array.array("h", [amplitude if (i % 40) < 20 else -amplitude for i in range(count)])
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(rate)
        fh.writeframes(samples.tobytes())


def _wav_peak(path: Path) -> int:
    with wave.open(str(path), "rb") as fh:
        samples = array.array("h")
        samples.frombytes(fh.readframes(fh.getnframes()))
    return max((abs(v) for v in samples), default=0)


class CutListTest(unittest.TestCase):
    def test_parse_timestamp_and_ranges(self) -> None:
        self.assertEqual(parse_timestamp("12.5"), 12.5)
        self.assertEqual(parse_timestamp("01:05.5"), 65.5)
        self.assertEqual(parse_timestamp("1:02:03"), 3723.0)
        self.assertEqual(parse_ranges("0-2.5, 00:10-00:15.5"), [(0.0, 2.5), (10.0, 15.5)])
        with self.assertRaises(ValueError):
            parse_ranges("5-2")
        with self.assertRaises(ValueError):
            parse_ranges("invalid")

    def test_build_remove_cuts_and_mapping(self) -> None:
        # Remove 0-2s and 10-15s from a 20s video -> keeps 2-10s (8s) and 15-20s (5s) = 13s.
        cuts = build_remove_cuts([(0.0, 2.0), (10.0, 15.0)], source_duration=20.0)
        self.assertEqual(len(cuts.clips), 2)
        self.assertAlmostEqual(cuts.edited_duration, 13.0, places=3)
        self.assertFalse(cuts.is_identity())
        self.assertIsNone(cuts.source_to_output(1.0))
        self.assertEqual(cuts.source_to_output(4.0), 2.0)
        self.assertEqual(cuts.source_to_output(17.0), 10.0)
        self.assertEqual(cuts.output_to_source(2.0), 4.0)
        self.assertEqual(cuts.output_to_source(10.0), 17.0)
        self.assertEqual(cuts.output_interval_to_source(9.0, 11.0), (16.0, 18.0))

    def test_build_remove_cuts_all_footage_raises(self) -> None:
        with self.assertRaises(ValueError):
            build_remove_cuts([(0.0, 10.0)], source_duration=10.0)

    def test_project_scenes_and_persistence(self) -> None:
        scenes = [
            Scene(id=1, start=0.0, end=2.0, summary="Intro"),
            Scene(id=2, start=2.0, end=10.0, summary="Main"),
            Scene(id=3, start=10.0, end=15.0, summary="Cut"),
            Scene(id=4, start=15.0, end=20.0, summary="Outro"),
        ]
        cuts = build_keep_cuts([(2.0, 10.0), (15.0, 20.0)], source_duration=20.0)
        projected, dropped = cuts.project_scenes(scenes)
        self.assertEqual(dropped, {1, 3})
        self.assertEqual([(s.id, s.start, s.end) for s in projected], [(2, 0.0, 8.0), (4, 8.0, 13.0)])

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cuts.json"
            save_cuts(path, cuts)
            loaded = load_cuts(path)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.edited_duration, 13.0)
            self.assertEqual(len(loaded.clips), 2)


class SegmentFineTuningTest(unittest.TestCase):
    def test_segment_serialization_omits_defaults(self) -> None:
        default_seg = NarrationSegment(id=1, scene_ids=[1], text="Hello")
        d = default_seg.to_dict()
        for key in ("start_override", "gain_db", "audio_override", "enabled"):
            self.assertNotIn(key, d)

        tuned_seg = NarrationSegment(
            id=2,
            scene_ids=[2],
            text="World",
            start_override=5.25,
            gain_db=3.0,
            audio_override="imports/seg_002.wav",
            enabled=False,
        )
        d2 = tuned_seg.to_dict()
        self.assertEqual(d2["start_override"], 5.25)
        self.assertEqual(d2["gain_db"], 3.0)
        self.assertEqual(d2["audio_override"], "imports/seg_002.wav")
        self.assertFalse(d2["enabled"])

    def test_place_segments_respects_start_override_disabled_and_overlap_flag(self) -> None:
        scenes = [
            Scene(id=1, start=0.0, end=5.0),
            Scene(id=2, start=5.0, end=10.0),
            Scene(id=3, start=10.0, end=15.0),
        ]
        segs = [
            NarrationSegment(id=1, scene_ids=[1], text="A", duration=3.0, start_override=2.0),
            NarrationSegment(id=2, scene_ids=[2], text="B", duration=2.0, enabled=False),
            NarrationSegment(id=3, scene_ids=[3], text="C", duration=2.0, start_override=4.0),
        ]
        flags: list[QualityFlag] = []
        place_segments(segs, scenes, 15.0, Settings(), estimate_only=True, flags=flags)
        self.assertEqual((segs[0].start, segs[0].end), (2.0, 5.0))
        self.assertEqual((segs[1].start, segs[1].end), (0.0, 0.0))
        # Locked segment 3 is NOT shifted even though it overlaps segment 1; a warning flag is added.
        self.assertEqual((segs[2].start, segs[2].end), (4.0, 6.0))
        self.assertTrue(any(f.code == "timeline.segment_overlap" for f in flags))

    def test_build_narration_track_applies_gain_and_skips_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wav1 = root / "s1.wav"
            wav2 = root / "s2.wav"
            _write_tone_wav(wav1, seconds=0.4, amplitude=4000)
            _write_tone_wav(wav2, seconds=0.4, amplitude=4000)

            base_out = root / "base.wav"
            louder_out = root / "louder.wav"
            disabled_out = root / "disabled.wav"

            seg_base = NarrationSegment(
                id=1, scene_ids=[1], text="A", audio_path=str(wav1), duration=0.4, start=0.2, end=0.6
            )
            seg_louder = NarrationSegment(
                id=1, scene_ids=[1], text="A", audio_path=str(wav1), duration=0.4, start=0.2, end=0.6, gain_db=6.0
            )
            seg_disabled = NarrationSegment(
                id=2, scene_ids=[1], text="B", audio_path=str(wav2), duration=0.4, start=0.2, end=0.6, enabled=False
            )
            build_narration_track([seg_base], 1.5, base_out)
            build_narration_track([seg_louder], 1.5, louder_out)
            build_narration_track([seg_disabled], 1.5, disabled_out)

            base_peak = _wav_peak(base_out)
            louder_peak = _wav_peak(louder_out)
            self.assertGreater(louder_peak, int(base_peak * 1.8))
            self.assertEqual(_wav_peak(disabled_out), 0)

    def test_synthesize_segments_uses_audio_override_and_blocks_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp) / "job"
            audio_dir = workdir / "tts"
            override_wav = workdir / "imports" / "seg_001.wav"
            _write_tone_wav(override_wav, seconds=0.75)

            segs = [
                NarrationSegment(id=1, scene_ids=[1], text="Hi", audio_override="imports/seg_001.wav"),
                NarrationSegment(id=2, scene_ids=[2], text="Skip", enabled=False),
            ]
            # Does not even touch TTS engine since segment 1 uses audio_override and segment 2 is disabled.
            flags = synthesize_segments(
                segs, audio_dir, Settings(tts_engine="kokoro"), workdir=workdir
            )
            self.assertEqual(flags, [])
            self.assertEqual(Path(segs[0].audio_path).resolve(), override_wav.resolve())
            self.assertAlmostEqual(segs[0].duration, 0.75, places=2)
            self.assertEqual(segs[1].audio_path, "")

            # Path traversal outside workdir must raise.
            escape_wav = Path(tmp) / "outside.wav"
            _write_tone_wav(escape_wav, seconds=0.3)
            bad = [NarrationSegment(id=1, scene_ids=[1], text="Hi", audio_override="../outside.wav")]
            with self.assertRaises(RuntimeError):
                synthesize_segments(bad, audio_dir, Settings(), workdir=workdir)


class CutFilterGraphsTest(unittest.TestCase):
    def test_mix_filter_graph_with_cuts(self) -> None:
        single = CutList(clips=[CutClip(1, 2.0, 8.0)], source_duration=10.0)
        graph_single = ";".join(
            mix_filter_graph(
                duration=6.0, has_ambient=True, has_bgm=False, settings=Settings(), cuts=single
            )
        )
        self.assertIn("[0:a]atrim=start=2.000:end=8.000,asetpts=PTS-STARTPTS[amb_src]", graph_single)
        self.assertIn("[amb_src]aformat=", graph_single)

        multi = CutList(clips=[CutClip(1, 1.0, 4.0), CutClip(2, 6.0, 9.0)], source_duration=10.0)
        graph_multi = ";".join(
            mix_filter_graph(
                duration=6.0, has_ambient=True, has_bgm=True, settings=Settings(), cuts=multi
            )
        )
        self.assertIn("[0:a]asplit=2[amb_s0][amb_s1]", graph_multi)
        self.assertIn("concat=n=2:v=0:a=1[amb_src]", graph_multi)

    def test_video_cut_filter_graph(self) -> None:
        single = CutList(clips=[CutClip(1, 1.5, 5.5)], source_duration=10.0)
        g1 = video_cut_filter_graph(single, ["scale=-2:1080:flags=lanczos"])
        self.assertEqual(
            g1, "[0:v]trim=start=1.500:end=5.500,setpts=PTS-STARTPTS,scale=-2:1080:flags=lanczos[vout]"
        )

        multi = CutList(clips=[CutClip(1, 0.0, 3.0), CutClip(2, 5.0, 8.0)], source_duration=10.0)
        g2 = video_cut_filter_graph(multi, ["fps=30"])
        self.assertIn("[0:v]split=2[v_s0][v_s1]", g2)
        self.assertIn("[v_c0][v_c1]concat=n=2:v=1:a=0[v_cat]", g2)
        self.assertIn("[v_cat]fps=30[vout]", g2)


if __name__ == "__main__":
    unittest.main()
