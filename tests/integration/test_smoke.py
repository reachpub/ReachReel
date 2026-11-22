"""Opt-in end-to-end smoke test for the stage commands.

The test builds a small clip with FFmpeg, seeds ``scenes.json``/``script.json``
without any model calls, then runs the real CLI chain:

    tts -> subtitle --asr none -> mix --no-bgm -> render --no-burn -> qa

It needs macOS ``say`` but no Ollama, Whisper, or Kokoro download. Enable it
explicitly so the default unit-test suite stays fast and deterministic:

    REACHREEL_INTEGRATION=1 python -m unittest tests.integration.test_smoke -v

Set ``REACHREEL_KEEP_FIXTURES=1`` to keep the temporary work directory for
debugging.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from reachreel.core.config import Settings
from reachreel.core.util import atomic_write_json, run
from reachreel.domain.models import NarrationSegment
from reachreel.pipeline.orchestrator import Pipeline

ENABLED = os.environ.get("REACHREEL_INTEGRATION") == "1"
KEEP_FIXTURES = os.environ.get("REACHREEL_KEEP_FIXTURES") == "1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]

CLIP_SECONDS = 6
SEGMENT_TEXTS = (
    "风吹过山谷。",
    "我们继续向前。",
)


def _say_voices() -> list[tuple[str, str]]:
    """Return ``(name, locale)`` pairs reported by ``say -v ?``."""
    proc = subprocess.run(
        ["say", "-v", "?"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    voices: list[tuple[str, str]] = []
    for line in (proc.stdout or "").splitlines():
        fields = [field.strip() for field in line.split("  ") if field.strip()]
        if len(fields) >= 2:
            voices.append((fields[0], fields[1]))
    return voices


def _pick_say_voice() -> str | None:
    voices = _say_voices()
    # Prefer Tingting, then any installed Chinese voice.
    for name, _locale in voices:
        if name == "Tingting":
            return name
    for name, locale in voices:
        if locale.lower().startswith("zh"):
            return name
    return None


@unittest.skipUnless(
    ENABLED, "set REACHREEL_INTEGRATION=1 to run the integration smoke test"
)
@unittest.skipUnless(
    sys.platform == "darwin", "the smoke test needs the macOS `say` command"
)
@unittest.skipUnless(
    shutil.which("ffmpeg") and shutil.which("ffprobe") and shutil.which("say"),
    "ffmpeg, ffprobe, and say are required",
)
class StageSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="reachreel-smoke-"))
        cls.video = cls.tmp / "clip.mp4"
        cls.workdir = cls.tmp / "work"
        cls.config = cls.tmp / "config.toml"
        voice = _pick_say_voice()
        if voice is None:
            raise unittest.SkipTest("no macOS `say` voice is installed")
        cls.voice = voice

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
                f"testsrc2=size=480x270:rate=24:duration={CLIP_SECONDS}",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                str(cls.video),
            ],
            timeout=120,
        )

        cls.config.write_text(
            "\n".join(
                [
                    'ui_language = "en"',
                    'content_language = "zh"',
                    'tts_engine = "say"',
                    f"say_voice = {json.dumps(voice)}",
                    'asr_language = "zh"',
                    "subtitle_max_chars = 12",
                    "subtitle_max_lines = 2",
                    "subtitle_size = 24",
                    "subtitle_margin_v = 10",
                    "subtitle_margin_left = 10",
                    "subtitle_margin_right = 10",
                    "crf = 30",
                    'preset = "ultrafast"',
                    "",
                ]
            ),
            encoding="utf-8",
        )

        settings = Settings.load(cls.config)
        pipeline = Pipeline(cls.video, settings, workdir=cls.workdir)
        # Scene detection + frame extraction only; no VLM or LLM call.
        pipeline.prepare_scenes()
        pipeline.write_manifest(started=time.time(), burned=False, announce=False)
        atomic_write_json(
            cls.workdir / "script.json",
            {
                "content_language": "zh",
                "segments": [
                    NarrationSegment(
                        id=index,
                        scene_ids=[1],
                        text=text,
                    ).to_dict()
                    for index, text in enumerate(SEGMENT_TEXTS, start=1)
                ],
            },
        )

    @classmethod
    def tearDownClass(cls) -> None:
        tmp = getattr(cls, "tmp", None)
        if tmp is None:
            return
        if KEEP_FIXTURES:
            print(f"\nKept integration fixture: {tmp}")
            return
        shutil.rmtree(tmp, ignore_errors=True)

    def _cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(
            [sys.executable, "-m", "reachreel", "--lang", "en", *args],
            cwd=str(PROJECT_ROOT),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        self.assertEqual(
            proc.returncode,
            0,
            msg=(
                f"reachreel {' '.join(args)} failed with {proc.returncode}\n"
                f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
            ),
        )
        return proc

    def test_stage_chain_renders_and_passes_qa(self) -> None:
        workdir = str(self.workdir)
        config = str(self.config)

        self._cli("tts", workdir, "--config", config, "--tts", "say", "--voice", self.voice)
        self._cli("subtitle", workdir, "--config", config, "--asr", "none")
        self._cli("mix", workdir, "--config", config, "--no-bgm")
        self._cli("render", workdir, "--config", config, "--no-burn")
        self._cli("qa", workdir, "--config", config)
        self._cli("licenses", workdir)

        final = self.workdir / "final.mp4"
        self.assertTrue(final.is_file(), "render did not create final.mp4")
        self.assertGreater(final.stat().st_size, 1000)

        srt = (self.workdir / "subtitles.srt").read_text(encoding="utf-8")
        for keyword in ("山谷", "向前"):
            self.assertIn(keyword, srt)

        qa = json.loads((self.workdir / "qa.json").read_text(encoding="utf-8"))
        self.assertEqual(
            qa["summary"]["fail"],
            0,
            msg=json.dumps(qa["checks"], ensure_ascii=False, indent=2),
        )

        manifest = json.loads((self.workdir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["narration_segments"], len(SEGMENT_TEXTS))

        licenses = (self.workdir / "licenses.md").read_text(encoding="utf-8")
        self.assertIn("BGM", licenses)


if __name__ == "__main__":
    unittest.main()
