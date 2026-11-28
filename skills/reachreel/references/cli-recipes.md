# ReachReel CLI Recipes

## 1. Environment & discovery

```bash
# Default Chinese setup check
reachreel doctor --json

# Check English Kokoro setup (requires `tts-en` extra + en_core_web_sm)
reachreel doctor --content-language en --tts kokoro --json

# Check Qwen3-TTS setup
reachreel doctor --tts qwen3 --voice Serena --json

# List available voices and style presets
reachreel voices --json
```

## 2. Chinese portrait short video (Douyin / Reels / Shorts)

```bash
reachreel run video.mp4 \
  --workdir output/clip_zh \
  --content-language zh \
  --style-preset oral \
  --brief "Weekend indoor ski session in Shanghai" \
  --subtitle-preset douyin \
  --json
```

## 3. English landscape narration

```bash
reachreel run video.mp4 \
  --workdir output/clip_en \
  --content-language en \
  --tts kokoro \
  --voice af_heart \
  --subtitle-preset landscape \
  --json
```

## 4. Qwen3-TTS CustomVoice with delivery instruction

```bash
reachreel run video.mp4 \
  --workdir output/clip_qwen3 \
  --tts qwen3 \
  --voice Serena \
  --tts-instruct "Warm, natural, relaxed storytelling tone" \
  --json
```

## 5. Qwen3-TTS voice cloning

```bash
reachreel run video.mp4 \
  --workdir output/clip_clone \
  --tts qwen3 \
  --tts-ref-audio /path/to/ref_5s.wav \
  --tts-ref-text "Exact transcript of the reference audio clip." \
  --json
```

## 6. Two-step script review & Agent editing

```bash
# Step 1: Stop after script generation (writes scenes.json, script.json, script.md)
reachreel run video.mp4 \
  --workdir output/clip_edit \
  --brief "Context about the location and people" \
  --review-script \
  --json

# Step 2: Inspect output/clip_edit/script.md and edit output/clip_edit/script.json

# Step 3: Check what is pending/stale
reachreel status output/clip_edit --json

# Step 4: Resume all remaining stages (TTS through QA) in one command
reachreel resume output/clip_edit --json
```

## 7. Targeted stage reruns on an existing workdir

```bash
# Change voice only -> rebuild TTS, subtitles, mix, render, QA
reachreel tts output/clip_zh --tts qwen3 --voice Vivian
reachreel subtitle output/clip_zh
reachreel mix output/clip_zh
reachreel render output/clip_zh
reachreel qa output/clip_zh --json

# Change subtitle layout only -> rebuild subtitles + render
reachreel subtitle output/clip_zh --subtitle-preset shorts --subtitle-size 44
reachreel render output/clip_zh --subtitle-preset shorts --subtitle-size 44
reachreel qa output/clip_zh --json

# Change BGM only -> rebuild mix + render
reachreel mix output/clip_zh --bgm /path/to/track.mp3
reachreel render output/clip_zh
reachreel qa output/clip_zh --json

# Re-render with soft subtitles instead of burned-in subtitles
reachreel render output/clip_zh --no-burn
```

## 8. Export for external NLE & license check

```bash
# Export H.264 MP4 editing bundle (default: <workdir>/edit)
reachreel export output/clip_zh --format mp4

# Export ProRes 422 MOV editing bundle to a custom folder
reachreel export output/clip_zh --format mov --out output/clip_zh_prores

# Generate BGM attribution markdown
reachreel licenses output/clip_zh
```

## 9. Shared cache management

```bash
reachreel cache info
reachreel cache clean --dry-run
reachreel cache clean --yes
```
