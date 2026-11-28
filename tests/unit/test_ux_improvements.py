"""Unit tests for CLI UX, JSON purity, portable workdirs, resume, voices, and export."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
import wave
import xml.etree.ElementTree as ET
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from reachreel.cli.main import _overrides, build_parser, main
from reachreel.core.config import Settings
from reachreel.core.util import (
    atomic_write_json,
    display_width,
    find_ffmpeg,
    find_ffprobe,
    pad_display,
)
from reachreel.domain.models import NarrationSegment, Scene, VideoInfo
from reachreel.media.edit_export import _timeline, _write_fcpxml, _write_xmeml
from reachreel.media.scenes import save_scenes
from reachreel.pipeline.orchestrator import Pipeline, doctor_report, workdir_status
from reachreel.pipeline.qa import _resolve_workdir_file
from reachreel.pipeline.stage_inputs import StageInputs


def _write_silence_wav(path: Path, *, duration_s: float = 1.0, channels: int = 1, rate: int = 48000) -> None:
    frames = int(duration_s * rate)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(channels)
        fh.setsampwidth(2)
        fh.setframerate(rate)
        fh.writeframes(b"\x00\x00" * frames * channels)


def _seed_post_script_workdir(root: Path, *, delete_source: bool = False) -> tuple[Path, Path]:
    root = root.resolve()
    video = root / "clip.mp4"
    video.write_bytes(b"video-bytes")
    workdir = root / "job"
    workdir.mkdir(parents=True, exist_ok=True)
    info = VideoInfo(str(video), 10.0, 1080, 1920, 30.0, True)
    scene = Scene(1, 0.0, 10.0, summary="A misty waterfall.")
    save_scenes(workdir / "scenes.json", info, [scene])
    seg = NarrationSegment(1, [1], "The mist rises above the river.")
    atomic_write_json(
        workdir / "script.json",
        {
            "content_language": "en",
            "segments": [seg.to_dict()],
            "generation": {"inputs_key": "k1", "segments_key": "orig"},
        },
    )
    stage_inputs = StageInputs(workdir, video)
    atomic_write_json(
        workdir / "manifest.json",
        {
            "schema_version": 2,
            "video": {
                "path": str(video),
                "duration": 10.0,
                "width": 1080,
                "height": 1920,
                "fps": 30.0,
                "has_audio": True,
            },
            "workdir": str(workdir),
            "output": str(workdir / "final.mp4"),
            "settings": {"tts_engine": "kokoro", "voice": "af_heart"},
            "settings_snapshot": {"content_language": "en"},
            "artifact_inputs": {"vlm": stage_inputs.capture("vlm")},
        },
    )
    if delete_source:
        video.unlink()
    return video, workdir


class BinaryDiscoveryAndAlignmentTests(unittest.TestCase):
    def test_find_ffmpeg_and_ffprobe_from_env_full(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bindir = Path(tmp) / "bin"
            bindir.mkdir()
            fake_ffmpeg = bindir / "ffmpeg"
            fake_ffprobe = bindir / "ffprobe"
            fake_ffmpeg.write_text("#!/bin/sh\n", encoding="utf-8")
            fake_ffprobe.write_text("#!/bin/sh\n", encoding="utf-8")
            with (
                patch.dict("os.environ", {"FFMPEG_FULL": str(fake_ffmpeg)}),
                patch("shutil.which", return_value=None),
            ):
                self.assertEqual(find_ffmpeg(), str(fake_ffmpeg))
                self.assertEqual(find_ffprobe(), str(fake_ffprobe))

    def test_doctor_report_includes_fix_hints_on_missing_items(self) -> None:
        settings = Settings()
        with (
            patch("reachreel.pipeline.orchestrator.find_ffmpeg", return_value=None),
            patch("reachreel.pipeline.orchestrator.find_ffprobe", return_value=None),
            patch("reachreel.pipeline.orchestrator.ffmpeg_full", return_value=None),
            patch("reachreel.core.util.ffmpeg_filters", return_value=set()),
            patch("urllib.request.urlopen", side_effect=OSError("refused")),
        ):
            report = doctor_report(settings)
        self.assertFalse(report["ok"])
        by_id = {c["id"]: c for c in report["checks"]}
        self.assertEqual(by_id["ffmpeg"]["hint"], "brew install ffmpeg-full")
        self.assertEqual(by_id["ffprobe"]["hint"], "brew install ffmpeg-full")
        self.assertEqual(by_id["ollama"]["hint"], "ollama serve")

    def test_display_width_and_pad_display_handle_cjk(self) -> None:
        self.assertEqual(display_width("abc"), 3)
        self.assertEqual(display_width("\u5e73\u53f0"), 4)
        padded = pad_display("\u5e73\u53f0", 10)
        self.assertEqual(display_width(padded), 10)
        self.assertTrue(padded.endswith("      "))


class CliFlagsAndJsonPurityTests(unittest.TestCase):
    def test_global_flags_accepted_after_subcommand(self) -> None:
        parser = build_parser()
        args = parser.parse_args([
            "run", "/tmp/video.mp4", "--lang", "zh-CN", "--no-cache",
            "--cache-dir", "/tmp/rr", "--verbose", "--crf", "20",
            "--bgm-volume", "0.25", "--target-lufs", "-16",
        ])
        self.assertEqual(args.lang, "zh-CN")
        self.assertTrue(args.no_cache)
        self.assertEqual(args.cache_dir, "/tmp/rr")
        self.assertTrue(args.verbose)
        overrides = _overrides(args)
        self.assertEqual(overrides["crf"], 20)
        self.assertEqual(overrides["bgm_volume"], 0.25)
        self.assertEqual(overrides["target_lufs"], -16.0)

    def test_stage_specific_new_flags(self) -> None:
        parser = build_parser()
        tts_args = parser.parse_args(["tts", "/tmp/job", "--max-speedup", "1.4", "--json"])
        self.assertEqual(tts_args.max_speedup, 1.4)
        self.assertTrue(tts_args.json)

        mix_args = parser.parse_args([
            "mix", "/tmp/job", "--bgm-volume", "0.15", "--bgm-duck-db", "-18",
            "--ambient-volume", "0.1", "--ambient-duck-db", "-12", "--target-lufs", "-15", "--json",
        ])
        self.assertEqual(mix_args.bgm_duck_db, -18.0)
        self.assertTrue(mix_args.json)

        exp_args = parser.parse_args(["export", "/tmp/job", "--force", "--json"])
        self.assertTrue(exp_args.force)
        self.assertTrue(exp_args.json)

    def test_run_json_guards_stdout_against_third_party_prints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            video, workdir = _seed_post_script_workdir(Path(tmp))

            def noisy_run(self: Pipeline, **kwargs):
                print("Creating new KokoroPipeline for language: z")
                self.executed_stages = ["tts", "subtitles", "mix", "render", "qa"]
                self.burned_subtitles = True
                return self.output_path

            buf = io.StringIO()
            with (
                patch.object(Pipeline, "run", noisy_run),
                redirect_stdout(buf),
            ):
                rc = main([
                    "run", str(video), "--workdir", str(workdir),
                    "--only", "tts,subtitles,mix,render,qa", "--json",
                ])
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["output"], str((workdir / "final.mp4").resolve()))
            self.assertEqual(payload["last_artifact"], str((workdir / "qa.json").resolve()))

    def test_run_only_ending_in_qa_returns_final_mp4_when_render_selected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            video, workdir = _seed_post_script_workdir(Path(tmp))
            pipeline = Pipeline(video, Settings(), workdir=workdir)
            with (
                patch.object(Pipeline, "_run_stages"),
                patch.object(Pipeline, "write_review"),
                patch("reachreel.pipeline.qa.run_qa") as mock_qa,
                patch("reachreel.pipeline.qa.attach_to_manifest"),
                patch("reachreel.pipeline.qa.print_report"),
            ):
                mock_qa.return_value = None
                out = pipeline.run(only="render,qa", print_qa=False)
            self.assertEqual(out, pipeline.output_path)

    def test_licenses_rejects_missing_workdir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "not-created"
            with self.assertRaises(SystemExit) as ctx:
                main(["licenses", str(missing)])
            self.assertEqual(ctx.exception.code, 2)
            self.assertFalse(missing.exists())


class ResumeAndPortabilityTests(unittest.TestCase):
    def test_status_exposes_pending_stages_and_resume_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, workdir = _seed_post_script_workdir(Path(tmp))
            report = workdir_status(workdir)
            self.assertEqual(report["pending_stages"], ["tts", "subtitles", "mix", "render", "qa"])
            self.assertEqual(report["resume_command"], f"reachreel resume {workdir.resolve()}")

    def test_resume_executes_only_pending_stages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, workdir = _seed_post_script_workdir(Path(tmp))
            captured_only: list[list[str]] = []

            def fake_run(self: Pipeline, **kwargs):
                captured_only.append(list(kwargs.get("only") or []))
                self.executed_stages = list(kwargs.get("only") or [])
                return self.output_path

            buf = io.StringIO()
            with (
                patch.object(Pipeline, "run", fake_run),
                redirect_stdout(buf),
            ):
                rc = main(["resume", str(workdir), "--json"])
            self.assertEqual(rc, 0)
            self.assertEqual(captured_only, [["tts", "subtitles", "mix", "render", "qa"]])
            payload = json.loads(buf.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["stages_executed"], ["tts", "subtitles", "mix", "render", "qa"])

    def test_tts_stage_runs_without_original_source_video(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, workdir = _seed_post_script_workdir(Path(tmp), delete_source=True)
            ran: list[bool] = []

            def fake_stage_tts(pipe: Pipeline) -> None:
                info = pipe.load_video()
                self.assertEqual(info.duration, 10.0)
                ran.append(True)

            buf = io.StringIO()
            with (
                patch.object(Pipeline, "stage_tts", fake_stage_tts),
                redirect_stdout(buf),
            ):
                rc = main(["tts", str(workdir), "--json"])
            self.assertEqual(rc, 0)
            self.assertEqual(ran, [True])
            payload = json.loads(buf.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["stage"], "tts")

    def test_relocated_and_copied_workdir_resolves_local_final_mp4(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            job_a = root / "job_a"
            job_b = root / "job_b"
            job_a.mkdir()
            job_b.mkdir()
            final_a = job_a / "final.mp4"
            final_b = job_b / "final.mp4"
            final_a.write_bytes(b"mp4-a")
            final_b.write_bytes(b"mp4-b")
            # Moved workdir (original missing)
            resolved_moved = _resolve_workdir_file(job_b, "/old/machine/path/final.mp4", "final.mp4")
            self.assertEqual(resolved_moved, final_b)
            # Copied workdir (original still exists on disk) -> must check job_b, not job_a!
            resolved_copied = _resolve_workdir_file(
                job_b, str(final_a), "final.mp4", prefer_workdir=True
            )
            self.assertEqual(resolved_copied, final_b)

    def test_resume_does_not_force_vlm_cache_invalidation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            video, workdir = _seed_post_script_workdir(Path(tmp))
            pipeline = Pipeline(video, Settings(), workdir=workdir)
            seen_force: list[bool | None] = []

            def fake_understand(self: Pipeline, *, force: bool | None = None) -> None:
                seen_force.append(force)

            with (
                patch.object(Pipeline, "prepare_scenes"),
                patch.object(Pipeline, "understand", fake_understand),
                patch.object(Pipeline, "write_manifest"),
            ):
                pipeline.run(only=["vlm"], force_selected=False)
            self.assertEqual(seen_force, [False])

    def test_export_rejects_destination_containing_workdir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            video, workdir = _seed_post_script_workdir(Path(tmp))
            _write_silence_wav(workdir / "narration.wav", duration_s=1.0)
            (workdir / "subtitles.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n", encoding="utf-8")
            pipeline = Pipeline(video, Settings(), workdir=workdir)
            with patch.object(Pipeline, "_require_current"):
                with self.assertRaises(ValueError):
                    pipeline.export_edit(destination=workdir, overwrite=True)
                with self.assertRaises(ValueError):
                    pipeline.export_edit(destination=workdir.parent, overwrite=True)


class VoicesDryRunAndExportTests(unittest.TestCase):
    def test_voices_json_includes_details_and_preview_works(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["voices", "--json"])
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertIn("details", payload)
        self.assertEqual(payload["details"]["zf_xiaoxiao"]["gender"], "female")

        with tempfile.TemporaryDirectory() as tmp:
            out_wav = Path(tmp) / "sample.wav"

            def fake_preview(voice, settings, *, text=None, out_wav: Path, explicit_engine: bool = False):
                _write_silence_wav(out_wav, duration_s=1.2)
                return 1.2

            pbuf = io.StringIO()
            with (
                patch("reachreel.engines.tts.preview_voice", fake_preview),
                redirect_stdout(pbuf),
            ):
                prc = main([
                    "voices", "--preview", "zf_xiaoxiao", "--out", str(out_wav),
                    "--no-play", "--json",
                ])
            self.assertEqual(prc, 0)
            pdata = json.loads(pbuf.getvalue())
            self.assertTrue(pdata["ok"])
            self.assertEqual(pdata["voice"], "zf_xiaoxiao")
            self.assertEqual(pdata["output"], str(out_wav.resolve()))

            # Path traversal in default preview filename must be sanitized inside tempdir.
            seen_paths: list[Path] = []

            def capture_preview(voice, settings, *, text=None, out_wav: Path, explicit_engine: bool = False):
                seen_paths.append(out_wav)
                return 0.5

            tbuf = io.StringIO()
            with (
                patch("reachreel.engines.tts.preview_voice", capture_preview),
                redirect_stdout(tbuf),
            ):
                trc = main(["voices", "--preview", "../../evil/voice", "--no-play", "--json"])
            self.assertEqual(trc, 0)
            self.assertEqual(len(seen_paths), 1)
            self.assertEqual(seen_paths[0].parent.resolve(), Path(tempfile.gettempdir()).resolve())
            self.assertNotIn("..", seen_paths[0].name)

    def test_preview_voice_auto_selects_engine_and_case_insensitive_qwen3(self) -> None:
        from reachreel.engines.tts import preview_voice

        captured: list[tuple[str, str]] = []

        class _FakeEngine:
            def __init__(self, settings: Settings) -> None:
                self.settings = settings

            def synthesize(self, text, out_wav, *, speed=1.0, voice=None, instruct=None):
                captured.append((self.settings.tts_engine, str(voice)))
                return 1.0

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("reachreel.engines.tts.make_engine", side_effect=lambda s: _FakeEngine(s)),
        ):
            # Config defaulted to qwen3, previewing Kokoro voice switches to kokoro.
            preview_voice(
                "zf_xiaoxiao",
                Settings(tts_engine="qwen3"),
                out_wav=Path(tmp) / "a.wav",
            )
            # Lowercase Qwen3 speaker normalizes to canonical case & selects qwen3.
            preview_voice(
                "serena",
                Settings(tts_engine="kokoro"),
                out_wav=Path(tmp) / "b.wav",
            )
        self.assertEqual(captured, [("kokoro", "zf_xiaoxiao"), ("qwen3", "Serena")])

    def test_dry_run_plan_includes_rich_metadata_and_asr_none_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "clip.mp4"
            video.write_bytes(b"v")
            info = VideoInfo(str(video), 10.0, 1080, 1920, 30.0, True)
            pipeline = Pipeline(video, Settings(subtitle_preset="douyin"), workdir=Path(tmp) / "job", create_dir=False)
            with patch("reachreel.pipeline.orchestrator.probe_video", return_value=info):
                plan = pipeline.dry_run_plan(None, asr_engine="none", no_bgm=True)
            self.assertEqual(plan["language"]["content"], "zh")
            self.assertEqual(plan["subtitles"]["preset"], "douyin")
            self.assertEqual(plan["models"]["asr"], "none")
            self.assertFalse(plan["bgm"]["enabled"])
            sub_stage = next(s for s in plan["stages"] if s["stage"] == "subtitles")
            self.assertIn("none", sub_stage["note"].lower())

    def test_export_xml_includes_bgm_ambient_stems_and_scene_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            dest = root / "edit"
            dest.mkdir()
            video = root / "video.mp4"
            video.write_bytes(b"v")
            narration = root / "narration.wav"
            bgm = root / "bgm.wav"
            ambient = root / "ambient.wav"
            _write_silence_wav(narration, duration_s=2.0, channels=1)
            _write_silence_wav(bgm, duration_s=2.0, channels=2)
            _write_silence_wav(ambient, duration_s=2.0, channels=2)

            probe_payload = {
                "streams": [
                    {"codec_type": "video", "avg_frame_rate": "30/1", "nb_frames": "60", "width": 1920, "height": 1080},
                    {"codec_type": "audio", "sample_rate": "48000", "channels": 2, "bits_per_sample": 16},
                ]
            }
            scenes = [
                {"id": 1, "start": 0.0, "end": 1.0, "summary": "Opening view"},
                {"id": 2, "start": 1.0, "end": 2.0, "summary": "Close-up action"},
            ]
            with patch("reachreel.media.edit_export.ffprobe_json", return_value=probe_payload):
                edit = _timeline(video, narration, dest, bgm=bgm, ambient=ambient, scenes=scenes)

            xmeml_path = dest / "timeline.xml"
            fcp_path = dest / "timeline.fcpxml"
            _write_xmeml(xmeml_path, edit)
            _write_fcpxml(fcp_path, edit)

            xroot = ET.parse(xmeml_path).getroot()
            audio_tracks = xroot.findall("./sequence/media/audio/track")
            self.assertEqual(len(audio_tracks), 3)
            self.assertEqual(
                audio_tracks[0].findtext("./clipitem/file/name"),
                "narration.wav",
            )
            self.assertEqual(
                audio_tracks[1].findtext("./clipitem/file/name"),
                "bgm.wav",
            )
            self.assertEqual(
                audio_tracks[2].findtext("./clipitem/file/name"),
                "ambient.wav",
            )
            markers = xroot.findall("./sequence/marker")
            self.assertEqual(len(markers), 2)
            self.assertIn("Scene 2", markers[1].findtext("name") or "")

            froot = ET.parse(fcp_path).getroot()
            connected = froot.findall(".//spine/asset-clip/asset-clip")
            self.assertEqual([c.get("lane") for c in connected], ["-1", "-2", "-3"])
            self.assertEqual([c.get("audioRole") for c in connected], ["dialogue", "music", "effects"])
            fcp_markers = froot.findall(".//spine/asset-clip/marker")
            self.assertEqual(len(fcp_markers), 2)


if __name__ == "__main__":
    unittest.main()
