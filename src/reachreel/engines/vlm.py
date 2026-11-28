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
from urllib.parse import urlsplit

from ..core.util import atomic_write_json, read_json, sha1_file, sha1_text
from ..domain.models import QualityFlag, Scene
from ..prompts import load_prompt, text

LOG = logging.getLogger("reachreel.engines.vlm")

PROMPT_VERSION = "vf-vlm-3"


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


def _scene_frames(scene: Scene) -> list[Path]:
    return [frame for frame in map(Path, scene.frames) if frame.is_file()]


def analyze_scene(
    client: OllamaClient,
    scene: Scene,
    *,
    max_width: int = 768,
    context: str = "",
    content_language: str = "zh",
) -> dict[str, Any]:
    frames = _scene_frames(scene)
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


def settings_key(
    *,
    content_language: str,
    model: str,
    max_width: int,
    frame_dedup: float,
    context: str,
    base_url: str = "http://localhost:11434",
) -> str:
    """Fingerprint the endpoint, prompt, and options for VLM analysis."""
    try:
        url = urlsplit(base_url.rstrip("/"))
    except ValueError:
        # Let the request path report a bad endpoint without persisting the raw URL.
        endpoint = ("", sha1_text(base_url), "")
    else:
        endpoint = (url.scheme, url.netloc.rsplit("@", 1)[-1].lower(), url.path)
    settings = {
        "endpoint": endpoint,
        "language": content_language,
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "prompt": load_prompt(content_language, "vlm"),
        "max_width": max_width,
        "frame_dedup": frame_dedup,
        "context": context,
    }
    return sha1_text(json.dumps(settings, sort_keys=True))


def _scene_key(scene: Scene) -> str | None:
    # Only the ordered image bytes affect the request; absolute paths vary between jobs.
    try:
        frames = [sha1_file(frame) for frame in _scene_frames(scene)]
    except OSError:
        return None
    return sha1_text(json.dumps([scene.id, scene.start, scene.end, frames]))


def _successful_result(value: Any) -> bool:
    # All existing quality flags describe degraded results that a later run may recover.
    return isinstance(value, dict) and bool(value) and not value.get("quality")


def _load_scene_cache(path: Path | None, key: str) -> dict[str, dict[str, Any]]:
    cache = read_json(path, {}) if path is not None else {}
    if not isinstance(cache, dict) or cache.get("settings") != key:
        return {}
    scenes = cache.get("scenes")
    if not isinstance(scenes, dict):
        return {}
    return {scene_key: result for scene_key, result in scenes.items() if _successful_result(result)}


def _save_scene_cache(path: Path, key: str, scenes: dict[str, dict[str, Any]]) -> None:
    atomic_write_json(path, {"settings": key, "scenes": scenes})


def analyze_scenes(
    scenes: list[Scene],
    *,
    base_url: str,
    model: str,
    workers: int = 2,
    cache_path: Path | None = None,
    shared_cache_path: Path | None = None,
    max_width: int = 768,
    force: bool = False,
    context: str = "",
    content_language: str = "zh",
    frame_dedup: float = 0.0,
) -> list[QualityFlag]:
    settings_key_value = settings_key(
        content_language=content_language,
        model=model,
        max_width=max_width,
        frame_dedup=frame_dedup,
        context=context,
        base_url=base_url,
    )
    scene_cache = {} if force else _load_scene_cache(cache_path, settings_key_value)
    shared_cache = {} if force else _load_scene_cache(shared_cache_path, settings_key_value)

    results: list[dict[str, Any]] = [{} for _ in scenes]
    pending: list[tuple[int, str | None]] = []
    for index, scene in enumerate(scenes):
        key = _scene_key(scene)
        if key is not None and (cached := scene_cache.get(key) or shared_cache.get(key)):
            scene_cache[key] = cached
            results[index] = cached
            _apply(scene, cached)
        else:
            pending.append((index, key))

    if pending:
        client = OllamaClient(base_url, model)
        LOG.info(
            "Analyzing %d scenes with the local VLM (%s); first request may take 15-30s while the model loads...",
            len(pending),
            model,
        )
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {
                pool.submit(
                    analyze_scene,
                    client,
                    scenes[index],
                    max_width=max_width,
                    context=context,
                    content_language=content_language,
                ): (index, key)
                for index, key in pending
            }
            for completed, future in enumerate(as_completed(futures), start=1):
                index, key = futures[future]
                scene = scenes[index]
                try:
                    result = future.result()
                except Exception as exc:  # noqa: BLE001
                    LOG.warning("Scene %d analysis failed: %s", scene.id, exc)
                    result = _scene_failure(scene, exc, content_language)
                results[index] = result
                if key is not None and _successful_result(result):
                    scene_cache[key] = result
                _apply(scene, result)
                LOG.info(
                    "  [%d/%d] scene %d: %s",
                    completed,
                    len(pending),
                    scene.id,
                    (scene.summary or "no description")[:60],
                )
                if cache_path and completed % 3 == 0:
                    _save_scene_cache(cache_path, settings_key_value, scene_cache)
    else:
        LOG.info("VLM analysis cache hit (%d scenes)", len(scenes))

    if cache_path:
        _save_scene_cache(cache_path, settings_key_value, scene_cache)
    if shared_cache_path is not None and scene_cache:
        shared = _load_scene_cache(shared_cache_path, settings_key_value)
        combined = shared | scene_cache if force else scene_cache | shared
        if combined != shared:
            _save_scene_cache(shared_cache_path, settings_key_value, combined)
    return _collect_quality(results)


def _scene_failure(scene: Scene, exc: Exception, content_language: str) -> dict[str, Any]:
    return {
        "summary": text(content_language, "vlm.scene_failed", id=scene.id),
        "place": text(content_language, "vlm.unknown"),
        "quality": {
            "code": "vlm.scene_failed",
            "params": {"scene": scene.id, "error": str(exc)[:200]},
            "severity": "warning",
        },
    }


def _collect_quality(results: list[dict[str, Any]]) -> list[QualityFlag]:
    flags: list[QualityFlag] = []
    for result in results:
        quality = result.get("quality")
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
