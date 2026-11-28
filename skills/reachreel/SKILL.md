---
name: reachreel
description: Use when the user wants to narrate, voiceover, subtitle, mix BGM, render, QA-check, or export an NLE editing bundle for a local video using the ReachReel CLI (`reachreel`). Covers full-pipeline runs, script-review/editing loops, resuming existing work directories, voice/style selection, and QA remediation.
---

# ReachReel CLI Skill

ReachReel is a local-first video narration pipeline:

```text
probe -> vlm -> script -> tts -> subtitles -> mix -> render -> qa
```

**Core invariant — Disk is the source of truth.** Every job lives in a work directory containing `manifest.json` and stage artifacts (`scenes.json`, `script.json`, `timeline.json`, `narration.wav`, `subtitles.srt`, `subtitles.ass`, `mix.wav`, `final.mp4`, `qa.json`). Logs go to `stderr`; commands with `--json` emit pure JSON on `stdout`.

## 1. Pre-flight (always do first)

Before running any heavy stage (VLM, TTS, Whisper, or FFmpeg render):

1. **Check the environment**:
   ```bash
   reachreel doctor --json
   ```
   - Pass `--tts`, `--content-language`, `--voice`, or `--config` if the user requested non-default settings so `doctor` validates the exact target configuration.
   - If `ok` is `false`, inspect `checks` where `ok == false && required == true` and report the missing dependency (e.g., `ffmpeg-full`, Ollama model, `misaki` / `tts-en` extra) instead of running a doomed job.

2. **Discover valid voices and style presets** (never guess voice names):
   ```bash
   reachreel voices --json
   ```
   - Kokoro voices (e.g., `zf_xiaoxiao`, `af_heart`) only work with `--tts kokoro`.
   - Qwen3 speakers (e.g., `Serena`, `Vivian`, `Ryan`) only work with `--tts qwen3`.

3. **Dry-run before executing** (especially on new videos or option changes):
   ```bash
   reachreel run <video> --workdir <dir> --dry-run --json
   ```
   Verify detected resolution/orientation, chosen models/voice, stage plan, and cache reuse.

## 2. Core Workflows

### Workflow A: Full end-to-end narration

Always pass an explicit `--workdir` so the job path is predictable and resumable:

```bash
reachreel run <video> \
  --workdir <dir> \
  --content-language zh \
  --subtitle-preset douyin \
  --json
```

Inspect the returned JSON:
- `ok` and `exit_code` (non-zero when QA fails, or warns under `--qa-strict`)
- `output` (path to `final.mp4`)
- `qa.checks` (per-check `pass` / `warn` / `fail`)
- `quality_flags` (degradations such as VLM fallback or ASR fallback)

### Workflow B: Script-review and Agent-assisted editing (recommended for quality)

`--review-script` is a **non-interactive stage gate** — it runs `probe -> vlm -> script`, writes `script.json` and `script.md`, and exits cleanly without waiting on stdin.

1. Generate scenes and initial script:
   ```bash
   reachreel run <video> --workdir <dir> --review-script --json
   ```
2. Read `<dir>/script.md` (human-readable preview with scene evidence and estimated timings) and `<dir>/scenes.json` (per-scene timestamps, visual summaries, and extracted keyframe paths).
3. Edit `<dir>/script.json` directly (e.g., refine `segments[].text`, adjust `tts_speed`, or set per-segment `voice` / `instruct` for Qwen3). See [references/artifacts-and-schemas.md](references/artifacts-and-schemas.md) for the exact editable schema.
4. Resume downstream stages without re-running VLM or overwriting your edits:
   ```bash
   reachreel resume <dir> --json
   ```
   - Equivalent to `reachreel run <video> --workdir <dir> --only tts,subtitles,mix,render,qa --json`, without re-specifying `<video>`.
   - ReachReel detects manual edits via `generation.segments_key` and preserves them on resume.
   - Avoid `--force` or `reachreel script <dir>` unless you intentionally want the LLM to overwrite `script.json`.

### Workflow C: Inspect and resume an existing work directory

When taking over an existing work directory, **always start with `status`**:

```bash
reachreel status <workdir> --json
```

Use the response to decide what to run next:
- `stages[]`: each stage's `status` is `ok`, `stale`, or `missing`.
- `script_edited`: `true` if `script.json` was manually edited after generation.
- `next_stage` / `next_command`: the exact next stage and command needed to bring the job up to date (for example, after editing `script.json`, `tts` becomes `stale` and `next_command` points to `reachreel tts <workdir>`).

### Workflow D: Export for NLE editing & BGM licenses

To hand off tracks (clean video, narration WAV, SRT, optional mix, FCPXML) to CapCut, Final Cut Pro, Premiere Pro, or DaVinci Resolve:

```bash
reachreel export <workdir> --format mp4 --out <export_dir>
```

To export a BGM attribution/license report when BGM metadata is present:

```bash
reachreel licenses <workdir>
```

## 3. QA Remediation Loop

When `run --json` or `reachreel qa <workdir> --json` reports `warn` or `fail`, fix only the owning stage instead of rerunning from scratch:

| QA check issue | Likely fix | Stages to rerun |
| --- | --- | --- |
| Subtitle pixel overflow / line too long | Set `--subtitle-preset douyin\|reels\|shorts\|landscape`, lower `--subtitle-size`, or lower `--subtitle-chars` | `subtitle` -> `render` -> `qa` |
| Narration speed too high / narration past video end | Shorten verbose segments in `script.json`, or lower `--density` / adjust `--max-speedup` | `tts` -> `subtitles` -> `mix` -> `render` -> `qa` |
| Narration/subtitle alignment mismatch | Verify ASR (`mlx-whisper`) ran without `asr.fallback`, or check punctuation/script text | `subtitle` -> `render` -> `qa` |
| Loudness / true peak / clipping | Adjust `[audio]` / BGM mix settings in config | `mix` -> `render` -> `qa` |
| Missing/degraded scene descriptions (`vlm.*` in `quality_flags`) | Verify Ollama is healthy (`doctor --json`) and re-run analysis | `analyze` (or `--only vlm,script`) |

## 4. Operational Guardrails

- **Separate UI and content languages**: `--lang` (`en` / `zh-CN`) controls CLI text only; `--content-language` (`zh` / `en`) controls narration and subtitles; `--tts-language` (`zh-CN` / `en-US` / `en-GB`) overrides voiceover pronunciation.
- **Respect runtime**: VLM, TTS, Whisper, and FFmpeg render are compute-heavy and can take several minutes on longer videos. Prefer `--dry-run --json` first, reuse `--workdir` and caches, and split into `--review-script` + downstream stages when helpful.
- **Avoid destructive defaults**: Never pass `--force` unless rebuilding stale/bad caches intentionally; use `reachreel cache info` before `reachreel cache clean --dry-run` / `--yes`.
- **Platform requirement**: Local MLX TTS/ASR requires macOS on Apple Silicon; confirm via `reachreel doctor --json`.

## References

- Work directory artifacts, `script.json` editable fields, and JSON output schemas: [references/artifacts-and-schemas.md](references/artifacts-and-schemas.md)
- Common CLI recipes and troubleshooting flags: [references/cli-recipes.md](references/cli-recipes.md)
