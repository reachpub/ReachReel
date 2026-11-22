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
        # Exercise non-ASCII and whitespace paths end to end.
        fixture_dir = cls.tmp / "测试 素材"
        fixture_dir.mkdir(parents=True, exist_ok=True)
        cls.video = fixture_dir / "测试 素材 6s.mp4"
        cls.workdir = cls.tmp / "工作 目录"
        cls.config = cls.tmp / "配置 文件.toml"
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
        cls.bgm = fixture_dir / "背景 音乐.wav"
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
                f"sine=frequency=220:sample_rate=48000:duration={CLIP_SECONDS + 4}",
                "-c:a",
                "pcm_s16le",
                str(cls.bgm),
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
                    "cache_enabled = false",
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

    def test_reused_scenes_refresh_video_metadata(self) -> None:
        path = self.workdir / "scenes.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["video"].update(
            {
                "rotation": 0.0,
                "width": 999,
                "height": 111,
                "coded_width": 0,
                "coded_height": 0,
            }
        )
        path.write_text(json.dumps(data), encoding="utf-8")

        pipeline = Pipeline(self.video, Settings.load(self.config), workdir=self.workdir)
        pipeline.prepare_scenes()
        info = pipeline.load_video()
        self.assertEqual((info.width, info.height), (480, 270))
        self.assertEqual((info.coded_width, info.coded_height), (480, 270))
        refreshed = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(refreshed["video"]["coded_width"], 480)

    def test_stage_chain_renders_and_passes_qa(self) -> None:
        workdir = str(self.workdir)
        config = str(self.config)

        self._cli("tts", workdir, "--config", config, "--tts", "say", "--voice", self.voice)
        self._cli("subtitle", workdir, "--config", config, "--asr", "none")
        self._cli("mix", workdir, "--config", config, "--bgm", str(self.bgm))
        # Let render burn subtitles when ffmpeg-full/libass is available; the
        # non-ASCII workdir path exercises the filter-path escaping.
        self._cli("render", workdir, "--config", config)
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
        self.assertIn("Media licenses", licenses)
        self.assertIn(self.bgm.name, licenses)


@unittest.skipUnless(
    ENABLED, "set REACHREEL_INTEGRATION=1 to run the `run --only` reuse smoke"
)
@unittest.skipUnless(
    sys.platform == "darwin", "the reuse smoke needs the macOS `say` command"
)
@unittest.skipUnless(
    shutil.which("ffmpeg") and shutil.which("ffprobe") and shutil.which("say"),
    "ffmpeg, ffprobe, and say are required",
)
class OnlyReuseTest(unittest.TestCase):
    """`run --only` must reuse existing artifacts instead of redoing stages."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="reachreel-only-"))
        cls.video = cls.tmp / "素材 clip.mp4"
        cls.workdir = cls.tmp / "工作 dir"
        cls.config = cls.tmp / "cfg.toml"
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
                f"testsrc2=size=320x180:rate=24:duration={CLIP_SECONDS}",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=330:duration={CLIP_SECONDS}",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
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
                    "subtitle_size = 24",
                    "crf = 30",
                    'preset = "ultrafast"',
                    "cache_enabled = false",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        settings = Settings.load(cls.config)
        pipeline = Pipeline(cls.video, settings, workdir=cls.workdir)
        pipeline.prepare_scenes()
        pipeline.write_manifest(started=time.time(), burned=False, announce=False)
        atomic_write_json(
            cls.workdir / "script.json",
            {
                "content_language": "zh",
                "segments": [
                    NarrationSegment(id=index, scene_ids=[1], text=text).to_dict()
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

    def _digest(self, name: str) -> str:
        import hashlib

        return hashlib.sha1((self.workdir / name).read_bytes()).hexdigest()

    def _run_only(self, stages: str) -> None:
        self._cli(
            "run",
            str(self.video),
            "--workdir",
            str(self.workdir),
            "--config",
            str(self.config),
            "--only",
            stages,
        )

    def test_only_reuses_earlier_stages(self) -> None:
        scenes_digest = self._digest("scenes.json")

        self._run_only("tts")
        self.assertTrue((self.workdir / "narration.wav").is_file())
        self.assertTrue((self.workdir / "timeline.json").is_file())
        self.assertEqual(self._digest("scenes.json"), scenes_digest)
        narration_digest = self._digest("narration.wav")

        self._cli("subtitle", str(self.workdir), "--config", str(self.config), "--asr", "none")
        subtitles_digest = self._digest("subtitles.srt")

        # `mix,render` must not re-synthesize narration or re-run ASR.
        self._run_only("mix,render")
        self.assertEqual(self._digest("narration.wav"), narration_digest)
        self.assertEqual(self._digest("subtitles.srt"), subtitles_digest)
        self.assertTrue((self.workdir / "mix.wav").is_file())
        self.assertTrue((self.workdir / "final.mp4").is_file())
        self.assertGreater((self.workdir / "final.mp4").stat().st_size, 1000)

    def test_only_qa_needs_no_models(self) -> None:
        if not (self.workdir / "final.mp4").is_file():
            self.test_only_reuses_earlier_stages()
        narration_digest = self._digest("narration.wav")

        self._run_only("qa")

        qa = json.loads((self.workdir / "qa.json").read_text(encoding="utf-8"))
        self.assertIn("summary", qa)
        self.assertEqual(self._digest("narration.wav"), narration_digest)


if __name__ == "__main__":
    unittest.main()
