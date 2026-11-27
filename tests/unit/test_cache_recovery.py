from __future__ import annotations

import shutil
import tempfile
import unittest
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch

from reachreel.core.config import Settings
from reachreel.domain.models import NarrationSegment, Scene
from reachreel.engines.tts import synthesize_segments, tts_cache_key
from reachreel.engines.vlm import analyze_scenes


class VlmCacheRecoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.local = self.root / "local.json"
        self.shared = self.root / "shared.json"
        self.frame = self.root / "frame.jpg"
        self.frame.write_bytes(b"first frame")

    def analyze(self, scenes: list[Scene], **overrides):
        options = {
            "base_url": "http://ollama:11434",
            "model": "vlm-model",
            "workers": 1,
            "cache_path": self.local,
            "shared_cache_path": self.shared,
        }
        return analyze_scenes(scenes, **(options | overrides))

    def test_retry_failed_scene_and_reuse_successful_scene(self) -> None:
        scenes = [
            Scene(1, 0, 1, frames=[str(self.frame)]),
            Scene(2, 1, 2, frames=[str(self.frame)]),
        ]
        results = [
            {"summary": "Scene one."},
            OSError("Temporary failure."),
            {"summary": "Scene two recovered."},
        ]
        with patch("reachreel.engines.vlm.analyze_scene", side_effect=results) as analyze:
            flags = self.analyze(scenes)
            self.assertEqual([flag.code for flag in flags], ["vlm.scene_failed"])
            self.assertEqual(self.analyze(scenes), [])
            self.assertEqual([call.args[1].id for call in analyze.call_args_list], [1, 2, 2])
        self.assertEqual([scene.summary for scene in scenes], ["Scene one.", "Scene two recovered."])

    def test_forced_failure_does_not_overwrite_shared_success(self) -> None:
        scene = Scene(1, 0, 1, frames=[str(self.frame)])
        results = [{"summary": "Good shared result."}, OSError("Unavailable.")]
        with patch("reachreel.engines.vlm.analyze_scene", side_effect=results) as analyze:
            self.assertEqual(self.analyze([scene]), [])
            flags = self.analyze([scene], force=True)
            self.assertEqual([flag.code for flag in flags], ["vlm.scene_failed"])
            self.assertEqual(self.analyze([scene], cache_path=self.root / "another.json"), [])
            self.assertEqual(analyze.call_count, 2)
        self.assertEqual(scene.summary, "Good shared result.")

    def test_cache_depends_on_frame_bytes_and_endpoint(self) -> None:
        scene = Scene(1, 0, 1, frames=[str(self.frame)])
        results = [
            {"summary": "First frame."},
            {"summary": "Second frame."},
            {"summary": "Different endpoint."},
        ]
        with patch("reachreel.engines.vlm.analyze_scene", side_effect=results) as analyze:
            self.analyze([scene])
            copy = self.root / "another-frame.jpg"
            copy.write_bytes(self.frame.read_bytes())
            scene.frames = [str(copy)]
            self.analyze([scene], cache_path=self.root / "another.json")
            self.assertEqual(analyze.call_count, 1)
            copy.write_bytes(b"changed frame")
            self.analyze([scene])
            self.assertEqual(scene.summary, "Second frame.")
            self.analyze([scene], base_url="http://user:private-pass@other-ollama:11434")
            self.assertEqual(scene.summary, "Different endpoint.")
            self.assertEqual(analyze.call_count, 3)
        self.assertNotIn("private-pass", self.local.read_text(encoding="utf-8"))
        self.assertNotIn("private-pass", self.shared.read_text(encoding="utf-8"))

    def test_newly_available_frame_recovers_empty_scene(self) -> None:
        self.frame.unlink()
        scene = Scene(1, 0, 1, frames=[str(self.frame)])
        with patch(
            "reachreel.engines.vlm.OllamaClient.chat",
            return_value='{"summary": "Frame available."}',
        ) as chat:
            self.assertEqual([flag.code for flag in self.analyze([scene])], ["vlm.no_frames"])
            chat.assert_not_called()
            self.frame.write_bytes(b"recovered frame")
            self.assertEqual(self.analyze([scene]), [])
            chat.assert_called_once()
        self.assertEqual(scene.summary, "Frame available.")


class TtsCacheRecoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.local, self.shared = self.root / "tts", self.root / "shared"
        self.local.mkdir()
        self.shared.mkdir()
        self.settings = Settings(tts_engine="say")
        self.text = "A fresh recording."
        key = tts_cache_key(
            self.settings, self.text, speed=1.0, voice=self.settings.resolved_say_voice()
        )
        self.local_wav = self.local / f"seg_001_{key}.wav"
        self.shared_wav = self.shared / f"{key}.wav"
        self.engine = Mock(spec=["synthesize"])
        self.engine.synthesize.side_effect = self.synthesize

    @staticmethod
    def write_wav(out: Path, marker: bytes) -> None:
        with wave.open(str(out), "wb") as pcm:
            pcm.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
            pcm.writeframes(marker * 48000)

    def synthesize(self, text: str, out: Path, **kwargs) -> float:
        self.write_wav(out, b"\x01\x00")
        return 1.0

    def run_segment(self, local: Path, *, force: bool = False) -> NarrationSegment:
        segment = NarrationSegment(1, [1], self.text)
        flags = synthesize_segments(
            [segment], local, self.settings, force=force, retries=0, shared_dir=self.shared
        )
        self.assertEqual(flags, [])
        return segment

    def test_force_ignores_local_and_shared_audio(self) -> None:
        self.write_wav(self.local_wav, b"\x00\x00")
        self.write_wav(self.shared_wav, b"\x00\x00")
        with patch("reachreel.engines.tts.make_engine", return_value=self.engine):
            self.run_segment(self.local, force=True)

        self.engine.synthesize.assert_called_once()
        with wave.open(str(self.local_wav), "rb") as pcm:
            self.assertEqual(pcm.readframes(1), b"\x01\x00")
        self.assertEqual(self.shared_wav.read_bytes(), self.local_wav.read_bytes())

    def test_truncated_local_audio_falls_back_to_shared_audio(self) -> None:
        self.write_wav(self.shared_wav, b"\x00\x00")
        valid = self.shared_wav.read_bytes()
        self.local_wav.write_bytes(valid[:4096])

        with patch("reachreel.engines.tts.make_engine", return_value=self.engine):
            segment = self.run_segment(self.local)

        self.engine.synthesize.assert_not_called()
        self.assertEqual(segment.duration, 1.0)
        self.assertTrue(self.local_wav.read_bytes() == valid, "The local WAV was not repaired")

    def test_invalid_shared_audio_is_replaced_by_synthesis(self) -> None:
        for invalid in (b"not a WAV" * 200, None):
            with self.subTest(truncated=invalid is None):
                self.local_wav.unlink(missing_ok=True)
                self.engine.reset_mock()
                if invalid is None:
                    self.write_wav(self.shared_wav, b"\x00\x00")
                    invalid = self.shared_wav.read_bytes()[:4096]
                self.shared_wav.write_bytes(invalid)

                with patch("reachreel.engines.tts.make_engine", return_value=self.engine):
                    segment = self.run_segment(self.local)

                self.engine.synthesize.assert_called_once()
                self.assertEqual(segment.duration, 1.0)
                self.assertEqual(self.shared_wav.read_bytes(), self.local_wav.read_bytes())

    def test_reader_gets_complete_previous_audio_during_forced_publication(self) -> None:
        self.write_wav(self.shared_wav, b"\x00\x00")
        previous = self.shared_wav.read_bytes()
        publication_started, reader_finished = Event(), Event()
        original_copy = shutil.copy2

        def pause_shared_copy(source: Path, destination: Path) -> Path:
            if not destination.is_relative_to(self.shared):
                return original_copy(source, destination)
            data = source.read_bytes()
            with destination.open("wb") as partial:
                partial.write(data[:4096])
                partial.flush()
                publication_started.set()
                self.assertTrue(reader_finished.wait(5))
                partial.write(data[4096:])
            return destination

        with (
            patch("reachreel.engines.tts.make_engine", return_value=self.engine),
            patch("reachreel.engines.tts.shutil.copy2", side_effect=pause_shared_copy),
            ThreadPoolExecutor(max_workers=1) as workers,
        ):
            writer = workers.submit(self.run_segment, self.local, force=True)
            try:
                self.assertTrue(publication_started.wait(5))
                reader = self.run_segment(self.root / "reader")
            finally:
                reader_finished.set()
            writer.result(timeout=5)

        self.engine.synthesize.assert_called_once()
        self.assertTrue(
            Path(reader.audio_path).read_bytes() == previous,
            "The reader did not receive the previous complete WAV",
        )
        self.assertTrue(
            self.shared_wav.read_bytes() != previous, "The shared WAV was not refreshed"
        )

    def test_failed_force_preserves_the_previous_complete_cache(self) -> None:
        self.write_wav(self.local_wav, b"\x00\x00")
        self.write_wav(self.shared_wav, b"\x00\x00")
        previous = self.local_wav.read_bytes()

        def fail_after_partial_output(text: str, out: Path, **kwargs) -> float:
            out.write_bytes(b"partial")
            raise RuntimeError("Interrupted.")

        self.engine.synthesize.side_effect = fail_after_partial_output
        with (
            patch("reachreel.engines.tts.make_engine", return_value=self.engine),
            self.assertRaisesRegex(RuntimeError, "TTS failed for every segment"),
        ):
            self.run_segment(self.local, force=True)

        self.assertEqual(self.local_wav.read_bytes(), previous)
        self.assertEqual(self.shared_wav.read_bytes(), previous)
