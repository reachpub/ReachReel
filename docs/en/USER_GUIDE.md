**English** | [简体中文](../zh-CN/USER_GUIDE.md)

# ReachReel User Guide

This is the complete user-facing operating manual. For development and architecture notes see [DEVELOPMENT.md](DEVELOPMENT.md); for every configuration key see [CONFIGURATION.md](CONFIGURATION.md).

---

## 1. What it does

Give it a local video and it automatically:

1. splits the video into scenes and extracts keyframes for each scene;
2. uses a local VLM (`qwen3-vl`) to look at the footage and describe location, people, actions, details, and mood;
3. uses a local LLM (`qwen3`) to write Chinese narration in chronological order;
4. uses local TTS (Kokoro-82M / MLX) to voice each segment;
5. anchors the narration to the corresponding scenes, speeding it up overall when necessary;
6. uses `mlx-whisper` to detect speech boundaries and generate SRT subtitles;
7. uses FFmpeg to mix BGM, ambient audio, and narration with sidechain ducking;
8. uses `ffmpeg-full` to burn the subtitles into the picture and outputs an H.264/AAC MP4.

Everything runs locally; no cloud API is required. The first run needs to download the Kokoro and Whisper models.

### What it is not for

- Word-by-word karaoke subtitles, bilingual subtitles, or subtitle translation;
- HDR / 10-bit / high-bitrate 4K masters;
- Smart vertical reframing (automatically cropping a landscape subject to 9:16);
- Multiple camera angles, transitions, animated text, or intro/outro templates;
- Batch queues, a web UI, or automatic publishing.

These are covered as future work in [ROADMAP.md](ROADMAP.md).

---

## 2. System requirements

| Item | Requirement |
|---|---|
| Machine | Apple Silicon Mac (M1 / M2 / M3 / M4) |
| OS | macOS 14+ (Sonoma or later) on Apple silicon; CI validates macOS 15 |
| Python | 3.11 – 3.12 (3.10 is unsupported because `mlx-audio` requires 3.11+; do not use 3.13, the Kokoro dependency chain is not fully compatible yet) |
| Memory | 16 GB works; 32 GB is more comfortable; 64 GB allows higher VLM concurrency |
| Disk | At least 10 GB free (models + intermediate files) |
| External tools | `ffmpeg`, `ffmpeg-full`, `ffprobe`, `ollama` |
| Network | Needed for the initial model download; offline afterwards |

Intel Macs are **not supported**. `mlx-audio` / `mlx-whisper` publish no x86_64 macOS wheels, and a Rosetta/x86 build of the same pipeline is both slower and lower quality (draft models, fewer frames, no Kokoro). `reachreel doctor` fails the `Platform` check on any host that is not macOS on Apple silicon; if you only want to check tool wiring on such a machine, expect that one `!!` row and ignore the rest of the report.

---

## 3. Installation

### 3.1 Install the system tools

```bash
brew install ffmpeg ffmpeg-full
```

Check:

```bash
ffmpeg -version
/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg -hide_banner -filters | grep subtitles
```

The second command should list `subtitles`. If it does not, `ffmpeg-full` is not installed correctly.

> The regular `ffmpeg` has no libass and cannot burn hard subtitles. ReachReel automatically prefers
> `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg`; you can also point to it with the `FFMPEG_FULL` environment variable.

### 3.2 Create the Python environment

In the project directory:

```bash
cd /path/to/reachreel
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
```

If you use `uv`:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e .
```

### 3.3 Prepare the Ollama models

```bash
ollama serve          # if it is not already running in the background
ollama pull qwen3-vl:8b-instruct
ollama pull qwen3:30b-instruct
```

Low memory, or want it faster:

```bash
ollama pull qwen3-vl:4b-instruct
ollama pull qwen3.5:9b
```

Then specify them on the command line:

```bash
reachreel run video.mp4 \
  --vlm-model qwen3-vl:4b-instruct \
  --llm-model qwen3.5:9b
```

### 3.4 Self-check

```bash
reachreel doctor
```

Once everything shows `OK`, you are ready to start. An empty BGM directory only shows `--` and does not affect the run.

---

## 4. Preparing your media

### 4.1 Video format

Common formats that FFmpeg can read are supported: `.mp4` `.mov` `.mkv` `.webm` `.m4v`, and so on.

Recommendations:

- Resolution: 1080p is the fastest; for 4K, downscale to 1080p on output.
- Frame rate: anything from 24–60 fps works; output keeps the source frame rate by default, and specifying 30 explicitly is recommended.
- Duration: the MVP works best with 1–10 minute clips; longer clips significantly increase VLM time.
- Audio track: ambient audio is preferred; the pipeline also runs without an audio track.
- Rotation: iPhone portrait videos carry rotation metadata. The program outputs the frame as auto-rotated by FFmpeg, and the logs/`manifest` record the display resolution.

### 4.2 Landscape / portrait

```bash
# 4K landscape -> 1080p30
reachreel run video.mp4 --height 1080 --fps 30

# Portrait -> 1080x1920
reachreel run video.mp4 --height 1920 --fps 30
```

`--height` sets the output height; the width is calculated automatically to preserve the aspect ratio.

### 4.3 Shooting tips

- Clear cuts or movement between scenes make it easier for the VLM to write something distinct.
- Continuous single-take footage is split into chunks according to `max_scene_seconds` (default 8s), and the narration may end up describing similar shots repeatedly.
- If the footage involves place names, relationships between people, or background context for the activity, put them in `--brief`; this significantly reduces fabrication.

---

## 5. Preparing BGM

`inputs/bgm/` is a local input directory, ignored by Git and created automatically on first run. No BGM is bundled. See [BGM](BGM.md) for details.

Put music in `inputs/bgm/`:

```text
inputs/bgm/
  calm-morning.mp3
  mountain-air.mp3
  river-walk.wav
```

Supported formats: `.mp3` `.wav` `.m4a` `.aac` `.flac` `.ogg` `.opus`.

- A track is chosen at random by default;
- it loops automatically to the video length;
- it is ducked automatically whenever narration is present;
- ambient audio is also ducked slightly, but keeps its sense of place.

To pick a specific track:

```bash
reachreel run video.mp4 --bgm inputs/bgm/calm-morning.mp3
```

To skip BGM:

```bash
reachreel run video.mp4 --no-bgm
```

Recommended sources: Pixabay Music, Free Music Archive (CC0), YouTube Audio Library, and CC0 audio from the Internet Archive. Verify the license of each track before publishing.

---

## 6. Your first full run

```bash
reachreel run /path/to/video.mp4 \
  --brief "拍摄于云南雨崩，清晨从雨崩村出发，途经冰湖" \
  --style "安静、克制，像独自徒步时的自言自语" \
  --voice zf_xiaoxiao \
  --density 0.5 \
  --height 1080 --fps 30
```

### What happens during the run

1. `Probe video`: ffprobe reads duration, resolution, frame rate, and audio tracks.
2. `Detect scenes`: FFmpeg scene scores find hard cuts; long takes are split into chunks and fragmented shots are merged.
3. `Extract keyframes`: 1–3 frames per scene, 768 px on the long edge.
4. `VLM analysis`: Ollama is called scene by scene and returns structured visual descriptions.
5. `Write script`: the local LLM reads the scene table and writes JSON narration segments.
6. `Voiceover`: Kokoro synthesizes each segment and caches it in `tts/`.
7. `Narration placement`: narration is anchored to scene starts; when it runs long, atempo speeds it up overall.
8. `Subtitles`: Whisper detects speech boundaries; script text + timestamps produce the SRT.
9. `Mix`: sidechain ducking for BGM / ambient / narration, with loudness normalized to -14 LUFS.
10. `Render`: burn subtitles, H.264, AAC, faststart.

### Time estimates

Measured on an M4 Pro / 64 GB with `qwen3-vl:8b` and 2 workers:

| Footage | Scenes | VLM | Script | TTS | Subtitles | Render | Total |
|---|---:|---:|---:|---:|---:|---:|---:|
| 82s portrait 1080p | 12 | ~3 min | ~10s | ~5s | ~5s | ~30s | ~4 min |
| 93s landscape 4K | 12 | ~4 min | ~10s | ~5s | ~5s | ~50s | ~5 min |

Add model download time for the first run.

---

## 7. Command reference

### 7.1 `reachreel run`

Runs the full pipeline.

```bash
reachreel run VIDEO [options]
```

| Option | Default | Description |
|---|---|---|
| `VIDEO` | Required | Input video path |
| `--workdir DIR` | Auto | Work directory; defaults to `output/<video-name>_<timestamp>` |
| `--out DIR` | `output` | Work directory root; only takes effect when `--workdir` is not specified |
| `--config FILE` | None | TOML configuration file |
| `--bgm FILE` | Random | Specific BGM file |
| `--bgm-dir DIR` | `bgm` | BGM directory |
| `--bgm-fade N` | `2.0` | Fade the BGM out over the final N seconds |
| `--no-bgm` | Off | Do not mix BGM |
| `--voice NAME` | Follows language | Kokoro voice (`zf_xiaoxiao`, `af_heart`, `bf_emma`, ...) |
| `--tts ENGINE` | `kokoro` | `kokoro` or `say` (for debugging) |
| `--tts-language TAG` | Follows content language | Voiceover language as a BCP 47 tag (`zh-CN`, `en-US`, `en-GB`); the voice must match it |
| `--vlm-model NAME` | `qwen3-vl:8b-instruct` | Visual understanding model |
| `--llm-model NAME` | `qwen3:30b-instruct` | Script-writing model |
| `--whisper-model NAME` | `mlx-community/whisper-large-v3-turbo` | Subtitle model |
| `--vlm-workers N` | `2` | VLM concurrency |
| `--vlm-frames N` | `3` | Frames extracted per scene |
| `--vlm-dedup X` | `4.0` | Drop frames whose difference from the previous kept frame is below `X` (`0` disables deduplication) |
| `--scene-threshold X` | `0.32` | Scene cut threshold; lower is more sensitive |
| `--style TEXT` | Built-in | Narration style prompt |
| `--brief TEXT` | Empty | Shooting location / people / background information |
| `--density X` | `0.62` | Narration density 0.2–1.0; lower leaves more breathing room |
| `--max-speedup X` | `1.32` | Maximum speedup allowed when narration runs too long |
| `--asr MODE` | `mlx-whisper` | `mlx-whisper` or `none` (use the TTS timeline) |
| `--height N` | Source resolution | Output height |
| `--fps N` | Source frame rate | Output frame rate |
| `--subtitle-size N` | `48` | Subtitle font size (output pixels) |
| `--subtitle-chars N` | Follows language (`18` zh / `42` en) | Maximum characters per line |
| `--subtitle-preset NAME` | Empty | `douyin` / `reels` / `shorts` / `landscape` safe-area preset; overrides the margins |
| `--subtitle-render MODE` | `outline` | `outline`: white text + black stroke + soft shadow; `box`: translucent black box (older look, hides more video); `none`: plain white text |
| `--subtitle-outline X` | `0` | Stroke width in output pixels (`outline` mode); `0` = auto (about 6% of the font size) |
| `--subtitle-box-padding X` | `0` | Text padding in output pixels (`box` mode); `0` = auto (about 6.7 px at 1920 height) |
| `--subtitle-margin-v N` | `36` | Bottom safe margin (output pixels) |
| `--subtitle-margin-left N` | `50` | Left safe margin (output pixels) |
| `--subtitle-margin-right N` | `50` | Right safe margin (output pixels) |
| `--no-burn` | Off | Do not burn hard subtitles; use a soft subtitle track instead |
| `--no-qa` | Off | Skip the QA gate |
| `--qa-strict` | Off | Return non-zero even when QA only reports warnings |
| `--only STAGES` | All | Run only the specified stages, comma-separated, e.g. `vlm,script` |
| `--dry-run` | Off | Only print the stages that would run and their cache status; do not call models or write files |
| `--review-script` | Off | Stop after the script stage and write `script.md` for review |
| `--force` | Off | Ignore all intermediate artifacts and redo everything |
| `--no-resume` | Off | Do not reuse scenes/script, but keep the VLM/TTS cache |
| `--verbose` | Off | Debug logging |

> Global options: `--lang en|zh-CN` switches the CLI language; `--content-language zh|en` switches narration/subtitle language. They are independent. `--cache-dir DIR` and `--no-cache` control the shared cache.

Common patterns:

```bash
# First confirm which stages would run
reachreel run video.mp4 --workdir output/xxx --only vlm,script --dry-run

# Redo VLM + script only, without re-voicing
reachreel run video.mp4 --workdir output/xxx --only vlm,script

# Redo subtitles and the final render only
reachreel run video.mp4 --workdir output/xxx --only subtitles,mix,render

# Generate the script, review script.md, then continue
reachreel run video.mp4 --workdir output/xxx --review-script
# edit output/xxx/script.json, then:
reachreel tts output/xxx
reachreel subtitle output/xxx && reachreel mix output/xxx && reachreel render output/xxx

# English narration, voiceover, and subtitles (requires the tts-en extra + en_core_web_sm)
reachreel run video.mp4 --content-language en --height 1080 --fps 30
# British English voiceover
reachreel run video.mp4 --content-language en --tts-language en-GB --voice bf_emma
```

Available `--only` stages: `probe`, `vlm` (alias `analyze`), `script`, `tts`, `subtitles` (alias `subtitle`), `mix`, `render`, `qa`. Selected stages are redone proactively; unselected stages are left untouched. If a required intermediate file is missing, the command prints an actionable next step.

### 7.2 `reachreel analyze`

Runs scene detection + frame extraction + VLM analysis only; no script, no voiceover.

```bash
reachreel analyze video.mp4 --workdir output/analysis
```

Good for:

- checking whether the VLM actually understands the footage;
- reusing the analysis results and writing the script later;
- debugging `--brief` / `--scene-threshold`.

### 7.3 `reachreel render`

Re-renders from an existing work directory without rerunning VLM/TTS.

```bash
reachreel render output/ski_20261121_203000
```

Optional overrides:

```bash
reachreel render output/ski_20261121_203000 --crf 22
reachreel render output/ski_20261121_203000 --height 1080 --fps 30
reachreel render output/ski_20261121_203000 --subtitle-size 52 --subtitle-margin-v 220
reachreel render output/ski_20261121_203000 --no-burn
```

It reads `render_settings` from `manifest.json` in the work directory and keeps the previous subtitle style and output size unless you override them with options.

### 7.4 `reachreel doctor`

Checks the environment:

```bash
reachreel doctor
reachreel doctor --json
```

`--json` prints a machine-readable report: `{"ok": true, "checks": [{"id", "label", "value", "status", "required", "ok"}]}`.

Checks:

- Python version;
- `ffmpeg` / `ffprobe`;
- `ffmpeg-full` and the `subtitles` / `sidechaincompress` / `loudnorm` / `blackdetect` / `freezedetect` filters;
- `mlx_audio` / `mlx_whisper` / `misaki`;
- whether Ollama is reachable and the models are pulled;
- the BGM directory (optional).

Exit code 0 means all required items passed.

### 7.5 Stage commands

A full run is slow; when you change the script, subtitles, or BGM, redo only the corresponding stage:

```bash
# Rewrite the script only: reuse scenes.json / the VLM cache, call the local LLM again
reachreel script output/xxx

# Re-voice and recompute the narration timeline only
reachreel tts output/xxx

# Redo subtitles only: mlx-whisper by default; --asr none uses the TTS timeline
reachreel subtitle output/xxx

# Redo mixing only: reuses the BGM source from the manifest by default
reachreel mix output/xxx
reachreel mix output/xxx --no-bgm
reachreel mix output/xxx --bgm inputs/bgm/another.mp3

# Re-render only
reachreel render output/xxx

# Run quality checks only
reachreel qa output/xxx
```

Stage commands read settings from `manifest.json` (models, voice, render parameters); command-line options take precedence.

### 7.6 `reachreel qa`

Runs the quality gate against an existing work directory:

```bash
reachreel qa output/xxx
reachreel qa output/xxx --strict
reachreel qa output/xxx --json
reachreel qa output/xxx --config config.toml   # override manifest with [qa] thresholds
```

Checks:

- whether the final video exists and has video/audio streams;
- whether the final duration matches the source video and the audio/video stream durations;
- integrated loudness, true peak; `astats` full-scale sample / Flat factor clipping check;
- black frames and frozen frames (compared against the source, so static long takes no longer produce false positives);
- subtitle count, characters per line, line count, timing, overlap, and boundaries;
- actual rendered pixel margins/overflow for hard subtitles (`bbox` measurement, not OCR);
- sentence-level text/timing deviation between narration and subtitles;
- narration speed and narration end position;
- whether the BGM file exists;
- whether the hard/soft subtitle mode matches.

Thresholds can be tuned in the `[qa]` table of `config.toml`; see the [configuration reference](CONFIGURATION.md#10-qa-thresholds). `qa.json` records the thresholds used for the run.

Exit codes:

- `0`: passed, or only warnings;
- `1`: at least one `fail` item;
- `--strict`: returns `1` even when there are only warnings.

Results are written to `qa.json`. `reachreel run` also runs QA once by default; add `--no-qa` to skip it.

The hard-subtitle pixel overflow check renders the subtitle layer on a 1080p black background using the SRT timings (2–4 fps), which usually adds a few seconds. If you do not need it, set `skip_subtitle_pixels = true` in `[qa]`.

### 7.7 `reachreel licenses`

Export the BGM license list for a work directory:

```bash
reachreel licenses output/xxx
reachreel licenses output/xxx --out /tmp/licenses.md
```

The command reads `bgm_metadata` from `manifest.json` (populated from `inputs/bgm/manifest.json` during `mix`) and writes `licenses.md`. If the work directory was mixed before the catalog existed, run `reachreel mix output/xxx` again first.

### 7.8 `reachreel cache`

The shared cache lives in the platform cache directory (`~/Library/Caches/reachreel` on macOS) and lets a different work directory reuse VLM analysis and TTS audio for the same footage/text. If the legacy `~/.cache/reachreel` exists and the new directory does not, the legacy directory is used.

```bash
reachreel cache info
reachreel cache clean --dry-run
reachreel cache clean --yes
```

`cache info` prints the location, file count, and size. `cache clean` refuses to delete without `--yes`; `--dry-run` only reports what would be removed.

Disable the shared cache for one run with the global flag before the subcommand:

```bash
reachreel --no-cache run video.mp4 --workdir output/xxx
reachreel --cache-dir /tmp/rr-cache run video.mp4
```

For subtitles, `--subtitle-preset douyin|reels|shorts|landscape` (or `subtitle_preset` in config) applies platform safe margins and overrides the manual margin values.

---

## 8. Output directory

```text
output/<video-name>_<timestamp>/
├── final.mp4          # final cut
├── subtitles.srt      # subtitles (soft-subtitle compatible)
├── subtitles.ass      # hard-subtitle layout (PlayRes = output resolution)
├── script.json        # editable narration segments
├── script.md          # script preview for the review gate
├── scenes.json        # scenes + VLM analysis
├── vlm_cache.json     # VLM resume cache
├── timeline.json      # narration placement
├── narration.wav      # narration-only track, 48k
├── mix.wav            # narration + ambient + BGM
├── bgm_loop.wav       # BGM after looping
├── manifest.json      # settings, statistics, render_settings
├── review.md          # script/placement review
├── qa.json            # QA results
├── licenses.md        # BGM license list (reachreel licenses)
├── frames/            # extracted keyframes
└── tts/               # per-segment voiceover wav
```

### `script.json`

```json
{
  "segments": [
    {
      "id": 1,
      "scene_ids": [1, 2],
      "text": "第一段旁白。",
      "audio_path": "",
      "duration": 0.0,
      "start": 0.0,
      "end": 0.0,
      "tts_speed": 1.0
    }
  ]
}
```

`audio_path`, `duration`, `start`, and `end` are filled in by the program; you only need to edit `text` and `scene_ids`.

### `manifest.json`

Records:

- video information;
- the models, voice, and BGM used for this run;
- scene count, narration segment count, and character count;
- the narration timeline;
- `render_settings` (reused by the `render` command);
- `qa` (summary of the most recent QA run).

---

## 9. Tuning recipes

### 9.1 More like a travel vlog (less narration, more ambient sound)

```bash
reachreel run video.mp4 --density 0.4 --style "轻松、口语化，像和朋友边走边聊"
```

### 9.2 More like a documentary (denser, steadier narration)

```bash
reachreel run video.mp4 --density 0.75 --style "克制、客观、有画面感，不煽情"
```

### 9.3 Fast turnaround from 4K footage

```bash
reachreel run 4k.mp4 --height 1080 --fps 30 --vlm-workers 2
```

### 9.4 Vertical short video

```bash
reachreel run portrait.mov --height 1920 --fps 30 --subtitle-size 56 --subtitle-chars 14
```

### 9.5 Low-memory machines

```bash
reachreel run video.mp4 \
  --vlm-model qwen3-vl:4b-instruct \
  --llm-model qwen3.5:9b \
  --vlm-workers 1 \
  --vlm-frames 2
```

### 9.6 No BGM

```bash
reachreel run video.mp4 --no-bgm
```

### 9.7 Subtitles only, no voiceover

There is currently no dedicated "subtitles only" command. You can:

```bash
reachreel analyze video.mp4 --workdir output/analysis
```

and then write an SRT yourself based on `scenes.json`, or run a pass with `subtitle_text="asr"`. A `--subtitle-only` option will be added later.

### 9.8 The file is too large

```bash
reachreel render output/xxx --crf 22
```

The higher the CRF, the smaller the file and the lower the quality. The default is 18; 20–23 is recommended.

---

## 10. Manual review and rework

### 10.1 Change the script

Edit:

```text
output/xxx/script.json
```

Change only `text`, save, then rerun the same command (with the same `--workdir`):

```bash
reachreel run video.mp4 --workdir output/xxx
```

The program will:

- reuse `scenes.json` / the VLM cache;
- read your edited `script.json`;
- re-run TTS on the new text;
- redo the timeline, subtitles, mixing, and rendering.

You can also do it step by step instead of running the whole pipeline at once:

```bash
reachreel script   output/xxx   # regenerate the script
reachreel tts      output/xxx   # re-voice the new script
reachreel subtitle output/xxx   # redo subtitles
reachreel mix      output/xxx   # redo mixing
reachreel render   output/xxx   # re-render
reachreel qa       output/xxx   # quality checks
reachreel licenses output/xxx   # export the BGM license list
reachreel cache info            # shared cache location and size
```

### 10.2 Change subtitles only

Edit `output/xxx/subtitles.srt`, then:

```bash
reachreel render output/xxx
```

`render` uses the existing `mix.wav` and your SRT to regenerate `subtitles.ass` and burn the subtitles; it does not rerun TTS.

### 10.3 Redo everything

```bash
reachreel run video.mp4 --workdir output/xxx --force
```

This re-extracts frames, re-runs VLM, rewrites the script, re-voices, and re-renders.

### 10.4 Redo scenes/script only, keep VLM

```bash
reachreel run video.mp4 --workdir output/xxx --no-resume
```

This re-detects scenes and rewrites the script, but the VLM cache and TTS cache can still hit.

### 10.5 Change the BGM volume

Edit `config.toml`:

```toml
bgm_volume = 0.15       # lighter
ambient_volume = 0.22   # ambient sound more audible
```

Then rerun the same command (this remixes and re-renders).

---

## 11. Pre-publish checklist

Run through this before publishing:

```bash
reachreel qa output/xxx --strict
```

Then confirm by hand:

- [ ] `script.json` contains no invented place names, people, or numbers;
- [ ] the narration has no obvious meta-commentary such as “in the frame” or “in the shot”;
- [ ] subtitles stay inside the safe area and are not cropped;
- [ ] subtitle line breaks are natural, with no half sentence pushed into the next segment;
- [ ] the narration does not drown out important information in the ambient audio;
- [ ] the BGM does not compete with the voice, and the ending is not cut off abruptly;
- [ ] the final video has no excessively long black frames or silence at the start or end;
- [ ] for portrait video, check subtitle placement on a phone;
- [ ] BGM licensing has been confirmed.

---

## 12. FAQ

### 12.1 `ffmpeg has no 'subtitles' filter`

```bash
brew install ffmpeg-full
```

Or specify the path:

```bash
export FFMPEG_FULL=/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg
```

### 12.2 `No module named 'misaki'`

```bash
pip install "misaki[zh]"
```

### 12.3 Ollama connection fails

```bash
ollama serve
curl http://localhost:11434/api/tags
```

Make sure the model names match `--vlm-model` / `--llm-model`.

### 12.4 VLM is slow / out of memory

```bash
reachreel run video.mp4 --vlm-workers 1 --vlm-frames 2
```

Or switch to the 4B model.

### 12.5 Narration is too long / too short

- Too long: `--density 0.45`, or edit `script.json` and delete sentences;
- Too short: `--density 0.75`;
- Speech is too fast: lower `--max-speedup`, or shorten the script.

### 12.6 Subtitles are too large / too small / overflow the frame

```bash
# Portrait 1080x1920
reachreel run video.mp4 --subtitle-size 56 --subtitle-chars 14

# Landscape 1920x1080
reachreel run video.mp4 --subtitle-size 46 --subtitle-chars 18
```

`subtitle_size` now means the pixel height in the final video. For portrait, 52–64 with 14–16 characters per line is recommended; for landscape, 42–52 with 18 characters per line. If a portrait video also needs to leave room for a platform's bottom UI:

```toml
subtitle_margin_v = 220
subtitle_margin_left = 60
subtitle_margin_right = 60
```

QA's `subtitle_overflow_pixels` renders the actual `subtitles.ass` used by hard subtitles (falling back to the SRT for old work directories) and measures the left/right/bottom margins with pixel bounding boxes. When it reports overflow, first reduce the font size or characters per line, or adjust the safe margins.

### 12.7 Subtitles contain typos

By default subtitle text comes from the script, not ASR, so there are usually no typos. If you switch to:

```toml
subtitle_text = "asr"
```

the text will be Whisper's transcription directly, which may contain homophone errors; change it back to `script` to fix that.

### 12.8 The video is portrait, but the output is landscape

Check whether the source video has rotation metadata:

```bash
ffprobe -v error -select_streams v:0 -show_entries stream=width,height:stream_side_data=rotation -of json video.mp4
```

The program outputs the frame as auto-rotated by FFmpeg. For portrait video, use `--height 1920`.

### 12.9 No audio track

It still runs; the program skips ambient audio and mixes only narration + BGM. `doctor` / `manifest` will show `has_audio=false`.

### 12.10 The cache is not reused on rerun

Confirm that:

- you used the same `--workdir`;
- you did not add `--force`;
- the script/models/voice did not change (a change triggers a new TTS or VLM run).

### 12.11 QA reports a `true_peak` warning

This means the final video's true peak is slightly above the target ceiling (default `-1.5 dBTP`). You can:

```toml
true_peak = -2.0
```

Then rerun `mix` + `render`. For personal publishing, -1.0 dBTP is usually acceptable too, but leaving a little headroom before platform transcoding is safer.

### 12.12 QA reports `freeze_frames`

QA compares motion in the same time range of the source video: if the source is itself a static long take, it is classified as a "static long take" and skipped, so there is no more false positive. Only a genuine freeze (the source has motion, the final video does not) produces a warning/failure.

If you still want to adjust the thresholds:

```toml
[qa]
freeze_noise = 0.0005     # lower is stricter
freeze_min_seconds = 3.0  # how long a still must last before detection starts
freeze_fail_seconds = 8.0 # how long before it becomes a failure
```

---

## 13. Copyright and privacy

- Video, audio, subtitles, and models are all processed on your machine; the only network access is Hugging Face when downloading models.
- Ollama listens only on local `11434` by default; do not expose it to the public internet.
- Use CC0 or clearly commercial-use-licensed tracks for BGM; see [BGM](BGM.md) for formats, selection, and licensing notes.
- Code is Apache-2.0 licensed; third-party models and BGM follow their own licenses.
