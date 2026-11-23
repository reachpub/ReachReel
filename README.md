**English** | [简体中文](README.zh-CN.md)

# ReachReel

**Turn raw footage into a narrated, subtitled, broadcast-loudness cut — 100% locally on your Mac.**

Drop in a raw video clip, optionally add a few words of context (`--brief`) and background music, and ReachReel watches the scenes, writes a voiceover script, records natural narration, aligns subtitles, ducks the music around speech, and runs a pre-publish quality check — with **no cloud APIs, no subscriptions, and no footage leaving your machine**.

> **Docs**: [Online documentation](https://reachpub.github.io/ReachReel/) · [中文文档](https://reachpub.github.io/ReachReel/zh-CN/)  
> **Validated end to end**: 82s indoor-ski vertical reel (`1080x1920`) and 93s Niagara Falls landscape cut (`1080p`, both Chinese and English), passing all 20 QA checks.

---

## Who ReachReel is for

- **Video creators & vloggers**: Go from raw camera clips to a ready-to-post narrated reel (Douyin, Reels, Shorts, YouTube, Bilibili) without staring at a blank page or recording scratch tracks.
- **Video editors**: Generate a first narrated rough cut, review and edit `script.md` / `script.json` before voiceover (`--review-script`), and re-render only the subtitles, mix, or final cut without re-analyzing the video.
- **Developers**: A clean, resumable, local-first Python pipeline (`probe -> vlm -> script -> tts -> subtitles -> mix -> render -> qa`) built on FFmpeg, Ollama, Kokoro-82M (MLX), and `mlx-whisper`.

---

## What you get

### Story & Script
- **Scene-aware writing**: Understands what happens in each shot (location, subjects, actions, mood) and writes chronological narration instead of generic filler.
- **Your voice & context**: Guide the tone with `--style` and anchor facts/place names with `--brief`, or control pacing with `--density`.
- **Edit before voiceover**: Run with `--review-script` to pause after writing `script.md`, tweak the lines, and continue straight to voiceover.

### Voiceover (Chinese & English)
- **Bilingual narration**: Supports Mandarin Chinese (`zh-CN`), American English (`en-US`), and British English (`en-GB`) via Kokoro-82M on Apple Silicon — offline after first model download.
- **Independent UI & content languages**: Run the CLI in English or Chinese (`--lang`) while producing Chinese or English videos (`--content-language zh|en`).

### Subtitles & Framing
- **Script-accurate subtitles**: Uses `mlx-whisper` for speech timing while keeping text from your script, avoiding homophone typos.
- **Platform safe areas**: Built-in `--subtitle-preset` layouts for `douyin`, `reels`, `shorts`, and `landscape`, plus clean white-with-outline defaults (`outline` / `box` / `none`) and soft-subtitle (`--no-burn`) output.
- **Portrait & rotation ready**: Properly handles rotated iPhone footage and portrait `1080x1920` exports.

### Audio Mix & Pre-publish QA
- **Automatic ducking & loudness**: Gently lowers BGM and ambient sound whenever narration speaks, normalizes loudness to `-14 LUFS`, fades out music at the end, and exports `licenses.md` for BGM attribution.
- **20-point QA gate**: Automatically checks audio/video sync, loudness, clipping, black/frozen frames, subtitle pixel overflow, and narration alignment before you publish.
- **Fast targeted reruns**: Changing one line of narration or adjusting subtitle size only rebuilds the affected stage (`script`, `tts`, `subtitle`, `mix`, `render`, `qa`).

---

## Quick start

### 1. Environment

- Apple Silicon Mac (M1 or newer)
- Python 3.11–3.12
- Homebrew
- 32 GB RAM recommended; 16 GB works with `vlm_workers = 1`

```bash
brew install ffmpeg ffmpeg-full

python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .

# Optional: English voiceover (torch-free G2P stack)
pip install -e '.[tts-en]'
python -m spacy download en_core_web_sm
```

> Regular Homebrew `ffmpeg` does not include `libass` for burned-in subtitles.
> ReachReel automatically prefers `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg`.

### 2. Local models

```bash
ollama pull qwen3-vl:8b-instruct
ollama pull qwen3:30b-instruct     # or qwen3.5:9b / qwen3:8b
```

### 3. Check the environment

```bash
reachreel doctor
```

### 4. Add BGM (optional)

Put CC0 / royalty-free music in `inputs/bgm/`:

```text
inputs/bgm/
  morning-light.mp3
  mountain-air.wav
```

### 5. Run

```bash
# Chinese narration + subtitles (default)
reachreel run /path/to/video.mp4 \
  --brief "Filmed in Yubeng, Yunnan; leaving the village at dawn and hiking to the ice lake" \
  --style "Quiet and restrained, like talking to yourself on a solo hike"

# English narration + voiceover + subtitles (American English default; add --tts-language en-GB for British)
reachreel run /path/to/video.mp4 \
  --content-language en --height 1080 --fps 30 \
  --brief "Niagara Falls boat tour and misty viewpoints"
```

Output goes to `output/<video-name>_<timestamp>/`:

```text
final.mp4        # publish-ready video
subtitles.srt    # subtitles (soft-subtitle compatible)
subtitles.ass    # hard-subtitle layout (real output pixel coordinates)
script.json      # editable narration segments
scenes.json      # detected scenes + visual analysis
timeline.json    # narration placement
narration.wav    # narration-only track
mix.wav          # narration + ambient + BGM
manifest.json    # settings, quality flags, and statistics
review.md        # script & placement overview
qa.json          # 20-point QA results
licenses.md      # BGM license list
```

Review, QA, and targeted reruns:

```bash
reachreel qa output/<video-name>_<timestamp>
reachreel licenses output/<video-name>_<timestamp>

# Inspect the plan, then rerun VLM + script only
reachreel run video.mp4 --workdir output/<video-name>_<timestamp> --only vlm,script --dry-run
reachreel run video.mp4 --workdir output/<video-name>_<timestamp> --only vlm,script
```

---

## Documentation

| Document | Content |
|---|---|
| [Documentation site](https://reachpub.github.io/ReachReel/) | Searchable bilingual web docs (English default, `/zh-CN/` Chinese) |
| [Documentation index](docs/README.md) | Repository navigation and language policy |
| [User guide](docs/en/USER_GUIDE.md) | Install, media prep, commands, output, tuning, reruns, FAQ |
| [Configuration](docs/en/CONFIGURATION.md) | Every `config.toml` key, defaults, environment variables, precedence |
| [BGM](docs/en/BGM.md) | Formats, selection, local-only policy, licensing notes |
| [Languages & voices](docs/en/LANGUAGES.md) | BCP 47 naming, language axes, and supported TTS voices |
| [Development](docs/en/DEVELOPMENT.md) | Architecture, modules, data formats, algorithms, extension points, testing |
| [Desktop architecture](docs/en/DESKTOP_ARCHITECTURE.md) | Local service contract, job model, event protocol, engines, packaging |
| [Localization](docs/en/LOCALIZATION.md) | English code, UI i18n, bilingual documentation, content language |
| [Glossary](docs/GLOSSARY.md) | Chinese/English terminology |
| [Roadmap](docs/en/ROADMAP.md) | Priorities and acceptance criteria |
| [Changelog](CHANGELOG.md) | Version history |

---

## For developers

```text
video -> FFmpeg scene detection -> Ollama qwen3-vl vision
      -> local LLM narration -> Kokoro-82M (MLX) voiceover
      -> mlx-whisper speech windows -> BGM ducking + mix -> FFmpeg render -> QA
```

```text
reachreel/
├── src/reachreel/
│   ├── cli/            # command-line entry points and subcommands
│   ├── core/           # config, subprocess/JSON utilities, shared cache
│   ├── domain/         # pure models and narration timeline placement
│   ├── engines/        # VLM / TTS engine adapters
│   ├── generation/     # narration script generation
│   ├── i18n/           # UI language negotiation and catalog loader
│   ├── locales/        # en.toml, zh-CN.toml UI copy
│   ├── media/          # scenes, subtitles, mixing, rendering
│   ├── pipeline/       # orchestration, caching, QA
│   └── prompts/        # language-specific prompt packs (zh/, en/)
├── inputs/             # local inputs (ignored by Git): bgm/, fonts/, models/
├── docs/               # bilingual docs (docs/en/, docs/zh-CN/)
├── tests/              # unit and integration tests
├── config.example.toml
├── mkdocs.yml
└── pyproject.toml
```

---

## Current limitations

- **Apple Silicon macOS only**; TTS and Whisper rely on MLX.
- VLM inspects keyframes per scene rather than the full video stream, so very fast in-scene action may be missed.
- Narration is written by a local LLM and may occasionally over-interpret footage; review `script.json` before publishing.
- Subtitles are phrase/line-timed from Whisper speech boundaries, not word-level karaoke.
- BGM is chosen from `inputs/bgm/` without automatic mood matching.
- Exports H.264/AAC only; no HDR, 10-bit, hardware encoding, or smart vertical reframing yet.
- Desktop UI, batch folder watching, and direct publishing integrations (CapCut/Bilibili/YouTube) are on the [roadmap](docs/en/ROADMAP.md).

---

## License

Code is Apache-2.0 licensed. Check the license of every BGM track you use; see [BGM](docs/en/BGM.md) for formats, selection, and licensing notes.
