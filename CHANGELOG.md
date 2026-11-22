**English** | [简体中文](CHANGELOG.zh-CN.md)

# Changelog

## [Unreleased]

### Changed

- Supported platform narrowed to macOS 14+ on Apple silicon; Intel macOS is not targeted, and CI pins the current arm64 label.
- `reachreel doctor` now checks the host architecture and fails a `Platform` row on non-Apple-silicon machines instead of letting them run slowly.
- Minimum Python is now 3.11 (`mlx-audio` requires it) and the `tomli` fallback was removed; Python 3.10 can no longer install the package.

### Fixed

- CI now runs on `macos-15`: the `macos-14` label was retired and queued jobs failed with "The configuration 'macos-14-arm64-us-xl' is not supported".

## [0.2.0] - 2026-11-22

### Added

- CI workflow (`.github/workflows/ci.yml`) running Ruff, the unit suite, and `uv build` on macOS for Python 3.10 and 3.12.
- Opt-in integration smoke test (`tests/integration/test_smoke.py`) covering `tts -> subtitle -> mix -> render -> qa` through the real CLI with macOS `say`.
- `tomli` fallback so Python 3.10 can load TOML catalogs and configuration.
- `manifest.json` now records `quality_flags` for degradations that do not stop the run (VLM scene failure, TTS segment failure, ASR fallback, QA warnings).
- Per-segment TTS retry and failure isolation; a single failed segment is skipped, while a total TTS failure aborts with a clear message.
- VLM and Whisper calls now retry with backoff.
- Optional `inputs/bgm/manifest.json` track catalog; `manifest.json` records `bgm_metadata`, and `reachreel licenses <workdir>` exports `licenses.md`.
- `VideoInfo` now records `coded_width` / `coded_height` alongside the rotated display size.
- Optional shared cache in the platform cache dir: VLM results keyed by a video fingerprint and TTS audio keyed by content, plus `reachreel cache info` and `cache clean`.
- Global `--cache-dir` / `--no-cache` flags.
- `subtitle_preset` platform safe areas (`douyin` / `reels` / `shorts` / `landscape`).
- `reachreel doctor --json` for machine-readable environment checks.
- The integration smoke now runs from a Chinese/space path, mixes a BGM track, and burns subtitles.
- `run --review-script` stops after the script stage and writes `script.md` for human review.
- Two-pass `loudnorm` (`two_pass_loudnorm`) with a single-pass fallback, and BGM fade-out (`bgm_fade_seconds` / `--bgm-fade`).
- Subtitle splitting keeps English words intact, and cue timing adds a pause bonus after sentence/clause punctuation.

### Changed

- Project license changed from MIT to Apache-2.0; added the full license text and a `NOTICE` file.
- `--height` is consistently documented and logged as display height for rotated footage.
- Subtitle line length and line count are enforced as hard limits; QA compares text with whitespace normalized.
- Mixing now measures loudness first and then linear-normalizes by default, which adds one audio-only pass.
- The shared cache now defaults to the platform cache dir (`~/Library/Caches/reachreel` on macOS) via `platformdirs`; the legacy `~/.cache/reachreel` is reused when the new directory does not exist yet.

### Fixed

- Test paths in `docs/en/DEVELOPMENT.md` and `docs/zh-CN/DEVELOPMENT.md` now match the `tests/unit/` layout.
- Reusing `scenes.json` no longer keeps stale video metadata; rotation and coded dimensions are refreshed from a fresh probe.

### Validation

- `output/ski` (1080x1920, 82s): full run with existing caches took 41s wall time; QA 20/20 pass.
- `output/niagara` (1920x1080, 93s): full run with existing caches took 56s wall time; QA 20/20 pass.
- `--review-script` wrote `script.md` on the ski work directory without re-running VLM or TTS.

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
