"""Unit tests for reachreel import, reachreel tune, and XML/FCPXML round-trip."""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from reachreel.cli.main import build_parser, main
from reachreel.core.config import Settings
from reachreel.core.util import atomic_write_json, read_json
from reachreel.domain.models import NarrationSegment
from reachreel.media.edit_export import (
    _AudioFormat,
    _SegmentClip,
    _Timeline,
    _VideoClip,
    _write_fcpxml,
    _write_xmeml,
)
from reachreel.media.edit_import import parse_timeline_xml
from reachreel.pipeline.orchestrator import MANIFEST_SCHEMA_VERSION, Pipeline, workdir_status


def _seed_workdir(root: Path) -> tuple[Path, Path]:
    video = root / "clip.mp4"
    video.write_bytes(b"fake-video-bytes")
    workdir = root / "job"
    workdir.mkdir()

    scenes_data = {
        "video": {
            "path": str(video),
            "duration": 20.0,
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
            "has_audio": True,
        },
        "scenes": [
            {"id": 1, "start": 0.0, "end": 5.0, "summary": "Opening"},
            {"id": 2, "start": 5.0, "end": 12.0, "summary": "Middle"},
            {"id": 3, "start": 12.0, "end": 20.0, "summary": "Ending"},
        ],
    }
    atomic_write_json(workdir / "scenes.json", scenes_data)

    script_data = {
        "content_language": "en",
        "segments": [
            {"id": 1, "scene_ids": [1], "text": "Opening line."},
            {"id": 2, "scene_ids": [2], "text": "Middle line."},
            {"id": 3, "scene_ids": [3], "text": "Ending line."},
        ],
    }
    atomic_write_json(workdir / "script.json", script_data)
    atomic_write_json(
        workdir / "timeline.json",
        {
            "segments": [
                {"id": 1, "scene_ids": [1], "text": "Opening line.", "audio_path": "tts/seg_001.wav", "duration": 2.0, "start": 0.4, "end": 2.4},
                {"id": 2, "scene_ids": [2], "text": "Middle line.", "audio_path": "tts/seg_002.wav", "duration": 2.0, "start": 5.0, "end": 7.0},
                {"id": 3, "scene_ids": [3], "text": "Ending line.", "audio_path": "tts/seg_003.wav", "duration": 2.0, "start": 12.0, "end": 14.0},
            ]
        },
    )
    (workdir / "narration.wav").write_bytes(b"fake-narration")
    (workdir / "subtitles.srt").write_text(
        "1\n00:00:00,400 --> 00:00:02,400\nOpening line.\n",
        encoding="utf-8",
    )
    (workdir / "mix.wav").write_bytes(b"fake-mix")
    (workdir / "final.mp4").write_bytes(b"fake-final")

    pipeline = Pipeline(video, Settings(content_language="en"), workdir=workdir, create_dir=False)
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "video": scenes_data["video"],
        "workdir": str(workdir),
        "output": str(workdir / "final.mp4"),
        "settings": {
            "vlm_model": "qwen3-vl:8b-instruct",
            "llm_model": "qwen3:30b-instruct",
            "tts_engine": "say",
            "voice": "Samantha",
            "whisper_model": "none",
            "burned_subtitles": False,
        },
        "settings_snapshot": {"content_language": "en", "tts_engine": "say"},
        "artifact_inputs": {
            "vlm": pipeline.stage_inputs.capture("vlm"),
            "tts": pipeline.stage_inputs.capture("tts"),
            "subtitles": pipeline.stage_inputs.capture("subtitles"),
            "mix": pipeline.stage_inputs.capture("mix"),
            "render": {
                **pipeline.stage_inputs.capture("render"),
                "output": pipeline.stage_inputs.rendered_key(),
            },
        },
        "qa": {"status": "pass", "summary": {"pass": 1, "warn": 0, "fail": 0}},
    }
    atomic_write_json(workdir / "manifest.json", manifest)
    atomic_write_json(
        workdir / "qa.json",
        {"summary": {"pass": 1, "warn": 0, "fail": 0}, "checks": []},
    )
    return video, workdir


class TimelineXmlRoundTripTest(unittest.TestCase):
    def _sample_timeline(self, root: Path) -> _Timeline:
        fmt = _AudioFormat(rate=48000, channels=1, depth=16)
        return _Timeline(
            video=root / "video.mp4",
            narration=root / "narration.wav",
            width=1920,
            height=1080,
            fps=Fraction(30),
            video_frames=600,
            sequence_frames=390,
            audio_frames=390,
            audio_duration=Fraction(13),
            narration_audio=fmt,
            embedded_audio=_AudioFormat(rate=48000, channels=2, depth=16),
            video_clips=(
                _VideoClip(timeline_start=0, timeline_end=240, source_in=60, source_out=300),
                _VideoClip(timeline_start=240, timeline_end=390, source_in=450, source_out=600),
            ),
            segment_clips=(
                _SegmentClip(
                    id=1,
                    name="seg_001.wav",
                    path=root / "segments" / "seg_001.wav",
                    start_frame=30,
                    frames=60,
                    duration=Fraction(2),
                    fmt=fmt,
                ),
                _SegmentClip(
                    id=3,
                    name="seg_003.wav",
                    path=root / "segments" / "seg_003.wav",
                    start_frame=270,
                    frames=60,
                    duration=Fraction(2),
                    fmt=fmt,
                ),
            ),
        )

    def test_xmeml_and_fcpxml_round_trip(self) -> None:
        existing = [
            NarrationSegment(id=1, scene_ids=[1], text="A"),
            NarrationSegment(id=2, scene_ids=[2], text="B"),
            NarrationSegment(id=3, scene_ids=[3], text="C"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tl = self._sample_timeline(root)
            xmeml_path = root / "timeline.xml"
            fcpxml_path = root / "timeline.fcpxml"
            _write_xmeml(xmeml_path, tl)
            _write_fcpxml(fcpxml_path, tl)

            for path, fmt_name in ((xmeml_path, "xmeml"), (fcpxml_path, "fcpxml")):
                plan = parse_timeline_xml(path, source_duration=20.0, existing_segments=existing)
                self.assertEqual(plan.source_format, fmt_name)
                self.assertTrue(plan.cuts_changed)
                self.assertIsNotNone(plan.cuts)
                assert plan.cuts is not None
                self.assertEqual(
                    [(c.start, c.end) for c in plan.cuts.clips],
                    [(2.0, 10.0), (15.0, 20.0)],
                )
                self.assertEqual(plan.segment_starts, {1: 1.0, 3: 9.0})
                self.assertEqual(plan.disabled_segment_ids, [2])
                self.assertEqual(plan.warnings, [])

    def test_unsupported_nle_features_emit_warnings(self) -> None:
        xml_text = """<?xml version="1.0" encoding="UTF-8"?>
<fcpxml version="1.9">
  <resources>
    <format id="r1" frameDuration="1/30s" width="1920" height="1080"/>
    <asset id="r2" name="video.mp4" start="0s" duration="10s" hasVideo="1" format="r1"/>
    <asset id="r3" name="seg_001.wav" start="0s" duration="2s" hasAudio="1"/>
  </resources>
  <event name="ReachReel"><project name="ReachReel">
    <sequence format="r1" duration="10s">
      <spine>
        <asset-clip ref="r2" name="video.mp4" offset="0s" start="0s" duration="5s">
          <asset-clip ref="r3" name="seg_001.wav" lane="-1" offset="1.5s" start="0s" duration="2s">
            <adjust-volume amount="+2.5dB"/>
          </asset-clip>
        </asset-clip>
        <transition name="Cross Dissolve" offset="4.5s" duration="1s"/>
        <asset-clip ref="r2" name="video.mp4" offset="5s" start="5s" duration="5s"/>
      </spine>
    </sequence>
  </project></event>
</fcpxml>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "edited.fcpxml"
            path.write_text(xml_text, encoding="utf-8")
            plan = parse_timeline_xml(
                path,
                source_duration=10.0,
                existing_segments=[NarrationSegment(id=1, scene_ids=[1], text="A")],
            )
            self.assertEqual(plan.segment_starts, {1: 1.5})
            self.assertEqual(plan.segment_gains, {1: 2.5})
            self.assertTrue(any("transition" in w.lower() for w in plan.warnings))

    def test_xmeml_uses_clip_fps_and_skips_disabled_video_clips(self) -> None:
        xml_text = """<?xml version="1.0" encoding="UTF-8"?>
<xmeml version="5">
  <sequence id="seq">
    <name>ReachReel</name>
    <duration>300</duration>
    <rate><timebase>30</timebase><ntsc>FALSE</ntsc></rate>
    <media>
      <video>
        <track>
          <clipitem id="v1">
            <name>video.mp4</name>
            <enabled>FALSE</enabled>
            <start>0</start><end>60</end><in>0</in><out>120</out>
            <rate><timebase>60</timebase><ntsc>FALSE</ntsc></rate>
          </clipitem>
          <clipitem id="v2">
            <name>video.mp4</name>
            <enabled>TRUE</enabled>
            <start>60</start><end>240</end><in>120</in><out>480</out>
            <rate><timebase>60</timebase><ntsc>FALSE</ntsc></rate>
          </clipitem>
        </track>
      </video>
    </media>
  </sequence>
</xmeml>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "edited.xml"
            path.write_text(xml_text, encoding="utf-8")
            plan = parse_timeline_xml(path, source_duration=10.0, existing_segments=[])
            self.assertTrue(plan.cuts_changed)
            assert plan.cuts is not None
            # Disabled clip v1 is skipped; v2 uses 60fps -> 120/60=2.0s to 480/60=8.0s.
            self.assertEqual([(c.start, c.end) for c in plan.cuts.clips], [(2.0, 8.0)])


class ImportAndTunePipelineTest(unittest.TestCase):
    def test_import_subtitles_marks_only_render_and_qa_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video, workdir = _seed_workdir(root)
            new_srt = root / "edited.srt"
            new_srt.write_text(
                "1\n00:00:00,500 --> 00:00:02,800\nPolished opening line.\n",
                encoding="utf-8",
            )
            pipe = Pipeline(video, Settings(content_language="en"), workdir=workdir, create_dir=False)
            res = pipe.import_artifacts(subtitles_file=new_srt)
            self.assertTrue(res["ok"])
            self.assertEqual(res["affected_stages"], ["render", "qa"])
            self.assertIn("Polished opening line.", (workdir / "subtitles.srt").read_text(encoding="utf-8"))

    def test_import_script_md_and_bgm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video, workdir = _seed_workdir(root)
            md = root / "script.md"
            md.write_text(
                "| # | Scenes | Start | End | Text | Evidence |\n"
                "|---|---|---|---|---|---|\n"
                "| 2 | 2 | 5.0 | 7.0 | Updated middle line! | Middle |\n",
                encoding="utf-8",
            )
            bgm = root / "calm.mp3"
            bgm.write_bytes(b"fake-mp3")
            pipe = Pipeline(video, Settings(content_language="en"), workdir=workdir, create_dir=False)
            res = pipe.import_artifacts(script_file=md, bgm_file=bgm)
            self.assertTrue(res["ok"])
            saved = read_json(workdir / "script.json")
            self.assertEqual(saved["segments"][1]["text"], "Updated middle line!")
            self.assertEqual(saved["segments"][0]["text"], "Opening line.")
            manifest = read_json(workdir / "manifest.json")
            self.assertTrue(str(manifest["bgm_source"]).endswith("bgm_calm.mp3"))
            self.assertIn("tts", res["affected_stages"])
            self.assertIn("mix", res["affected_stages"])

    def test_tune_segment_cuts_and_undo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video, workdir = _seed_workdir(root)
            pipe = Pipeline(video, Settings(content_language="en"), workdir=workdir, create_dir=False)

            res = pipe.tune(
                segment_id=2,
                text="Tuned middle line.",
                voice="Serena",
                speed=1.1,
                start=6.25,
                gain_db=2.0,
            )
            self.assertTrue(res["ok"])
            self.assertIn("tts", res["affected_stages"])
            saved = read_json(workdir / "script.json")
            seg2 = saved["segments"][1]
            self.assertEqual(seg2["text"], "Tuned middle line.")
            self.assertEqual(seg2["voice"], "Serena")
            self.assertEqual(seg2["speed"], 1.1)
            self.assertEqual(seg2["start_override"], 6.25)
            self.assertEqual(seg2["gain_db"], 2.0)

            # Cut out scene 1 (0-5s) -> segment 1 should be auto-disabled.
            res_cut = pipe.tune(cut_ranges=[(0.0, 5.0)])
            self.assertTrue((workdir / "cuts.json").is_file())
            self.assertTrue(any("Auto-disabled" in w for w in res_cut["warnings"]))
            saved_after_cut = read_json(workdir / "script.json")
            self.assertFalse(saved_after_cut["segments"][0]["enabled"])
            status = workdir_status(workdir)
            self.assertIsNotNone(status["cuts"])
            self.assertEqual(status["cuts"]["edited_duration"], 15.0)

            # Undo restores pre-cut state and removes cuts.json.
            res_undo = pipe.tune(undo=True)
            self.assertTrue(res_undo["ok"])
            self.assertFalse((workdir / "cuts.json").is_file())
            restored = read_json(workdir / "script.json")
            self.assertTrue(restored["segments"][0].get("enabled", True))
            self.assertEqual(restored["segments"][1]["text"], "Tuned middle line.")

    def test_cli_wiring_tune_import_resume_export(self) -> None:
        parser = build_parser()
        # resume now accepts subtitle layout options (zero-overlap global path).
        res_args = parser.parse_args(["resume", "output/job", "--subtitle-size", "52"])
        self.assertEqual(res_args.subtitle_size, 52)
        # export accepts --narration-mode segments.
        exp_args = parser.parse_args(["export", "output/job", "--narration-mode", "segments"])
        self.assertEqual(exp_args.narration_mode, "segments")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, workdir = _seed_workdir(root)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = main(
                    [
                        "tune",
                        str(workdir),
                        "--segment",
                        "1",
                        "--start",
                        "00:01.5",
                        "--gain-db",
                        "-2.0",
                        "--json",
                    ]
                )
            self.assertEqual(code, 0)
            payload = json.loads(buf.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["affected_stages"], ["tts", "subtitles", "mix", "render", "qa"])

            # Dry-run import via CLI.
            srt_file = root / "in.srt"
            srt_file.write_text("1\n00:00:00,200 --> 00:00:01,500\nCLI line\n", encoding="utf-8")
            buf2 = io.StringIO()
            with contextlib.redirect_stdout(buf2):
                code2 = main(
                    [
                        "import",
                        str(workdir),
                        "--subtitles",
                        str(srt_file),
                        "--dry-run",
                        "--json",
                    ]
                )
            self.assertEqual(code2, 0)
            payload2 = json.loads(buf2.getvalue())
            self.assertTrue(payload2["dry_run"])
            self.assertEqual(payload2["affected_stages"], ["render", "qa"])


if __name__ == "__main__":
    unittest.main()
