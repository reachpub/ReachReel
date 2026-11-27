"""Exercise Ollama endpoint persistence through settings and the CLI."""
from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from reachreel.cli.main import main
from reachreel.core.config import Settings, settings_to_dict
from reachreel.core.util import atomic_write_json, read_json
from reachreel.domain.models import VideoInfo
from reachreel.i18n import current_language, set_language


class OllamaSettingsPersistenceTest(unittest.TestCase):
    credential_url = (
        "https://test-user:test-password@[::1]:11434/proxy"
        "?api_key=test-query-secret#test-fragment-secret"
    )
    endpoint = "https://[::1]:11434/proxy"

    def test_snapshot_excludes_credentials_without_changing_runtime_settings(self) -> None:
        settings = Settings(ollama_url=self.credential_url)

        snapshot = settings_to_dict(settings)

        self.assertEqual(snapshot["ollama_url"], self.endpoint)
        self.assertEqual(settings.ollama_url, self.credential_url)

    def test_snapshot_preserves_public_endpoints_and_omits_invalid_values(self) -> None:
        for url in (Settings().ollama_url, "https://ollama.example.net:10443/reverse-proxy/"):
            with self.subTest(url=url):
                self.assertEqual(settings_to_dict(Settings(ollama_url=url))["ollama_url"], url)
        for url in ("http://test-user:test-password@[invalid", "test-user:test-password@localhost"):
            with self.subTest(url=url):
                self.assertNotIn("ollama_url", settings_to_dict(Settings(ollama_url=url)))

    def test_resume_sanitizes_legacy_snapshot_and_preserves_explicit_overrides(self) -> None:
        legacy = {"ollama_url": self.credential_url}
        with patch.dict(os.environ, {}, clear=True):
            resumed = Settings.load(inherited=legacy)
            explicit = Settings.load(overrides={"ollama_url": self.credential_url}, inherited=legacy)

        self.assertEqual(resumed.ollama_url, self.endpoint)
        self.assertEqual(explicit.ollama_url, self.credential_url)

    def test_probe_only_writes_a_sanitized_snapshot(self) -> None:
        self.addCleanup(set_language, current_language())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "source.mp4"
            video.write_bytes(b"test video")
            job = root / "job"
            info = VideoInfo(str(video), 1.0, 160, 90, 24.0, False)
            with (
                redirect_stdout(io.StringIO()),
                patch("reachreel.cli.main.setup_logging"),
                patch("reachreel.pipeline.orchestrator.probe_video", return_value=info),
                patch.dict(os.environ, {}, clear=True),
            ):
                status = main([
                    "--no-cache", "run", str(video), "--workdir", str(job), "--only", "probe",
                    "--ollama-url", self.credential_url,
                ])

            self.assertEqual(status, 0)
            manifest = read_json(job / "manifest.json")
            self.assertEqual(manifest["settings_snapshot"]["ollama_url"], self.endpoint)

    def test_qa_does_not_write_legacy_credentials_back_to_disk(self) -> None:
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            workdir = Path(directory)
            manifest = workdir / "manifest.json"
            atomic_write_json(manifest, {"settings_snapshot": {"ollama_url": self.credential_url}})
            self.assertEqual(main(["qa", str(workdir), "--json"]), 1)
            self.assertEqual(read_json(manifest)["settings_snapshot"]["ollama_url"], self.endpoint)
