"""End-to-end FFmpeg integration tests for reachreel tune, import, CutList, and XML round-trip."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from reachreel.core.util import (
    atomic_write_json,
    ffmpeg_bin,
    ffprobe_duration,
    find_ffmpeg,
    find_ffprobe,
    read_json,
    run,
    sha1_file,
)


def _make_video_with_audio(path: Path, duration: float = 6.0) -> None:
    run(
        [
            ffmpeg_bin(),
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=320x180:rate=30:duration={duration:g}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=220:sample_rate=48000:duration={duration:g}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ],
        timeout=60,
    )


def _make_wav(path: Path, duration: float = 1.0, freq: int = 440) -> None:
    run(
        [
            ffmpeg_bin(),
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={freq}:sample_rate=48000:duration={duration:g}",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        timeout=30,
    )


def _cli(*args: str | Path) -> list[str | Path]:
    return [sys.executable, "-m", "reachreel", "--lang", "en", *args]


@unittest.skipUnless(os.environ.get("REACHREEL_INTEGRATION") == "1", "set REACHREEL_INTEGRATION=1")
@unittest.skipUnless(find_ffmpeg() and find_ffprobe(), "FFmpeg is required")
class FineTuningIntegrationTest(unittest.TestCase):
    def _seed_job(self, root: Path) -> tuple[Path, Path]:
        video = root / "source.mp4"
        _make_video_with_audio(video, duration=6.0)
        workdir = root / "job"
        workdir.mkdir()
        (workdir / "imports").mkdir()
        seg1_wav = workdir / "imports" / "seg_001.wav"
        seg2_wav = workdir / "imports" / "seg_002.wav"
        _make_wav(seg1_wav, duration=1.0, freq=440)
        _make_wav(seg2_wav, duration=1.0, freq=660)

        atomic_write_json(
            workdir / "scenes.json",
            {
                "video": {
                    "path": str(video),
                    "duration": 6.0,
                    "width": 320,
                    "height": 180,
                    "fps": 30.0,
                    "has_audio": True,
                    "audio_channels": 1,
                    "audio_sample_rate": 48000,
                },
                "scenes": [
                    {"id": 1, "start": 0.0, "end": 3.0, "summary": "First shot"},
                    {"id": 2, "start": 3.0, "end": 6.0, "summary": "Second shot"},
                ],
            },
        )
        atomic_write_json(
            workdir / "script.json",
            {
                "content_language": "en",
                "segments": [
                    {
                        "id": 1,
                        "scene_ids": [1],
                        "text": "First line.",
                        "audio_override": "imports/seg_001.wav",
                    },
                    {
                        "id": 2,
                        "scene_ids": [2],
                        "text": "Second line.",
                        "audio_override": "imports/seg_002.wav",
                    },
                ],
            },
        )
        atomic_write_json(
            workdir / "manifest.json",
            {
                "schema_version": 2,
                "video": {
                    "path": str(video),
                    "duration": 6.0,
                    "width": 320,
                    "height": 180,
                    "fps": 30.0,
                    "has_audio": True,
                },
                "settings": {"tts_engine": "say", "burned_subtitles": False},
                "settings_snapshot": {
                    "content_language": "en",
                    "tts_engine": "say",
                    "burn_subtitles": False,
                },
            },
        )
        # Build full job without any external model (audio_override + --asr none + --no-burn).
        run(
            _cli(
                "resume",
                workdir,
                "--asr",
                "none",
                "--no-bgm",
                "--no-burn",
                "--no-qa",
            ),
            timeout=120,
        )
        return video, workdir

    def test_import_subtitles_and_segment_audio_and_cut_and_nle_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory(prefix="reachreel-finetune-") as tmp:
            root = Path(tmp).resolve()
            _, workdir = self._seed_job(root)

            self.assertAlmostEqual(ffprobe_duration(workdir / "final.mp4"), 6.0, delta=0.3)
            nar_hash = sha1_file(workdir / "narration.wav")
            mix_hash = sha1_file(workdir / "mix.wav")

            # 1. Import edited subtitles -> only render + qa run; narration & mix stay untouched.
            edited_srt = root / "edited.srt"
            edited_srt.write_text(
                "1\n00:00:00,400 --> 00:00:01,400\nImported line one.\n\n"
                "2\n00:00:03,000 --> 00:00:04,000\nImported line two.\n",
                encoding="utf-8",
            )
            proc = run(
                _cli("import", workdir, "--subtitles", edited_srt, "--resume", "--asr", "none", "--no-qa", "--json"),
                timeout=120,
            )
            payload = json.loads(proc.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["resume"]["stages_executed"], ["render"])
            self.assertEqual(sha1_file(workdir / "narration.wav"), nar_hash)
            self.assertEqual(sha1_file(workdir / "mix.wav"), mix_hash)

            # 2. Import replacement audio for segment 1 (different duration).
            custom_seg1 = root / "custom1.wav"
            _make_wav(custom_seg1, duration=1.4, freq=520)
            proc2 = run(
                _cli(
                    "import",
                    workdir,
                    "--segment-audio",
                    f"1={custom_seg1}",
                    "--resume",
                    "--asr",
                    "none",
                    "--no-qa",
                    "--json",
                ),
                timeout=120,
            )
            payload2 = json.loads(proc2.stdout)
            self.assertTrue(payload2["ok"])
            self.assertNotEqual(sha1_file(workdir / "narration.wav"), nar_hash)

            # 3. Cut 1.0-3.0s (remove 2.0s) via reachreel tune --cut --apply -> output becomes ~4.0s.
            proc3 = run(
                _cli("tune", workdir, "--cut", "1.0-3.0", "--apply", "--asr", "none", "--json"),
                timeout=120,
            )
            payload3 = json.loads(proc3.stdout)
            self.assertTrue(payload3["ok"])
            self.assertAlmostEqual(ffprobe_duration(workdir / "final.mp4"), 4.0, delta=0.35)
            qa_data = read_json(workdir / "qa.json")
            dur_check = next(c for c in qa_data["checks"] if c["id"] == "duration_match")
            self.assertEqual(dur_check["status"], "pass")
            self.assertTrue(dur_check["details"]["cuts_applied"])

            # 4. Export with --narration-mode segments, move segment 2 in XML, import back!
            edit_dir = root / "edit-segs"
            run(
                _cli("export", workdir, "--narration-mode", "segments", "--out", edit_dir),
                timeout=120,
            )
            xml_path = edit_dir / "timeline.xml"
            self.assertTrue((edit_dir / "segments" / "seg_001.wav").is_file())
            self.assertTrue((edit_dir / "segments" / "seg_002.wav").is_file())

            # Shift segment 2 start in timeline.xml to frame 60 (2.0s at 30fps).
            tree = ET.parse(xml_path)
            root_el = tree.getroot()
            seg2_clip = root_el.find(".//clipitem[@id='reachreel-seg-2']")
            self.assertIsNotNone(seg2_clip)
            assert seg2_clip is not None
            seg2_clip.find("start").text = "60"
            seg2_clip.find("end").text = "90"
            edited_xml = root / "edited_timeline.xml"
            tree.write(edited_xml, encoding="utf-8", xml_declaration=True)

            proc4 = run(
                _cli(
                    "import",
                    workdir,
                    "--timeline",
                    edited_xml,
                    "--resume",
                    "--asr",
                    "none",
                    "--no-qa",
                    "--json",
                ),
                timeout=120,
            )
            payload4 = json.loads(proc4.stdout)
            self.assertTrue(payload4["ok"])
            tl = read_json(workdir / "timeline.json")
            seg2 = next(s for s in tl["segments"] if s["id"] == 2)
            self.assertAlmostEqual(seg2["start"], 2.0, places=2)


if __name__ == "__main__":
    unittest.main()
