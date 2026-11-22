"""Local VLM scene understanding through Ollama's OpenAI-compatible API."""
from __future__ import annotations

import base64
import json
import logging
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from ..core.util import atomic_write_json, read_json
from ..domain.models import QualityFlag, Scene
from ..prompts import load_prompt, text

LOG = logging.getLogger("reachreel.engines.vlm")

PROMPT_VERSION = "vf-vlm-2"


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, base_url: str, model: str, timeout: float = 300.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 700,
        retries: int = 3,
    ) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        body = json.dumps(payload).encode("utf-8")
        url = f"{self.base_url}/v1/chat/completions"
        last_error: Exception | None = None
        for attempt in range(1, retries + 1):
            request = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer ollama",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    data = json.loads(response.read().decode("utf-8"))
                message = data["choices"][0]["message"]
                return (message.get("content") or message.get("reasoning_content") or "").strip()
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                last_error = OllamaError(f"HTTP {exc.code}: {detail}")
            except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as exc:
                last_error = exc
            if attempt < retries:
                wait = min(2 ** attempt, 15)
                LOG.warning("VLM call failed (attempt %d); retrying in %ss: %s", attempt, wait, last_error)
                time.sleep(wait)
        raise OllamaError(f"Ollama call failed: {last_error}") from last_error


def _json_from_text(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
        return value if isinstance(value, dict) else {}
    except ValueError:
        pass
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if match:
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else {}
        except ValueError:
            return {}
    return {}


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _image_data_uri(path: Path) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def analyze_scene(
    client: OllamaClient,
    scene: Scene,
    *,
    max_width: int = 768,
    context: str = "",
    content_language: str = "zh",
) -> dict[str, Any]:
    frames = [Path(p) for p in scene.frames if Path(p).exists()]
    if not frames:
        return {
            "summary": text(
                content_language,
                "vlm.no_frames",
                id=scene.id,
                start=scene.start,
                end=scene.end,
            ),
            "place": text(content_language, "vlm.unknown"),
            "subjects": [],
            "actions": [],
            "details": [],
            "mood": "",
            "narration_hint": "",
            "quality": {
                "code": "vlm.no_frames",
                "params": {"scene": scene.id},
                "severity": "warning",
            },
        }

    content: list[dict[str, Any]] = []
    for frame in frames:
        content.append({"type": "image_url", "image_url": {"url": _image_data_uri(frame)}})
    context_line = ""
    if context:
        context_line = text(content_language, "vlm.context_label", context=context)
    content.append(
        {
            "type": "text",
            "text": load_prompt(content_language, "vlm").format(
                n=len(frames),
                context=context_line,
            ),
        }
    )
    raw = client.chat(
        [{"role": "user", "content": content}],
        temperature=0.2,
        max_tokens=700,
    )
    parsed = _json_from_text(raw)
    quality: dict[str, Any] | None = None
    if not parsed:
        parsed = {
            "summary": raw.strip()[:200]
            or text(content_language, "vlm.analysis_failed")
        }
        quality = {
            "code": "vlm.unparsed",
            "params": {"scene": scene.id},
            "severity": "warning",
        }
    result: dict[str, Any] = {
        "summary": str(parsed.get("summary") or "").strip(),
        "place": str(
            parsed.get("place") or text(content_language, "vlm.unknown")
        ).strip(),
        "subjects": _as_list(parsed.get("subjects")),
        "actions": _as_list(parsed.get("actions")),
        "details": _as_list(parsed.get("details")),
        "mood": str(parsed.get("mood") or "").strip(),
        "narration_hint": str(parsed.get("narration_hint") or "").strip(),
        "raw": raw[:1000],
    }
    if quality is not None:
        result["quality"] = quality
    return result


def analyze_scenes(
    scenes: list[Scene],
    *,
    base_url: str,
    model: str,
    workers: int = 2,
    cache_path: Path | None = None,
    max_width: int = 768,
    force: bool = False,
    context: str = "",
    content_language: str = "zh",
) -> list[QualityFlag]:
    cache = {} if force or not cache_path else (read_json(cache_path, {}) or {})
    settings_key = f"{content_language}|{model}|{PROMPT_VERSION}|{max_width}|{context}"
    if cache.get("settings") != settings_key:
        cache = {"settings": settings_key, "scenes": {}}
    scene_cache: dict[str, Any] = cache.setdefault("scenes", {})

    client = OllamaClient(base_url, model)
    pending = []
    for scene in scenes:
        key = f"{scene.id}:{scene.start:.3f}:{scene.end:.3f}"
        if key in scene_cache:
            _apply(scene, scene_cache[key])
        else:
            pending.append((key, scene))

    if not pending:
        LOG.info("VLM analysis cache hit (%d scenes)", len(scenes))
        if cache_path:
            atomic_write_json(cache_path, cache)
        return _collect_quality(scenes, scene_cache)

    LOG.info("Analyzing %d scenes with the local VLM (%s)...", len(pending), model)
    completed = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(
                analyze_scene,
                client,
                scene,
                max_width=max_width,
                context=context,
                content_language=content_language,
            ): (key, scene)
            for key, scene in pending
        }
        for future in as_completed(futures):
            key, scene = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001
                LOG.warning("Scene %d analysis failed: %s", scene.id, exc)
                result = {
                    "summary": text(
                        content_language,
                        "vlm.scene_failed",
                        id=scene.id,
                    ),
                    "place": text(content_language, "vlm.unknown"),
                    "subjects": [],
                    "actions": [],
                    "details": [],
                    "mood": "",
                    "narration_hint": "",
                    "quality": {
                        "code": "vlm.scene_failed",
                        "params": {"scene": scene.id, "error": str(exc)[:200]},
                        "severity": "warning",
                    },
                }
            scene_cache[key] = result
            _apply(scene, result)
            completed += 1
            LOG.info(
                "  [%d/%d] scene %d: %s",
                completed,
                len(pending),
                scene.id,
                (scene.summary or "no description")[:60],
            )
            if cache_path and completed % 3 == 0:
                atomic_write_json(cache_path, cache)

    if cache_path:
        atomic_write_json(cache_path, cache)
    return _collect_quality(scenes, scene_cache)


def _collect_quality(scenes: list[Scene], scene_cache: dict[str, Any]) -> list[QualityFlag]:
    flags: list[QualityFlag] = []
    for scene in scenes:
        key = f"{scene.id}:{scene.start:.3f}:{scene.end:.3f}"
        quality = (scene_cache.get(key) or {}).get("quality")
        if not isinstance(quality, dict) or not quality.get("code"):
            continue
        params = quality.get("params")
        flags.append(
            QualityFlag(
                code=str(quality["code"]),
                params=params if isinstance(params, dict) else {},
                severity=str(quality.get("severity") or "warning"),
            )
        )
    return flags


def _apply(scene: Scene, result: dict[str, Any]) -> None:
    scene.summary = str(result.get("summary") or "")
    scene.place = str(result.get("place") or "")
    scene.details = _as_list(result.get("details"))
    scene.mood = str(result.get("mood") or "")
    scene.hint = str(result.get("narration_hint") or "")
    scene.subjects = _as_list(result.get("subjects"))
    scene.actions = _as_list(result.get("actions"))
