[English](../en/CONFIGURATION.md) | **简体中文**

# 配置参考

本文列出 `config.toml` 的全部字段、默认值、环境变量和优先级。使用流程见 [USER_GUIDE.md](USER_GUIDE.md)。

---

## 1. 加载方式

```bash
cp config.example.toml config.toml
reachreel run video.mp4 --config config.toml
```

不指定 `--config` 时使用内置默认值；不会自动读取项目里的 `config.toml`，需要显式传入。

### 优先级

从低到高：

1. 代码内置默认值；
2. `config.toml`；
3. 环境变量（`REACHREEL_*`）；
4. 命令行参数。

### 路径解析

- `config.toml` 里的 `bgm_dir`、`bgm_file`、`work_root` 如果是相对路径，会相对于 **config 文件所在目录** 解析。
- 命令行传入的路径相对于 **当前工作目录**。
- `~` 会展开为用户目录。

---

## 2. 最小配置示例

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

### 语言设置

| 字段 | 默认 | 说明 |
|---|---|---|
| `ui_language` | `en` | CLI / 界面语言：`en` 或 `zh-CN`；`--lang` 优先 |
| `content_language` | `zh` | 成片语言：旁白与字幕；`zh` 或 `en`；决定 `prompts/<lang>/` 与 CLI `--content-language` |

`ui_language` 与 `content_language` 完全独立：界面可以用中文，成片用英文，反之亦然。

---

## 3. 模型与服务

| 字段 | 默认 | 说明 |
|---|---|---|
| `ollama_url` | `http://localhost:11434` | Ollama 地址 |
| `vlm_model` | `qwen3-vl:8b-instruct` | 画面理解模型；低内存可换 `qwen3-vl:4b-instruct` |
| `llm_model` | `qwen3:30b-instruct` | 写稿模型；可换 `qwen3.5:9b`、`qwen3:8b` |
| `vlm_workers` | `2` | VLM 并发数；越大越快，但更吃内存 |
| `vlm_frames_per_scene` | `3` | 每个镜头抽几张关键帧 |
| `vlm_max_width` | `768` | 抽帧长边像素；越小越快、越省内存 |
| `vlm_frame_dedup` | `4.0` | 抽帧后与上一张保留帧的 16×16 灰度差异低于该值就丢弃，可减少送进 VLM 的重复帧；`0` 表示关闭、全部发送 |
| `llm_temperature` | `0.6` | 写稿温度；越低越稳，越高越发散 |

---

## 4. 镜头检测

| 字段 | 默认 | 说明 |
|---|---|---|
| `scene_threshold` | `0.32` | FFmpeg scene 分数阈值；越低越敏感，切得越碎 |
| `min_scene_seconds` | `1.2` | 短于此值的场景会尝试合并 |
| `max_scene_seconds` | `8.0` | 长镜头会切成不超过这个长度的块 |
| `max_scenes` | `160` | 场景数上限；超过会均匀合并 |

调参建议：

- 一镜到底 / 慢摇：降低 `scene_threshold` 到 `0.22–0.26`，或增大 `max_scene_seconds`。
- 快切 / 运动镜头：提高 `scene_threshold` 到 `0.38–0.45`，避免碎镜头。
- VLM 太慢：增大 `max_scene_seconds`（减少场景数）、减少 `vlm_frames_per_scene`。`vlm_frame_dedup` 已能把静态长镜头压缩成 1 张；慢摇镜头如果仍然送出近似帧，可以调高它。

---

## 5. 解说词

| 字段 | 默认 | 说明 |
|---|---|---|
| `style` | 空 = `prompts/<lang>/style.txt` | 旁白风格提示；留空使用语言默认风格 |
| `brief` | 空 | 拍摄地、人物、活动等背景；强烈建议填写 |
| `speech_chars_per_second` | `3.9` | 口播速度估算，用于算目标字数；英文建议约 `14.5` |
| `narration_density` | `0.62` | 旁白密度 0.2–1.0；0.4 更 Vlog，0.75 更像纪录片 |
| `min_gap_seconds` | `0.28` | 旁白段之间的最小间隔 |
| `start_offset` | `0.7` | 第一段旁白最早开始时间 |
| `tail_padding` | `0.8` | 片尾预留时间 |
| `max_speedup` | `1.32` | 旁白总长超出时允许的最大 atempo 加速 |

### 目标字数怎么算

```text
target_chars ≈ duration × speech_chars_per_second × narration_density
```

例如 90 秒、3.9 字/秒、0.5 密度 → 约 175 字。LLM 会被要求接近这个值；偏少会触发扩写，偏多会触发压缩。

---

## 6. TTS

| 字段 | 默认 | 说明 |
|---|---|---|
| `tts_engine` | `kokoro` | `kokoro` 或 `say`（macOS 调试用） |
| `kokoro_model` | `mlx-community/Kokoro-82M-4bit` | Kokoro MLX 模型 |
| `voice` | `zf_xiaoxiao` | 中文音色 |
| `tts_speed` | `1.0` | 基础语速；1.0 正常，>1 更快 |
| `say_voice` | `Tingting` | `say` 引擎的音色 |

Kokoro 中文音色：

| 音色 | 性别/风格 |
|---|---|
| `zf_xiaoxiao` | 女声，默认，清晰 |
| `zf_xiaobei` | 女声，偏轻 |
| `zf_xiaoni` | 女声，偏活泼 |
| `zf_xiaoyi` | 女声，偏温柔 |
| `zm_yunjian` | 男声，偏沉稳 |
| `zm_yunxi` | 男声，偏年轻 |
| `zm_yunxia` | 男声，偏温和 |
| `zm_yunyang` | 男声，偏明亮 |

> `say` 只用于没有装好 Kokoro 时快速验证流程，不建议作为成品音色。

---

## 7. 字幕

| 字段 | 默认 | 说明 |
|---|---|---|
| `whisper_model` | `mlx-community/whisper-large-v3-turbo` | mlx-whisper 模型 |
| `asr_language` | `zh` | 识别语言 |
| `subtitle_text` | `script` | `script`：原稿文字 + Whisper 时间戳；`asr`：直接用 Whisper 文字 |
| `subtitle_max_chars` | `18` | 每行最多字数 |
| `subtitle_max_lines` | `2` | 每条字幕最多行数 |
| `subtitle_max_seconds` | `6.0` | 单条字幕最长显示时间 |
| `subtitle_font` | `PingFang SC` | 字体 |
| `subtitle_size` | `48` | 字幕字号（输出像素） |
| `subtitle_preset` | `""` | 平台安全区预设：`douyin` / `reels` / `shorts` / `landscape` |
| `subtitle_render` | `outline` | `outline`：白字 + 黑描边 + 轻微阴影，只盖住笔画边缘；`box`：半透明黑底色块（旧行为，遮挡更多画面）；`none`：纯白字，无描边无阴影（亮画面容易被吃掉） |
| `subtitle_outline` | `0.0` | 描边宽度（输出像素），仅 `outline` 模式；`0` 表示自动，约为 `subtitle_size` 的 6% |
| `subtitle_box_padding` | `0.0` | 文字四周内边距（输出像素），仅 `box` 模式；`0` 表示自动，1920 高时约 6.7 px |
| `subtitle_margin_v` | `36` | 底部安全边距（输出像素） |
| `subtitle_margin_left` | `50` | 左侧安全边距（输出像素） |
| `subtitle_margin_right` | `50` | 右侧安全边距（输出像素） |

硬字幕会生成 `subtitles.ass`，其中 `PlayResX/Y` 等于输出分辨率，所以上面的字号和边距就是成片里的真实像素，不再受 libass 默认 384×288 缩放影响。软字幕仍然使用 `subtitles.srt`。

平台预设会设置三个边距，并优先于 `subtitle_margin_*`：

```toml
subtitle_preset = "douyin"   # douyin | reels | shorts | landscape
```

| 预设 | 底部 | 左 | 右 |
|---|---|---|---|
| `douyin` | 280 | 80 | 80 |
| `reels` | 260 | 90 | 90 |
| `shorts` | 320 | 80 | 80 |
| `landscape` | 40 | 60 | 60 |

保持 `subtitle_preset = ""` 即可手动调 `subtitle_margin_*`。

### 字幕样式

默认就是主流平台的做法：白色字、黑色描边、轻微阴影，没有底色块，对画面遮挡最少：

```toml
subtitle_render = "outline"   # outline | box | none
subtitle_outline = 0.0        # 0 表示自动，约为 subtitle_size 的 6%（outline 模式）
subtitle_box_padding = 0.0    # 0 表示自动，1920 高时约 6.7 px（box 模式）
```

`subtitle_render = "box"` 恢复旧的半透明黑底色块，内边距随输出高度缩放（1920 高时约 6.7 px），可用 `subtitle_box_padding` 固定。`subtitle_render = "none"` 是纯白字：只有字幕所在区域整体偏暗时才建议使用。已有工作目录会沿用 `manifest.json` → `render_settings` 里记录的值；想复现旧观感时在 `render` 上传 `--subtitle-render box`：

竖屏建议：

```toml
subtitle_size = 56
subtitle_max_chars = 14
subtitle_margin_v = 220
subtitle_margin_left = 60
subtitle_margin_right = 60
```

横屏建议：

```toml
subtitle_size = 46
subtitle_max_chars = 18
subtitle_margin_v = 60
```

旧工作目录里的 `subtitle_size`（例如 16/18）是按旧相对单位写的；升级后请显式传新像素值重渲染，例如：

```bash
reachreel render output/xxx --subtitle-size 52
```

---

## 8. BGM 与混音

| 字段 | 默认 | 说明 |
|---|---|---|
| `bgm_dir` | `inputs/bgm` | BGM 目录（本地，Git 忽略；不存在时自动创建） |
| `bgm_file` | 空 | 指定 BGM；为空则从目录随机选 |
| `bgm_volume` | `0.20` | BGM 基础音量（线性） |
| `bgm_duck_db` | `-16.0` | BGM 闪避深度；绝对值越大压得越狠 |
| `ambient_volume` | `0.16` | 原声环境音基础音量 |
| `ambient_duck_db` | `-10.0` | 原声闪避深度 |
| `target_lufs` | `-14.0` | 成品响度目标 |
| `true_peak` | `-1.5` | 真峰值上限 dBTP |
| `two_pass_loudnorm` | `true` | 先测量混音，再线性归一 |
| `bgm_fade_seconds` | `2.0` | BGM 结尾淡出秒数；`0` 关闭 |

`bgm_duck_db` / `ambient_duck_db` 会换算成 `sidechaincompress` 的 ratio（约 `abs(db)/2`，限制在 2–20）。不是精确的 dB 衰减，而是“压多狠”的近似控制。

如果 BGM 太抢：

```toml
bgm_volume = 0.14
bgm_duck_db = -20.0
```

如果环境声太弱：

```toml
ambient_volume = 0.24
ambient_duck_db = -8.0
```

---

## 9. 渲染

| 字段 | 默认 | 说明 |
|---|---|---|
| `output_height` | `0` | 输出高度；0 = 保持源高度 |
| `output_fps` | `0` | 输出帧率；0 = 保持源帧率 |
| `crf` | `18` | x264 CRF；越大文件越小 |
| `preset` | `medium` | x264 preset：`veryfast` / `fast` / `medium` / `slow` |
| `audio_bitrate` | `192k` | AAC 码率 |
| `burn_subtitles` | `true` | 是否烧硬字幕 |
| `work_root` | `output` | 默认工作目录根 |
| `cache_enabled` | `true` | 跨工作目录复用 VLM 分析结果和 TTS 音频 |
| `cache_dir` | `""` | 共享缓存根目录；空表示平台缓存目录（macOS 为 `~/Library/Caches/reachreel`，旧 `~/.cache/reachreel` 存在时优先复用） |

共享缓存按“视频采样指纹”存 VLM 结果，按“文本/模型/音色”存 TTS 音频。可用 `cache_enabled = false`、`REACHREEL_CACHE=0` 或全局 `--no-cache` 关闭。管理命令：

```bash
reachreel cache info
reachreel cache clean --yes
```

常用：

```toml
# 4K 横屏
output_height = 1080
output_fps = 30
crf = 20

# 竖屏
output_height = 1920
output_fps = 30
subtitle_size = 56
subtitle_max_chars = 14
subtitle_margin_v = 220
```

---

## 10. QA 阈值

QA 阈值可以在配置文件里用 `[qa]` 表覆盖。`reachreel run`、`reachreel qa --config` 都会读取；`qa.json` 里会记录本次实际使用的阈值。

```toml
[qa]
freeze_min_seconds = 3.0
freeze_fail_seconds = 8.0
clip_peak_db = -0.1
skip_subtitle_pixels = false
```

| 字段 | 默认 | 说明 |
|---|---|---|
| `duration_warn_seconds` / `duration_fail_seconds` | `0.5` / `2.0` | 成片与源视频时长差 |
| `av_sync_warn_seconds` / `av_sync_fail_seconds` | `0.2` / `0.8` | 音视频流时长差 |
| `lufs_warn_delta` / `lufs_fail_delta` | `2.0` / `4.0` | 综合响度与目标值偏差 |
| `true_peak_tolerance_db` / `true_peak_ceiling_db` | `0.6` / `0.0` | 真峰值容差；超过 ceiling 直接失败 |
| `black_min_seconds` | `0.5` | 黑帧最短检测时长 |
| `black_pixel_threshold` | `0.10` | `blackdetect` 的像素阈值 |
| `black_longest_fail_seconds` / `black_total_fail_seconds` | `3.0` / `5.0` | 黑帧最长/累计失败阈值 |
| `freeze_noise` | `0.0005` | `freezedetect` 噪声容差，越小越严格 |
| `freeze_min_seconds` | `3.0` | 冻结画面最短检测时长 |
| `freeze_fail_seconds` | `8.0` | 超过该时长判失败 |
| `freeze_motion_threshold` | `0.02` | 区分“冻结”与“静态长镜头”的运动量阈值 |
| `freeze_sample_seconds` | `20.0` | 每段冻结最多抽样多少秒 |
| `clip_peak_db` | `-0.1` | astats 判定满幅的峰值门槛 |
| `clip_sample_ratio` | `0.0005` | 满幅样本占比达到该值判为削波 |
| `clip_flat_factor` | `0.5` | astats Flat factor 警告门槛 |
| `subtitle_char_warn_extra` / `subtitle_char_fail_extra` | `4` / `10` | 每行超出 `subtitle_max_chars` 多少字 |
| `subtitle_safe_tolerance_px` / `subtitle_overflow_tolerance_px` | `6` / `20` | 实测字幕边距容差/失败阈值（像素） |
| `narration_subtitle_tolerance_seconds` | `0.9` | 旁白与字幕的逐句时间偏差容差 |
| `narration_subtitle_fail_ratio` | `0.5` | 偏差句数占比超过该值判失败 |
| `skip_subtitle_pixels` | `false` | 关闭实际渲染像素超框检测 |

---

## 11. 环境变量

| 环境变量 | 对应字段 |
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
| `REACHREEL_CACHE_DIR` | `cache_dir` |
| `REACHREEL_CACHE` | 设为 `0` / `false` 关闭共享缓存（`cache_enabled`） |
| `FFMPEG_FULL` | 指定带 libass 的 ffmpeg 路径 |

示例：

```bash
export REACHREEL_OLLAMA_URL=http://127.0.0.1:11434
export REACHREEL_VLM_MODEL=qwen3-vl:4b-instruct
reachreel run video.mp4
```

---

## 12. 完整示例

```toml
# 语言
ui_language = "en"
content_language = "zh"

# 模型
ollama_url = "http://localhost:11434"
vlm_model = "qwen3-vl:8b-instruct"
llm_model = "qwen3:30b-instruct"
vlm_workers = 2
vlm_frames_per_scene = 3
vlm_max_width = 768
vlm_frame_dedup = 4.0
llm_temperature = 0.6

# 镜头
scene_threshold = 0.32
min_scene_seconds = 1.2
max_scene_seconds = 8.0
max_scenes = 160

# 文案
style = ""  # 留空 = prompts/<lang>/style.txt
brief = "拍摄于云南雨崩，清晨从雨崩村出发，途经冰湖。"
speech_chars_per_second = 3.9  # 英文建议约 14.5
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

# 字幕
whisper_model = "mlx-community/whisper-large-v3-turbo"
asr_language = "zh"
subtitle_text = "script"
subtitle_max_chars = 18
subtitle_max_lines = 2
subtitle_max_seconds = 6.0
subtitle_font = "PingFang SC"
subtitle_size = 48
subtitle_render = "outline"  # outline | box | none
subtitle_outline = 0.0       # 0 = auto（outline 模式）
subtitle_box_padding = 0.0   # 0 = auto（box 模式）
subtitle_preset = ""
subtitle_margin_v = 60
subtitle_margin_left = 50
subtitle_margin_right = 50

# BGM / 混音
bgm_dir = "inputs/bgm"
bgm_file = ""
bgm_volume = 0.20
bgm_duck_db = -16.0
ambient_volume = 0.16
ambient_duck_db = -10.0
target_lufs = -14.0
true_peak = -1.5
two_pass_loudnorm = true
bgm_fade_seconds = 2.0

# 渲染
output_height = 1080
output_fps = 30
crf = 20
preset = "medium"
audio_bitrate = "192k"
burn_subtitles = true
work_root = "output"
cache_enabled = true
cache_dir = ""

# QA（可选覆盖）
[qa]
freeze_min_seconds = 3.0
freeze_fail_seconds = 8.0
clip_peak_db = -0.1
```
