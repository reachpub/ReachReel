"""Configuration for the local video narration pipeline."""
from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from ..i18n import normalize_language
from ..languages import (
    PROFILES,
    LanguageProfile,
    normalize_tag,
    profile_for,
    resolve_asr_language,
)
from ..prompts import normalize_language as normalize_content_language


@dataclass
class Settings:
    # Language
    ui_language: str = "en"  # en | zh-CN
    content_language: str = "zh"  # zh | en

    # Model / service
    ollama_url: str = "http://localhost:11434"
    vlm_model: str = "qwen3-vl:8b-instruct"
    llm_model: str = "qwen3:30b-instruct"
    vlm_workers: int = 2
    vlm_frames_per_scene: int = 3
    vlm_max_width: int = 768
    # Mean absolute difference (0-255) between 16x16 grayscale thumbnails below
    # which a frame counts as a duplicate of the previous kept one. 0 disables it.
    vlm_frame_dedup: float = 4.0
    llm_temperature: float = 0.6

    # Scene detection
    scene_threshold: float = 0.32
    min_scene_seconds: float = 1.2
    max_scene_seconds: float = 8.0
    max_scenes: int = 160

    # Script
    style: str = ""  # empty = language-specific default from prompts/<lang>/style.txt
    brief: str = ""
    speech_chars_per_second: float = 0.0  # 0 = language default (3.9 zh, 14.5 en)
    narration_density: float = 0.62
    min_gap_seconds: float = 0.28
    start_offset: float = 0.7
    tail_padding: float = 0.8
    max_speedup: float = 1.32

    # TTS
    tts_engine: str = "kokoro"  # kokoro | say
    # BCP 47 tag for the voiceover language; empty follows content_language.
    # Engine codes (Kokoro a/b/z, Whisper zh/en) never appear here.
    tts_language: str = ""
    kokoro_model: str = "mlx-community/Kokoro-82M-4bit"
    voice: str = ""  # empty = language default (zf_xiaoxiao, af_heart, bf_emma)
    tts_speed: float = 1.0
    say_voice: str = ""  # empty = language default (Tingting, Samantha, Daniel)

    # ASR / subtitles
    whisper_model: str = "mlx-community/whisper-large-v3-turbo"
    # Whisper hint; empty follows the primary subtag of content_language.
    asr_language: str = ""
    subtitle_text: str = "script"  # script | asr
    subtitle_max_chars: int = 0  # 0 = language default (18 zh, 42 en)
    subtitle_max_lines: int = 2
    subtitle_max_seconds: float = 6.0
    subtitle_font: str = ""  # empty = language default (PingFang SC, Helvetica Neue)
    subtitle_size: int = 48  # output pixels (ASS PlayRes = output resolution)
    # outline: white text + black outline (covers little of the frame)
    # box: white text on a translucent black box (older behaviour)
    # none: plain white text, no stroke and no shadow (bright footage may wash it out)
    subtitle_render: str = "outline"
    subtitle_outline: float = 0.0  # outline thickness in output pixels; 0 = auto
    subtitle_box_padding: float = 0.0  # box mode padding in output pixels; 0 = auto
    subtitle_preset: str = ""  # "" | douyin | reels | shorts | landscape
    subtitle_margin_v: int = 36
    subtitle_margin_left: int = 50
    subtitle_margin_right: int = 50

    # Audio
    bgm_dir: str = "inputs/bgm"
    bgm_file: str = ""
    bgm_volume: float = 0.20
    bgm_duck_db: float = -16.0
    ambient_volume: float = 0.16
    ambient_duck_db: float = -10.0
    target_lufs: float = -14.0
    true_peak: float = -1.5
    two_pass_loudnorm: bool = True
    bgm_fade_seconds: float = 2.0

    # Render
    output_height: int = 0  # 0 = keep source height
    output_fps: float = 0.0  # 0 = keep source fps
    crf: int = 18
    preset: str = "medium"
    audio_bitrate: str = "192k"
    burn_subtitles: bool = True

    # QA thresholds. Config keys use a [qa] table, e.g. qa.freeze_min_seconds = 3.
    qa_duration_warn_seconds: float = 0.5
    qa_duration_fail_seconds: float = 2.0
    qa_av_sync_warn_seconds: float = 0.2
    qa_av_sync_fail_seconds: float = 0.8
    qa_lufs_warn_delta: float = 2.0
    qa_lufs_fail_delta: float = 4.0
    qa_true_peak_tolerance_db: float = 0.6
    qa_true_peak_ceiling_db: float = 0.0
    qa_black_min_seconds: float = 0.5
    qa_black_pixel_threshold: float = 0.10
    qa_black_longest_fail_seconds: float = 3.0
    qa_black_total_fail_seconds: float = 5.0
    qa_freeze_noise: float = 0.0005
    qa_freeze_min_seconds: float = 3.0
    qa_freeze_fail_seconds: float = 8.0
    qa_freeze_motion_threshold: float = 0.02
    qa_freeze_sample_seconds: float = 20.0
    qa_clip_peak_db: float = -0.1
    qa_clip_sample_ratio: float = 0.0005
    qa_clip_flat_factor: float = 0.5
    qa_subtitle_char_warn_extra: int = 4
    qa_subtitle_char_fail_extra: int = 10
    qa_subtitle_safe_tolerance_px: float = 6.0
    qa_subtitle_overflow_tolerance_px: float = 20.0
    qa_narration_subtitle_tolerance_seconds: float = 0.9
    qa_narration_subtitle_fail_ratio: float = 0.5
    qa_skip_subtitle_pixels: bool = False

    # Cache
    cache_enabled: bool = True
    cache_dir: str = ""  # empty = platform cache dir (macOS: ~/Library/Caches/reachreel)

    # Paths
    work_root: str = "output"
    bgm_root_resolved: Path | None = field(default=None, repr=False)

    @classmethod
    def load(cls, path: Path | None = None, overrides: dict[str, Any] | None = None) -> Settings:
        data: dict[str, Any] = {}
        if path and path.exists():
            with path.open("rb") as fh:
                data = tomllib.load(fh)
            base = path.parent
            for key in ("bgm_dir", "bgm_file", "work_root"):
                if data.get(key):
                    value = Path(str(data[key])).expanduser()
                    if not value.is_absolute():
                        value = (base / value)
                    data[key] = str(value)
        settings = cls()
        valid = {f.name for f in fields(cls)}
        for key, value in data.items():
            if key == "qa" and isinstance(value, dict):
                # [qa] table: freeze_min_seconds -> qa_freeze_min_seconds
                for sub_key, sub_value in value.items():
                    attr = f"qa_{sub_key}"
                    if attr in valid and sub_value is not None:
                        setattr(settings, attr, sub_value)
            elif key in valid and value is not None:
                setattr(settings, key, value)
        settings._apply_env()
        for key, value in (overrides or {}).items():
            if value is not None and key in valid:
                setattr(settings, key, value)
        settings.ui_language = normalize_language(settings.ui_language)
        settings.content_language = normalize_content_language(settings.content_language)
        settings.subtitle_preset = normalize_subtitle_preset(settings.subtitle_preset)
        settings.subtitle_render = normalize_subtitle_render(settings.subtitle_render)
        settings.tts_language = normalize_tag(settings.tts_language) or str(
            settings.tts_language or ""
        ).strip()
        settings.bgm_root_resolved = Path(settings.bgm_dir).expanduser()
        return settings

    def _apply_env(self) -> None:
        mapping = {
            "REACHREEL_LANG": "ui_language",
            "REACHREEL_CONTENT_LANGUAGE": "content_language",
            "REACHREEL_OLLAMA_URL": "ollama_url",
            "REACHREEL_VLM_MODEL": "vlm_model",
            "REACHREEL_LLM_MODEL": "llm_model",
            "REACHREEL_VOICE": "voice",
            "REACHREEL_BGM_DIR": "bgm_dir",
            "REACHREEL_WHISPER_MODEL": "whisper_model",
            "REACHREEL_TTS_ENGINE": "tts_engine",
            "REACHREEL_CACHE_DIR": "cache_dir",
        }
        for env_name, attr in mapping.items():
            value = os.environ.get(env_name)
            if value:
                setattr(self, attr, value)
        cache_flag = os.environ.get("REACHREEL_CACHE")
        if cache_flag and cache_flag.strip().lower() in {"0", "false", "no", "off"}:
            self.cache_enabled = False

    def resolved_bgm_dir(self) -> Path:
        return Path(self.bgm_dir).expanduser()

    def content_profile(self) -> LanguageProfile:
        return profile_for(self.content_language) or PROFILES["zh-CN"]

    def _voice_profile(self) -> LanguageProfile:
        if self.tts_language:
            profile = profile_for(self.tts_language)
            if profile is not None:
                return profile
        return self.content_profile()

    def resolved_speech_chars_per_second(self) -> float:
        if self.speech_chars_per_second > 0:
            return float(self.speech_chars_per_second)
        return self.content_profile().speech_chars_per_second

    def resolved_voice(self) -> str:
        explicit = (self.voice or "").strip()
        if explicit:
            return explicit
        return self._voice_profile().default_voice or PROFILES["zh-CN"].default_voice

    def resolved_say_voice(self) -> str:
        explicit = (self.say_voice or "").strip()
        if explicit:
            return explicit
        return self._voice_profile().default_say_voice or PROFILES["zh-CN"].default_say_voice

    def resolved_subtitle_max_chars(self) -> int:
        if self.subtitle_max_chars > 0:
            return int(self.subtitle_max_chars)
        return self.content_profile().subtitle_max_chars

    def resolved_subtitle_font(self) -> str:
        explicit = (self.subtitle_font or "").strip()
        if explicit:
            return explicit
        return self.content_profile().font_hint

    def resolved_asr_language(self) -> str:
        return resolve_asr_language(self.asr_language, self.content_language)


SUBTITLE_PRESETS = ("", "douyin", "reels", "shorts", "landscape")


def normalize_subtitle_preset(value: str | None) -> str:
    """Normalize a platform preset name; unknown values fall back to manual margins."""
    preset = str(value or "").strip().lower()
    return preset if preset in SUBTITLE_PRESETS else ""


SUBTITLE_RENDER_MODES = ("outline", "box", "none")


def normalize_subtitle_render(value: str | None) -> str:
    """Normalize the subtitle render mode; anything unknown means ``outline``."""
    mode = str(value or "").strip().lower()
    if mode in {"stroke", "border", "outlined"}:
        mode = "outline"
    if mode in {"background", "block", "opaque_box"}:
        mode = "box"
    if mode in {"plain", "bare", "no_outline", "no-outline"}:
        mode = "none"
    return mode if mode in SUBTITLE_RENDER_MODES else "outline"


def settings_to_dict(settings: Settings) -> dict[str, Any]:
    """JSON-serializable snapshot used by manifest.json and stage commands."""
    data = asdict(settings)
    data.pop("bgm_root_resolved", None)
    data["bgm_dir"] = str(settings.bgm_dir)
    data["bgm_file"] = str(settings.bgm_file)
    data["work_root"] = str(settings.work_root)
    return data
