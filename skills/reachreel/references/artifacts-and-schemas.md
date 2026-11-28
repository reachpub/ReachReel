# ReachReel Artifacts & JSON Contracts

## Work directory layout

Every job lives in `<workdir>/` and is self-contained on disk:

| File | Producer stage | Purpose |
| --- | --- | --- |
| `manifest.json` | all stages | Authoritative job state: video metadata, settings snapshot, stage input fingerprints, `quality_flags`, QA summary, artifact paths |
| `scenes.json` | `probe` / `vlm` | Detected scene cuts (`id`, `start`, `end`, `frames`, `summary`, `details`, `mood`, `place`, `subjects`, `actions`) |
| `frames/` | `probe` | Extracted JPEG keyframes per scene |
| `vlm_cache.json` | `vlm` | Local VLM cache per scene |
| `script.json` | `script` | Editable narration script consumed by `tts` |
| `script.md` | `script` (`--review-script` / `--only script`) | Human-readable script preview with scene evidence and estimated timings |
| `tts/` | `tts` | Synthesized per-segment WAV files |
| `timeline.json` | `tts` | Placed narration segments with measured `start`, `end`, `duration`, `audio_path`, `speed` |
| `narration.wav` | `tts` | Combined narration track aligned to video duration |
| `subtitles.srt` | `subtitles` | Timestamped SRT cues |
| `subtitles.ass` | `subtitles` | Styled libass subtitle file matching output pixel dimensions |
| `bgm_loop.wav` | `mix` | Prepared BGM track looped/faded to video duration (when BGM is enabled) |
| `mix.wav` | `mix` | Final mixed audio (narration + ducked ambient + ducked BGM + loudnorm) |
| `final.mp4` | `render` | Rendered video with burned-in or soft subtitles |
| `review.md` | `tts`..`render` | Post-synthesis narration placement review and shot-overflow notes |
| `qa.json` | `qa` | Full post-render QA gate report and thresholds |
| `edit/` | `export` | NLE editing bundle (video + audio tracks + SRT + FCPXML) |
| `licenses.md` | `licenses` | BGM license/attribution summary |

## Editing `script.json`

When refining narration after `--review-script` or `analyze` + `script`, edit `<workdir>/script.json` in place:

```json
{
  "content_language": "zh",
  "style_preset": "oral",
  "style": "",
  "segments": [
    {
      "id": 1,
      "scene_ids": [1, 2],
      "text": "Your refined narration sentence here.",
      "tts_speed": 1.0,
      "voice": "",
      "instruct": ""
    }
  ],
  "generation": {
    "inputs_key": "...",
    "segments_key": "..."
  }
}
```

Field notes:
- `id` (`int`): Unique ascending segment identifier.
- `scene_ids` (`list[int]`): Scene IDs from `scenes.json` that anchor this segment's placement window.
- `text` (`str`): Spoken narration text. Keep it concise enough to fit within the linked scenes' duration (see `script.md` for estimated speech duration).
- `tts_speed` (`float`, optional, default `1.0`): Per-segment base TTS speaking rate multiplier.
- `voice` (`str`, optional): Per-segment voice override (must match the active TTS engine).
- `instruct` (`str`, optional): Per-segment emotion/delivery instruction for Qwen3 CustomVoice.
- Leave `generation` untouched when editing `segments`; ReachReel compares `generation.segments_key` against the current `segments` hash to detect manual edits and preserve them on resume.
- `content_language` must match `--content-language` (default `"zh"`). Changing language on a manually edited script intentionally errors until you run `reachreel script <workdir> --content-language <lang>`.

## CLI `--json` contracts

### `reachreel status <workdir> --json`

```json
{
  "workdir": "/abs/path/to/job",
  "schema_version": 2,
  "complete": false,
  "video": {
    "path": "/abs/path/to/video.mp4",
    "exists": true,
    "duration": 82.0,
    "width": 1080,
    "height": 1920,
    "fps": 30.0
  },
  "settings": {
    "content_language": "zh",
    "vlm_model": "qwen2.5vl:7b",
    "llm_model": "qwen2.5:14b",
    "tts_engine": "kokoro",
    "voice": "zf_xiaoxiao",
    "whisper_model": "mlx-community/whisper-large-v3-turbo",
    "burned_subtitles": true
  },
  "script_edited": true,
  "stages": [
    {"stage": "probe", "status": "ok", "artifact": ".../manifest.json", "exists": true},
    {"stage": "vlm", "status": "ok", "artifact": ".../scenes.json", "exists": true},
    {"stage": "script", "status": "ok", "artifact": ".../script.json", "exists": true},
    {"stage": "tts", "status": "stale", "artifact": ".../timeline.json", "exists": true},
    {"stage": "subtitles", "status": "ok", "artifact": ".../subtitles.srt", "exists": true},
    {"stage": "mix", "status": "ok", "artifact": ".../mix.wav", "exists": true},
    {"stage": "render", "status": "ok", "artifact": ".../final.mp4", "exists": true},
    {"stage": "qa", "status": "stale", "artifact": ".../qa.json", "exists": true}
  ],
  "artifacts": {
    "manifest": ".../manifest.json",
    "scenes": ".../scenes.json",
    "script": ".../script.json"
  },
  "quality_flags": [],
  "qa": {
    "status": "pass",
    "summary": {"pass": 12, "warn": 0, "fail": 0},
    "stale": true
  },
  "next_stage": "tts",
  "next_command": "reachreel tts /abs/path/to/job"
}
```

### `reachreel run <video> ... --dry-run --json`

Returns the planned execution without calling models or creating files:
- `dry_run`: `true`
- `mode`: `"full"` | `"selected"` | `"review_script"`
- `video`: probed dimensions, fps, duration, `has_audio`
- `workdir`, `workdir_exists`
- `models`: `vlm`, `llm`, `tts`, `voice`
- `render`: `height`, `fps`, `burn_subtitles`
- `qa`: `enabled`, `strict`
- `stages`: list of `{stage, action ("run"|"skip"), label, note}`

### `reachreel run <video> ... --json`

Returns a summary after execution (exit code `0` on success, `1` on runtime error or QA failure / strict QA warning):
- On success or QA completion: `ok`, `exit_code`, `dry_run: false`, `workdir`, `video`, `output`, `stages_executed`, `review_script`, `burned_subtitles`, `artifacts`, `quality_flags`, `qa` (full QA report with `exit_code`, or `null`), `next_stage`, `next_command`.
- On runtime error: `ok: false`, `exit_code: 1`, `dry_run`, `workdir`, `video`, `error`.

### `reachreel doctor --json` and `reachreel qa <workdir> --json`

- `doctor --json`: `{ok: bool, checks: [{id, label, value, status, required, ok}]}`. Exit `0` when all required checks pass, `1` otherwise.
- `qa --json`: `{workdir, output, summary: {pass, warn, fail}, thresholds, checks: [{id, status, message, details}]}`. Exit `0` on pass (or warn without `--strict`), `1` on fail (or warn with `--strict`).
