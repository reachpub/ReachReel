"""Minimal UI localization for the CLI and user-facing errors.

The canonical catalog is ``en``. ``zh-CN`` overlays translated keys and falls
back to English for anything not translated yet. Content language (the language
of narration and subtitles) is a separate setting.
"""
from __future__ import annotations

import os
from importlib import resources

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib

DEFAULT_LANGUAGE = "en"
SUPPORTED_LANGUAGES = ("en", "zh-CN")

_catalog: dict[str, str] = {}
_current = DEFAULT_LANGUAGE


def supported_languages() -> tuple[str, ...]:
    return SUPPORTED_LANGUAGES


def normalize_language(value: str | None) -> str:
    """Normalize common locale identifiers to ``en`` or ``zh-CN``."""
    raw = (value or "").strip().lower().replace("_", "-").split(".", 1)[0]
    if not raw:
        return DEFAULT_LANGUAGE
    if raw.startswith("zh"):
        return "zh-CN"
    if raw.startswith("en"):
        return "en"
    return DEFAULT_LANGUAGE


def negotiate_language(
    explicit: str | None = None,
    config_language: str | None = None,
) -> str:
    """Resolve UI language: --lang > REACHREEL_LANG > config > LANG/LC_ALL."""
    for candidate in (
        explicit,
        os.environ.get("REACHREEL_LANG"),
        config_language,
        os.environ.get("LC_ALL"),
        os.environ.get("LANG"),
    ):
        if candidate:
            return normalize_language(candidate)
    return DEFAULT_LANGUAGE


def current_language() -> str:
    return _current


def set_language(value: str | None = None) -> str:
    """Load and activate a UI language, falling back to English."""
    global _catalog, _current
    language = normalize_language(value)
    _catalog = _load_catalog(language)
    _current = language
    return language


def t(key: str, /, **params: object) -> str:
    """Render a translated key; unknown keys return the key itself."""
    if not _catalog:
        set_language(negotiate_language())
    template = _catalog.get(key, key)
    try:
        return template.format(**params)
    except (KeyError, ValueError):
        return template


def _load_catalog(language: str) -> dict[str, str]:
    catalog = _read_catalog(DEFAULT_LANGUAGE)
    if language != DEFAULT_LANGUAGE:
        catalog.update(_read_catalog(language))
    return catalog


def _read_catalog(language: str) -> dict[str, str]:
    path = resources.files("reachreel") / "locales" / f"{language}.toml"
    if not path.is_file():
        return {}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return _flatten(data)


def _flatten(data: dict, prefix: str = "") -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in data.items():
        full = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            result.update(_flatten(value, full))
        else:
            result[full] = str(value)
    return result
