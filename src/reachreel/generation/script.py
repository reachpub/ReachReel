"""Narration script generation with a local LLM."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from ..core.util import clamp
from ..domain.models import NarrationSegment, Scene
from ..engines.vlm import OllamaClient
from ..prompts import load_prompt, text

LOG = logging.getLogger("reachreel.generation.script")


def _scene_table(scenes: list[Scene], content_language: str = "zh") -> str:
    english = content_language == "en"
    lines = []
    separator = "; " if english else "；"
    list_separator = ", " if english else "、"
    for scene in scenes:
        details = separator.join(scene.details[:4])
        subjects = list_separator.join(scene.subjects[:4])
        actions = list_separator.join(scene.actions[:3])
        parts = [
            f"{scene.id}. {scene.start:.1f}-{scene.end:.1f}s",
            text(
                content_language,
                "script.table.place",
                value=scene.place or text(content_language, "vlm.unknown"),
            ),
            text(
                content_language,
                "script.table.description",
                value=scene.summary or text(content_language, "script.table.none"),
            ),
        ]
        if subjects:
            parts.append(text(content_language, "script.table.subjects", value=subjects))
        if actions:
            parts.append(text(content_language, "script.table.actions", value=actions))
        if details:
            parts.append(text(content_language, "script.table.details", value=details))
        if scene.mood:
            parts.append(text(content_language, "script.table.mood", value=scene.mood))
        if scene.hint:
            parts.append(text(content_language, "script.table.angle", value=scene.hint))
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def _extract_json_array(text: str) -> list[dict[str, Any]]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict) and isinstance(value.get("segments"), list):
            return [item for item in value["segments"] if isinstance(item, dict)]
    except ValueError:
        pass
    match = re.search(r"\[.*\]", cleaned, flags=re.DOTALL)
    if match:
        try:
            value = json.loads(match.group(0))
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        except ValueError:
            pass
    return []


def _normalise_segments(
    raw_segments: list[dict[str, Any]],
    scenes: list[Scene],
    content_language: str = "zh",
) -> list[NarrationSegment]:
    valid_ids = {scene.id for scene in scenes}
    max_id = max(valid_ids) if valid_ids else 1
    result: list[NarrationSegment] = []
    cursor = 1
    for item in raw_segments:
        text = str(item.get("text") or "").strip()
        if content_language == "en":
            text = re.sub(r"\s+", " ", text).strip()
        else:
            text = re.sub(r"\s+", "", text)
        if not text:
            continue
        scene_ids_raw = item.get("scene_ids") or item.get("scenes") or []
        if isinstance(scene_ids_raw, (int, str)):
            scene_ids_raw = [scene_ids_raw]
        scene_ids: list[int] = []
        for value in scene_ids_raw:
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            if number in valid_ids and number not in scene_ids:
                scene_ids.append(number)
        if not scene_ids:
            scene_ids = [min(cursor, max_id)]
        scene_ids.sort()
        cursor = max(scene_ids) + 1
        result.append(NarrationSegment(id=len(result) + 1, scene_ids=scene_ids, text=text))
    return result


def _fallback_segments(
    scenes: list[Scene],
    target_chars: int,
    content_language: str = "zh",
) -> list[NarrationSegment]:
    """If the LLM fails, build a conservative script from the scene descriptions."""
    english = content_language == "en"
    groups: list[list[Scene]] = []
    current: list[Scene] = []
    current_len = 0
    for scene in scenes:
        text = (scene.hint or scene.summary or "").strip()
        length = len(text)
        if current and (current_len + length > 55 or scene.start - current[0].start > 8.0):
            groups.append(current)
            current = []
            current_len = 0
        current.append(scene)
        current_len += length
    if current:
        groups.append(current)

    segments: list[NarrationSegment] = []
    total = 0
    for group in groups:
        pieces = []
        for scene in group:
            piece = (scene.hint or scene.summary or "").strip()
            if piece and piece not in pieces:
                pieces.append(piece)
        script_text = (", " if english else "，").join(pieces)
        if not script_text:
            script_text = text(content_language, "script.fallback_intro")
        if english:
            if not script_text.endswith((".", "!", "?")):
                script_text += "."
        elif not script_text.endswith(("。", "！", "？")):
            script_text += "。"
        if total + len(script_text) > target_chars * 1.15 and segments:
            break
        segments.append(
            NarrationSegment(
                id=len(segments) + 1,
                scene_ids=[scene.id for scene in group],
                text=script_text,
            )
        )
        total += len(script_text)
    if not segments:
        segments.append(
            NarrationSegment(
                id=1,
                scene_ids=[scenes[0].id] if scenes else [1],
                text=text(content_language, "script.fallback_start"),
            )
        )
    return segments


def _condense_prompt(
    segments: list[NarrationSegment],
    target_chars: int,
    content_language: str = "zh",
) -> str:
    payload = [{"scene_ids": s.scene_ids, "text": s.text} for s in segments]
    return (
        text(
            content_language,
            "script.condense_instruction",
            target_chars=target_chars,
        )
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + text(content_language, "script.condense_ending")
    )


def _expand_prompt(
    segments: list[NarrationSegment],
    target_chars: int,
    duration: float,
    content_language: str = "zh",
) -> str:
    payload = [{"scene_ids": s.scene_ids, "text": s.text} for s in segments]
    return (
        text(
            content_language,
            "script.expand_instruction",
            target_chars=target_chars,
            duration=duration,
        )
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + text(content_language, "script.expand_ending")
    )


def generate_script(
    scenes: list[Scene],
    *,
    duration: float,
    client: OllamaClient,
    style: str,
    brief: str = "",
    cps: float = 3.9,
    density: float = 0.62,
    temperature: float = 0.6,
    content_language: str = "zh",
) -> list[NarrationSegment]:
    target_chars = max(12, int(duration * cps * clamp(density, 0.2, 1.0)))
    min_segments = max(1, min(10, round(duration / 14)))
    style = style.strip() or load_prompt(content_language, "style").strip()
    context = (
        text(content_language, "script.context_label", context=brief)
        if brief
        else ""
    )
    prompt = load_prompt(content_language, "script").format(
        context=context,
        table=_scene_table(scenes, content_language),
        target_chars=target_chars,
        cps=cps,
        duration=duration,
        min_segments=min_segments,
        style=style,
    )
    LOG.info("Writing narration with the local LLM (target about %d characters)...", target_chars)
    raw = client.chat(
        [{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=max(1200, target_chars * 3),
    )
    segments = _normalise_segments(_extract_json_array(raw), scenes, content_language)
    if not segments:
        LOG.warning("LLM output could not be parsed; using the scene summary fallback. Raw output: %s", raw[:300])
        segments = _fallback_segments(scenes, target_chars, content_language)

    total_chars = sum(len(segment.text) for segment in segments)
    if total_chars > target_chars * 1.22 and total_chars > 40:
        LOG.info("Narration is too long (%d chars); asking the LLM to compress to about %d...", total_chars, target_chars)
        try:
            raw2 = client.chat(
                [
                    {
                        "role": "user",
                        "content": _condense_prompt(segments, target_chars, content_language),
                    }
                ],
                temperature=0.3,
                max_tokens=max(1000, target_chars * 3),
            )
            condensed = _normalise_segments(_extract_json_array(raw2), scenes, content_language)
            if condensed:
                segments = condensed
        except Exception as exc:  # noqa: BLE001
            LOG.warning("Compression failed; keeping the original script: %s", exc)

    total_chars = sum(len(segment.text) for segment in segments)
    if duration > 25 and total_chars < target_chars * 0.72:
        LOG.info("Narration is too short (%d chars); asking the LLM to expand to about %d...", total_chars, target_chars)
        try:
            raw3 = client.chat(
                [
                    {
                        "role": "user",
                        "content": _expand_prompt(
                            segments, target_chars, duration, content_language
                        ),
                    }
                ],
                temperature=0.6,
                max_tokens=max(1200, target_chars * 3),
            )
            expanded = _normalise_segments(_extract_json_array(raw3), scenes, content_language)
            if expanded and sum(len(s.text) for s in expanded) > total_chars:
                segments = expanded
        except Exception as exc:  # noqa: BLE001
            LOG.warning("Expansion failed; keeping the original script: %s", exc)

    for index, segment in enumerate(segments, start=1):
        segment.id = index
        segment.text = _clean_text(segment.text, content_language)
    LOG.info(
        "Narration complete: %d segments, %d characters (target %d)",
        len(segments),
        sum(len(s.text) for s in segments),
        target_chars,
    )
    return segments


def _clean_text(text: str, content_language: str = "zh") -> str:
    if content_language == "en":
        text = re.sub(r"\s+", " ", text).strip()
        if text and not text.endswith((".", "!", "?")):
            text += "."
        return text
    text = re.sub(r"\s+", "", text)
    text = text.replace("..", "。").replace("，，", "，")
    if text and not text.endswith(("。", "！", "？", "…")):
        text += "。"
    return text
