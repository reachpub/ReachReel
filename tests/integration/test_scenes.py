"""Opt-in fixtures for scene detection and audio-track probing.

Both tests only need FFmpeg: they build tiny clips with known cut points and
known audio layouts, then assert what ``detect_scenes`` / ``probe_video``
report. No Ollama, Whisper, Kokoro, or ``say``.

    REACHREEL_INTEGRATION=1 python -m unittest tests.integration.test_scenes -v
"""
from __future__ import annotations

import itertools
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from reachreel.core.config import Settings
from reachreel.core.util import run
from reachreel.domain.models import Scene
from reachreel.media.render import output_dimensions
from reachreel.media.scenes import detect_scenes, extract_frames, probe_video

ENABLED = os.environ.get("REACHREEL_INTEGRATION") == "1"

COLOR_CUTS = (2.0, 4.0)
CLIP_SECONDS = 6.0
# Hard cuts between solid colors are unambiguous; allow FFmpeg's keyframe
# rounding to move the reported timestamp.
CUT_TOLERANCE = 0.3


def _build_color_clip(path: Path) -> None:
    """Three 2-second solid-color segments, so the cuts are known exactly."""
    graph = (
        "color=c=red:s=320x180:r=24:d=2[a];"
        "color=c=blue:s=320x180:r=24:d=2[b];"
        "color=c=white:s=320x180:r=24:d=2[c];"
        "[a][b][c]concat=n=3:v=1:a=0[v]"
    )
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-filter_complex",
            graph,
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ]
    )


@unittest.skipUnless(
    ENABLED, "set REACHREEL_INTEGRATION=1 to run the scene detection fixtures"
)
@unittest.skipUnless(
    shutil.which("ffmpeg") and shutil.which("ffprobe"),
    "ffmpeg and ffprobe are required",
)
class SceneDetectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="reachreel-scenes-"))
        cls.video = cls.tmp / "切点 三色.mp4"
        _build_color_clip(cls.video)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_detects_known_cut_points(self):
        scenes = detect_scenes(self.video, CLIP_SECONDS)
        self.assertEqual(len(scenes), 3, [f"{s.start}-{s.end}" for s in scenes])

        self.assertAlmostEqual(scenes[0].start, 0.0, places=3)
        self.assertAlmostEqual(scenes[-1].end, CLIP_SECONDS, places=1)
        for scene, expected in zip(scenes[1:], COLOR_CUTS, strict=True):
            self.assertLessEqual(
                abs(scene.start - expected),
                CUT_TOLERANCE,
                f"cut reported at {scene.start}, expected about {expected}",
            )

        # Scenes must tile the source with no gaps or overlaps.
        for previous, current in itertools.pairwise(scenes):
            self.assertAlmostEqual(previous.end, current.start, places=3)
        self.assertEqual([scene.id for scene in scenes], [1, 2, 3])

    def test_min_len_merges_short_scenes(self):
        scenes = detect_scenes(self.video, CLIP_SECONDS, min_len=3.0)
        self.assertEqual(len(scenes), 1)
        self.assertAlmostEqual(scenes[0].start, 0.0, places=3)
        self.assertAlmostEqual(scenes[0].end, CLIP_SECONDS, places=1)

    def test_max_len_splits_long_scenes(self):
        scenes = detect_scenes(self.video, CLIP_SECONDS, max_len=1.0)
        self.assertGreaterEqual(len(scenes), 6)
        for previous, current in itertools.pairwise(scenes):
            self.assertAlmostEqual(previous.end, current.start, places=3)
        self.assertAlmostEqual(scenes[-1].end, CLIP_SECONDS, places=1)


@unittest.skipUnless(
    ENABLED, "set REACHREEL_INTEGRATION=1 to run the audio-layout fixtures"
)
@unittest.skipUnless(
    shutil.which("ffmpeg") and shutil.which("ffprobe"),
    "ffmpeg and ffprobe are required",
)
class AudioLayoutProbeTest(unittest.TestCase):
    """Real footage arrives without audio, as mono, and as 5.1 surround."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="reachreel-audio-"))
        video = ["-f", "lavfi", "-i", "testsrc2=size=160x90:rate=12:duration=1"]
        common = [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            *video,
        ]
        encode = ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-t", "1"]

        cls.no_audio = cls.tmp / "no audio.mp4"
        run([*common, *encode, str(cls.no_audio)])

        cls.mono = cls.tmp / "mono.mp4"
        run(
            [
                *common,
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=1",
                "-ac",
                "1",
                *encode,
                "-c:a",
                "aac",
                str(cls.mono),
            ]
        )

        cls.surround = cls.tmp / "surround.mp4"
        run(
            [
                *common,
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=1",
                "-af",
                "aformat=channel_layouts=5.1",
                "-ac",
                "6",
                *encode,
                "-c:a",
                "aac",
                str(cls.surround),
            ]
        )

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_clip_without_audio_track(self):
        info = probe_video(self.no_audio)
        self.assertFalse(info.has_audio)
        self.assertEqual(info.audio_channels, 0)
        self.assertEqual((info.width, info.height), (160, 90))

    def test_mono_clip_is_probed(self):
        info = probe_video(self.mono)
        self.assertTrue(info.has_audio)
        self.assertEqual(info.audio_channels, 1)

    def test_surround_clip_is_probed(self):
        info = probe_video(self.surround)
        self.assertTrue(info.has_audio)
        self.assertEqual(info.audio_channels, 6)


@unittest.skipUnless(
    ENABLED, "set REACHREEL_INTEGRATION=1 to run the keyframe dedup fixtures"
)
@unittest.skipUnless(
    shutil.which("ffmpeg") and shutil.which("ffprobe"),
    "ffmpeg and ffprobe are required",
)
class FrameDedupTest(unittest.TestCase):
    """Static shots must not send near-identical frames to the VLM."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="reachreel-dedup-"))
        encode = [
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
        ]
        cls.static = cls.tmp / "static.mp4"
        run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=red:s=320x180:r=24:d=6",
                *encode,
                str(cls.static),
            ]
        )
        cls.moving = cls.tmp / "moving.mp4"
        run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=320x180:rate=24:duration=6",
                *encode,
                str(cls.moving),
            ]
        )

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _extract(self, video: Path, name: str, **kwargs) -> list[str]:
        scene = Scene(id=1, start=0.0, end=6.0)
        frames_dir = self.tmp / name
        extract_frames(video, [scene], frames_dir, per_scene=3, **kwargs)
        for frame in scene.frames:
            self.assertTrue(Path(frame).is_file(), frame)
        on_disk = sorted(path.name for path in frames_dir.glob("*.jpg"))
        self.assertEqual(on_disk, sorted(Path(frame).name for frame in scene.frames))
        return scene.frames

    def test_static_shot_keeps_one_frame(self):
        self.assertEqual(len(self._extract(self.static, "static", dedup=4.0)), 1)

    def test_moving_shot_keeps_several_frames(self):
        self.assertGreater(len(self._extract(self.moving, "moving", dedup=4.0)), 1)

    def test_dedup_zero_keeps_every_frame(self):
        self.assertEqual(len(self._extract(self.static, "static-raw", dedup=0.0)), 3)


@unittest.skipUnless(
    ENABLED, "set REACHREEL_INTEGRATION=1 to run the rotation fixtures"
)
@unittest.skipUnless(
    shutil.which("ffmpeg") and shutil.which("ffprobe"),
    "ffmpeg and ffprobe are required",
)
class RotationProbeTest(unittest.TestCase):
    """Phone clips are coded landscape and rotated by metadata."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="reachreel-rotation-"))
        cls.base = cls.tmp / "base.mp4"
        run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=320x180:rate=12:duration=1",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                str(cls.base),
            ]
        )
        cls.rotated: dict[int, Path] = {}
        for degrees in (90, 180, 270):
            out = cls.tmp / f"rot{degrees}.mp4"
            run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-nostdin",
                    "-loglevel",
                    "error",
                    "-y",
                    "-display_rotation",
                    str(degrees),
                    "-i",
                    str(cls.base),
                    "-c",
                    "copy",
                    str(out),
                ]
            )
            cls.rotated[degrees] = out

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_unrotated_clip_keeps_dimensions(self):
        info = probe_video(self.base)
        self.assertEqual((info.coded_width, info.coded_height), (320, 180))
        self.assertEqual((info.width, info.height), (320, 180))
        self.assertEqual(info.rotation, 0.0)

    def test_quarter_turns_swap_display_dimensions(self):
        for degrees in (90, 270):
            with self.subTest(degrees=degrees):
                info = probe_video(self.rotated[degrees])
                self.assertEqual((info.coded_width, info.coded_height), (320, 180))
                self.assertEqual((info.width, info.height), (180, 320))
                self.assertNotEqual(info.rotation % 180, 0)

    def test_half_turn_keeps_dimensions(self):
        info = probe_video(self.rotated[180])
        self.assertEqual((info.width, info.height), (320, 180))
        self.assertEqual(info.rotation % 180, 0)

    def test_portrait_output_dimensions_use_display_size(self):
        settings = Settings()
        settings.output_height = 1080
        info = probe_video(self.rotated[90])
        self.assertEqual(
            output_dimensions(info.width, info.height, settings),
            (608, 1080),
        )


if __name__ == "__main__":
    unittest.main()
