"""Configuration for the local video narration pipeline."""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib

from ..i18n import normalize_language
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
    llm_temperature: float = 0.6

    # Scene detection
    scene_threshold: float = 0.32
    min_scene_seconds: float = 1.2
    max_scene_seconds: float = 8.0
    max_scenes: int = 160

    # Script
    style: str = ""  # empty = language-specific default from prompts/<lang>/style.txt
    brief: str = ""
    speech_chars_per_second: float = 3.9
    narration_density: float = 0.62
    min_gap_seconds: float = 0.28
    start_offset: float = 0.7
    tail_padding: float = 0.8
    max_speedup: float = 1.32

    # TTS
    tts_engine: str = "kokoro"  # kokoro | say
    kokoro_model: str = "mlx-community/Kokoro-82M-4bit"
    voice: str = "zf_xiaoxiao"
    tts_speed: float = 1.0
    say_voice: str = "Tingting"

    # ASR / subtitles
    whisper_model: str = "mlx-community/whisper-large-v3-turbo"
    asr_language: str = "zh"
    subtitle_text: str = "script"  # script | asr
    subtitle_max_chars: int = 18
    subtitle_max_lines: int = 2
    subtitle_max_seconds: float = 6.0
    subtitle_font: str = "PingFang SC"
    subtitle_size: int = 48  # output pixels (ASS PlayRes = output resolution)
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
        }
        for env_name, attr in mapping.items():
            value = os.environ.get(env_name)
            if value:
                setattr(self, attr, value)

    def resolved_bgm_dir(self) -> Path:
        return Path(self.bgm_dir).expanduser()


def settings_to_dict(settings: Settings) -> dict[str, Any]:
    """JSON-serializable snapshot used by manifest.json and stage commands."""
    data = asdict(settings)
    data.pop("bgm_root_resolved", None)
    data["bgm_dir"] = str(settings.bgm_dir)
    data["bgm_file"] = str(settings.bgm_file)
    data["work_root"] = str(settings.work_root)
    return data
