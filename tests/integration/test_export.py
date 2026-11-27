"""Exercise editorial exports through the installed CLI without model providers."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

from reachreel.core.util import atomic_write_json, ffprobe_json, read_json, run


@unittest.skipUnless(os.environ.get("REACHREEL_INTEGRATION") == "1", "set REACHREEL_INTEGRATION=1")
@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
class EditExportTest(unittest.TestCase):
    def frame_rate_fixture(self, workdir: Path, output_fps: float) -> list[str | Path]:
        source = workdir / "source.mp4"
        run(
            ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
             "testsrc2=size=160x90:rate=60:duration=3", "-c:v", "libx264", source],
            timeout=30,
        )
        run(
            ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
             "sine=sample_rate=48000:duration=4", "-c:a", "pcm_s16le",
             workdir / "narration.wav"],
            timeout=30,
        )
        (workdir / "subtitles.srt").write_text(
            "1\n00:00:00,100 --> 00:00:02,900\nEditable subtitle\n", encoding="utf-8"
        )
        atomic_write_json(
            workdir / "manifest.json",
            {"video": {"path": str(source)}, "render_settings": {"output_fps": output_fps}},
        )
        return [sys.executable, "-m", "reachreel", "--lang", "en", "export", workdir]

    def test_export_rate_conversion_preserves_the_picture_span(self) -> None:
        with tempfile.TemporaryDirectory(prefix="reachreel-export-rate-") as tmp:
            workdir = Path(tmp).resolve()
            cli = self.frame_rate_fixture(workdir, 30)
            for extension in ("mp4", "mov"):
                with self.subTest(format=extension):
                    out = workdir / extension
                    run([*cli, "--format", extension, "--out", out], timeout=30)
                    media = ffprobe_json(out / f"video.{extension}")
                    picture = media["streams"][0]
                    self.assertEqual(picture["avg_frame_rate"], "30/1")
                    self.assertEqual(picture["nb_frames"], "90")
                    self.assertEqual(Fraction(picture["duration"]), 3)
                    self.assertEqual(Fraction(media["format"]["duration"]), 3)
                    xml = ET.parse(out / "timeline.xml")
                    self.assertEqual(xml.findtext("./sequence/duration"), "90")
                    fcp = ET.parse(out / "timeline.fcpxml")
                    self.assertEqual(fcp.find(".//sequence").get("duration"), "3s")

    def test_export_rejects_an_unsupported_fractional_rate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="reachreel-export-rate-") as tmp:
            workdir = Path(tmp).resolve()
            cli = self.frame_rate_fixture(workdir, 12.5)
            result = run(cli, timeout=30, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Unsupported editing frame rate: 12.5 fps", result.stderr)
            self.assertFalse((workdir / "edit").exists())

            run([*cli, "--fps", "30"], timeout=30)
            picture = ffprobe_json(workdir / "edit" / "video.mp4")["streams"][0]
            self.assertEqual(picture["avg_frame_rate"], "30/1")
            self.assertEqual(read_json(workdir / "manifest.json")["render_settings"]["output_fps"], 12.5)

    def test_export_creates_editable_media_and_timelines(self) -> None:
        with tempfile.TemporaryDirectory(prefix="reachreel-export-") as tmp:
            workdir = Path(tmp).resolve() / "Edit & review"
            workdir.mkdir()
            source = workdir / "source.mkv"
            run(
                ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                 "testsrc2=size=160x90:rate=30000/1001:duration=1", "-f", "lavfi", "-i",
                 "sine=duration=1", "-vf", "setsar=4/3", "-c:v", "libx264", "-c:a", "aac", source],
                timeout=30,
            )
            run(
                ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                 "sine=sample_rate=48000:duration=1.5", "-c:a", "pcm_s16le",
                 workdir / "narration.wav"],
                timeout=30,
            )
            run(
                ["ffmpeg", "-v", "error", "-i", workdir / "narration.wav", "-ac", "2",
                 workdir / "mix.wav"],
                timeout=30,
            )
            captions = b"1\n00:00:00,100 --> 00:00:00,900\nEditable subtitle\n"
            (workdir / "subtitles.srt").write_bytes(captions)
            manifest = workdir / "manifest.json"
            atomic_write_json(manifest, {"video": {"path": str(source)}})
            cli = [sys.executable, "-m", "reachreel", "--lang", "en", "export", workdir]

            for extension, codec, folder in (("mp4", "h264", "edit"), ("mov", "prores", "mov")):
                with self.subTest(format=extension):
                    if extension == "mov":
                        (workdir / "mix.wav").unlink()
                    out = workdir / folder
                    options = [] if extension == "mp4" else ["--format", "mov", "--out", out]
                    result = run([*cli, *options], timeout=30, check=False)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(Path(result.stdout.strip()), out)
                    video = out / f"video.{extension}"
                    streams = ffprobe_json(video)["streams"]
                    self.assertEqual([stream["codec_type"] for stream in streams], ["video", "audio"])
                    self.assertEqual(streams[1]["codec_name"], "aac" if extension == "mp4" else "pcm_s16le")
                    self.assertEqual(streams[1]["channels"], 2 if extension == "mp4" else 1)
                    self.assertEqual(streams[0]["codec_name"], codec)
                    self.assertEqual(streams[0]["avg_frame_rate"], "30000/1001")
                    self.assertEqual(streams[0]["sample_aspect_ratio"], "1:1")
                    self.assertEqual((streams[0]["width"], streams[0]["height"]), (214, 90))
                    self.assertEqual((out / "subtitles.srt").read_bytes(), captions)
                    self.assertEqual(
                        (out / "narration.wav").read_bytes(), (workdir / "narration.wav").read_bytes()
                    )
                    inventory = read_json(out / "manifest.json")
                    self.assertEqual(inventory["video"], video.name)
                    if extension == "mp4":
                        self.assertEqual((out / "mix.wav").read_bytes(), (workdir / "mix.wav").read_bytes())
                        self.assertEqual(inventory["mix"], "mix.wav")
                    else:
                        self.assertFalse((out / "mix.wav").exists())
                        self.assertIsNone(inventory["mix"])

                    xml = ET.parse(out / "timeline.xml")
                    self.assertEqual(xml.getroot().tag, "xmeml")
                    self.assertEqual(xml.findtext("./sequence/rate/ntsc"), "TRUE")
                    self.assertEqual(xml.findtext("./sequence/rate/timebase"), "30")
                    self.assertEqual(xml.findtext("./sequence/duration"), streams[0]["nb_frames"])
                    self.assertEqual(xml.findtext(".//video/track/clipitem/file/pathurl"), video.as_uri())
                    self.assertEqual(xml.findtext(".//audio/track/clipitem/end"), streams[0]["nb_frames"])
                    self.assertEqual(
                        xml.findtext("./sequence/media/audio/track/clipitem/file/pathurl"),
                        (out / "narration.wav").as_uri(),
                    )

                    fcp = ET.parse(out / "timeline.fcpxml")
                    self.assertEqual(fcp.getroot().get("version"), "1.9")
                    self.assertEqual(fcp.find("./resources/format").get("frameDuration"), "1001/30000s")
                    self.assertEqual(
                        fcp.find("./resources/asset/media-rep").get("src"), video.as_uri()
                    )
                    self.assertEqual(fcp.find(".//spine/asset-clip/asset-clip").get("lane"), "-1")
                    self.assertEqual(fcp.find(".//spine/asset-clip").get("srcEnable"), "video")

                    duplicate = run([*cli, *options], timeout=30, check=False)
                    self.assertNotEqual(duplicate.returncode, 0)
                    self.assertIn("already exists", duplicate.stderr)
                    self.assertTrue(video.is_file())

            (workdir / "narration.wav").write_bytes(b"invalid WAV")
            broken = workdir / "broken"
            result = run([*cli, "--out", broken], timeout=30, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("narration.wav", result.stderr)
            self.assertFalse(broken.exists())
            self.assertEqual(list(workdir.glob(".reachreel-export-*")), [])

            (workdir / "subtitles.srt").unlink()
            (workdir / "subtitles.srt").symlink_to(Path(tmp) / "outside.srt")
            (Path(tmp) / "outside.srt").write_bytes(captions)
            rejected = workdir / "rejected"
            result = run([*cli, "--out", rejected], timeout=30, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("outside the work directory", result.stderr)
            self.assertFalse(rejected.exists())
            self.assertEqual(read_json(manifest)["video"]["path"], str(source))
