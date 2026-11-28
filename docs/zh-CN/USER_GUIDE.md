[English](../en/USER_GUIDE.md) | **简体中文**

# ReachReel 使用手册

本文是面向使用者的完整操作说明。开发/架构说明见 [DEVELOPMENT.md](DEVELOPMENT.md)，所有配置项见 [CONFIGURATION.md](CONFIGURATION.md)。

---

## 1. 它能做什么

输入一段本地视频，自动完成：

1. 按镜头切分视频，抽取每个镜头的关键帧；
2. 用本地 VLM（`qwen3-vl`）看画面，描述地点、人物、动作、细节、情绪；
3. 用本地 LLM（`qwen3`）按时间顺序写中文旁白；
4. 用本地 TTS（Kokoro-82M / MLX）逐段配音；
5. 把旁白锚定到对应镜头，必要时整体加速；
6. 用 `mlx-whisper` 检测语音边界，生成 SRT 字幕；
7. 用 FFmpeg 把 BGM、原声、旁白做侧链闪避混音；
8. 用 `ffmpeg-full` 把字幕烧进画面，输出 H.264/AAC MP4。

全程本地，不需要云 API。第一次运行需要下载 Kokoro 和 Whisper 模型。

### 不适合的场景

- 需要逐字卡拉 OK 字幕、双语字幕、字幕翻译；
- 需要 HDR / 10-bit / 4K 高码率母版；
- 需要竖屏智能重构图（把横屏人物自动裁成 9:16）；
- 需要多机位、转场、花字、片头片尾模板；
- 需要批量队列、Web UI、自动发布。

这些在 [ROADMAP.md](ROADMAP.md) 里列了后续计划。

---

## 2. 系统要求

| 项目 | 要求 |
|---|---|
| 机器 | Apple Silicon Mac（M1 / M2 / M3 / M4） |
| 系统 | macOS 14+（Sonoma 及以上），Apple Silicon；CI 验证 macOS 15 |
| Python | 3.11 – 3.12（不支持 3.10，`mlx-audio` 要求 3.11+；也不要用 3.13，Kokoro 依赖链未完全适配） |
| 内存 | 16 GB 可跑；32 GB 更稳；64 GB 可提高 VLM 并发 |
| 磁盘 | 至少 10 GB 空闲（模型 + 中间文件） |
| 外部工具 | `ffmpeg`、`ffmpeg-full`、`ffprobe`、`ollama` |
| 网络 | 第一次下载模型需要；之后可离线 |

**不支持 Intel Mac**。`mlx-audio` / `mlx-whisper` 没有 x86_64 macOS 轮子，用 Rosetta 或 x86 版跑同一条流水线既慢、质量也差（只能用更小的模型、更少的抽帧、没有 Kokoro）。`reachreel doctor` 会在任何非「Apple Silicon macOS」的主机上让 `平台` 这一项失败；如果只是想在某台机器上验证工具链接线，忽略那一行 `!!`、看其余检查即可。

---

## 3. 安装

### 3.1 安装系统工具

```bash
brew install ffmpeg ffmpeg-full
```

检查：

```bash
ffmpeg -version
/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg -hide_banner -filters | grep subtitles
```

第二条命令应该能看到 `subtitles`。如果没有，说明 `ffmpeg-full` 没装好。

> 普通 `ffmpeg` 没有 libass，不能烧硬字幕。本项目会自动优先找
> `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg`，也可以用环境变量 `FFMPEG_FULL` 指定。

### 3.2 创建 Python 环境

在项目目录：

```bash
cd /path/to/reachreel
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
```

如果你用 `uv`：

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e .
```

### 3.3 准备 Ollama 模型

```bash
ollama serve          # 如果还没在后台运行
ollama pull qwen3-vl:8b-instruct
ollama pull qwen3:30b-instruct
```

低内存或想更快：

```bash
ollama pull qwen3-vl:4b-instruct
ollama pull qwen3.5:9b
```

然后在命令里指定：

```bash
reachreel run video.mp4 \
  --vlm-model qwen3-vl:4b-instruct \
  --llm-model qwen3.5:9b
```

### 3.4 自检

```bash
reachreel doctor
```

全部 `OK` 即可开始。BGM 目录为空只会显示 `--`，不影响运行。

---

## 4. 素材准备

### 4.1 视频格式

支持 FFmpeg 能读的常见格式：`.mp4` `.mov` `.mkv` `.webm` `.m4v` 等。

建议：

- 分辨率：1080p 最省时间；4K 建议输出时降到 1080p。
- 帧率：24–60 fps 都可；输出默认保持原帧率，建议显式指定 30。
- 时长：MVP 最适合 1–10 分钟；更长会显著增加 VLM 时间。
- 音轨：有环境声最好；没有音轨也能跑。
- 旋转：iPhone 竖屏视频带旋转元数据，程序会按 FFmpeg 自动旋转后的画面输出，日志/`manifest` 里记录的是显示分辨率。

### 4.2 横屏 / 竖屏

```bash
# 4K 横屏 → 1080p30
reachreel run video.mp4 --height 1080 --fps 30

# 竖屏 → 1080x1920
reachreel run video.mp4 --height 1920 --fps 30
```

`--height` 指输出高度，宽度按比例自动计算。

### 4.3 拍摄建议

- 镜头之间有明确切换或运动，VLM 更容易写出差异。
- 一镜到底的素材会被按 `max_scene_seconds`（默认 8s）切块，旁白可能重复描述相似画面。
- 如果画面里有地名、人物关系、活动背景，写在 `--brief` 里，能显著减少胡编。

---

## 5. BGM 准备

`inputs/bgm/` 是本地输入目录，被 Git 忽略，首次运行会自动创建；仓库不附带 BGM。详细说明见 [BGM](BGM.md)。

把音乐放进 `inputs/bgm/`：

```text
inputs/bgm/
  calm-morning.mp3
  mountain-air.mp3
  river-walk.wav
```

支持 `.mp3` `.wav` `.m4a` `.aac` `.flac` `.ogg` `.opus`。

- 默认随机选一首；
- 自动循环到视频长度；
- 旁白出现时自动压低；
- 原声也会轻微压低，但保留环境感。

指定某一首：

```bash
reachreel run video.mp4 --bgm inputs/bgm/calm-morning.mp3
```

不用 BGM：

```bash
reachreel run video.mp4 --no-bgm
```

推荐来源：Pixabay Music、Free Music Archive (CC0)、YouTube Audio Library、Internet Archive 的 CC0 音频。正式发布前逐首确认授权。

---

## 6. 第一次完整运行

```bash
reachreel run /path/to/video.mp4 \
  --brief "拍摄于云南雨崩，清晨从雨崩村出发，途经冰湖" \
  --style "安静、克制，像独自徒步时的自言自语" \
  --voice zf_xiaoxiao \
  --density 0.5 \
  --height 1080 --fps 30
```

<a name="character-focused-narration"></a>

### 人物解说

- 用 `--brief` 告诉 ReachReel **需要知道哪些背景**。可以写拍摄地点、人物身份，以及他们做了什么、说了什么。写人物解说时，补充能体现性格、习惯或专业知识的具体动作、反复出现的行为或判断。画面分析和旁白生成都会参考这些信息。
- 用 `--style` 告诉 ReachReel **要怎么讲**。可以指定叙述视角、口吻和想突出的特征，比如用第三人称通过人物的动作体现他的专业知识。填写 `--style` 会覆盖 `--style-preset`。

下面的例子通过咖啡师调整萃取的过程，体现他做事细致。加上 `--review-script`，生成脚本后会暂停，可以先在 `output/barista/script.md` 里看旁白，再配音。

```bash
reachreel run video.mp4 --workdir output/barista --content-language zh --review-script \
  --brief '主角是咖啡师阿杰。第一杯萃取得比预期快。他看了眼计时器，把研磨调细一格，用同样的粉量再试一杯。' \
  --style '用自然口语做第三人称解说。讲清阿杰怎么调整研磨、哪些条件保持原样，通过这些细节体现他做事细致。'
```

旁白可以写成「第一杯出得快了。阿杰看了眼计时器，把研磨调细一格。下一杯，他还用原来的粉量。」

### 运行过程会发生什么

1. `读取视频`：ffprobe 读取时长、分辨率、帧率、音轨。
2. `检测镜头`：FFmpeg scene 分数找硬切点，长镜头切块，碎镜头合并。
3. `抽取关键帧`：每个镜头 1–3 张，长边 768px。
4. `VLM 分析`：逐镜头调用 Ollama，输出结构化画面描述。
5. `写解说`：本地 LLM 读画面表，写 JSON 分段旁白。
6. `配音`：Kokoro 逐段合成，缓存到 `tts/`。
7. `旁白落点`：按场景起点锚定；超长时整体 atempo 加速。
8. `字幕`：Whisper 检测语音边界，原稿文字 + 时间戳生成 SRT。
9. `混音`：BGM / 原声 / 旁白侧链闪避，响度归一到 -14 LUFS。
10. `渲染`：烧字幕、H.264、AAC、faststart。

### 时间预估

以 M4 Pro / 64 GB、`qwen3-vl:8b`、2 并发为例：

| 素材 | 镜头数 | VLM | 写稿 | TTS | 字幕 | 渲染 | 总计 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 82s 竖屏 1080p | 12 | ~3 min | ~10s | ~5s | ~5s | ~30s | ~4 min |
| 93s 横屏 4K | 12 | ~4 min | ~10s | ~5s | ~5s | ~50s | ~5 min |

第一次运行还要加模型下载时间。

---

## 7. 命令参考

### 7.1 `reachreel run`

完整跑一遍。

```bash
reachreel run VIDEO [options]
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `VIDEO` | 必填 | 输入视频路径 |
| `--workdir DIR` | 自动 | 指定工作目录；默认 `output/<视频名>_<时间戳>` |
| `--out DIR` | `output` | 工作目录根；仅在未指定 `--workdir` 时生效 |
| `--config FILE` | 无 | TOML 配置文件 |
| `--bgm FILE` | 随机 | 指定 BGM 文件 |
| `--bgm-dir DIR` | `inputs/bgm` | BGM 目录 |
| `--bgm-fade N` | `2.0` | BGM 结尾淡出秒数 |
| `--no-bgm` | 关 | 不混 BGM |
| `--voice NAME` | 跟随语言 | 内置音色（`zf_xiaoxiao`、`af_heart`、`Serena`、`Ryan` 等）；可用 `reachreel voices --preview <音色名>` 试听 |
| `--tts ENGINE` | `kokoro` | `kokoro`、`qwen3` 或 `say`（调试用） |
| `--tts-language TAG` | 跟随成片语言 | 配音语言，BCP 47 标签（`zh-CN`、`en-US`、`en-GB`），音色必须与之一致 |
| `--vlm-model NAME` | `qwen3-vl:8b-instruct` | 画面理解模型 |
| `--llm-model NAME` | `qwen3:30b-instruct` | 写稿模型 |
| `--whisper-model NAME` | `mlx-community/whisper-large-v3-turbo` | 字幕模型 |
| `--vlm-workers N` | `2` | VLM 并发数 |
| `--vlm-frames N` | `3` | 每个镜头抽帧数 |
| `--vlm-dedup X` | `4.0` | 与上一张保留帧的差异低于 `X` 的帧会被丢弃（`0` 表示关闭去重） |
| `--scene-threshold X` | `0.32` | 镜头切换阈值；越低越敏感 |
| `--style TEXT` | 内置 | 旁白怎么讲，包括叙述视角、口吻和重点 |
| `--brief TEXT` | 空 | 视频背景，包括拍摄地、人物以及他们做过或说过的事 |
| `--density X` | `0.62` | 旁白密度 0.2–1.0；越低留白越多 |
| `--max-speedup X` | `1.32` | 旁白过长时允许的最大加速 |
| `--asr MODE` | `mlx-whisper` | `mlx-whisper` 或 `none`（用 TTS 时间轴） |
| `--height N` | 原分辨率 | 输出高度 |
| `--fps N` | 原帧率 | 输出帧率 |
| `--subtitle-size N` | `48` | 字幕字号（输出像素） |
| `--subtitle-chars N` | 跟随语言（中文 `18` / 英文 `42`） | 每行最多字数 |
| `--subtitle-preset NAME` | 空 | `douyin` / `reels` / `shorts` / `landscape` 安全区预设，覆盖边距 |
| `--subtitle-render MODE` | `outline` | `outline`：白字 + 黑描边 + 轻微阴影；`box`：半透明黑底色块（旧观感，遮挡更多画面）；`none`：纯白字 |
| `--subtitle-outline X` | `0` | 描边宽度（输出像素，`outline` 模式），`0` 表示自动，约字号的 6% |
| `--subtitle-box-padding X` | `0` | 文字四周内边距（输出像素，`box` 模式），`0` 表示自动，1920 高时约 6.7 px |
| `--subtitle-margin-v N` | `36` | 底部安全边距（输出像素） |
| `--subtitle-margin-left N` | `50` | 左侧安全边距（输出像素） |
| `--subtitle-margin-right N` | `50` | 右侧安全边距（输出像素） |
| `--no-burn` | 关 | 不烧硬字幕，改为软字幕轨 |
| `--no-qa` | 关 | 跳过 QA 闸门 |
| `--qa-strict` | 关 | QA 出现警告也返回非 0 |
| `--only STAGES` | 全部 | 只运行指定阶段，逗号分隔，如 `vlm,script` |
| `--dry-run` | 关 | 只打印将要运行的阶段和缓存状态，不调用模型、不写文件 |
| `--review-script` | 关 | 跑到脚本阶段停下，输出 `script.md` 供人工确认 |
| `--json` | 关 | 向标准输出打印机器可读的 JSON 计划（`--dry-run`）或执行摘要（含 `workdir`、产物路径、QA 报告与 `next_command`） |
| `--force` | 关 | 重新生成选中的阶段，跳过工作目录缓存和共享缓存的读取 |
| `--no-resume` | 关 | 不复用场景/解说，但保留 VLM/TTS 缓存 |
| `--verbose` | 关 | 调试日志 |

> 全局选项：`--lang en|zh-CN` 切换 CLI 语言；`--content-language zh|en` 切换旁白/字幕语言。两者互相独立。`--cache-dir DIR` 与 `--no-cache` 控制共享缓存。

常用：

```bash
# 先确认会跑哪些阶段
reachreel run video.mp4 --workdir output/xxx --only vlm,script --dry-run

# 只重做 VLM + 文案，不重新配音
reachreel run video.mp4 --workdir output/xxx --only vlm,script

# 只重做字幕和成片
reachreel run video.mp4 --workdir output/xxx --only subtitles,mix,render

# 先出脚本，人工确认 script.md 后一键续跑
reachreel run video.mp4 --workdir output/xxx --review-script
# 编辑 output/xxx/script.json 后：
reachreel resume output/xxx

# 英文旁白、配音与字幕（需先装 tts-en extra 与 en_core_web_sm）
reachreel run video.mp4 --content-language en --height 1080 --fps 30
# 英式英语配音
reachreel run video.mp4 --content-language en --tts-language en-GB --voice bf_emma
```

`--only` 接受 `probe`、`vlm`（别名 `analyze`）、`script`、`tts`、`subtitles`（别名 `subtitle`）、`mix`、`render`、`qa`。选中的阶段按流水线顺序执行。缺少中间文件时会给出补跑前置阶段的命令。

`run --only` 回显最后一个所选阶段的产物路径。例如，`run --only vlm,script` 回显 `script.json`，随后可用 `reachreel tts <workdir>` 接续。依赖命令回显成片路径的脚本应让 `render` 成为最后一个阶段。

### 7.2 `reachreel analyze`

执行镜头检测、抽帧和 VLM 分析，回显 `scenes.json`，并保存可由 `reachreel script <workdir>` 接续的工作目录。

```bash
reachreel analyze video.mp4 --workdir output/analysis
```

适合：

- 先看看 VLM 是否看懂画面；
- 复用分析结果，之后再写稿；
- 调试 `--brief` / `--scene-threshold`。

### 7.3 `reachreel render`

用已有工作目录重新渲染，不重跑 VLM/TTS。

```bash
reachreel render output/ski_20261121_203000
```

可选覆盖：

```bash
reachreel render output/ski_20261121_203000 --crf 22
reachreel render output/ski_20261121_203000 --height 1080 --fps 30
reachreel render output/ski_20261121_203000 --subtitle-size 52 --subtitle-margin-v 220
reachreel render output/ski_20261121_203000 --no-burn
```

默认沿用已保存的输出尺寸、字幕样式和硬字幕或软字幕模式。可用 `--config` 或渲染参数覆盖这些设置。

替换原片后先重跑 `analyze`。配音后修改文案或镜头时间点，需重跑 `reachreel tts <workdir>`。渲染和导出会检查字幕、混音是否过时，并提示补跑命令。仍可直接编辑 `subtitles.srt` 后重新渲染。渲染成功后会替换成片并清除旧 QA 摘要。编码失败时会保留上一版成片及其报告。

### 7.4 `reachreel doctor`

检查环境：

```bash
reachreel doctor
reachreel doctor --json
```

`--json` 输出机器可读报告：`{"ok": true, "checks": [{"id", "label", "value", "status", "required", "ok"}]}`。

检查项：

- Python 版本；
- `ffmpeg` / `ffprobe`；
- `ffmpeg-full` 和 `subtitles` / `sidechaincompress` / `loudnorm` / `blackdetect` / `freezedetect` 滤镜；
- `mlx_audio` / `mlx_whisper` / `misaki`；
- Ollama 是否可达，模型是否已 pull；
- BGM 目录（可选）。

退出码 0 表示必需项都通过。

### 7.5 阶段级命令

完整跑一遍很慢；改文案、改字幕、换 BGM 时只重做对应阶段：

```bash
# 根据已有的 scenes.json 重新生成文案
reachreel script output/xxx

# 只重新配音 + 重算旁白时间轴
reachreel tts output/xxx

# 只重做字幕：默认 mlx-whisper；也可以 --asr none 用 TTS 时间轴
reachreel subtitle output/xxx

# 只重做混音。同一 BGM 目录可复用自动选曲结果。
reachreel mix output/xxx
reachreel mix output/xxx --no-bgm
reachreel mix output/xxx --bgm inputs/bgm/another.mp3

# 只重新渲染
reachreel render output/xxx

# 只跑质量检查
reachreel qa output/xxx
```

阶段命令以 `manifest.json` 中保存的设置为基础，依次应用 `--config` 中显式设置的字段、环境变量和命令行参数。[配置优先级](CONFIGURATION.md#precedence)

`reachreel script` 会在 VLM 设置改变时重新分析镜头。使用 `run --only` 时，需要包含 `vlm,script` 才能应用这些变化。传入新的 `--bgm-dir` 会从该目录重新选曲，显式传入的 `--bgm` 仍优先。

### 7.6 `reachreel qa`

对已有工作目录跑质量闸门：

```bash
reachreel qa output/xxx
reachreel qa output/xxx --strict
reachreel qa output/xxx --json
reachreel qa output/xxx --config config.toml   # 用 [qa] 阈值覆盖 manifest
```

检查项：

- 成片是否存在、是否有视频/音频流；
- 成片时长与源视频、音视频时长是否一致；
- 综合响度、真峰值；`astats` 满幅样本/Flat factor 削波检查；
- 黑帧、冻结画面（会对比源片，静态长镜头不再误报）；
- 字幕条数、每行字数、行数、时间、重叠、边界；
- 硬字幕实际渲染后的像素边距/超框（`bbox` 测光，非 OCR）；
- 旁白与字幕逐句文本/时间偏差；
- 旁白速度、旁白结束位置；
- BGM 文件是否存在；
- 硬字幕/软字幕模式是否匹配。

`qa.json` 会记录本次使用的阈值。可以在 `config.toml` 的 `[qa]` 表中调整这些值。[配置参考](CONFIGURATION.md#10-qa-thresholds)

退出码：

- `0`：通过，或只有警告；
- `1`：有 `fail` 项；
- `--strict`：有警告也返回 `1`。

结果写入 `qa.json`。`reachreel run` 默认也会跑一次 QA；不想跑加 `--no-qa`。

QA 会校验成片与记录的输入是否一致。更新旁白、字幕或混音后，请按提示补跑对应阶段，再渲染成片并执行 QA。

硬字幕的像素超框检查会按 SRT 时长在 1080p 黑底上渲染一遍字幕层（2–4 fps），通常多花几秒；不需要时可在 `[qa]` 里设 `skip_subtitle_pixels = true`。

### 7.7 `reachreel licenses`

导出工作目录用到的 BGM 授权清单：

```bash
reachreel licenses output/xxx
reachreel licenses output/xxx --out /tmp/licenses.md
```

命令从 `manifest.json` 读取 `bgm_metadata`（在 `mix` 阶段由 `inputs/bgm/manifest.json` 写入），并输出 `licenses.md`。如果工作目录是在目录文件出现之前混的音，先重新运行 `reachreel mix output/xxx`。

### 7.8 `reachreel cache`

共享缓存默认在平台缓存目录（macOS 为 `~/Library/Caches/reachreel`），同一素材/同一段文本换工作目录时可以复用 VLM 分析和 TTS 音频。如果旧的 `~/.cache/reachreel` 存在且新目录不存在，则继续使用旧目录。

```bash
reachreel cache info
reachreel cache clean --dry-run
reachreel cache clean --yes
```

`cache info` 显示位置、文件数和大小；`cache clean` 不加 `--yes` 不会删除，加 `--dry-run` 只预览。

单次运行关闭共享缓存（全局参数放在子命令前面）：

```bash
reachreel --no-cache run video.mp4 --workdir output/xxx
reachreel --cache-dir /tmp/rr-cache run video.mp4
```

字幕可用 `--subtitle-preset douyin|reels|shorts|landscape`（或配置里的 `subtitle_preset`）套用平台安全区，预设会覆盖手动边距。

### 7.9 `reachreel status`

检查已有工作目录的各阶段状态（`ok` 已完成、`stale` 输入已变需重跑、`missing` 缺失）、`script.json` 是否被手动编辑、最新 QA 状态，以及下一步可执行命令：

```bash
reachreel status output/xxx
reachreel status output/xxx --json
```

加 `--json` 会输出机器可读的结构化报告（包含 `complete`、`video`、`settings`、`script_edited`、`stages`、`artifacts`、`quality_flags`、`qa`、`next_stage` 与 `next_command`），适合脚本集成或让 Codex 等 AI Agent 接续与排查已有任务。

---

## 8. 输出目录

```text
output/<视频名>_<时间戳>/
├── final.mp4          # 成片
├── edit/              # 可选的剪辑素材包（reachreel export）
├── subtitles.srt      # 字幕（软字幕/兼容）
├── subtitles.ass      # 硬字幕布局（PlayRes = 输出分辨率）
├── script.json        # 分段解说词（可编辑）
├── script.md          # 可选的审稿预览，包含镜头证据
├── scenes.json        # 镜头 + VLM 分析
├── vlm_cache.json     # VLM 断点缓存
├── timeline.json      # 旁白落点
├── narration.wav      # 纯旁白轨 48k
├── mix.wav            # 旁白 + 原声 + BGM
├── bgm_loop.wav       # 循环后的 BGM
├── manifest.json      # 参数、统计、render_settings
├── review.md          # 文案/落点速览
├── qa.json            # 质量检查结果
├── licenses.md        # BGM 授权清单（reachreel licenses）
├── frames/            # 抽出的关键帧
└── tts/               # 每段配音 wav
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

`audio_path`、`duration`、`start`、`end` 由程序回填；你只需要改 `text` 和 `scene_ids`。

### `manifest.json`

记录：

- 视频信息；
- 本次模型、音色、BGM；
- 场景数、旁白段数、字数；
- 旁白时间轴；
- `render_settings`（`render` 命令会复用）；
- `qa`（最近一次 QA 汇总）。

---

## 9. 调参配方

### 9.1 更像旅行 Vlog（少旁白、多环境声）

```bash
reachreel run video.mp4 --density 0.4 --style "轻松、口语化，像和朋友边走边聊"
```

### 9.2 更像纪录片（旁白更满、更稳）

```bash
reachreel run video.mp4 --density 0.75 --style "克制、客观、有画面感，不煽情"
```

### 9.3 4K 素材快速出片

```bash
reachreel run 4k.mp4 --height 1080 --fps 30 --vlm-workers 2
```

### 9.4 竖屏短视频

```bash
reachreel run portrait.mov --height 1920 --fps 30 --subtitle-size 56 --subtitle-chars 14
```

### 9.5 低内存机器

```bash
reachreel run video.mp4 \
  --vlm-model qwen3-vl:4b-instruct \
  --llm-model qwen3.5:9b \
  --vlm-workers 1 \
  --vlm-frames 2
```

### 9.6 没有 BGM

```bash
reachreel run video.mp4 --no-bgm
```

### 9.7 只要字幕，不要配音

目前没有单独的“只出字幕”命令；可以：

```bash
reachreel analyze video.mp4 --workdir output/analysis
```

然后基于 `scenes.json` 自己写 SRT，或用 `subtitle_text="asr"` 跑一遍。后续会加 `--subtitle-only`。

### 9.8 文件太大

```bash
reachreel render output/xxx --crf 22
```

CRF 越大文件越小、画质越低。默认 18，建议 20–23。

---

## 10. 人工校对与返工

### 10.1 改文案

加上 `--review-script` 会生成 `script.md`，其中包含文案和关联的镜头证据。缺少证据的段落可对照原片或 `brief` 核实。需要调整旁白时，编辑下面的文件。

```text
output/xxx/script.json
```

修改 `text` 并保存，再指定同一个 `--workdir` 运行。

```bash
reachreel run video.mp4 --workdir output/xxx
```

程序会：

- 复用 `scenes.json` / VLM 缓存；
- 读取你改过的 `script.json`；
- 按新文本重新 TTS；
- 重做时间轴、字幕、混音、渲染。

也可以分步做，避免一次跑完整条：

```bash
reachreel tts      output/xxx   # 按新文案重新配音
reachreel subtitle output/xxx   # 重做字幕
reachreel mix      output/xxx   # 重做混音
reachreel render   output/xxx   # 重新渲染
reachreel qa       output/xxx   # 质量检查
reachreel licenses output/xxx   # 导出 BGM 授权清单
reachreel cache info            # 共享缓存位置和大小
```

正常续跑会保留人工修改过的文案。`--brief`、`--style-preset` 等写稿输入改变时，带生成记录且未被编辑的文案会重新生成。需要主动替换草稿时，执行 `reachreel script <workdir>`，或在 `run --only` 中显式选择 `script`。执行写稿阶段时，`--force` 和 `--no-resume` 也可能替换已有文案。

如果请求的内容语言与带生成记录的人工改稿不同，续跑会在替换稿件或配音前停下。要切到新生成的英文文案，可显式执行 `reachreel script <workdir> --content-language en`。

配音后可在 `review.md` 中查看实测时长超出关联镜头可用时间的段落。缩短对应句子，再重做配音并检查新的成片。

### 10.2 只改字幕

编辑 `output/xxx/subtitles.srt`，然后：

```bash
reachreel render output/xxx
```

`render` 使用现有的 `mix.wav` 和编辑后的 SRT。硬字幕模式会重新生成 `subtitles.ass`，软字幕模式会更新字幕轨。

### 10.3 全部重做

```bash
reachreel run video.mp4 --workdir output/xxx --force
```

会重新抽帧、分析画面、写稿、配音和渲染。VLM 和 TTS 会跳过工作目录及共享缓存的读取。

### 10.4 只重做场景/文案，保留 VLM

```bash
reachreel run video.mp4 --workdir output/xxx --no-resume
```

会重新检测镜头、重新写稿，但 VLM 缓存和 TTS 缓存仍可命中。

### 10.5 改 BGM 音量

编辑 `config.toml`：

```toml
bgm_volume = 0.15       # 更轻
ambient_volume = 0.22   # 环境声更明显
```

重新混音时显式传入配置文件，再渲染和检查更新后的成片。

```bash
reachreel mix output/xxx --config config.toml
reachreel render output/xxx
reachreel qa output/xxx
```

### 10.6 导出到剪辑软件

原片、`narration.wav` 和 `subtitles.srt` 就绪后即可导出。`export` 读取现有工作目录，通过 FFmpeg 在本地生成素材包。

```bash
reachreel export output/my-job
reachreel export output/my-job --format mov --out output/my-job-prores
reachreel export output/my-job --fps 30 --out output/my-job-30fps
reachreel export output/my-job --narration-mode segments --out output/my-job-segs
```

默认导出到 `output/my-job/edit`。再次导出时，用 `--out` 指定一个新目录。MP4 使用 H.264 和 AAC。MOV 使用 ProRes 422 和 PCM。两种视频均不压制 ReachReel 字幕，并按工作目录的设置处理输出高度和帧率，生成恒定帧率视频。已有完整混音时，视频带完整混音。尚未混音时，视频带旁白。加上 `--narration-mode segments` 会按旁白段把独立切片导出到 `segments/seg_*.wav`，方便在剪辑软件里拖动单句位置后再通过 `reachreel import --timeline` 回导。

剪辑素材包支持整数帧率及标准分数帧率 23.976、29.97、59.94 和 119.88 fps。遇到其他帧率时，可用 `--fps` 指定支持的导出帧率，工作目录中的设置继续用于其他命令。

| 剪辑软件 | 导入方式 |
|---|---|
| Premiere Pro、DaVinci Resolve | 导入 `timeline.xml`（Final Cut Pro 7 XML） |
| Final Cut Pro | 通过「文件 → 导入 → XML」导入 `timeline.fcpxml`（FCPXML 1.9） |
| 剪映 / CapCut 桌面版 | 导入 `video.mp4`，再通过字幕菜单导入 `subtitles.srt`。如需独立编辑旁白，将视频静音，再把 `narration.wav` 对齐到时间线起点。 |

XML 时间线使用视频画面，并将 `narration.wav` 放在独立轨道上。单独导入 `subtitles.srt` 即可编辑字幕。如果素材包内有 `mix.wav`，可用它替换旁白轨，保留已有的旁白、原声与 BGM 混音。交付时请传整个目录。如果移动了目录，在剪辑软件中重新链接新目录下的视频和旁白文件。

交付前在接收方使用的剪辑软件中验证导入。核对片段时长、帧率、旁白和字幕的对齐，以及移动素材包后能否重新链接素材。

### 10.7 命令行精细微调（`reachreel tune`）

无需手改 `script.json`，可以用 `reachreel tune` 对单个旁白段做精细微调，或对源片做非破坏性硬切裁剪（写入 `cuts.json`，复用已有 VLM 缓存）：

```bash
# 微调第 3 段旁白：改文案、换音色、锁定出现时间、调节单段音量并自动重跑下游
reachreel tune output/my-job \
  --segment 3 \
  --text "他看了眼计时器，把研磨调细了一格。" \
  --voice Serena --speed 1.05 --start 14.5 --gain-db 1.5 \
  --apply

# 禁用第 6 段，或恢复某段自动落点
reachreel tune output/my-job --segment 6 --disable --apply
reachreel tune output/my-job --segment 3 --start auto --apply

# 轻量源片裁剪：删除 0-2.0s 和 34.5-41.0s（或用 --keep 指定保留区间，--reset-cuts 恢复原片）
reachreel tune output/my-job --cut 0-2.0,34.5-41.0 --apply
reachreel tune output/my-job --keep 2.0-34.5,41.0-88.0 --apply
reachreel tune output/my-job --reset-cuts --apply

# 撤销上一次微调（从 .history/ 恢复 script.json 与 cuts.json）
reachreel tune output/my-job --undo --apply
```

全局字幕样式与混音参数仍走现有阶段命令（`subtitle` / `mix` / `render`）或 `reachreel resume output/my-job --subtitle-size 52 --bgm-volume 0.14`，与 `tune` 职责完全不重叠。

### 10.8 导回外部微调产物与剪辑时间线（`reachreel import`）

在外部改好脚本、SRT 字幕、真人配音或剪辑软件时间线后，可以直接导入工作目录并只重跑受影响的下游阶段：

```bash
# 导入精修后的 SRT 字幕（仅重跑 render 和 qa，不重跑 TTS/混音）
reachreel import output/my-job --subtitles /path/to/edited.srt --resume

# 导入编辑后的 script.json / script.md、替换单段配音或整轨旁白、替换 BGM
reachreel import output/my-job --script /path/to/script.md --resume
reachreel import output/my-job --segment-audio 1=/path/to/line1.wav --resume
reachreel import output/my-job --narration /path/to/voiceover.wav --resume
reachreel import output/my-job --bgm /path/to/new-bgm.mp3 --resume

# 从 Premiere / DaVinci Resolve (.xml) 或 Final Cut Pro (.fcpxml) 导回时间线
reachreel import output/my-job --timeline /path/to/edited.fcpxml --dry-run
reachreel import output/my-job --timeline /path/to/edited.fcpxml --resume
```

导入 XML/FCPXML 时，主视频轨的硬切会写入 `cuts.json`，按段导出的旁白切片位置与删减会同步到 `script.json` 的 `start_override` / `enabled`，检测到不支持的转场或变速特效时会明确发出警告。

### 10.9 本地可视化微调面板（`reachreel serve`）

如果想在浏览器里像轻量视频编辑台一样直观微调，直接启动本地微调服务（无需额外依赖，默认只监听 `127.0.0.1` 并自动校验会话 Token）：

```bash
# 打开整个 output/ 目录并在浏览器中自动启动面板
reachreel serve --open

# 或直接定位到某一条任务目录
reachreel serve output/my-job --port 8760 --open
```

面板底层与 CLI 共享同一套 `Pipeline` 与工作目录文件，支持：
- 成片 / 原片切换播放 + 实时字幕样式叠加预览；
- 时间轴上直接拖动旁白段锁定入点（`start_override`）、在播放头切分/删除源片硬切片段（`cuts.json`）、点击镜头缩略图跳转画面；
- 检视器中编辑单段文案/音色/语速/语气/增益、单段即时试听与上传替换音频、编辑 SRT 字幕与全局样式、调节混音滑杆、导入外部文件或 NLE XML/FCPXML、导出 NLE 素材包；
- 一键撤销（`.history/`）与仅重跑受影响的下游阶段（通过 SSE 实时查看日志与 QA 报告）。

---

## 11. 质量检查清单

发布前建议过一遍：

```bash
reachreel qa output/xxx --strict
```

再人工确认：

- [ ] `script.json` 里没有编造的地名、人物、数字；
- [ ] 旁白没有明显的“画面中”“镜头里”等元话语；
- [ ] 字幕没有超出安全区、没有被裁切；
- [ ] 字幕断句自然，没有半句话被拆到下一段；
- [ ] 旁白没有压过原声里的关键信息；
- [ ] BGM 不抢人声，片尾没有突然截断；
- [ ] 成片首尾没有黑帧/静音过长；
- [ ] 竖屏视频在手机上检查字幕位置；
- [ ] BGM 授权已确认。

---

## 12. 常见问题

### 12.1 `ffmpeg has no 'subtitles' filter`

```bash
brew install ffmpeg-full
```

或指定：

```bash
export FFMPEG_FULL=/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg
```

### 12.2 `No module named 'misaki'`

```bash
pip install "misaki[zh]"
```

### 12.3 Ollama 连接失败

```bash
ollama serve
curl http://localhost:11434/api/tags
```

确认模型名和 `--vlm-model` / `--llm-model` 一致。

### 12.4 VLM 很慢 / 内存不够

```bash
reachreel run video.mp4 --vlm-workers 1 --vlm-frames 2
```

或换 4B 模型。

### 12.5 旁白太长 / 太短

- 太长：`--density 0.45`，或编辑 `script.json` 删句；
- 太短：`--density 0.75`；
- 语速太快：降低 `--max-speedup`，或减少文案。

### 12.6 字幕太大 / 太小 / 超出画面

```bash
# 竖屏 1080x1920
reachreel run video.mp4 --subtitle-size 56 --subtitle-chars 14

# 横屏 1920x1080
reachreel run video.mp4 --subtitle-size 46 --subtitle-chars 18
```

`subtitle_size` 现在表示成片里的像素高度。竖屏建议 52–64、每行 14–16 字；横屏建议 42–52、每行 18 字。竖屏如果还要给平台底部 UI 留空间：

```toml
subtitle_margin_v = 220
subtitle_margin_left = 60
subtitle_margin_right = 60
```

QA 的 `subtitle_overflow_pixels` 会直接渲染硬字幕使用的 `subtitles.ass`（旧工作目录回退 SRT），用像素包围盒测量左右/底部边距；报超框时优先减小字号/每行字数，或调整安全边距。

### 12.7 字幕有错字

默认字幕文字来自解说原稿，不是 ASR，所以通常不会错。若你切了：

```toml
subtitle_text = "asr"
```

就会直接用 Whisper 转写文字，可能出现同音错字；改回 `script` 即可。

### 12.8 视频是竖屏，但输出变成横屏

检查原视频是否带旋转元数据：

```bash
ffprobe -v error -select_streams v:0 -show_entries stream=width,height:stream_side_data=rotation -of json video.mp4
```

程序会按 FFmpeg 自动旋转后的画面输出。竖屏请用 `--height 1920`。

### 12.9 没有音轨

可以跑，程序会跳过原声，只混旁白 + BGM。`doctor` / `manifest` 会显示 `has_audio=false`。

### 12.10 重跑时没有复用缓存

确认：

- 用了同一个 `--workdir`；
- 没有加 `--force`；
- 文案/模型/音色没变（变了会重新 TTS 或 VLM）。

### 12.11 QA 报 `true_peak` 警告

说明成片真峰值略高于目标上限（默认 `-1.5 dBTP`）。可以：

```toml
true_peak = -2.0
```

然后重跑 `mix` + `render`。如果只是个人发布，-1.0 dBTP 通常也能接受，但平台转码前留一点余量更稳。

### 12.12 QA 报 `freeze_frames`

QA 会对比源片同一时间段的运动量：如果源片本身也是静止长镜头，会判为“静态长镜头”并跳过，不再误报。真正冻结（源片有运动、成片不动）才会警告/失败。

仍想调整阈值时：

```toml
[qa]
freeze_noise = 0.0005     # 越小越严格
freeze_min_seconds = 3.0  # 多长的静止才开始检测
freeze_fail_seconds = 8.0 # 超过多久判失败
```

---

## 13. 版权与隐私

- 视频、音频、字幕、模型都只在本机处理；只有下载模型时会访问 Hugging Face。
- Ollama 默认只监听本机 `11434`；不要暴露到公网。
- BGM 请使用 CC0 / 明确允许商用的曲目；格式、选择与授权说明见 [BGM](BGM.md)。
- 代码 Apache-2.0；第三方模型和 BGM 各自遵循自己的许可。
