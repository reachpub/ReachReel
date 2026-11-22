**English** | [简体中文](CHANGELOG.zh-CN.md)

# Changelog

## [Unreleased]

### Added

- CI workflow (`.github/workflows/ci.yml`) running Ruff, the unit suite, and `uv build` on macOS for Python 3.10 and 3.12.
- Opt-in integration smoke test (`tests/integration/test_smoke.py`) covering `tts -> subtitle -> mix -> render -> qa` through the real CLI with macOS `say`.
- `tomli` fallback so Python 3.10 can load TOML catalogs and configuration.

### Changed

- Project license changed from MIT to Apache-2.0; added the full license text and a `NOTICE` file.

### Fixed

- Test paths in `docs/en/DEVELOPMENT.md` and `docs/zh-CN/DEVELOPMENT.md` now match the `tests/unit/` layout.

## [0.1.0] - 2026-11-22

### Added

- Local video narration pipeline:
  - FFmpeg scene detection and keyframe extraction;
  - Ollama `qwen3-vl` scene understanding;
  - Ollama `qwen3` narration writing with language-specific prompt packs;
  - Kokoro-82M / MLX Chinese voiceover, with a macOS `say` fallback;
  - `mlx-whisper` speech boundaries + script-based SRT/ASS subtitles;
  - BGM / ambient / narration sidechain ducking;
  - `ffmpeg-full` + libass hard subtitles;
  - H.264/AAC output.
- CLI: `run`, `analyze`, `script`, `tts`, `subtitle`, `mix`, `render`, `qa`, `doctor`.
- Resumable scene, VLM, script, and TTS caches; `manifest.json` records settings, artifacts, and QA.
- QA gate: streams, duration, loudness/true peak, clipping, black/frozen frames, subtitle layout/overflow, and narration alignment.
- Bilingual CLI/UI via `--lang en|zh-CN` and `REACHREEL_LANG`.
- Independent content language via `--content-language zh|en` and `REACHREEL_CONTENT_LANGUAGE`.
- Language-specific prompt packs under `src/reachreel/prompts/{zh,en}`.
- Local input root `inputs/` (Git-ignored): `inputs/bgm/` for background music; `inputs/fonts/` and `inputs/models/` reserved for future use.
- Bilingual documentation: `README.md` / `README.zh-CN.md`, plus `docs/en/` and `docs/zh-CN/`.
- `src/` package layout: `cli`, `core`, `domain`, `engines`, `generation`, `i18n`, `locales`, `media`, `pipeline`, `prompts`.

### Known limitations

- Apple Silicon macOS only; TTS depends on MLX.
- VLM analyzes keyframes per scene, not the full video.
- Subtitles use segment/line-level timing, not word-level karaoke.
- BGM is selected randomly, not matched to mood.
- No GUI, batch queue, or publishing integration yet.
