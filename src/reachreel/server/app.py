"""Local studio HTTP server for interactive ReachReel fine-tuning."""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import secrets
import shutil
import threading
import webbrowser
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .. import __version__
from ..core.cache import tts_cache_dir
from ..core.config import STYLE_PRESETS, Settings
from ..core.util import atomic_write_json, host_platform, read_json
from ..domain.cuts import load_cuts, parse_ranges, parse_timestamp
from ..domain.models import Cue
from ..engines.tts import (
    KOKORO_EN_GB_VOICES,
    KOKORO_EN_US_VOICES,
    KOKORO_ZH_VOICES,
    QWEN3_EN_VOICES,
    QWEN3_ZH_VOICES,
    synthesize_segments,
    voice_metadata,
)
from ..media.audio import AUDIO_EXTS
from ..media.edit_export import NARRATION_MODES
from ..media.scenes import load_scenes
from ..media.subtitles import read_srt, write_srt
from ..pipeline.orchestrator import (
    EDIT_FORMATS,
    MANIFEST_SCHEMA_VERSION,
    Pipeline,
    _load_segments,
    doctor_report,
    workdir_status,
)
from .runner import JobRunner, expand_stage_dependencies

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_JSON_BODY_BYTES = 128 * 1024 * 1024  # 128 MB cap for uploaded audio/XML payloads.

_PREVIEW_ROOT_FILES = {
    "final.mp4",
    "narration.wav",
    "mix.wav",
    "subtitles.srt",
    "subtitles.ass",
    "bgm_loop.wav",
}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")

_EDITABLE_GLOBAL_SETTINGS: dict[str, tuple[type, str]] = {
    "subtitle_size": (int, "render"),
    "subtitle_max_chars": (int, "subtitles"),
    "subtitle_preset": (str, "render"),
    "subtitle_render": (str, "render"),
    "subtitle_outline": (float, "render"),
    "subtitle_box_padding": (float, "render"),
    "subtitle_margin_v": (int, "render"),
    "subtitle_margin_left": (int, "render"),
    "subtitle_margin_right": (int, "render"),
    "burn_subtitles": (bool, "render"),
    "bgm_volume": (float, "mix"),
    "bgm_duck_db": (float, "mix"),
    "bgm_fade_seconds": (float, "mix"),
    "ambient_volume": (float, "mix"),
    "ambient_duck_db": (float, "mix"),
    "target_lufs": (float, "mix"),
    "output_height": (int, "render"),
    "output_fps": (float, "render"),
    "crf": (int, "render"),
}


_VALID_SUBTITLE_RENDERS = {"outline", "box", "none"}
_VALID_SUBTITLE_PRESETS = {"", "douyin", "reels", "shorts", "landscape"}


def _safe_upload_name(raw: str) -> str:
    """Sanitize an uploaded filename into a single safe path segment."""
    base = Path((raw or "").replace("\\", "/")).name
    if base in {"", ".", ".."}:
        return "upload.bin"
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", base).strip(".")
    if not cleaned or cleaned in {".", ".."}:
        return "upload.bin"
    return cleaned


def _validate_global_setting(key: str, val: Any) -> Any:
    """Validate bounds for global settings edited from the studio UI."""
    if key == "subtitle_size" and not (12 <= val <= 200):
        raise ValueError("subtitle_size must be between 12 and 200.")
    if key == "subtitle_max_chars" and not (0 <= val <= 120):
        raise ValueError("subtitle_max_chars must be between 0 and 120.")
    if key == "subtitle_preset":
        val = val.strip().lower()
        if val not in _VALID_SUBTITLE_PRESETS:
            raise ValueError(f"Unsupported subtitle_preset: {val!r}")
    if key == "subtitle_render":
        val = val.strip().lower()
        if val not in _VALID_SUBTITLE_RENDERS:
            raise ValueError(f"Unsupported subtitle_render: {val!r}")
    if key in {"subtitle_outline", "subtitle_box_padding"} and not (0.0 <= val <= 50.0):
        raise ValueError(f"{key} must be between 0 and 50.")
    if key in {"subtitle_margin_v", "subtitle_margin_left", "subtitle_margin_right"} and not (0 <= val <= 800):
        raise ValueError(f"{key} must be between 0 and 800.")
    if key in {"bgm_volume", "ambient_volume"} and not (0.0 <= val <= 2.0):
        raise ValueError(f"{key} must be between 0.0 and 2.0.")
    if key in {"bgm_duck_db", "ambient_duck_db"} and not (-48.0 <= val <= 0.0):
        raise ValueError(f"{key} must be between -48.0 and 0.0 dB.")
    if key == "bgm_fade_seconds" and not (0.0 <= val <= 30.0):
        raise ValueError("bgm_fade_seconds must be between 0 and 30.")
    if key == "target_lufs" and not (-36.0 <= val <= -3.0):
        raise ValueError("target_lufs must be between -36.0 and -3.0.")
    if key == "output_height" and not (0 <= val <= 4320):
        raise ValueError("output_height must be between 0 and 4320.")
    if key == "output_fps" and not (0.0 <= val <= 120.0):
        raise ValueError("output_fps must be between 0 and 120.")
    if key == "crf" and not (0 <= val <= 51):
        raise ValueError("crf must be between 0 and 51.")
    return val


def _read_dict(path: Path) -> dict[str, Any]:
    data = read_json(path, {})
    return data if isinstance(data, dict) else {}


def _is_allowed_preview_path(workdir: Path, relpath: str) -> Path | None:
    cleaned = relpath.strip().lstrip("/")
    if not cleaned or "\\" in cleaned:
        return None
    parts = Path(cleaned).parts
    if not parts or any(part in {"..", "."} for part in parts):
        return None
    allowed = (
        (len(parts) == 1 and parts[0] in _PREVIEW_ROOT_FILES)
        or (len(parts) == 2 and parts[0] == "tts" and Path(parts[1]).suffix.lower() == ".wav")
        or (len(parts) == 2 and parts[0] == "imports" and Path(parts[1]).suffix.lower() in AUDIO_EXTS)
        or (len(parts) == 2 and parts[0] == "frames" and Path(parts[1]).suffix.lower() in _IMAGE_EXTS)
    )
    if not allowed:
        return None
    resolved_workdir = workdir.resolve()
    candidate = (resolved_workdir / cleaned).resolve()
    if not candidate.is_relative_to(resolved_workdir) or not candidate.is_file():
        return None
    return candidate


def _write_token_file(token: str) -> Path | None:
    try:
        token_dir = Path.home() / ".reachreel"
        token_dir.mkdir(parents=True, exist_ok=True)
        token_path = token_dir / "token"
        token_path.write_text(token + "\n", encoding="utf-8")
        os.chmod(token_path, 0o600)
        return token_path
    except OSError:
        return None


class StudioState:
    """Shared state for the local studio HTTP server."""

    def __init__(
        self,
        *,
        work_root: Path,
        pinned_workdir: Path | None = None,
        config_path: Path | None = None,
        token: str | None = None,
        default_lang: str = "zh-CN",
    ) -> None:
        self.work_root = work_root.expanduser().resolve()
        self.pinned_workdir = pinned_workdir.expanduser().resolve() if pinned_workdir else None
        self.config_path = config_path.expanduser().resolve() if config_path else None
        self.token = token or secrets.token_urlsafe(24)
        self.default_lang = default_lang
        self.runner = JobRunner()
        self._lock = threading.Lock()

    def discover_jobs(self) -> dict[str, Path]:
        jobs: dict[str, Path] = {}
        if self.pinned_workdir is not None and self.pinned_workdir.is_dir():
            jobs[self.pinned_workdir.name] = self.pinned_workdir
        if self.work_root.is_dir():
            candidates: list[tuple[float, Path]] = []
            for entry in self.work_root.iterdir():
                if not entry.is_dir() or entry.name.startswith("."):
                    continue
                if any((entry / marker).is_file() for marker in ("manifest.json", "scenes.json")):
                    try:
                        mtime = entry.stat().st_mtime
                    except OSError:
                        mtime = 0.0
                    candidates.append((mtime, entry.resolve()))
            candidates.sort(key=lambda item: item[0], reverse=True)
            for _, path in candidates:
                jobs.setdefault(path.name, path)
        return jobs

    def resolve_job(self, job_id: str) -> Path | None:
        if not job_id or "/" in job_id or "\\" in job_id or job_id in {".", ".."}:
            return None
        return self.discover_jobs().get(job_id)

    def load_pipeline(self, workdir: Path) -> tuple[Pipeline, dict[str, Any]]:
        manifest = _read_dict(workdir / "manifest.json")
        source = manifest if manifest else _read_dict(workdir / "scenes.json")
        video_raw = str((source.get("video") or {}).get("path") or "").strip()
        video = Path(video_raw) if video_raw else workdir / "source.mp4"
        old_settings = manifest.get("settings") or {}
        snapshot = manifest.get("settings_snapshot") or {}
        render_settings = manifest.get("render_settings") or {}
        modern = manifest.get("schema_version") == MANIFEST_SCHEMA_VERSION
        saved = {**render_settings, **snapshot} if modern else {**snapshot, **render_settings}
        inherited = {
            **{
                key: old_settings.get(key)
                for key in ("vlm_model", "llm_model", "tts_engine", "voice", "whisper_model")
            },
            **saved,
        }
        settings = Settings.load(self.config_path, inherited=inherited)
        pipeline = Pipeline(
            video,
            settings,
            workdir=workdir,
            resume=True,
            force=False,
            create_dir=False,
        )
        bgm_source = manifest.get("bgm_source") or old_settings.get("bgm")
        if bgm_source:
            pipeline.bgm_source = Path(bgm_source)
        return pipeline, manifest

    def job_card(self, job_id: str, workdir: Path) -> dict[str, Any]:
        status = workdir_status(workdir)
        thumb = None
        frames_dir = workdir / "frames"
        if frames_dir.is_dir():
            first_frame = next(
                (p for p in sorted(frames_dir.iterdir()) if p.suffix.lower() in _IMAGE_EXTS),
                None,
            )
            if first_frame is not None:
                thumb = f"/api/jobs/{job_id}/preview/frames/{first_frame.name}"
        return {
            "id": job_id,
            "workdir": str(workdir),
            "complete": status["complete"],
            "video": status["video"],
            "cuts": status["cuts"],
            "qa": status["qa"],
            "pending_stages": status["pending_stages"],
            "runtime_state": self.runner.job_state(job_id),
            "thumbnail": thumb,
            "has_final": (workdir / "final.mp4").is_file(),
        }

    def job_detail(self, job_id: str, workdir: Path) -> dict[str, Any]:
        status = workdir_status(workdir)
        manifest = _read_dict(workdir / "manifest.json")
        script_data = _read_dict(workdir / "script.json")
        timeline_data = _read_dict(workdir / "timeline.json")
        qa_data = _read_dict(workdir / "qa.json")

        # Merge script segments with latest timeline placement so UI shows both editable fields & timing.
        script_segs = _load_segments(script_data)
        tl_by_id = {seg.id: seg for seg in _load_segments(timeline_data)}
        merged_segments: list[dict[str, Any]] = []
        for seg in script_segs:
            tl = tl_by_id.get(seg.id)
            item = {
                "id": seg.id,
                "scene_ids": list(seg.scene_ids),
                "text": seg.text,
                "voice": seg.voice,
                "instruct": seg.instruct,
                "speed": seg.speed,
                "start_override": seg.start_override,
                "gain_db": seg.gain_db,
                "audio_override": seg.audio_override,
                "enabled": seg.enabled,
                "start": tl.start if tl is not None else seg.start,
                "end": tl.end if tl is not None else seg.end,
                "duration": tl.duration if tl is not None else seg.duration,
                "tts_speed": tl.tts_speed if tl is not None else seg.tts_speed,
                "audio_url": None,
            }
            audio_raw = (tl.audio_path if tl is not None and tl.audio_path else seg.audio_path) or ""
            if not audio_raw and seg.audio_override:
                audio_raw = seg.audio_override
            if audio_raw:
                audio_path = Path(audio_raw)
                if not audio_path.is_absolute():
                    audio_path = (workdir / audio_path).resolve()
                else:
                    audio_path = audio_path.resolve()
                if audio_path.is_file() and audio_path.is_relative_to(workdir.resolve()):
                    rel = audio_path.relative_to(workdir.resolve()).as_posix()
                    item["audio_url"] = f"/api/jobs/{job_id}/preview/{rel}"
            merged_segments.append(item)

        cuts_obj = None
        try:
            cuts_obj = load_cuts(workdir / "cuts.json")
        except (ValueError, TypeError):
            cuts_obj = None

        scenes_out: list[dict[str, Any]] = []
        if (workdir / "scenes.json").is_file():
            try:
                _, raw_scenes = load_scenes(workdir / "scenes.json")
                active_scenes = (
                    cuts_obj.project_scenes(raw_scenes)[0]
                    if cuts_obj is not None
                    else raw_scenes
                )
                for scene in active_scenes:
                    frame_urls: list[str] = []
                    for frame in scene.frames:
                        fp = Path(frame)
                        if not fp.is_absolute():
                            fp = (workdir / fp).resolve()
                        else:
                            fp = fp.resolve()
                        if fp.is_file() and fp.is_relative_to(workdir.resolve()):
                            rel = fp.relative_to(workdir.resolve()).as_posix()
                            frame_urls.append(f"/api/jobs/{job_id}/preview/{rel}")
                    entry = scene.to_dict()
                    entry["frame_urls"] = frame_urls
                    scenes_out.append(entry)
            except (OSError, ValueError, KeyError, TypeError):
                pass

        cues = [asdict(cue) for cue in read_srt(workdir / "subtitles.srt")]

        history_dir = workdir / ".history"
        history_count = (
            sum(1 for _ in history_dir.glob("*.json")) if history_dir.is_dir() else 0
        )

        snapshot = manifest.get("settings_snapshot") or {}
        render_settings = manifest.get("render_settings") or {}
        merged_settings = {**render_settings, **snapshot}

        return {
            "id": job_id,
            "workdir": str(workdir),
            "status": status,
            "runtime_state": self.runner.job_state(job_id),
            "video": status["video"],
            "has_final": (workdir / "final.mp4").is_file(),
            "has_source": bool(status["video"].get("exists")),
            "final_url": f"/api/jobs/{job_id}/preview/final.mp4"
            if (workdir / "final.mp4").is_file()
            else None,
            "source_url": f"/api/jobs/{job_id}/source-video"
            if status["video"].get("exists")
            else None,
            "narration_url": f"/api/jobs/{job_id}/preview/narration.wav"
            if (workdir / "narration.wav").is_file()
            else None,
            "scenes": scenes_out,
            "segments": merged_segments,
            "cuts": cuts_obj.to_dict() if cuts_obj is not None else None,
            "subtitles": cues,
            "settings": merged_settings,
            "qa": qa_data if qa_data else None,
            "history_count": history_count,
            "events": self.runner.read_events(workdir, since=0)[-80:],
        }


def _voices_payload() -> dict[str, Any]:
    voices = {
        "kokoro": {
            "zh-CN": KOKORO_ZH_VOICES,
            "en-US": KOKORO_EN_US_VOICES,
            "en-GB": KOKORO_EN_GB_VOICES,
        },
        "qwen3": {
            "zh-CN": QWEN3_ZH_VOICES,
            "en-US": QWEN3_EN_VOICES,
            "en-GB": QWEN3_EN_VOICES,
        },
    }
    details = {
        name: voice_metadata(name)
        for groups in voices.values()
        for names in groups.values()
        for name in names
    }
    return {
        "style_presets": list(STYLE_PRESETS),
        "voices": voices,
        "details": details,
    }


def _make_handler(state: StudioState) -> type[BaseHTTPRequestHandler]:
    class StudioHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:
            # Keep server stdout/stderr clean unless debugging.
            return

        # ---------- response helpers ----------

        def _send_json(self, status: int, payload: Any) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_error_json(self, status: int, code: str, message: str) -> None:
            self._send_json(status, {"ok": False, "error": {"code": code, "message": message}})

        def _read_json_body(self) -> dict[str, Any]:
            raw_len = self.headers.get("Content-Length")
            if not raw_len:
                return {}
            try:
                length = int(raw_len)
            except ValueError as exc:
                raise ValueError("Invalid Content-Length header.") from exc
            if length < 0 or length > MAX_JSON_BODY_BYTES:
                raise ValueError("Request body too large.")
            if length == 0:
                return {}
            raw = self.rfile.read(length)
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise ValueError(f"Invalid JSON body: {exc}") from exc
            if not isinstance(parsed, dict):
                raise TypeError("JSON request body must be an object.")
            return parsed

        def _serve_file(self, path: Path, *, allow_range: bool = True) -> None:
            if not path.is_file():
                self._send_error_json(HTTPStatus.NOT_FOUND, "not_found", "File not found")
                return
            size = path.stat().st_size
            ctype, _ = mimetypes.guess_type(path.name)
            if not ctype:
                ctype = "application/octet-stream"
            elif ctype.startswith("text/") or ctype == "application/javascript":
                ctype = f"{ctype}; charset=utf-8"

            range_header = self.headers.get("Range") if allow_range else None
            start = 0
            end = size - 1
            status = HTTPStatus.OK

            if range_header and size > 0:
                match = _RANGE_RE.match(range_header.strip())
                if match:
                    s_str, e_str = match.group(1), match.group(2)
                    if s_str:
                        start = int(s_str)
                        end = int(e_str) if e_str else size - 1
                    elif e_str:
                        suffix = int(e_str)
                        start = max(0, size - suffix)
                        end = size - 1
                    if start >= size or end < start:
                        self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    end = min(end, size - 1)
                    status = HTTPStatus.PARTIAL_CONTENT

            length = max(0, end - start + 1) if size > 0 else 0
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            if status == HTTPStatus.PARTIAL_CONTENT:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Content-Length", str(length))
            self.end_headers()

            if length == 0:
                return
            try:
                with path.open("rb") as fh:
                    fh.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = fh.read(min(65536, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                return

        # ---------- security checks ----------

        def _check_origin(self) -> bool:
            sec_site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
            if sec_site == "cross-site":
                return False
            origin = (self.headers.get("Origin") or "").strip()
            if not origin:
                return True
            parsed_origin = urlparse(origin)
            host_header = (self.headers.get("Host") or "").strip()
            if not host_header:
                return True
            allowed_hosts = {host_header}
            if ":" in host_header:
                host_part, port_part = host_header.rsplit(":", 1)
                if host_part in {"127.0.0.1", "localhost"}:
                    allowed_hosts.add(f"127.0.0.1:{port_part}")
                    allowed_hosts.add(f"localhost:{port_part}")
            return parsed_origin.netloc in allowed_hosts

        def _check_auth(self, query: dict[str, list[str]], *, allow_query_token: bool = False) -> bool:
            if not self._check_origin():
                self._send_error_json(
                    HTTPStatus.FORBIDDEN, "cross_origin", "Cross-origin requests are forbidden."
                )
                return False
            header_tok = (self.headers.get("X-ReachReel-Token") or "").strip()
            if header_tok and secrets.compare_digest(header_tok, state.token):
                return True
            if allow_query_token:
                q_tok = (query.get("token") or [""])[0].strip()
                if q_tok and secrets.compare_digest(q_tok, state.token):
                    return True
            self._send_error_json(
                HTTPStatus.UNAUTHORIZED, "unauthorized", "Missing or invalid X-ReachReel-Token."
            )
            return False

        # ---------- routing ----------

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            query = parse_qs(parsed.query)

            # Static UI shell
            if path in {"/", "/index.html"}:
                self._serve_file(STATIC_DIR / "index.html", allow_range=False)
                return
            if path.startswith("/static/"):
                rel = path.removeprefix("/static/")
                if not rel or ".." in rel or "/" in rel or "\\" in rel:
                    self._send_error_json(HTTPStatus.NOT_FOUND, "not_found", "Asset not found")
                    return
                self._serve_file(STATIC_DIR / rel, allow_range=False)
                return

            if path == "/api/health":
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "version": __version__,
                        "platform": host_platform(),
                        "default_lang": state.default_lang,
                        "work_root": str(state.work_root),
                        "pinned_job": state.pinned_workdir.name if state.pinned_workdir else None,
                        **state.runner.status_summary(),
                    },
                )
                return

            allow_q = "/preview/" in path or path.endswith(("/source-video", "/events"))
            if not self._check_auth(query, allow_query_token=allow_q):
                return

            if path == "/api/doctor":
                settings = Settings.load(state.config_path)
                self._send_json(HTTPStatus.OK, doctor_report(settings))
                return

            if path == "/api/voices":
                self._send_json(HTTPStatus.OK, _voices_payload())
                return

            if path == "/api/jobs":
                jobs = state.discover_jobs()
                cards = [state.job_card(jid, wd) for jid, wd in jobs.items()]
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "pinned_job": state.pinned_workdir.name if state.pinned_workdir else None,
                        "jobs": cards,
                    },
                )
                return

            # Job-scoped GET routes
            parts = [p for p in path.split("/") if p]
            if len(parts) >= 3 and parts[0] == "api" and parts[1] == "jobs":
                job_id = parts[2]
                workdir = state.resolve_job(job_id)
                if workdir is None:
                    self._send_error_json(HTTPStatus.NOT_FOUND, "job_not_found", f"Unknown job: {job_id}")
                    return

                if len(parts) == 3:
                    self._send_json(HTTPStatus.OK, state.job_detail(job_id, workdir))
                    return

                sub = parts[3]
                if sub == "cuts" and len(parts) == 4:
                    cuts_obj = load_cuts(workdir / "cuts.json")
                    self._send_json(
                        HTTPStatus.OK,
                        {"ok": True, "cuts": cuts_obj.to_dict() if cuts_obj else None},
                    )
                    return

                if sub == "subtitles" and len(parts) == 4:
                    cues = [asdict(cue) for cue in read_srt(workdir / "subtitles.srt")]
                    self._send_json(HTTPStatus.OK, {"ok": True, "cues": cues})
                    return

                if sub == "events" and len(parts) == 4:
                    since = 0
                    try:
                        since = int((query.get("since") or ["0"])[0])
                    except ValueError:
                        since = 0
                    self._stream_events(job_id, workdir, since=since)
                    return

                if sub == "source-video" and len(parts) == 4:
                    manifest = _read_dict(workdir / "manifest.json")
                    src = str((manifest.get("video") or {}).get("path") or "").strip()
                    src_path = Path(src) if src else Path()
                    if not src or not src_path.is_file():
                        self._send_error_json(
                            HTTPStatus.NOT_FOUND, "source_missing", "Source video not found."
                        )
                        return
                    self._serve_file(src_path, allow_range=True)
                    return

                if sub == "preview" and len(parts) >= 5:
                    rel = "/".join(parts[4:])
                    target = _is_allowed_preview_path(workdir, rel)
                    if target is None:
                        self._send_error_json(
                            HTTPStatus.NOT_FOUND, "preview_not_found", f"Preview not available: {rel}"
                        )
                        return
                    self._serve_file(target, allow_range=True)
                    return

            self._send_error_json(HTTPStatus.NOT_FOUND, "not_found", f"Unknown route: {path}")

        def _stream_events(self, job_id: str, workdir: Path, *, since: int) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                for event in state.runner.subscribe(job_id, workdir, since=since):
                    data = json.dumps(event, ensure_ascii=False)
                    chunk = f"event: {event.get('type', 'message')}\ndata: {data}\n\n"
                    self.wfile.write(chunk.encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

        def do_PATCH(self) -> None:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            query = parse_qs(parsed.query)
            if not self._check_auth(query):
                return

            parts = [p for p in path.split("/") if p]
            # PATCH /api/jobs/{id}/segments/{seg_id}
            if len(parts) == 5 and parts[0] == "api" and parts[1] == "jobs" and parts[3] == "segments":
                job_id = parts[2]
                workdir = state.resolve_job(job_id)
                if workdir is None:
                    self._send_error_json(HTTPStatus.NOT_FOUND, "job_not_found", f"Unknown job: {job_id}")
                    return
                if not parts[4].isdigit():
                    self._send_error_json(HTTPStatus.BAD_REQUEST, "invalid_segment", "Invalid segment ID.")
                    return
                seg_id = int(parts[4])
                try:
                    body = self._read_json_body()
                    pipeline, _ = state.load_pipeline(workdir)
                    start_arg: float | str | None = None
                    if "start_override" in body:
                        raw_start = body["start_override"]
                        if raw_start is None or (
                            isinstance(raw_start, str) and raw_start.strip().lower() in {"", "auto"}
                        ):
                            start_arg = "auto"
                        elif isinstance(raw_start, str):
                            start_arg = parse_timestamp(raw_start)
                        else:
                            start_arg = float(raw_start)
                    res = pipeline.tune(
                        segment_id=seg_id,
                        text=body.get("text") if "text" in body else None,
                        voice=body.get("voice") if "voice" in body else None,
                        instruct=body.get("instruct") if "instruct" in body else None,
                        speed=float(body["speed"]) if "speed" in body and body["speed"] is not None else None,
                        start=start_arg,
                        gain_db=float(body["gain_db"]) if "gain_db" in body and body["gain_db"] is not None else None,
                        enabled=bool(body["enabled"]) if "enabled" in body and body["enabled"] is not None else None,
                        clear_audio_override=bool(body.get("clear_audio_override", False)),
                    )
                except (OSError, RuntimeError, TypeError, ValueError) as exc:
                    self._send_error_json(HTTPStatus.BAD_REQUEST, "tune_failed", str(exc))
                    return
                self._send_json(HTTPStatus.OK, {**res, "job": state.job_detail(job_id, workdir)})
                return

            self._send_error_json(HTTPStatus.NOT_FOUND, "not_found", f"Unknown route: {path}")

        def do_PUT(self) -> None:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            query = parse_qs(parsed.query)
            if not self._check_auth(query):
                return

            parts = [p for p in path.split("/") if p]
            if len(parts) == 4 and parts[0] == "api" and parts[1] == "jobs":
                job_id, sub = parts[2], parts[3]
                workdir = state.resolve_job(job_id)
                if workdir is None:
                    self._send_error_json(HTTPStatus.NOT_FOUND, "job_not_found", f"Unknown job: {job_id}")
                    return
                try:
                    body = self._read_json_body()
                except (TypeError, ValueError) as exc:
                    self._send_error_json(HTTPStatus.BAD_REQUEST, "invalid_json", str(exc))
                    return

                if sub == "cuts":
                    try:
                        pipeline, _ = state.load_pipeline(workdir)
                        if body.get("reset"):
                            res = pipeline.tune(reset_cuts=True)
                        elif "cut_ranges" in body and isinstance(body["cut_ranges"], str):
                            res = pipeline.tune(cut_ranges=parse_ranges(body["cut_ranges"]))
                        elif "clips" in body and isinstance(body["clips"], list):
                            ranges = [(float(c["start"]), float(c["end"])) for c in body["clips"]]
                            res = pipeline.tune(keep_ranges=ranges)
                        else:
                            raise ValueError("Provide 'clips', 'cut_ranges', or 'reset: true'.")
                    except (OSError, RuntimeError, TypeError, ValueError, KeyError) as exc:
                        self._send_error_json(HTTPStatus.BAD_REQUEST, "cuts_failed", str(exc))
                        return
                    self._send_json(HTTPStatus.OK, {**res, "job": state.job_detail(job_id, workdir)})
                    return

                if sub == "subtitles":
                    try:
                        raw_cues = body.get("cues")
                        if not isinstance(raw_cues, list) or not raw_cues:
                            raise ValueError("Subtitles payload must contain a non-empty 'cues' list.")
                        cues = [
                            Cue(
                                start=float(item["start"]),
                                end=float(item["end"]),
                                text=str(item["text"]),
                            )
                            for item in raw_cues
                        ]
                        pipeline, _ = state.load_pipeline(workdir)
                        with TemporaryDirectory(prefix=".reachreel-srt-") as tmp:
                            staged = Path(tmp) / "edited.srt"
                            write_srt(cues, staged)
                            res = pipeline.import_artifacts(subtitles_file=staged)
                    except (OSError, RuntimeError, TypeError, ValueError, KeyError) as exc:
                        self._send_error_json(HTTPStatus.BAD_REQUEST, "subtitles_failed", str(exc))
                        return
                    self._send_json(HTTPStatus.OK, {**res, "job": state.job_detail(job_id, workdir)})
                    return

                if sub == "settings":
                    try:
                        manifest = _read_dict(workdir / "manifest.json")
                        snapshot = dict(manifest.get("settings_snapshot") or {})
                        render_settings = dict(manifest.get("render_settings") or {})
                        artifact_inputs = dict(manifest.get("artifact_inputs") or {})
                        touched_stages: set[str] = set()
                        updated: dict[str, Any] = {}
                        for key, raw_val in body.items():
                            spec = _EDITABLE_GLOBAL_SETTINGS.get(key)
                            if spec is None:
                                continue
                            expected_type, owner_stage = spec
                            if expected_type is bool:
                                val = bool(raw_val)
                            elif expected_type is int:
                                val = int(raw_val)
                            elif expected_type is float:
                                val = float(raw_val)
                            else:
                                val = str(raw_val)
                            val = _validate_global_setting(key, val)
                            snapshot[key] = val
                            if key in render_settings or owner_stage == "render":
                                render_settings[key] = val
                            updated[key] = val
                            touched_stages.add(owner_stage)
                        if not updated:
                            raise ValueError("No supported global settings were provided.")
                        manifest["settings_snapshot"] = snapshot
                        if render_settings:
                            manifest["render_settings"] = render_settings
                        stamp = secrets.token_hex(6)
                        for st in touched_stages:
                            existing_st = dict(artifact_inputs.get(st) or {})
                            existing_st["studio_settings_rev"] = stamp
                            artifact_inputs[st] = existing_st
                        manifest["artifact_inputs"] = artifact_inputs
                        atomic_write_json(workdir / "manifest.json", manifest)
                    except (OSError, TypeError, ValueError) as exc:
                        self._send_error_json(HTTPStatus.BAD_REQUEST, "settings_failed", str(exc))
                        return
                    self._send_json(
                        HTTPStatus.OK,
                        {"ok": True, "updated": updated, "job": state.job_detail(job_id, workdir)},
                    )
                    return

            self._send_error_json(HTTPStatus.NOT_FOUND, "not_found", f"Unknown route: {path}")

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            query = parse_qs(parsed.query)
            if not self._check_auth(query):
                return

            parts = [p for p in path.split("/") if p]
            if len(parts) < 4 or parts[0] != "api" or parts[1] != "jobs":
                self._send_error_json(HTTPStatus.NOT_FOUND, "not_found", f"Unknown route: {path}")
                return

            job_id = parts[2]
            workdir = state.resolve_job(job_id)
            if workdir is None:
                self._send_error_json(HTTPStatus.NOT_FOUND, "job_not_found", f"Unknown job: {job_id}")
                return

            try:
                body = self._read_json_body()
            except (TypeError, ValueError) as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "invalid_json", str(exc))
                return

            # POST /api/jobs/{id}/segments/{seg_id}/audition
            if len(parts) == 6 and parts[3] == "segments" and parts[5] == "audition":
                if not parts[4].isdigit():
                    self._send_error_json(HTTPStatus.BAD_REQUEST, "invalid_segment", "Invalid segment ID.")
                    return
                seg_id = int(parts[4])
                try:
                    pipeline, _ = state.load_pipeline(workdir)
                    segments = _load_segments(_read_dict(workdir / "script.json"))
                    found = next(
                        ((idx, s) for idx, s in enumerate(segments, start=1) if s.id == seg_id),
                        None,
                    )
                    if found is None:
                        raise ValueError(f"Segment {seg_id} not found.")
                    seg_index, target = found
                    if not target.enabled:
                        raise ValueError(f"Segment {seg_id} is disabled; enable it before auditioning.")
                    shared_dir = (
                        tts_cache_dir(pipeline.shared_cache_root)
                        if pipeline.shared_cache_root is not None
                        else None
                    )
                    synthesize_segments(
                        [target],
                        pipeline.tts_dir,
                        pipeline.settings,
                        speed=pipeline.settings.tts_speed,
                        force=bool(body.get("force", False)),
                        shared_dir=shared_dir,
                        workdir=workdir,
                        start_index=seg_index,
                    )
                    audio_path = Path(target.audio_path).resolve()
                    if not audio_path.is_file() or not audio_path.is_relative_to(workdir.resolve()):
                        raise RuntimeError("Segment synthesis produced no readable audio file.")
                    rel = audio_path.relative_to(workdir.resolve()).as_posix()
                except (OSError, RuntimeError, TypeError, ValueError) as exc:
                    self._send_error_json(HTTPStatus.BAD_REQUEST, "audition_failed", str(exc))
                    return
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "segment_id": seg_id,
                        "duration": round(target.duration, 3),
                        "audio_url": f"/api/jobs/{job_id}/preview/{rel}",
                    },
                )
                return

            sub = parts[3]
            if len(parts) == 4 and sub == "undo":
                try:
                    pipeline, _ = state.load_pipeline(workdir)
                    res = pipeline.undo_last_edit(dry_run=bool(body.get("dry_run", False)))
                except (OSError, RuntimeError, TypeError, ValueError) as exc:
                    self._send_error_json(HTTPStatus.BAD_REQUEST, "undo_failed", str(exc))
                    return
                self._send_json(HTTPStatus.OK, {**res, "job": state.job_detail(job_id, workdir)})
                return

            if len(parts) == 4 and sub == "import":
                try:
                    kind = str(body.get("kind") or "").strip().lower()
                    filename = _safe_upload_name(str(body.get("filename") or "upload.bin"))
                    b64 = str(body.get("content_base64") or "")
                    dry_run = bool(body.get("dry_run", False))
                    if not kind or not b64:
                        raise ValueError("Import requires 'kind' and 'content_base64'.")
                    raw_bytes = base64.b64decode(b64, validate=True)
                    pipeline, _ = state.load_pipeline(workdir)
                    with TemporaryDirectory(prefix=".reachreel-upload-") as tmp:
                        tmp_root = Path(tmp).resolve()
                        staged = (tmp_root / filename).resolve()
                        if not staged.is_relative_to(tmp_root):
                            raise ValueError("Invalid upload filename.")
                        staged.write_bytes(raw_bytes)
                        kwargs: dict[str, Any] = {"dry_run": dry_run}
                        if kind == "script":
                            kwargs["script_file"] = staged
                        elif kind == "subtitles":
                            kwargs["subtitles_file"] = staged
                        elif kind == "narration":
                            kwargs["narration_file"] = staged
                        elif kind == "segment_audio":
                            seg_id = int(body["segment_id"])
                            kwargs["segment_audio"] = [(seg_id, staged)]
                        elif kind == "bgm":
                            kwargs["bgm_file"] = staged
                        elif kind == "cuts":
                            kwargs["cuts_file"] = staged
                        elif kind == "timeline":
                            kwargs["timeline_file"] = staged
                        else:
                            raise ValueError(f"Unsupported import kind: {kind}")
                        res = pipeline.import_artifacts(**kwargs)
                except (OSError, RuntimeError, TypeError, ValueError, KeyError) as exc:
                    self._send_error_json(HTTPStatus.BAD_REQUEST, "import_failed", str(exc))
                    return
                self._send_json(HTTPStatus.OK, {**res, "job": state.job_detail(job_id, workdir)})
                return

            if len(parts) == 4 and sub == "export":
                try:
                    fmt = str(body.get("format") or "mp4").strip().lower()
                    mode = str(body.get("narration_mode") or "segments").strip().lower()
                    if fmt not in EDIT_FORMATS:
                        raise ValueError(f"Unsupported format: {fmt}")
                    if mode not in NARRATION_MODES:
                        raise ValueError(f"Unsupported narration_mode: {mode}")
                    pipeline, _ = state.load_pipeline(workdir)
                    dest = pipeline.export_edit(
                        video_format=fmt,
                        narration_mode=mode,
                        overwrite=True,
                    )
                except (OSError, RuntimeError, TypeError, ValueError) as exc:
                    self._send_error_json(HTTPStatus.BAD_REQUEST, "export_failed", str(exc))
                    return
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "output": str(dest),
                        "format": fmt,
                        "narration_mode": mode,
                    },
                )
                return

            if len(parts) == 4 and sub == "stages":
                try:
                    raw_stages = body.get("stages")
                    no_qa = bool(body.get("no_qa", False))
                    qa_strict = bool(body.get("qa_strict", False))
                    asr_engine = str(body.get("asr") or "mlx-whisper").strip()
                    if asr_engine not in {"mlx-whisper", "none"}:
                        raise ValueError(f"Unsupported ASR mode: {asr_engine}")
                    if raw_stages is None or raw_stages == "pending":
                        status = workdir_status(workdir)
                        stages = [
                            s for s in (status.get("pending_stages") or [])
                            if not (no_qa and s == "qa")
                        ]
                    elif isinstance(raw_stages, list):
                        stages = expand_stage_dependencies(
                            [str(s) for s in raw_stages],
                            include_qa=not no_qa,
                        )
                    else:
                        raise TypeError("'stages' must be a list of stage names or 'pending'.")
                    run_info = state.runner.enqueue(
                        job_id,
                        workdir,
                        stages,
                        asr_engine=asr_engine,
                        no_qa=no_qa,
                        qa_strict=qa_strict,
                        config_path=state.config_path,
                    )
                except (OSError, RuntimeError, TypeError, ValueError) as exc:
                    self._send_error_json(HTTPStatus.BAD_REQUEST, "stages_failed", str(exc))
                    return
                self._send_json(
                    HTTPStatus.ACCEPTED if run_info["queued"] else HTTPStatus.OK,
                    {
                        "ok": True,
                        **run_info,
                        "events_url": f"/api/jobs/{job_id}/events",
                    },
                )
                return

            if len(parts) == 4 and sub == "cancel":
                canceled = state.runner.cancel(job_id)
                self._send_json(HTTPStatus.OK, {"ok": True, "canceled": canceled})
                return

            if len(parts) == 4 and sub == "reveal":
                if shutil.which("open"):
                    import subprocess

                    target = workdir / "final.mp4" if (workdir / "final.mp4").is_file() else workdir
                    subprocess.Popen(["open", "-R", str(target)])
                self._send_json(HTTPStatus.OK, {"ok": True, "workdir": str(workdir)})
                return

            self._send_error_json(HTTPStatus.NOT_FOUND, "not_found", f"Unknown route: {path}")

    return StudioHandler


def create_server(
    *,
    work_root: Path,
    pinned_workdir: Path | None = None,
    config_path: Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8760,
    token: str | None = None,
    default_lang: str = "zh-CN",
    write_token_file: bool = True,
) -> tuple[ThreadingHTTPServer, StudioState]:
    """Create a configured ReachReel Studio HTTP server without blocking."""
    state = StudioState(
        work_root=work_root,
        pinned_workdir=pinned_workdir,
        config_path=config_path,
        token=token,
        default_lang=default_lang,
    )
    if write_token_file:
        _write_token_file(state.token)
    handler = _make_handler(state)
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    return httpd, state


def serve_studio(
    *,
    work_root: Path,
    pinned_workdir: Path | None = None,
    config_path: Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8760,
    open_browser: bool = False,
    default_lang: str = "zh-CN",
) -> int:
    """Start the local studio server and block until interrupted."""
    httpd, state = create_server(
        work_root=work_root,
        pinned_workdir=pinned_workdir,
        config_path=config_path,
        host=host,
        port=port,
        default_lang=default_lang,
        write_token_file=True,
    )
    actual_port = httpd.server_address[1]
    url = f"http://{host}:{actual_port}/#token={state.token}"
    print(f"ReachReel Studio running at http://{host}:{actual_port}/")
    print(f"Open with auth token: {url}")
    print("Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping ReachReel Studio...")
    finally:
        state.runner.stop()
        httpd.server_close()
    return 0
