**English** | [简体中文](../zh-CN/ROADMAP.md)

# Roadmap

This document answers two questions:

1. What the current version can already do;
2. What still needs work, and in what priority order.

> The complete design for the desktop panel, cross-platform support, and the engine abstraction is in [Desktop and multi-frontend architecture](DESKTOP_ARCHITECTURE.md). This document only records priorities and acceptance criteria.
> The strategy for English code, UI i18n, and multilingual output is in [Localization](LOCALIZATION.md).

---

## 0. Current status

Already working reliably:

- local scene detection + frame extraction;
- per-scene visual understanding with an Ollama VLM;
- Chinese narration written by a local LLM;
- Kokoro Chinese voiceover;
- mlx-whisper speech boundaries + script-based subtitles;
- sidechain ducking between BGM / ambient audio / narration;
- burned-in / soft subtitles;
- landscape 1080p, portrait 1080×1920;
- cache reuse, manifest, basic doctor, unit tests;
- stage commands: `script` / `tts` / `subtitle` / `mix` / `render` / `qa`;
- QA gate: stream/duration/loudness/true peak/clipping/black frames/frozen frames (static long shots tuned)/subtitle pixel overflow/narration-vs-subtitle per-sentence deviation/narration speed; thresholds are configurable.

Validated on two real clips:

- 82s indoor ski, portrait;
- 93s Niagara Falls, landscape 4K input → 1080p output.

---

## 0.1 Recently completed

- **P0-2 stage commands (done)**: `script` / `tts` / `subtitle` / `mix` / `render` / `qa`; the full `script → tts → subtitle → mix → render → qa` chain has been exercised on 6s footage; `run --only vlm,script` and `run --dry-run` are available.
- **P0-5 QA gate (done)**: `reachreel qa`, writes `qa.json`; `run` executes it automatically by default; `--strict` makes warnings fail too. Added subtitle pixel overflow, narration/subtitle per-sentence deviation, `astats` clipping checks, `[qa]` threshold configuration, and tuning for static long-shot freeze false positives.
- **Subtitle layout (done)**: burned-in subtitles now use `subtitles.ass`, `PlayRes` equals the output resolution, and `subtitle_size` / margins are real pixels; the portrait overflow issue is fixed.
- **P0-1 rotation metadata (done)**: display vs coded dimensions and rotation tests.
- **P0-3 retries and quality flags (done)**: VLM/LLM/TTS/Whisper retry or fall back; degradations land in `manifest.quality_flags`.
- **P0-8 BGM metadata (done)**: `inputs/bgm/manifest.json`, `manifest.bgm_metadata`, and `reachreel licenses`.
- **P0-4 subtitle safe areas (done)**: platform presets, punctuation/word-aware splitting, pause-weighted timing, hard line limits.
- **P0-7 cross-workdir cache (done)**: video-fingerprint VLM cache, content-keyed TTS audio, `cache info` / `cache clean`.
- **P0-9 macOS slice (done)**: UTF-8 subprocesses, `platformdirs` cache, `doctor --json`, and a Chinese/space-path integration smoke.
- **P1-3 review gate (done)**: `--review-script` writes `script.md` and stops before TTS.
- **P1-4 audio (done)**: two-pass `loudnorm` with single-pass fallback and BGM fade-out.
- Rotation/portrait display resolution fix.
- `render` reuses `manifest.render_settings`.

---

## P0: Correctness and usability (next priority)

If these are not solved, the more features we add, the more likely things break.

### P0-1 Rotation/portrait metadata (done)

- `probe_video` records `coded_width`/`coded_height` plus the swapped display `width`/`height`; `manifest.json` carries both.
- `--height` is documented and logged as display height.
- Tests cover 0°/90°/180°/270°/-90° and square frames.

**Acceptance**: a portrait iPhone clip logs 1080×1920, outputs 1080×1920, and the `manifest` agrees. Passed.

### P0-2 Stage commands (done)

```bash
reachreel script   output/xxx
reachreel tts      output/xxx
reachreel subtitle output/xxx
reachreel mix      output/xxx
reachreel render   output/xxx
reachreel qa       output/xxx

reachreel run video.mp4 --workdir output/xxx --only vlm,script
reachreel run video.mp4 --workdir output/xxx --only vlm,script --dry-run
```

`--only` supports `probe` / `vlm` (`analyze`) / `script` / `tts` / `subtitles` (`subtitle`) / `mix` / `render` / `qa`, executed in pipeline order; when a dependency is missing, it gives the next command. `--dry-run` only reads `ffprobe` and the existing cache state; it does not call models or write files.

**Acceptance**: editing the script does not require rerunning the VLM; editing subtitles does not require rerunning TTS; every step can be retried independently. Passed.

### P0-3 Error handling and retries (done)

- Ollama VLM/LLM calls retry with backoff; `quality_flags` records `vlm.scene_failed`, `vlm.no_frames`, and `vlm.unparsed`.
- TTS retries each segment and isolates failures as `tts.segment_failed`; only a total TTS failure aborts the stage.
- Whisper retries once, then falls back to the TTS timeline (`asr.fallback`).
- When FFmpeg fails, the full command and stderr tail are preserved.

**Acceptance**: when the network is down / Ollama is dead / a model is missing, the error directly points to "which step, which file, how to fix it". Passed.

### P0-4 Subtitle safe area and line breaking (done)

- `subtitle_preset` supports `douyin` / `reels` / `shorts` / `landscape`; a preset overrides the manual margins in both ASS output and QA.
- CJK splitting prefers punctuation; whitespace languages pack whole words instead of breaking mid-word.
- Cue duration is weighted by character count plus a pause bonus after sentence/clause punctuation.
- `subtitle_max_chars` and `subtitle_max_lines` are hard limits.
- Old-workdir subtitle style migration is documented in CONFIGURATION.

**Acceptance**: 20 randomly sampled cues with no dropped characters, no overflow, and no half-sentence breaks. Covered by unit tests and the integration smoke.

### P0-5 QA gate (done)

Implemented:

- final cut/stream/duration/audio-video sync;
- integrated loudness, true peak;
- `astats` peak / full-scale sample ratio / Flat factor clipping checks;
- black frames, frozen frames; freeze detection compares against source-footage motion, so static long shots are no longer false-flagged;
- subtitle cue count, characters per line, line count, timing, overlaps, boundaries, per-cue duration;
- actual rendered subtitle overflow detection: render onto a black background with the real libass style and measure pixel margins with `bbox`;
- per-sentence text/timing deviation between narration and subtitles;
- narration speed, narration end position;
- BGM file, burned-in/soft subtitle mode;
- writes `qa.json`; `run` executes it by default; `qa --strict` makes warnings fail too;
- thresholds via `[qa]` configuration; `qa --config` can override; `qa.json` records the actual thresholds.

**Acceptance**: a good cut returns 0, and a bad cut can be traced to a specific check; tuning thresholds no longer requires code changes.

### P0-6 Tests and CI (done)

- scene detection fixtures: `tests/integration/test_scenes.py` builds a three-colour clip with cuts at 2 s / 4 s and asserts the reported boundaries, the `min_len` merge, and the `max_len` split;
- subtitle character-preservation property test;
- timeline boundary tests: empty list, no scenes, unknown scene id, overlong narration logs a warning instead of raising, clips shorter than `tail_padding`, and the no-speed-up case; the real `atempo` rewrite is covered by `tests/integration/test_timeline.py`;
- mix filter graph unit tests: `mix_filter_graph()` is pure string construction; tests cover narration-only, ambient-only, BGM-only, both side-chains, zero ambient volume, and duck-ratio clamping;
- `render` reuses manifest settings: `_workdir_context` merging is covered, including that CLI flags still win;
- stage command smoke test: the smoke chain runs `tts` / `subtitle` / `mix` / `render` / `qa` / `licenses` as separate commands on a non-ASCII, space-containing workdir;
- QA SRT parsing/exit code tests, plus QA threshold, astats parsing, subtitle margin, per-sentence alignment, and `--only` parsing tests;
- `run --only` reuse smoke: `tests/integration/test_smoke.py::OnlyReuseTest` hashes `scenes.json` / `narration.wav` / `subtitles.srt` and proves `--only tts`, `--only mix,render`, and `--only qa` do not redo earlier stages;
- no audio track / mono / 5.1 at probe level (`probe_video` reports 0 / 1 / 6 channels) and rotation end to end (90° / 180° / 270° clips written with `-display_rotation`, asserting coded vs display size);
- GitHub Actions: `macos-15` runner + lint + unit tests (no large models).

**Acceptance**: PRs must pass lint + unit tests; core paths have regression tests; integration fixtures run locally with `REACHREEL_INTEGRATION=1`.

### P0-7 Cache keys and cross-workdir reuse (done)

- VLM results are keyed by a sampled video fingerprint plus model, prompt version, language, frame width, and brief.
- TTS audio is keyed by engine, model, voice, speed, and text.
- The optional shared root defaults to the platform cache dir; `cache_dir` overrides it.
- Workdir caches remain the primary visible artifacts, and the shared cache is merged into them.
- `reachreel cache info` and `cache clean --yes` inspect and remove the shared cache.

### P0-8 BGM metadata (done)

- `inputs/bgm/manifest.json` carries title, author, source URL, license, duration, and mood tags.
- `mix` copies the matching entry into `manifest.json` as `bgm_metadata`.
- `reachreel licenses <workdir>` writes `licenses.md` for publishing review.

### P0-9 Cross-platform correctness (macOS slice done; Windows pending)

See [DESKTOP_ARCHITECTURE.md §9](DESKTOP_ARCHITECTURE.md).

Done on macOS:

- `util.run` subprocesses use UTF-8 with replacement;
- the shared cache uses `platformdirs` (`~/Library/Caches/reachreel`) with a legacy-directory fallback;
- `doctor --json` exposes the availability matrix;
- the integration smoke runs from a Chinese/space path and burns subtitles.

Remaining for Windows/desktop:

- resolve `work_root` without relying on the CWD (`.app` / `.exe`);
- split extras (`accel-mlx` / `cpu` / `gui`);
- ship a subsetted Chinese font + `fontsdir`;
- prefer the app-bundled FFmpeg binary;
- replace MLX TTS/ASR with cross-platform engines (P2-1).

**Acceptance**: non-ASCII filenames and paths containing spaces can run through to `final.mp4` on all three platforms. macOS passes today; Windows waits for P2-1.

### P0-10 Code language and string externalization (must be done before the panel)

See [LOCALIZATION.md](LOCALIZATION.md).

- code comments/logs/CLI/errors are English; Python source has 0 CJK, Chinese exists only in `prompts/` and `locales/`, so enable the CJK lint in CI;
- `i18n.py` + `locales/{en,zh-CN}.toml`; externalize user-visible strings as `t()`;
- change events/errors to "code + params + current-language msg" so the CLI and the panel can use different languages;
- docs are bilingual: README, CHANGELOG, user docs, and engineering docs all have Chinese and English versions; user docs are Chinese-authoritative, engineering docs English-authoritative.

See [LOCALIZATION.md §4](LOCALIZATION.md) (split table / migration steps / banner / sync rules / CI / effort).

**Acceptance**: `doctor` / error messages / panel copy are each correct under `--lang en` and `--lang zh-CN`; both catalogs have identical keys.

---

## P1: Quality improvements (determine whether the final cut looks good)

### P1-1 TTS upgrade

- add Qwen3-TTS, IndexTTS2, Ming-3.0 TTS;
- voice preview/selection;
- pronunciation dictionary for polyphonic characters/proper nouns;
- emotion/speed control per paragraph;
- more natural pauses at sentence ends;
- optional voice cloning (requires explicit authorization).

**Goal**: Chinese naturalness clearly better than Kokoro, close to commercial narration.

### P1-2 VLM scene understanding upgrade

- done: representative-frame deduplication — near-identical keyframes are dropped before the VLM call, so a static long shot sends one frame instead of three;
- instead of uniform frame sampling, choose representative frames by "visual change/motion intensity";
- in-scene action recognition (jumping, turning, water flow direction);
- deduplication: merge descriptions of similar scenes;
- support video clip input (Qwen3-VL video / other local video models);
- subject tracking in frame to reduce generic labels like "tourist" and "child".

**Goal**: summaries for 12 scenes are no longer all alike, and can describe specific actions and spatial relationships.

### P1-3 Script quality

- Done: human review gate (`--review-script` writes `script.md` and stops before voiceover);
- fact constraints: every sentence must be traceable to VLM evidence from a `scene_id`;
- proper-noun whitelist (from `brief`);
- style presets: Vlog / documentary / commentary / healing / suspense;
- first-3-second hook;
- paragraph duration budget: derive character counts from actual TTS duration instead of only the target character count;
- first-3-second hook quality metrics.

### P1-4 Audio quality

- Done: two-pass `loudnorm` (measure first, then linear normalization) with a single-pass fallback;
- Done: BGM fade-out over `bgm_fade_seconds`;
- BGM structure-aware ducking (different depth for intro/chorus);
- ambient denoising/wind noise removal;
- Demucs separation of vocals/ambient audio to avoid the original speech clashing with the narration;
- optional "keep ambient audio only, drop the original speech".

### P1-5 Subtitle upgrade

- Done: portrait/landscape safe-area presets (`subtitle_preset`);
- per-character/per-word highlighting (karaoke);
- bilingual subtitles;
- subtitle templates: bottom bar, top title, name tags;
- portrait safe-area presets (Douyin/Reels/Shorts);
- SRT/ASS dual output;
- subtitle editor (local web).

### P1-6 Render upgrade

- `h264_videotoolbox` / `hevc_videotoolbox` hardware encoding;
- 4K / 10-bit / HDR (requires tone mapping);
- smart vertical reframing (face/subject tracking);
- intro/outro/title cards/progress bar;
- transitions (fade in/out, cross dissolve);
- cover image export;
- multi-platform master presets (Douyin/Xiaohongshu/Bilibili/YouTube).

### P1-7 Batch and project management

- `reachreel batch ./videos/`;
- watch folders and process new footage automatically;
- project file (`project.json`) storing footage, script, style, BGM;
- resume from checkpoint;
- failed job queue;
- progress/ETA.

---

## P2: Platform and productization

### P2-1 Cross-platform

- Windows / Linux;
- CUDA / ROCm / CPU backends;
- non-Apple TTS: Piper, Kokoro ONNX, Edge-TTS (optional cloud);
- non-Apple VLM: cross-platform Ollama is enough, but needs testing.

### P2-2 Graphical interface

**Chosen approach: local service (`reachreel serve`) + browser panel**, later wrapped in a Tauri shell reusing the same frontend.
The complete job model, event protocol, API contract, and v1 scope are in [DESKTOP_ARCHITECTURE.md](DESKTOP_ARCHITECTURE.md).
Do not use Gradio/Streamlit, and do not build a timeline editor.

- media import;
- scene/keyframe preview;
- script editing + re-voiceover;
- subtitle editing;
- BGM selection/preview;
- one-click render;
- job queue.

### P2-3 Distribution

- `pipx install reachreel`;
- Homebrew formula;
- macOS `.dmg` / menu bar app;
- Docker (CPU/GPU);
- model download manager (Kokoro/Whisper/Ollama).

### P2-4 Provider plugins

- `providers/` directory;
- VLM / LLM / TTS / ASR / Renderer interfaces;
- entry points plugin;
- config schema validation;
- third-party provider example.

### P2-5 Publishing integration

- export to Jianying/CapCut drafts;
- export to Premiere/FCPXML;
- publish directly to YouTube/Bilibili/Douyin (requires user authorization);
- generate titles/descriptions/tags.

---

## Not recommended now

These would significantly increase complexity with little current payoff:

- local music generation models (slow, unstable quality; CC0 BGM is more reliable);
- lip sync / digital humans;
- a full non-linear editing timeline;
- cloud rendering/cloud TTS (violates "local-first");
- training our own video generation models.

---

## Suggested version milestones

### v0.2 — Shippable (released as 0.2.0)

- P0-1 through P0-4;
- stage commands;
- subtitle safe areas;
- basic QA;
- tests filled in.

### v0.3 — Good-looking

- TTS upgrade;
- VLM representative frames/deduplication;
- script style presets + human review;
- two-pass loudnorm;
- subtitle templates;
- portrait presets.

### v0.4 — Easy to use

- batch/folder watching;
- local web UI;
- project files;
- model management;
- initial cross-platform support.

### v1.0 — Distributable

- plugin-based providers;
- Homebrew/DMG/pipx;
- complete docs + sample footage;
- stable QA/regression tests;
- a clear license/media compliance process.

---

## Contribution priorities

If you want to contribute, the most welcome areas are:

1. **Testing**: regression tests for scene detection, subtitles, timeline, and mixing;
2. **Subtitles**: line breaking, safe areas, per-character highlighting;
3. **TTS providers**: Qwen3-TTS / IndexTTS2 / Piper;
4. **VLM representative frames**: motion/change-based frame selection;
5. **QA**: black frames, freezes, clipping, overflow;
6. **Docs**: tuning case studies from more real footage.

Before submitting, please run:

```bash
uvx ruff check src/reachreel tests
python -m unittest discover -s tests -v
reachreel doctor
```
