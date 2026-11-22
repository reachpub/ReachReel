**English** | [简体中文](CHANGELOG.zh-CN.md)

# Changelog

## [Unreleased]

### Added

- PyPI-facing project metadata in `pyproject.toml`: `Project-URL` entries (homepage, repository, documentation, changelog, issues), eleven Trove classifiers, keywords, and the author. `twine check` passes for the wheel and the sdist, the licence renders through PEP 639 (`License-Expression: Apache-2.0` plus `License-File: LICENSE`/`NOTICE`), and the build emits Metadata-Version 2.5.

### Fixed

- The `dev` extra did not actually list `pre-commit`, although `.pre-commit-config.yaml` and the docs tell contributors that `uv pip install -e '.[dev]'` provides it. It is part of the extra again (`pre-commit>=3.5`).

## [0.3.0] - 2026-11-22

### Added

- `src/reachreel/languages.py`: one registry of language profiles per `docs/en/LANGUAGES.md`, mapping BCP 47 tags to per-language defaults (speech rate, subtitle line length, font, engine code, dependency extra). Engine codes such as Kokoro's `a`/`b`/`z` now live only there.
- `tts_language` (BCP 47, `--tts-language`) and the voice/language guard: an unsupported tag or a voice whose language disagrees fails with the list of validated tags instead of producing a wrong-language voiceover that still passes QA. `reachreel doctor` prints the resolved `content` / `tts` / `asr` axes and the engine code.
- `doctor` reports the resolved TTS language and marks a planned language as missing.
- `docs/en/LANGUAGES.md` and its Chinese counterpart define the language and voice specification: BCP 47 tags only, four separate language axes, one language-profile registry, an engine capability contract, per-language dependency extras, and the validation gate a language must pass before it is called supported. Today only `zh-CN` TTS passes that gate.
- `render` warns when `output_height` is 0 and the source is larger than 1080p on its short side, because such a run silently keeps 4K, produces a much larger file, and makes the subtitle size look small. Found by running the Niagara 4K clip end to end.
- Representative-frame deduplication for the VLM stage: each extracted frame is thumbnailed to 16x16 grayscale and dropped when it barely differs from the last kept frame. New setting `vlm_frame_dedup` (default `4.0`, `0` disables) and `--vlm-dedup`; a static long shot now sends one frame instead of three, and the threshold is part of the VLM cache key.
- CI runs the "no CJK in Python source" guard as its own step, so a violation reports the file and line instead of hiding inside the full test output.
- Commit messages are now validated: `scripts/check_commit_messages.py` runs from the `commit-message` pre-commit hook and in CI over every commit a push or pull request adds.
- `.pre-commit-config.yaml` mirrors the CI convention checks locally (`apdev check-imports`, `apdev check-chars`, `ruff`, commit message), installed with `pre-commit install`; the older `.githooks` directory is gone, so clones that used it run `git config --unset core.hooksPath` once.
- `scripts/check_changelog.py` keeps `CHANGELOG.md` and `CHANGELOG.zh-CN.md` structurally in sync in CI: version headings, section order, and the language of each heading. A section that is empty in one language fails; a per-section bullet-count drift only warns unless `--strict` is passed, because one long English bullet can legitimately become two Chinese ones. It found the untranslated `### Added` / `### Known limitations` headings of the 0.1.0 entry, which are now Chinese.
- Development tooling moves to `apdev` (`apdev[dev]>=0.2.5`, APCore's shared toolkit) through a `dev` extra and `[tool.apdev]`. `apdev check-chars` with `scripts/apdev_charset.json` replaces the in-test CJK scan and allows full-width punctuation while keeping Chinese ideographs illegal, and `apdev check-imports` guards the dependency direction.
- `scripts/check_config_docs.py` keeps the configuration surface honest: every `Settings` field must exist in `config.example.toml` and be documented in both `CONFIGURATION.md` files, unknown keys fail, and a default drift only warns unless `--strict` is passed. Its first run found 18 `[qa]` thresholds missing from `config.example.toml`; they are now listed.
- `ci.yml` is split into a `checks` job on `ubuntu-latest` (commit messages, changelog parity, Python sources are English) and the macOS `test` job, so a bad commit subject fails in seconds instead of after a macOS runner is allocated. The unit-test matrix no longer repeats those checks twice.
- Unit tests for the mix filter graph: `mix_filter_graph()` now builds the ducking graph as a pure function, covered for narration-only, ambient-only, BGM-only, both side-chains, zero ambient volume, and duck-ratio clamping.
- `tests/integration/test_scenes.py`: FFmpeg-only fixtures with known cut points (2 s / 4 s) for `detect_scenes`, no-audio / mono / 5.1 clips for `probe_video` channel reporting, and 90° / 180° / 270° clips for coded-vs-display dimensions.
- `tests/integration/test_timeline.py`: covers the narration `atempo` fit path on real WAVs, and that a timeline that already fits is left untouched.
- `tests/integration/test_smoke.py::OnlyReuseTest`: `run --only tts`, `--only mix,render`, and `--only qa` are asserted not to redo earlier stages, using artifact hashes.
- Unit tests for timeline boundaries: empty list, no scenes, unknown scene id, overlong narration warning, and clips shorter than `tail_padding`.
- Retroactive `v0.1.0` (`b8a60ae`) and `v0.2.0` (`c9c3652`) tags, each with a GitHub release whose wheel and sdist were built from the tagged commit. Both are labelled retroactive because neither version had ever been tagged or announced.

### Changed

- `asr_language` now defaults to empty, which means "follow the primary subtag of `content_language`"; Chinese content still resolves to `zh`, so nothing changes for the supported pipeline.
- Hard subtitles now default to white text with a black outline and a soft shadow (`BorderStyle=1`, `subtitle_render = "outline"`), instead of the translucent black box that hid a large part of the frame. The stroke is about 6% of `subtitle_size` and can be overridden with `subtitle_outline` / `--subtitle-outline`.
- `subtitle_render = "box"` (or `--subtitle-render box`) restores the old look, with its padding now configurable via `subtitle_box_padding` / `--subtitle-box-padding`; `subtitle_render = "none"` draws plain white text without stroke or shadow. Work directories that recorded `render_settings` keep their previous mode.
- Supported platform narrowed to macOS 14+ on Apple silicon; Intel macOS is not targeted, and CI pins the current arm64 label.
- `reachreel doctor` now checks the host architecture and fails a `Platform` row on non-Apple-silicon machines instead of letting them run slowly.
- Minimum Python is now 3.11 (`mlx-audio` requires it) and the `tomli` fallback was removed; Python 3.10 can no longer install the package.

### Fixed

- CI now runs on `macos-15`: the `macos-14` label was retired and queued jobs failed with "The configuration 'macos-14-arm64-us-xl' is not supported".
- `render --crf` was parsed but never applied; the value is now forwarded to `Settings.crf` like `--height` / `--fps`, and a test asserts manifest `render_settings` are reused while CLI flags still win.

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
