"""Opt-in integration test for the narration speed-up path.

``place_segments`` only calls FFmpeg (``atempo``) when the narration overruns the
clip and the segments actually own audio files. The unit suite covers the
placement maths; this fixture covers the real ``atempo`` rewrite.

    REACHREEL_INTEGRATION=1 python -m unittest tests.integration.test_timeline -v
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
import wave
from pathlib import Path

from reachreel.core.config import Settings
from reachreel.domain.models import NarrationSegment, Scene
from reachreel.domain.timeline import place_segments

ENABLED = os.environ.get("REACHREEL_INTEGRATION") == "1"

SAMPLE_RATE = 48000


def _write_tone(path: Path, seconds: float) -> None:
    """Write a short 16-bit mono WAV; content does not matter for ``atempo``."""
    import math

    frames = int(seconds * SAMPLE_RATE)
    samples = bytearray()
    for index in range(frames):
        value = int(12000 * math.sin(2 * math.pi * 220 * index / SAMPLE_RATE))
        samples += value.to_bytes(2, "little", signed=True)
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(SAMPLE_RATE)
        fh.writeframes(bytes(samples))


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as fh:
        return fh.getnframes() / float(fh.getframerate())


@unittest.skipUnless(
    ENABLED, "set REACHREEL_INTEGRATION=1 to run the timeline speed-up test"
)
@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is required")
class NarrationSpeedUpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="reachreel-timeline-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _segments(self, durations: tuple[float, ...]) -> list[NarrationSegment]:
        segments: list[NarrationSegment] = []
        for index, seconds in enumerate(durations, start=1):
            audio = self.tmp / f"seg{index}.wav"
            _write_tone(audio, seconds)
            segments.append(
                NarrationSegment(
                    id=index,
                    scene_ids=[1],
                    text="x" * 10,
                    duration=seconds,
                    audio_path=str(audio),
                )
            )
        return segments

    def test_overlong_narration_is_speed_up_to_fit(self):
        segments = self._segments((4.0, 4.0, 3.0))
        original_total = sum(segment.duration for segment in segments)
        settings = Settings()
        settings.max_speedup = 1.6

        place_segments(segments, [Scene(id=1, start=0.0, end=10.0)], 10.0, settings)

        total = sum(segment.duration for segment in segments)
        self.assertLess(total, original_total)
        for segment in segments:
            self.assertGreater(segment.tts_speed, 1.0)
            self.assertAlmostEqual(
                segment.duration,
                _wav_duration(Path(segment.audio_path)),
                places=3,
            )
            self.assertIn("_fit", Path(segment.audio_path).name)
        self.assertLessEqual(segments[-1].end, 10.0)

    def test_narration_that_fits_is_left_alone(self):
        segments = self._segments((1.0, 1.0))
        paths = [segment.audio_path for segment in segments]
        settings = Settings()

        place_segments(segments, [Scene(id=1, start=0.0, end=30.0)], 30.0, settings)

        self.assertEqual([segment.audio_path for segment in segments], paths)
        self.assertEqual([segment.tts_speed for segment in segments], [1.0, 1.0])


if __name__ == "__main__":
    unittest.main()
