"""Single-worker subprocess job runner and JSONL event log for the studio server."""
from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..pipeline.orchestrator import STAGE_ORDER, workdir_status

STAGE_DOWNSTREAM: dict[str, tuple[str, ...]] = {
    "probe": ("vlm", "script", "tts", "subtitles", "mix", "render", "qa"),
    "vlm": ("script", "tts", "subtitles", "mix", "render", "qa"),
    "script": (),  # Let user review script before running downstream unless explicitly requested.
    "tts": ("subtitles", "mix", "render", "qa"),
    "subtitles": ("render", "qa"),
    "mix": ("render", "qa"),
    "render": ("qa",),
    "qa": (),
}


def expand_stage_dependencies(
    requested: list[str],
    *,
    include_qa: bool = True,
) -> list[str]:
    """Expand requested stages with required downstream dependencies in canonical order."""
    expanded: set[str] = set()
    for stage in requested:
        if stage not in STAGE_ORDER:
            raise ValueError(f"Unknown stage: {stage}")
        expanded.add(stage)
        expanded.update(STAGE_DOWNSTREAM.get(stage, ()))
    if not include_qa:
        expanded.discard("qa")
    return [stage for stage in STAGE_ORDER if stage in expanded]


@dataclass
class _QueuedRun:
    job_id: str
    workdir: Path
    stages: list[str]
    asr_engine: str = "mlx-whisper"
    no_qa: bool = False
    qa_strict: bool = False
    config_path: Path | None = None
    extra_args: list[str] = field(default_factory=list)


class JobRunner:
    """Serialize stage reruns on one background worker using child CLI processes."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queue: queue.Queue[_QueuedRun | None] = queue.Queue()
        self._pending_ids: list[str] = []
        self._active_job_id: str | None = None
        self._active_proc: subprocess.Popen[str] | None = None
        self._canceled_jobs: set[str] = set()
        self._subscribers: dict[str, list[queue.Queue[dict[str, Any]]]] = {}
        self._stopped = False
        self._worker = threading.Thread(
            target=self._loop,
            name="reachreel-studio-worker",
            daemon=True,
        )
        self._worker.start()

    def _terminate_proc(self, proc: subprocess.Popen[str] | None) -> None:
        if proc is None or proc.poll() is not None:
            return
        with suppress(OSError, ProcessLookupError):
            os.killpg(proc.pid, signal.SIGTERM)
        with suppress(OSError):
            proc.terminate()

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            proc = self._active_proc
        self._terminate_proc(proc)
        self._queue.put(None)

    def status_summary(self) -> dict[str, Any]:
        with self._lock:
            return {
                "busy": self._active_job_id is not None,
                "active_job": self._active_job_id,
                "queue_length": len(self._pending_ids),
                "queued_jobs": list(self._pending_ids),
            }

    def job_state(self, job_id: str) -> str:
        with self._lock:
            if self._active_job_id == job_id:
                return "running"
            if job_id in self._pending_ids:
                return "queued"
            return "idle"

    def enqueue(
        self,
        job_id: str,
        workdir: Path,
        stages: list[str],
        *,
        asr_engine: str = "mlx-whisper",
        no_qa: bool = False,
        qa_strict: bool = False,
        config_path: Path | None = None,
    ) -> dict[str, Any]:
        """Queue a stage run for ``job_id`` if not already active/queued."""
        if not stages:
            return {
                "queued": False,
                "job_id": job_id,
                "stages": [],
                "state": self.job_state(job_id),
            }
        item = _QueuedRun(
            job_id=job_id,
            workdir=workdir.resolve(),
            stages=list(stages),
            asr_engine=asr_engine,
            no_qa=no_qa,
            qa_strict=qa_strict,
            config_path=config_path,
        )
        with self._lock:
            if self._active_job_id == job_id or job_id in self._pending_ids:
                raise RuntimeError(f"Job {job_id} is already running or queued.")
            self._canceled_jobs.discard(job_id)
            self._pending_ids.append(job_id)
        self._emit(
            workdir,
            job_id,
            {"type": "job_queued", "job": job_id, "stages": list(stages)},
        )
        self._queue.put(item)
        return {
            "queued": True,
            "job_id": job_id,
            "stages": list(stages),
            "state": self.job_state(job_id),
        }

    def cancel(self, job_id: str) -> bool:
        proc: subprocess.Popen[str] | None = None
        canceled = False
        with self._lock:
            self._canceled_jobs.add(job_id)
            if self._active_job_id == job_id and self._active_proc is not None:
                proc = self._active_proc
                canceled = True
            elif job_id in self._pending_ids:
                canceled = True
        if proc is not None:
            self._terminate_proc(proc)
        return canceled

    def read_events(self, workdir: Path, *, since: int = 0) -> list[dict[str, Any]]:
        path = workdir / "events.jsonl"
        if not path.is_file():
            return []
        events: list[dict[str, Any]] = []
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if isinstance(item, dict) and int(item.get("seq") or 0) > since:
                    events.append(item)
        except OSError:
            return []
        return events

    def subscribe(self, job_id: str, workdir: Path, *, since: int = 0) -> Iterator[dict[str, Any]]:
        """Yield historical events after ``since`` and then live events until closed."""
        sub_q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=256)
        with self._lock:
            self._subscribers.setdefault(job_id, []).append(sub_q)
        try:
            last_seq = since
            for event in self.read_events(workdir, since=since):
                last_seq = max(last_seq, int(event.get("seq") or 0))
                yield event
            while not self._stopped:
                try:
                    event = sub_q.get(timeout=10.0)
                except queue.Empty:
                    yield {"type": "heartbeat", "t": round(time.time(), 3)}
                    continue
                seq = int(event.get("seq") or 0)
                if seq <= last_seq:
                    continue
                last_seq = seq
                yield event
        finally:
            with self._lock:
                subs = self._subscribers.get(job_id)
                if subs and sub_q in subs:
                    subs.remove(sub_q)

    def _next_seq(self, path: Path) -> int:
        if not path.is_file():
            return 1
        last_seq = 0
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    last_seq = max(last_seq, int(data.get("seq") or 0))
                except ValueError:
                    continue
        except OSError:
            return 1
        return last_seq + 1

    def _emit(self, workdir: Path, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        events_path = workdir / "events.jsonl"
        with self._lock:
            seq = self._next_seq(events_path)
            event = {"seq": seq, "t": round(time.time(), 3), **payload}
            try:
                events_path.parent.mkdir(parents=True, exist_ok=True)
                with events_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(event, ensure_ascii=False) + "\n")
            except OSError:
                pass
            subs = list(self._subscribers.get(job_id, ()))
        for sub_q in subs:
            try:
                sub_q.put_nowait(event)
            except queue.Full:
                pass
        return event

    def _build_stage_command(self, item: _QueuedRun, stage: str) -> list[str]:
        cmd = [sys.executable, "-m", "reachreel", "--lang", "en"]
        cfg = ["--config", str(item.config_path)] if item.config_path else []
        if stage == "vlm":
            report = workdir_status(item.workdir)
            video_path = report["video"].get("path") or ""
            return [*cmd, "analyze", str(video_path), "--workdir", str(item.workdir), *cfg]
        if stage == "script":
            return [*cmd, "script", str(item.workdir), *cfg]
        if stage == "tts":
            return [*cmd, "tts", str(item.workdir), *cfg]
        if stage == "subtitles":
            return [*cmd, "subtitle", str(item.workdir), "--asr", item.asr_engine, *cfg]
        if stage == "mix":
            return [*cmd, "mix", str(item.workdir), *cfg]
        if stage == "render":
            return [*cmd, "render", str(item.workdir), *cfg]
        if stage == "qa":
            extra = ["--strict"] if item.qa_strict else []
            return [*cmd, "qa", str(item.workdir), *cfg, *extra]
        raise ValueError(f"Unsupported stage for studio execution: {stage}")

    def _loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            canceled_before_start = False
            with self._lock:
                if item.job_id in self._pending_ids:
                    self._pending_ids.remove(item.job_id)
                if item.job_id in self._canceled_jobs:
                    self._canceled_jobs.discard(item.job_id)
                    canceled_before_start = True
                else:
                    self._active_job_id = item.job_id
            if canceled_before_start:
                self._emit(
                    item.workdir,
                    item.job_id,
                    {"type": "job_end", "job": item.job_id, "status": "canceled"},
                )
                continue

            started = time.time()
            self._emit(
                item.workdir,
                item.job_id,
                {"type": "job_start", "job": item.job_id, "stages": item.stages},
            )
            status = "done"
            error_msg: str | None = None
            exit_code = 0

            for stage in item.stages:
                if stage == "probe":
                    continue
                with self._lock:
                    if item.job_id in self._canceled_jobs:
                        status = "canceled"
                        break
                stage_start = time.time()
                self._emit(
                    item.workdir,
                    item.job_id,
                    {"type": "stage_start", "job": item.job_id, "stage": stage},
                )
                try:
                    cmd = self._build_stage_command(item, stage)
                    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
                    proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        env=env,
                        start_new_session=True,
                    )
                    with self._lock:
                        self._active_proc = proc
                    tail: list[str] = []
                    assert proc.stdout is not None
                    for raw_line in proc.stdout:
                        line = raw_line.rstrip()
                        if not line:
                            continue
                        tail.append(line)
                        if len(tail) > 20:
                            tail.pop(0)
                        self._emit(
                            item.workdir,
                            item.job_id,
                            {
                                "type": "log",
                                "job": item.job_id,
                                "stage": stage,
                                "msg": line,
                            },
                        )
                    rc = proc.wait()
                except Exception as exc:  # noqa: BLE001 - isolate worker exceptions
                    rc = 1
                    tail = [str(exc)]
                finally:
                    with self._lock:
                        self._active_proc = None

                elapsed = round(time.time() - stage_start, 2)
                with self._lock:
                    canceled = item.job_id in self._canceled_jobs
                if canceled:
                    status = "canceled"
                    self._emit(
                        item.workdir,
                        item.job_id,
                        {
                            "type": "stage_end",
                            "job": item.job_id,
                            "stage": stage,
                            "ok": False,
                            "elapsed": elapsed,
                        },
                    )
                    break
                ok = rc == 0
                self._emit(
                    item.workdir,
                    item.job_id,
                    {
                        "type": "stage_end",
                        "job": item.job_id,
                        "stage": stage,
                        "ok": ok,
                        "elapsed": elapsed,
                        "exit_code": rc,
                    },
                )
                if not ok:
                    status = "failed"
                    exit_code = rc
                    error_msg = "\n".join(tail[-5:]) if tail else f"Stage {stage} exited with {rc}"
                    break

            with self._lock:
                self._active_job_id = None
                self._active_proc = None
                self._canceled_jobs.discard(item.job_id)

            self._emit(
                item.workdir,
                item.job_id,
                {
                    "type": "job_end",
                    "job": item.job_id,
                    "status": status,
                    "exit_code": exit_code,
                    "error": error_msg,
                    "elapsed": round(time.time() - started, 2),
                },
            )
