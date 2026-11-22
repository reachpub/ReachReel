**English** | [简体中文](README.zh-CN.md)

# ReachReel

**Turn a raw video into a publish-ready narrated cut, fully on your machine.**

```text
video -> FFmpeg scene detection -> Ollama qwen3-vl vision
      -> local LLM narration -> Kokoro-82M (MLX) voiceover
      -> mlx-whisper speech windows -> BGM ducking + mix -> FFmpeg render
```

No cloud API and no per-minute billing. The first run downloads the Kokoro and Whisper models; after that the pipeline can run offline.

> Status: v0.2.0. Validated on two real clips: an 82s indoor-ski vertical video and a 93s Niagara Falls landscape video, both rendered at 1080p with hard subtitles, BGM ducking, and QA 20/20.

---

## Features

- **Local vision**: Ollama + `qwen3-vl` analyzes location, subjects, actions, details, and mood per scene.
- **Local narration**: Ollama + `qwen3` writes a time-ordered script with style, brief, and density controls.
- **Local voiceover**: Kokoro-82M / MLX with eight Chinese voices, rendered as 48 kHz mono narration.
- **Local subtitles**: `mlx-whisper` finds speech boundaries; subtitle text comes from the script to avoid homophone errors.
- **BGM ducking**: FFmpeg `sidechaincompress` lowers music and ambient audio whenever narration is present.
- **Hard subtitles**: `ffmpeg-full` + libass; ASS `PlayRes` matches the output resolution, so size and margins are real pixels.
- **Stage commands**: `script` / `tts` / `subtitle` / `mix` / `render` / `qa`; `run --only vlm,script` and `run --dry-run` for precise reruns.
- **QA gate**: stream/duration checks, loudness and true peak, `astats` clipping, black/frozen frames (with source-motion comparison), subtitle pixel overflow, and narration/subtitle alignment.
- **Resumable**: scene analysis, VLM, script, and TTS caches; changing one line only rebuilds the affected work.
- **Fail-soft stages**: VLM/TTS/Whisper retry or fall back; degradations land in `manifest.json` as `quality_flags` instead of aborting the run.
- **License tracking**: optional `inputs/bgm/manifest.json` metadata; `reachreel licenses <workdir>` exports `licenses.md`.
- **Cross-workdir cache**: VLM analysis and TTS audio are reused through the platform cache dir (`~/Library/Caches/reachreel` on macOS); disable with `--no-cache` or clean with `reachreel cache clean --yes`.
- **Subtitle presets**: `--subtitle-preset douyin|reels|shorts|landscape` applies platform safe areas; line length and line count are hard limits.
- **Script review gate**: `run --review-script` stops after writing `script.md`, so you can edit the script before paying for TTS.
- **Two-pass loudness**: the mix is measured first and linear-normalized to the target LUFS; the BGM fades out over the final seconds.
- **Bilingual CLI**: `--lang en|zh-CN` switches the UI; `--content-language zh|en` independently selects the narration/subtitle language and prompt pack.

---

## Quick start

### 1. Environment

- Apple Silicon Mac (M1 or newer)
- Python 3.10–3.12
- Homebrew
- 32 GB RAM recommended; 16 GB works with `vlm_workers = 1`

```bash
brew install ffmpeg ffmpeg-full

python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
```

> The regular Homebrew `ffmpeg` has no libass and cannot burn hard subtitles.
> ReachReel prefers `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg` automatically.

### 2. Local models

```bash
ollama pull qwen3-vl:8b-instruct
ollama pull qwen3:30b-instruct     # or qwen3.5:9b / qwen3:8b
```

### 3. Check the environment

```bash
reachreel doctor
```

### 4. Add BGM

Put CC0 / royalty-free music in `inputs/bgm/`:

```text
inputs/bgm/
  morning-light.mp3
  mountain-air.wav
```

### 5. Run

```bash
reachreel run /path/to/video.mp4 \
  --brief "Filmed in Yubeng, Yunnan; leaving the village at dawn and hiking to the ice lake" \
  --style "Quiet and restrained, like talking to yourself on a solo hike"
```

Output goes to `output/<video-name>_<timestamp>/`:

```text
final.mp4        # rendered video
subtitles.srt    # subtitles (soft-subtitle compatible)
subtitles.ass    # hard-subtitle layout (PlayRes = output resolution)
script.json      # editable narration segments
scenes.json      # scenes + VLM analysis
timeline.json    # narration placement
narration.wav    # narration-only track
mix.wav          # narration + ambient + BGM
manifest.json    # settings and statistics
review.md        # script/placement review
qa.json          # QA results
licenses.md      # BGM license list
```

QA and targeted reruns:

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
| [Documentation index](docs/README.md) | Navigation and language policy |
| [User guide](docs/en/USER_GUIDE.md) | Install, media prep, commands, output, tuning, reruns, FAQ |
| [Configuration](docs/en/CONFIGURATION.md) | Every `config.toml` key, defaults, environment variables, precedence |
| [BGM](docs/en/BGM.md) | Formats, selection, local-only policy, licensing notes |
| [Development](docs/en/DEVELOPMENT.md) | Architecture, modules, data formats, algorithms, extension points, testing |
| [Desktop and multi-frontend architecture](docs/en/DESKTOP_ARCHITECTURE.md) | Local service contract, job model, event protocol, engines, packaging |
| [Localization](docs/en/LOCALIZATION.md) | English code, UI i18n, bilingual documentation, content language |
| [Glossary](docs/GLOSSARY.md) | Chinese/English terminology |
| [Roadmap](docs/en/ROADMAP.md) | Priorities and acceptance criteria |
| [Changelog](CHANGELOG.md) | Version history |

> Documentation is bilingual: English docs live in `docs/en/`, Chinese docs in `docs/zh-CN/`. Commands, paths, and config keys are never translated.

---

## Project layout

```text
reachreel/
├── src/reachreel/
│   ├── cli/            # command-line entry points and subcommands
│   ├── core/           # config, subprocess/JSON utilities
│   ├── domain/         # domain models and narration timeline
│   ├── engines/        # VLM / TTS engine adapters
│   ├── generation/     # narration script generation
│   ├── media/          # scenes, subtitles, mixing, rendering
│   └── pipeline/       # orchestration, caching, QA
├── inputs/             # local inputs (ignored by Git)
│   ├── bgm/            # background music
│   ├── fonts/          # reserved for custom subtitle fonts
│   └── models/         # reserved for local model files
├── docs/               # documentation
├── tests/              # unit and integration tests
├── config.example.toml
└── pyproject.toml
```

---

## Current limitations

- **Apple Silicon macOS only**; TTS depends on MLX.
- VLM looks at keyframes per scene, **not the full video**; fast in-scene action may be missed.
- Narration is generated by a local LLM and **may over-interpret the footage**; review `script.json` before publishing.
- Subtitle timing comes from Whisper speech boundaries + script text, **not word-level karaoke timing**.
- BGM is selected randomly, **not matched to mood**.
- H.264/AAC only; no HDR, 10-bit, hardware encoding, or smart vertical reframing.
- QA is heuristic: subtitle overflow uses pixel bounding boxes rather than OCR; narration/subtitle alignment is sentence-level.
- No GUI or batch queue yet; the local-service + browser panel design is documented but not implemented.
- Publishing integrations (CapCut/Bilibili/YouTube) are not implemented.

See the [roadmap](docs/en/ROADMAP.md) for the full list.

---

## License

Code is Apache-2.0 licensed. Check the license of every BGM track you use; see [BGM](docs/en/BGM.md) for formats, selection, and licensing notes.
