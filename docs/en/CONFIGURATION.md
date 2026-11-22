**English** | [简体中文](../zh-CN/CONFIGURATION.md)

# Configuration Reference

This document lists every `config.toml` field, default value, environment variable, and precedence rule. For the usage workflow see [USER_GUIDE.md](USER_GUIDE.md).

---

## 1. How configuration is loaded

```bash
cp config.example.toml config.toml
reachreel run video.mp4 --config config.toml
```

When `--config` is not specified, built-in defaults are used. A `config.toml` in the project is not read automatically; you must pass it explicitly.

### Precedence

From lowest to highest:

1. Built-in defaults in code;
2. `config.toml`;
3. Environment variables (`REACHREEL_*`);
4. Command-line options.

### Path resolution

- In `config.toml`, relative `bgm_dir`, `bgm_file`, and `work_root` values resolve relative to the **directory containing the config file**.
- Paths passed on the command line resolve relative to the **current working directory**.
- `~` expands to your home directory.

---

## 2. Minimal configuration example

```toml
ui_language = "en"
content_language = "zh"
ollama_url = "http://localhost:11434"
vlm_model = "qwen3-vl:8b-instruct"
llm_model = "qwen3:30b-instruct"
voice = "zf_xiaoxiao"
bgm_dir = "inputs/bgm"
output_height = 1080
output_fps = 30
```

### Language settings

| Field | Default | Description |
|---|---|---|
| `ui_language` | `en` | CLI / UI language: `en` or `zh-CN`; `--lang` wins |
| `content_language` | `zh` | Content language for narration and subtitles: `zh` or `en`; selects `prompts/<lang>/` and CLI `--content-language` |

`ui_language` and `content_language` are completely independent: the UI can be Chinese while the output is English, or vice versa.

---

## 3. Models and services

| Field | Default | Description |
|---|---|---|
| `ollama_url` | `http://localhost:11434` | Ollama URL |
| `vlm_model` | `qwen3-vl:8b-instruct` | Visual understanding model; use `qwen3-vl:4b-instruct` on low memory |
| `llm_model` | `qwen3:30b-instruct` | Script-writing model; `qwen3.5:9b` and `qwen3:8b` are alternatives |
| `vlm_workers` | `2` | VLM concurrency; higher is faster but uses more memory |
| `vlm_frames_per_scene` | `3` | Number of keyframes extracted per scene |
| `vlm_max_width` | `768` | Long-edge pixel size of extracted frames; smaller is faster and uses less memory |
| `llm_temperature` | `0.6` | Script-writing temperature; lower is steadier, higher is more varied |

---

## 4. Scene detection

| Field | Default | Description |
|---|---|---|
| `scene_threshold` | `0.32` | FFmpeg scene score threshold; lower is more sensitive and cuts into smaller pieces |
| `min_scene_seconds` | `1.2` | Scenes shorter than this are candidates for merging |
| `max_scene_seconds` | `8.0` | Long takes are split into chunks no longer than this |
| `max_scenes` | `160` | Maximum number of scenes; excess scenes are merged evenly |

Tuning suggestions:

- Continuous single take / slow pan: lower `scene_threshold` to `0.22–0.26`, or increase `max_scene_seconds`.
- Fast cuts / action footage: raise `scene_threshold` to `0.38–0.45` to avoid fragmented scenes.
- VLM too slow: increase `max_scene_seconds` (fewer scenes) and reduce `vlm_frames_per_scene`.

---

## 5. Narration script

| Field | Default | Description |
|---|---|---|
| `style` | Empty = `prompts/<lang>/style.txt` | Narration style hint; empty uses the language default |
| `brief` | Empty | Background such as shooting location, people, and activities; strongly recommended |
| `speech_chars_per_second` | `3.9` | Speech-rate estimate for the target character count; use about `14.5` for English |
| `narration_density` | `0.62` | Narration density 0.2–1.0; 0.4 is more vlog-like, 0.75 is more documentary-like |
| `min_gap_seconds` | `0.28` | Minimum gap between narration segments |
| `start_offset` | `0.7` | Earliest start time for the first narration segment |
| `tail_padding` | `0.8` | Time reserved at the end of the video |
| `max_speedup` | `1.32` | Maximum atempo speedup allowed when total narration runs long |

### How the target character count is calculated

```text
target_chars ≈ duration × speech_chars_per_second × narration_density
```

For example, 90 seconds at 3.9 characters/second and 0.5 density → about 175 characters. The LLM is asked to approach this value; coming in low triggers expansion, and coming in high triggers compression.

---

## 6. TTS

| Field | Default | Description |
|---|---|---|
| `tts_engine` | `kokoro` | `kokoro` or `say` (for macOS debugging) |
| `kokoro_model` | `mlx-community/Kokoro-82M-4bit` | Kokoro MLX model |
| `voice` | `zf_xiaoxiao` | Chinese voice |
| `tts_speed` | `1.0` | Base speech rate; 1.0 is normal, >1 is faster |
| `say_voice` | `Tingting` | Voice for the `say` engine |

Kokoro Chinese voices:

| Voice | Gender / style |
|---|---|
| `zf_xiaoxiao` | Female, default, clear |
| `zf_xiaobei` | Female, lighter |
| `zf_xiaoni` | Female, more lively |
| `zf_xiaoyi` | Female, gentler |
| `zm_yunjian` | Male, steadier |
| `zm_yunxi` | Male, younger |
| `zm_yunxia` | Male, milder |
| `zm_yunyang` | Male, brighter |

> `say` is only for quickly validating the pipeline when Kokoro is not installed properly; it is not recommended as a production voice.

---

## 7. Subtitles

| Field | Default | Description |
|---|---|---|
| `whisper_model` | `mlx-community/whisper-large-v3-turbo` | mlx-whisper model |
| `asr_language` | `zh` | Recognition language |
| `subtitle_text` | `script` | `script`: script text + Whisper timestamps; `asr`: use Whisper text directly |
| `subtitle_max_chars` | `18` | Maximum characters per line |
| `subtitle_max_lines` | `2` | Maximum lines per subtitle cue |
| `subtitle_max_seconds` | `6.0` | Maximum display time for a single subtitle cue |
| `subtitle_font` | `PingFang SC` | Font |
| `subtitle_size` | `48` | Subtitle font size (output pixels) |
| `subtitle_margin_v` | `36` | Bottom safe margin (output pixels) |
| `subtitle_margin_left` | `50` | Left safe margin (output pixels) |
| `subtitle_margin_right` | `50` | Right safe margin (output pixels) |

Hard subtitles generate `subtitles.ass`, where `PlayResX/Y` equals the output resolution. The font size and margins above are therefore real pixels in the final video and are no longer affected by libass's default 384×288 scaling. Soft subtitles still use `subtitles.srt`.

Portrait recommendations:

```toml
subtitle_size = 56
subtitle_max_chars = 14
subtitle_margin_v = 220
subtitle_margin_left = 60
subtitle_margin_right = 60
```

Landscape recommendations:

```toml
subtitle_size = 46
subtitle_max_chars = 18
subtitle_margin_v = 60
```

Older work directories contain `subtitle_size` values (for example 16/18) written in the old relative units; after upgrading, pass explicit pixel values and re-render, for example:

```bash
reachreel render output/xxx --subtitle-size 52
```

---

## 8. BGM and mixing

| Field | Default | Description |
|---|---|---|
| `bgm_dir` | `inputs/bgm` | BGM directory (local, Git-ignored, created automatically) |
| `bgm_file` | Empty | Specific BGM; when empty, a track is chosen at random from the directory |
| `bgm_volume` | `0.20` | Base BGM volume (linear) |
| `bgm_duck_db` | `-16.0` | BGM ducking depth; a larger absolute value ducks harder |
| `ambient_volume` | `0.16` | Base ambient audio volume |
| `ambient_duck_db` | `-10.0` | Ambient ducking depth |
| `target_lufs` | `-14.0` | Target loudness for the final mix |
| `true_peak` | `-1.5` | True-peak ceiling in dBTP |

`bgm_duck_db` / `ambient_duck_db` are converted to the `sidechaincompress` ratio (roughly `abs(db)/2`, clamped to 2–20). This is not an exact dB attenuation but an approximate "how hard to duck" control.

If the BGM is too dominant:

```toml
bgm_volume = 0.14
bgm_duck_db = -20.0
```

If the ambient sound is too weak:

```toml
ambient_volume = 0.24
ambient_duck_db = -8.0
```

---

## 9. Rendering

| Field | Default | Description |
|---|---|---|
| `output_height` | `0` | Output height; 0 = keep the source height |
| `output_fps` | `0` | Output frame rate; 0 = keep the source frame rate |
| `crf` | `18` | x264 CRF; higher means a smaller file |
| `preset` | `medium` | x264 preset: `veryfast` / `fast` / `medium` / `slow` |
| `audio_bitrate` | `192k` | AAC bitrate |
| `burn_subtitles` | `true` | Whether to burn hard subtitles |
| `work_root` | `output` | Default work directory root |

Common settings:

```toml
# 4K landscape
output_height = 1080
output_fps = 30
crf = 20

# Portrait
output_height = 1920
output_fps = 30
subtitle_size = 56
subtitle_max_chars = 14
subtitle_margin_v = 220
```

---

## 10. QA thresholds

QA thresholds can be overridden in the config file with the `[qa]` table. Both `reachreel run` and `reachreel qa --config` read it; `qa.json` records the thresholds actually used for that run.

```toml
[qa]
freeze_min_seconds = 3.0
freeze_fail_seconds = 8.0
clip_peak_db = -0.1
skip_subtitle_pixels = false
```

| Field | Default | Description |
|---|---|---|
| `duration_warn_seconds` / `duration_fail_seconds` | `0.5` / `2.0` | Duration difference between the final video and the source video |
| `av_sync_warn_seconds` / `av_sync_fail_seconds` | `0.2` / `0.8` | Duration difference between the audio and video streams |
| `lufs_warn_delta` / `lufs_fail_delta` | `2.0` / `4.0` | Deviation of integrated loudness from the target |
| `true_peak_tolerance_db` / `true_peak_ceiling_db` | `0.6` / `0.0` | True-peak tolerance; exceeding the ceiling fails immediately |
| `black_min_seconds` | `0.5` | Minimum duration for black-frame detection |
| `black_pixel_threshold` | `0.10` | Pixel threshold for `blackdetect` |
| `black_longest_fail_seconds` / `black_total_fail_seconds` | `3.0` / `5.0` | Longest / cumulative black-frame failure thresholds |
| `freeze_noise` | `0.0005` | `freezedetect` noise tolerance; lower is stricter |
| `freeze_min_seconds` | `3.0` | Minimum duration for frozen-frame detection |
| `freeze_fail_seconds` | `8.0` | Durations above this fail |
| `freeze_motion_threshold` | `0.02` | Motion threshold separating “frozen” from “static long take” |
| `freeze_sample_seconds` | `20.0` | Maximum seconds sampled per freeze segment |
| `clip_peak_db` | `-0.1` | Peak threshold at which `astats` considers a sample full-scale |
| `clip_sample_ratio` | `0.0005` | Full-scale sample ratio that counts as clipping |
| `clip_flat_factor` | `0.5` | `astats` Flat factor warning threshold |
| `subtitle_char_warn_extra` / `subtitle_char_fail_extra` | `4` / `10` | How many characters per line may exceed `subtitle_max_chars` |
| `subtitle_safe_tolerance_px` / `subtitle_overflow_tolerance_px` | `6` / `20` | Measured subtitle margin tolerance / failure threshold (pixels) |
| `narration_subtitle_tolerance_seconds` | `0.9` | Per-sentence timing deviation tolerance between narration and subtitles |
| `narration_subtitle_fail_ratio` | `0.5` | Fraction of deviating sentences above which QA fails |
| `skip_subtitle_pixels` | `false` | Disable the actual rendered pixel overflow check |

---

## 11. Environment variables

| Environment variable | Corresponding field |
|---|---|
| `REACHREEL_LANG` | `ui_language` |
| `REACHREEL_CONTENT_LANGUAGE` | `content_language` |
| `REACHREEL_OLLAMA_URL` | `ollama_url` |
| `REACHREEL_VLM_MODEL` | `vlm_model` |
| `REACHREEL_LLM_MODEL` | `llm_model` |
| `REACHREEL_VOICE` | `voice` |
| `REACHREEL_BGM_DIR` | `bgm_dir` |
| `REACHREEL_WHISPER_MODEL` | `whisper_model` |
| `REACHREEL_TTS_ENGINE` | `tts_engine` |
| `FFMPEG_FULL` | Path to an ffmpeg build with libass |

Example:

```bash
export REACHREEL_OLLAMA_URL=http://127.0.0.1:11434
export REACHREEL_VLM_MODEL=qwen3-vl:4b-instruct
reachreel run video.mp4
```

---

## 12. Full example

```toml
# Language
ui_language = "en"
content_language = "zh"

# Models
ollama_url = "http://localhost:11434"
vlm_model = "qwen3-vl:8b-instruct"
llm_model = "qwen3:30b-instruct"
vlm_workers = 2
vlm_frames_per_scene = 3
vlm_max_width = 768
llm_temperature = 0.6

# Scenes
scene_threshold = 0.32
min_scene_seconds = 1.2
max_scene_seconds = 8.0
max_scenes = 160

# Script
style = ""  # empty = prompts/<lang>/style.txt
brief = "拍摄于云南雨崩，清晨从雨崩村出发，途经冰湖。"
speech_chars_per_second = 3.9  # about 14.5 for English
narration_density = 0.55
min_gap_seconds = 0.28
start_offset = 0.7
tail_padding = 0.8
max_speedup = 1.32

# TTS
tts_engine = "kokoro"
kokoro_model = "mlx-community/Kokoro-82M-4bit"
voice = "zf_xiaoxiao"
tts_speed = 1.0
say_voice = "Tingting"

# Subtitles
whisper_model = "mlx-community/whisper-large-v3-turbo"
asr_language = "zh"
subtitle_text = "script"
subtitle_max_chars = 18
subtitle_max_lines = 2
subtitle_max_seconds = 6.0
subtitle_font = "PingFang SC"
subtitle_size = 48
subtitle_margin_v = 60
subtitle_margin_left = 50
subtitle_margin_right = 50

# BGM / mixing
bgm_dir = "inputs/bgm"
bgm_file = ""
bgm_volume = 0.20
bgm_duck_db = -16.0
ambient_volume = 0.16
ambient_duck_db = -10.0
target_lufs = -14.0
true_peak = -1.5

# Rendering
output_height = 1080
output_fps = 30
crf = 20
preset = "medium"
audio_bitrate = "192k"
burn_subtitles = true
work_root = "output"

# QA (optional overrides)
[qa]
freeze_min_seconds = 3.0
freeze_fail_seconds = 8.0
clip_peak_db = -0.1
```
