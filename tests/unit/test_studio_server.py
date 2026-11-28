"""Unit tests for the local ReachReel Studio HTTP server and fine-tuning API."""
from __future__ import annotations

import array
import base64
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import wave
from pathlib import Path
from typing import Any

from reachreel.cli.main import build_parser
from reachreel.core.config import Settings
from reachreel.core.util import atomic_write_json
from reachreel.pipeline.orchestrator import MANIFEST_SCHEMA_VERSION, Pipeline
from reachreel.server import create_server
from reachreel.server.runner import expand_stage_dependencies


def _write_wav(path: Path, seconds: float = 0.4, rate: int = 48000) -> None:
    count = int(seconds * rate)
    samples = array.array("h", [5000 if (i % 40) < 20 else -5000 for i in range(count)])
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(rate)
        fh.writeframes(samples.tobytes())


def _seed_studio_job(work_root: Path, job_name: str = "demo_job") -> Path:
    video = work_root / "clip.mp4"
    video.write_bytes(b"0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    workdir = work_root / job_name
    workdir.mkdir(parents=True)
    (workdir / "frames").mkdir()
    (workdir / "frames" / "s1.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    _write_wav(workdir / "imports" / "seg_001.wav", seconds=0.5)

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
            {
                "id": 1,
                "start": 0.0,
                "end": 10.0,
                "summary": "Intro shot",
                "frames": [str(workdir / "frames" / "s1.jpg")],
            },
            {
                "id": 2,
                "start": 10.0,
                "end": 20.0,
                "summary": "Outro shot",
                "frames": [],
            },
        ],
    }
    atomic_write_json(workdir / "scenes.json", scenes_data)
    atomic_write_json(
        workdir / "script.json",
        {
            "content_language": "en",
            "segments": [
                {
                    "id": 1,
                    "scene_ids": [1],
                    "text": "Hello opening.",
                    "audio_override": "imports/seg_001.wav",
                },
                {"id": 2, "scene_ids": [2], "text": "Hello ending."},
            ],
        },
    )
    atomic_write_json(
        workdir / "timeline.json",
        {
            "segments": [
                {
                    "id": 1,
                    "scene_ids": [1],
                    "text": "Hello opening.",
                    "audio_path": str(workdir / "imports" / "seg_001.wav"),
                    "duration": 0.5,
                    "start": 0.4,
                    "end": 0.9,
                },
                {
                    "id": 2,
                    "scene_ids": [2],
                    "text": "Hello ending.",
                    "audio_path": str(workdir / "imports" / "seg_001.wav"),
                    "duration": 0.5,
                    "start": 10.0,
                    "end": 10.5,
                },
            ]
        },
    )
    (workdir / "narration.wav").write_bytes(b"RIFF-fake-narration")
    (workdir / "subtitles.srt").write_text(
        "1\n00:00:00,400 --> 00:00:00,900\nHello opening.\n",
        encoding="utf-8",
    )
    (workdir / "mix.wav").write_bytes(b"RIFF-fake-mix")
    (workdir / "final.mp4").write_bytes(b"0123456789abcdef")

    pipe = Pipeline(video, Settings(content_language="en"), workdir=workdir, create_dir=False)
    atomic_write_json(
        workdir / "manifest.json",
        {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "video": scenes_data["video"],
            "workdir": str(workdir),
            "output": str(workdir / "final.mp4"),
            "settings": {
                "tts_engine": "say",
                "voice": "Samantha",
                "burned_subtitles": True,
            },
            "settings_snapshot": {
                "content_language": "en",
                "tts_engine": "say",
                "subtitle_size": 48,
                "bgm_volume": 0.2,
            },
            "artifact_inputs": {
                "vlm": pipe.stage_inputs.capture("vlm"),
                "tts": pipe.stage_inputs.capture("tts"),
                "subtitles": pipe.stage_inputs.capture("subtitles"),
                "mix": pipe.stage_inputs.capture("mix"),
                "render": {
                    **pipe.stage_inputs.capture("render"),
                    "output": pipe.stage_inputs.rendered_key(),
                },
            },
            "qa": {"status": "pass", "summary": {"pass": 1, "warn": 0, "fail": 0}},
        },
    )
    atomic_write_json(
        workdir / "qa.json",
        {"summary": {"pass": 1, "warn": 0, "fail": 0}, "checks": [{"id": "video_stream", "status": "pass", "message": "ok"}]},
    )
    return workdir


class StudioServerTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="reachreel-studio-test-")
        self.work_root = Path(self._tmp.name).resolve()
        self.workdir = _seed_studio_job(self.work_root, "demo_job")
        self.token = "test-secret-token-123"
        self.httpd, self.state = create_server(
            work_root=self.work_root,
            host="127.0.0.1",
            port=0,
            token=self.token,
            default_lang="en",
            write_token_file=False,
        )
        self.port = self.httpd.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.state.runner.stop()
        self.httpd.server_close()
        self._tmp.cleanup()

    def _req(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        token: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        hdrs = dict(headers or {})
        if token is not None:
            hdrs["X-ReachReel-Token"] = token
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        req = urllib.request.Request(f"{self.base_url}{path}", data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def test_expand_stage_dependencies_and_cli_serve_parser(self) -> None:
        self.assertEqual(
            expand_stage_dependencies(["tts"]),
            ["tts", "subtitles", "mix", "render", "qa"],
        )
        self.assertEqual(
            expand_stage_dependencies(["subtitles"], include_qa=False),
            ["subtitles", "render"],
        )
        args = build_parser().parse_args(["serve", "output/my-job", "--port", "9000", "--open"])
        self.assertEqual(args.command, "serve")
        self.assertEqual(args.port, 9000)
        self.assertTrue(args.open)

    def test_security_token_origin_and_preview_whitelist(self) -> None:
        # Health endpoint and UI shell do not require token.
        code, _, raw = self._req("GET", "/api/health")
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(raw)["ok"])

        code_ui, _, html = self._req("GET", "/")
        self.assertEqual(code_ui, 200)
        self.assertIn(b"ReachReel Studio", html)

        # Missing token -> 401.
        code_unauth, _, _ = self._req("GET", "/api/jobs")
        self.assertEqual(code_unauth, 401)

        # Cross-site Sec-Fetch-Site -> 403 even with valid token.
        code_xsite, _, _ = self._req(
            "GET",
            "/api/jobs",
            token=self.token,
            headers={"Sec-Fetch-Site": "cross-site"},
        )
        self.assertEqual(code_xsite, 403)

        # Preview blocks secret/non-whitelisted files & path traversal.
        code_sec, _, _ = self._req(
            "GET",
            "/api/jobs/demo_job/preview/manifest.json",
            token=self.token,
        )
        self.assertEqual(code_sec, 404)
        code_trav, _, _ = self._req(
            "GET",
            "/api/jobs/demo_job/preview/../clip.mp4",
            token=self.token,
        )
        self.assertEqual(code_trav, 404)

        # HTTP Range request works on whitelisted final.mp4.
        code_rng, hdrs, body = self._req(
            "GET",
            f"/api/jobs/demo_job/preview/final.mp4?token={self.token}",
            headers={"Range": "bytes=4-9"},
        )
        self.assertEqual(code_rng, 206)
        self.assertEqual(body, b"456789")
        self.assertEqual(hdrs.get("Content-Range"), "bytes 4-9/16")

    def test_fine_tuning_endpoints_segment_cuts_subtitles_settings_import_audition_undo(self) -> None:
        # List jobs & inspect detail.
        code, _, raw = self._req("GET", "/api/jobs", token=self.token)
        self.assertEqual(code, 200)
        self.assertEqual([j["id"] for j in json.loads(raw)["jobs"]], ["demo_job"])

        code, _, raw = self._req("GET", "/api/jobs/demo_job", token=self.token)
        self.assertEqual(code, 200)
        detail = json.loads(raw)
        self.assertEqual(len(detail["segments"]), 2)
        self.assertEqual(len(detail["scenes"]), 2)

        # PATCH segment 1 (text, start_override, gain_db).
        code, _, raw = self._req(
            "PATCH",
            "/api/jobs/demo_job/segments/1",
            token=self.token,
            body={"text": "Studio tuned opening.", "start_override": 1.25, "gain_db": 2.0},
        )
        self.assertEqual(code, 200)
        res = json.loads(raw)
        seg1 = res["job"]["segments"][0]
        self.assertEqual(seg1["text"], "Studio tuned opening.")
        self.assertEqual(seg1["start_override"], 1.25)
        self.assertEqual(seg1["gain_db"], 2.0)

        # Audition segment 1 (uses audio_override directly without MLX).
        code, _, raw = self._req(
            "POST",
            "/api/jobs/demo_job/segments/1/audition",
            token=self.token,
            body={},
        )
        self.assertEqual(code, 200)
        aud = json.loads(raw)
        self.assertTrue(aud["ok"])
        self.assertEqual(aud["audio_url"], "/api/jobs/demo_job/preview/imports/seg_001.wav")

        # PUT cuts -> updates cuts.json, marks downstream stale, and projects scenes onto edited timeline.
        code, _, raw = self._req(
            "PUT",
            "/api/jobs/demo_job/cuts",
            token=self.token,
            body={"clips": [{"start": 2.0, "end": 18.0}]},
        )
        self.assertEqual(code, 200)
        cuts_res = json.loads(raw)
        self.assertEqual(cuts_res["job"]["cuts"]["edited_duration"], 16.0)
        # Scene 1 (source 0-10s) clipped to 2-10s -> edited 0-8s; scene 2 -> edited 8-16s.
        self.assertEqual(cuts_res["job"]["scenes"][0]["start"], 0.0)
        self.assertEqual(cuts_res["job"]["scenes"][0]["end"], 8.0)
        self.assertEqual(cuts_res["job"]["scenes"][1]["start"], 8.0)
        self.assertEqual(cuts_res["job"]["scenes"][1]["end"], 16.0)

        # POST undo -> removes cuts.json and restores pre-cut state.
        code, _, raw = self._req(
            "POST",
            "/api/jobs/demo_job/undo",
            token=self.token,
            body={},
        )
        self.assertEqual(code, 200)
        undo_res = json.loads(raw)
        self.assertIsNone(undo_res["job"]["cuts"])

        # PUT subtitles -> updates subtitles.srt.
        code, _, raw = self._req(
            "PUT",
            "/api/jobs/demo_job/subtitles",
            token=self.token,
            body={"cues": [{"start": 0.5, "end": 2.0, "text": "Edited in Studio"}]},
        )
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(raw)["job"]["subtitles"][0]["text"], "Edited in Studio")

        # PUT global settings -> updates snapshot and marks mix/render stale.
        code, _, raw = self._req(
            "PUT",
            "/api/jobs/demo_job/settings",
            token=self.token,
            body={"subtitle_size": 56, "bgm_volume": 0.14},
        )
        self.assertEqual(code, 200)
        settings_res = json.loads(raw)
        self.assertEqual(settings_res["job"]["settings"]["subtitle_size"], 56)
        self.assertEqual(settings_res["job"]["settings"]["bgm_volume"], 0.14)

        # Invalid global settings are rejected with 400.
        code_bad_set, _, _ = self._req(
            "PUT",
            "/api/jobs/demo_job/settings",
            token=self.token,
            body={"subtitle_size": -10, "subtitle_render": "invalid"},
        )
        self.assertEqual(code_bad_set, 400)

        # POST import with crafted traversal filename ".." is safely sanitized inside temp dir.
        srt_b64 = base64.b64encode(b"1\n00:00:00,200 --> 00:00:01,500\nUploaded SRT\n").decode("ascii")
        code, _, raw = self._req(
            "POST",
            "/api/jobs/demo_job/import",
            token=self.token,
            body={
                "kind": "subtitles",
                "filename": "../../..",
                "content_base64": srt_b64,
                "dry_run": True,
            },
        )
        self.assertEqual(code, 200)
        imp_res = json.loads(raw)
        self.assertTrue(imp_res["dry_run"])
        self.assertEqual(imp_res["affected_stages"], ["render", "qa"])

    def test_runner_cancel_queued_job_does_not_deadlock(self) -> None:
        # Hold the worker by pre-marking a queued job canceled before it runs.
        runner = self.state.runner
        with runner._lock:
            runner._canceled_jobs.add("canceled_job")
            runner._pending_ids.append("canceled_job")
        from reachreel.server.runner import _QueuedRun

        runner._queue.put(_QueuedRun(job_id="canceled_job", workdir=self.workdir, stages=["qa"]))
        # Worker must process the canceled item and remain responsive without deadlocking.
        import time

        deadline = time.time() + 2.0
        while time.time() < deadline:
            events = runner.read_events(self.workdir)
            if any(ev.get("job") == "canceled_job" and ev.get("status") == "canceled" for ev in events):
                break
            time.sleep(0.02)
        events = runner.read_events(self.workdir)
        self.assertTrue(
            any(ev.get("job") == "canceled_job" and ev.get("status") == "canceled" for ev in events)
        )


if __name__ == "__main__":
    unittest.main()
