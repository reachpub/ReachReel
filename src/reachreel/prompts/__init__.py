"""Language-specific prompt packs.

Prompts are content, not code. The Chinese pipeline loads ``prompts/zh``;
English output loads ``prompts/en``. The selected pack follows
``Settings.content_language``.
"""
from __future__ import annotations

import tomllib
from functools import lru_cache
from importlib import resources

DEFAULT_LANGUAGE = "zh"
SUPPORTED_LANGUAGES = ("zh", "en")


def normalize_language(language: str | None) -> str:
    """Return a supported prompt language, falling back to Chinese."""
    value = (language or "").strip().lower()
    if value in SUPPORTED_LANGUAGES:
        return value
    return DEFAULT_LANGUAGE


def load_prompt(language: str | None, name: str) -> str:
    """Load ``prompts/<language>/<name>.txt`` and fall back to Chinese."""
    requested = normalize_language(language)
    candidates = [requested]
    if DEFAULT_LANGUAGE not in candidates:
        candidates.append(DEFAULT_LANGUAGE)
    for candidate in candidates:
        path = resources.files(__package__) / candidate / f"{name}.txt"
        if path.is_file():
            return path.read_text(encoding="utf-8").strip() + "\n"
    raise FileNotFoundError(f"prompt pack not found: {name}.txt")


@lru_cache(maxsize=8)
def load_content(language: str | None, name: str = "messages") -> dict[str, str]:
    """Load the language-specific content catalog (fallbacks, labels, prompts)."""
    requested = normalize_language(language)
    catalog = _read_content(DEFAULT_LANGUAGE, name)
    if requested != DEFAULT_LANGUAGE:
        catalog.update(_read_content(requested, name))
    return catalog


def text(language: str | None, key: str, /, **params: object) -> str:
    """Render a content-catalog string for the selected content language."""
    template = load_content(language).get(key, key)
    try:
        return template.format(**params)
    except (KeyError, ValueError):
        return template


def _read_content(language: str, name: str) -> dict[str, str]:
    path = resources.files(__package__) / language / f"{name}.toml"
    if not path.is_file():
        return {}
    return _flatten(tomllib.loads(path.read_text(encoding="utf-8")))


def _flatten(data: dict, prefix: str = "") -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in data.items():
        full = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            result.update(_flatten(value, full))
        else:
            result[full] = str(value)
    return result
