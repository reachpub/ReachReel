"""Language registry shared by the TTS and ASR adapters.

Defined by `docs/en/LANGUAGES.md`: user-facing values are BCP 47 tags, engine
codes (`kokoro`: a/b/z/j/..., `whisper`: zh/en/...) live here and never leak into
configuration, CLI flags, logs, or docs.

Only profiles with ``status="supported"`` may synthesize audio; the rest are
declared so the error message can list them instead of silently falling back.
"""
from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass, field
from functools import lru_cache

SUPPORTED = "supported"
PLANNED = "planned"

# Two-letter primary subtags that map onto a BCP 47 tag with a region.
_DEFAULT_REGIONS = {
    "en": "US",
    "zh": "CN",
    "ja": "JP",
    "es": "ES",
    "fr": "FR",
    "hi": "IN",
    "it": "IT",
    "pt": "BR",
}

_TAG_RE = re.compile(r"^(?P<primary>[a-z]{2,3})(?:[-_](?P<region>[a-z0-9]{2,8}))?$")


@dataclass(frozen=True)
class LanguageProfile:
    """Everything the pipeline needs to know about one language."""

    tag: str
    primary: str
    status: str
    # Script and subtitle defaults per language: 3.9 CJK characters per second
    # and 18 characters per line are wrong for English by a factor of ~3.
    speech_chars_per_second: float
    subtitle_max_chars: int
    font_hint: str
    # Default voices used when the user does not set `voice` / `say_voice`.
    default_voice: str = ""
    default_say_voice: str = ""
    # ISO 639-2 code written to soft-subtitle track metadata (e.g. chi / eng).
    iso639_2: str = ""
    # Engine code per adapter. Keys match `Settings.tts_engine` values.
    tts: dict[str, str] = field(default_factory=dict)
    # Optional dependency extra that provides the G2P backend.
    requires: str = ""
    notes: str = ""


PROFILES: dict[str, LanguageProfile] = {
    "zh-CN": LanguageProfile(
        tag="zh-CN",
        primary="zh",
        status=SUPPORTED,
        speech_chars_per_second=3.9,
        subtitle_max_chars=18,
        font_hint="PingFang SC",
        default_voice="zf_xiaoxiao",
        default_say_voice="Tingting",
        iso639_2="chi",
        tts={"kokoro": "z"},
        requires="tts-zh",
        notes="misaki[zh]; validated end to end on real footage",
    ),
    "en-US": LanguageProfile(
        tag="en-US",
        primary="en",
        status=SUPPORTED,
        speech_chars_per_second=14.5,
        subtitle_max_chars=42,
        font_hint="Helvetica Neue",
        default_voice="af_heart",
        default_say_voice="Samantha",
        iso639_2="eng",
        tts={"kokoro": "a"},
        requires="tts-en",
        notes="spacy (en_core_web_sm) + espeak-ng fallback; validated end to end",
    ),
    "en-GB": LanguageProfile(
        tag="en-GB",
        primary="en",
        status=SUPPORTED,
        speech_chars_per_second=14.5,
        subtitle_max_chars=42,
        font_hint="Helvetica Neue",
        default_voice="bf_emma",
        default_say_voice="Daniel",
        iso639_2="eng",
        tts={"kokoro": "b"},
        requires="tts-en",
        notes="spacy (en_core_web_sm) + espeak-ng fallback; validated end to end",
    ),
    "ja-JP": LanguageProfile(
        tag="ja-JP",
        primary="ja",
        status=PLANNED,
        speech_chars_per_second=6.5,
        subtitle_max_chars=16,
        font_hint="Hiragino Sans",
        default_voice="jf_alpha",
        default_say_voice="Kyoko",
        iso639_2="jpn",
        tts={"kokoro": "j"},
        requires="tts-ja",
    ),
    "es-ES": LanguageProfile(
        tag="es-ES",
        primary="es",
        status=PLANNED,
        speech_chars_per_second=13.0,
        subtitle_max_chars=38,
        font_hint="Helvetica Neue",
        iso639_2="spa",
        tts={"kokoro": "e"},
    ),
    "fr-FR": LanguageProfile(
        tag="fr-FR",
        primary="fr",
        status=PLANNED,
        speech_chars_per_second=13.0,
        subtitle_max_chars=38,
        font_hint="Helvetica Neue",
        iso639_2="fra",
        tts={"kokoro": "f"},
    ),
    "hi-IN": LanguageProfile(
        tag="hi-IN",
        primary="hi",
        status=PLANNED,
        speech_chars_per_second=9.0,
        subtitle_max_chars=32,
        font_hint="Kohinoor Devanagari",
        iso639_2="hin",
        tts={"kokoro": "h"},
    ),
    "it-IT": LanguageProfile(
        tag="it-IT",
        primary="it",
        status=PLANNED,
        speech_chars_per_second=13.0,
        subtitle_max_chars=38,
        font_hint="Helvetica Neue",
        iso639_2="ita",
        tts={"kokoro": "i"},
    ),
    "pt-BR": LanguageProfile(
        tag="pt-BR",
        primary="pt",
        status=PLANNED,
        speech_chars_per_second=13.0,
        subtitle_max_chars=38,
        font_hint="Helvetica Neue",
        iso639_2="por",
        tts={"kokoro": "p"},
    ),
}

# Kokoro voice names are `<language><gender>_<name>`: af_heart = American
# English female, bm_george = British English male, zf_xiaoxiao = Mandarin.
VOICE_LANGUAGE_PREFIXES: dict[str, str] = {
    "a": "en-US",
    "b": "en-GB",
    "z": "zh-CN",
    "j": "ja-JP",
    "e": "es-ES",
    "f": "fr-FR",
    "h": "hi-IN",
    "i": "it-IT",
    "p": "pt-BR",
}


def normalize_tag(value: str | None) -> str | None:
    """Return the canonical BCP 47 tag, or ``None`` when it is unknown.

    Tolerant on input: ``en_us``, ``EN-us`` and ``zh_CN.UTF-8`` all normalize.
    """
    raw = (value or "").strip().lower().split(".", 1)[0]
    if not raw:
        return None
    match = _TAG_RE.match(raw)
    if match is None:
        return None
    primary = match.group("primary")
    region = match.group("region")
    if region is not None:
        candidate = f"{primary}-{region.upper()}"
        if candidate in PROFILES:
            return candidate
        # An unknown region still resolves to the default regional profile.
        region = None
    default = _DEFAULT_REGIONS.get(primary)
    if default is None:
        return None
    candidate = f"{primary}-{default}"
    return candidate if candidate in PROFILES else None


def profile_for(tag: str | None) -> LanguageProfile | None:
    canonical = normalize_tag(tag)
    return PROFILES.get(canonical) if canonical else None


def resolve_tts_language(
    *,
    tts_language: str = "",
    content_language: str = "zh",
    voice: str = "",
) -> LanguageProfile:
    """Resolve the pronunciation language for a synthesis run.

    Order: explicit ``tts_language``, then the content language, then the voice.
    The voice is a fallback only, because the content language decides what the
    script says; a voice that disagrees is rejected separately. An unknown tag
    raises, because a wrong-language voiceover still produces audio and passes QA.
    """
    for candidate, source in (
        (tts_language, "tts_language"),
        (content_language, "content_language"),
        (voice_language(voice) or "", f"voice {voice!r}"),
    ):
        if not candidate:
            continue
        profile = profile_for(candidate)
        if profile is None:
            raise ValueError(
                f"unsupported language {candidate!r} from {source}; "
                f"supported tags: {', '.join(sorted(PROFILES))}"
            )
        return profile
    raise ValueError(
        "cannot resolve the TTS language; set tts_language or content_language. "
        f"supported tags: {', '.join(sorted(PROFILES))}"
    )


def resolve_asr_language(asr_language: str = "", content_language: str = "zh") -> str:
    """Whisper hint: explicit value wins, otherwise the content primary subtag."""
    explicit = (asr_language or "").strip().lower()
    if explicit:
        return explicit
    profile = profile_for(content_language)
    if profile is not None:
        return profile.primary
    return "zh"


def voice_language(voice: str) -> str | None:
    """Tag implied by a Kokoro voice name, or ``None`` when it is not one."""
    name = (voice or "").strip().lower()
    if len(name) < 3 or name[1] not in "fm" or name[2] != "_":
        return None
    return VOICE_LANGUAGE_PREFIXES.get(name[0])


def engine_code(profile: LanguageProfile, engine: str) -> str | None:
    return profile.tts.get(engine)


def require_tts(profile: LanguageProfile, engine: str) -> str:
    """Return the engine code for a profile, or explain why it cannot be used."""
    if profile.status != SUPPORTED:
        detail = f" ({profile.notes})" if profile.notes else ""
        raise ValueError(
            f"TTS does not support {profile.tag} yet{detail}; "
            f"validated tags: {', '.join(supported_tags())}"
        )
    code = engine_code(profile, engine)
    if code is None:
        raise ValueError(
            f"{engine} has no voice for {profile.tag}; "
            f"validated tags: {', '.join(supported_tags())}"
        )
    return code


def require_voice_match(profile: LanguageProfile, voice: str) -> None:
    """Reject a voice whose language disagrees with the resolved profile."""
    voice_tag = voice_language(voice)
    if voice_tag is None or voice_tag == profile.tag:
        return
    hint = ""
    voice_profile = PROFILES.get(voice_tag)
    if voice_profile is not None and voice_profile.status != SUPPORTED:
        hint = f"; note: {voice_tag} is planned and cannot synthesize yet"
    raise ValueError(
        f"voice {voice!r} speaks {voice_tag} but the resolved language is "
        f"{profile.tag}{hint}; set tts_language or pick a matching voice"
    )


@lru_cache(maxsize=1)
def supported_tags() -> tuple[str, ...]:
    return tuple(sorted(tag for tag, profile in PROFILES.items() if profile.status == SUPPORTED))


@lru_cache(maxsize=1)
def planned_tags() -> tuple[str, ...]:
    return tuple(sorted(tag for tag, profile in PROFILES.items() if profile.status == PLANNED))


def check_tts_dependencies(
    profile: LanguageProfile,
    engine: str = "kokoro",
    *,
    find_spec=importlib.util.find_spec,
) -> tuple[bool, str]:
    """Check whether the G2P stack for ``profile`` is installed.

    Returns ``(ok, detail)`` so ``doctor`` and the TTS adapter report the exact
    same missing packages and install command.
    """
    if engine != "kokoro":
        return True, engine
    if profile.primary == "zh":
        missing = [mod for mod in ("misaki", "pypinyin", "jieba", "cn2an") if find_spec(mod) is None]
        if missing:
            return (
                False,
                f"missing {', '.join(missing)} (install: uv pip install 'reachreel[tts-zh]')",
            )
        return True, "misaki[zh]"
    if profile.primary == "en":
        pkg_mods = ("misaki", "spacy", "num2words", "espeakng_loader", "phonemizer")
        missing_pkgs = [mod for mod in pkg_mods if find_spec(mod) is None]
        missing_model = find_spec("en_core_web_sm") is None
        if not missing_pkgs and not missing_model:
            return True, "misaki + spacy (en_core_web_sm) + espeak-ng"
        hints: list[str] = []
        if missing_pkgs:
            hints.append(
                f"missing {', '.join(missing_pkgs)} (install: uv pip install 'reachreel[tts-en]')"
            )
        if missing_model:
            hints.append("missing en_core_web_sm (run: python -m spacy download en_core_web_sm)")
        return False, "; ".join(hints)
    if find_spec("misaki") is None:
        return False, "missing misaki"
    return True, "misaki"
