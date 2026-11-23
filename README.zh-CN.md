[English](README.md) | **简体中文**

# ReachReel

**把一段原始素材，直接在你的 Mac 上做成带旁白、字幕、配音与混音的成片。**

放进一段原始视频，按需补充几句背景信息（`--brief`）和背景音乐，ReachReel 就会自动看懂每个镜头、写出时间对齐的旁白、录制自然人声配音、对齐双语字幕、在人声出现时自动压低背景音乐，并在输出前跑一遍质量检查——**无需云端 API，没有按分钟计费，素材全程不离开本地**。

> **在线文档**：[English Docs](https://reachpub.github.io/ReachReel/) · [简体中文文档](https://reachpub.github.io/ReachReel/zh-CN/)  
> **端到端实测**：室内滑雪竖屏（82s，`1080x1920`）与尼亚加拉瀑布横屏（93s，`1080p`，中文与英文版本）均已完整跑通，20 项 QA 检查全部通过。

---

## 适合谁用

- **视频创作者 / Vlog 博主**：把相机或手机里的原始素材快速变成可直接发布的口播成片（抖音、小红书、Reels、Shorts、B 站、YouTube），告别对着空白文档写稿和反复对轨。
- **视频剪辑师**：先生成一版带旁白的粗剪，用 `--review-script` 在配音前审校并修改 `script.md` / `script.json`；后续只改文案、字号或 BGM 时，只需重跑对应阶段，不用整条从头来。
- **开发者**：一条清晰、可断点复用、本地优先的 Python 流水线（`probe -> vlm -> script -> tts -> subtitles -> mix -> render -> qa`），基于 FFmpeg、Ollama、Kokoro-82M (MLX) 与 `mlx-whisper` 构建。

---

## 核心体验

### 看片与写稿
- **按镜头理解画面**：逐镜头识别地点、人物主体、动作、细节与氛围，按时间顺序写出贴合画面的旁白，而不是空泛套话。
- **可控的风格与节奏**：通过 `--style` 指定口吻、`--brief` 补充地名与人物背景、`--density` 调节旁白疏密留白。
- **配音前先审稿**：加上 `--review-script` 会在写完 `script.md` 后暂停，确认或修改文案后再继续配音与渲染。

### 语音配音（中文与英文）
- **中英双语本地配音**：基于 Apple Silicon 的 Kokoro-82M (MLX)，支持普通话（`zh-CN`）、美式英语（`en-US`）与英式英语（`en-GB`），首次下载模型后可完全离线运行。
- **界面与成片语言独立**：可以用英文或中文界面（`--lang`）操作，同时独立产出中文或英文旁白与字幕（`--content-language zh|en`）。

### 字幕与构图
- **对齐语音边界、不出错字**：使用 `mlx-whisper` 检测语音起止时间，字幕文字取自审定原稿，避免语音识别同音错字。
- **平台安全区预设**：内置 `douyin`、`reels`、`shorts`、`landscape` 安全边距预设，默认白字黑描边（支持 `outline` / `box` / `none`），也支持 `--no-burn` 输出软字幕轨。
- **横竖屏与旋转兼容**：正确处理手机竖拍旋转元数据，支持 `1080x1920` 竖屏与 `1080p` 横屏输出。

### 混音与发布前 QA
- **自动闪避与响度达标**：旁白出现时自动压低 BGM 与环境原声，综合响度归一到 `-14 LUFS`，结尾自动淡出音乐，并支持用 `reachreel licenses` 导出 BGM 授权清单。
- **20 项发布前 QA 闸门**：自动检查音画时长同步、响度、削波、黑帧/冻结帧、字幕像素超框与旁白字幕对齐情况。
- **按阶段精确返工**：改一句文案或调一次字幕字号，只需重跑受影响的阶段（`script`、`tts`、`subtitle`、`mix`、`render`、`qa`）。

---

## 快速开始

### 1. 环境

- Apple Silicon Mac（M1 及以上）
- Python 3.11 – 3.12
- Homebrew
- 建议 32 GB 内存以上；16 GB 也能跑，可把 `vlm_workers` 设为 1

```bash
brew install ffmpeg ffmpeg-full

python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .

# 可选：英文配音（无 torch 的 G2P 依赖）
pip install -e '.[tts-en]'
python -m spacy download en_core_web_sm
```

> Homebrew 的普通 `ffmpeg` 没有 `libass`，无法烧录硬字幕。
> ReachReel 会自动优先使用 `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg`。

### 2. 本地模型

```bash
ollama pull qwen3-vl:8b-instruct
ollama pull qwen3:30b-instruct     # 也可以换 qwen3.5:9b / qwen3:8b
```

### 3. 自检

```bash
reachreel doctor
```

### 4. 放 BGM（可选）

把 CC0 / 无版权音乐放进 `inputs/bgm/`：

```text
inputs/bgm/
  morning-light.mp3
  mountain-air.wav
```

### 5. 跑

```bash
# 中文旁白 + 字幕（默认）
reachreel run /path/to/video.mp4 \
  --brief "拍摄于云南雨崩，清晨从雨崩村出发，途经冰湖" \
  --style "安静、克制，像独自徒步时的自言自语"

# 英文旁白 + 配音 + 字幕（默认美式英语；加 --tts-language en-GB 切英式英语）
reachreel run /path/to/video.mp4 \
  --content-language en --height 1080 --fps 30 \
  --brief "Niagara Falls boat tour and misty viewpoints"
```

输出在 `output/<视频名>_<时间戳>/`，核心文件：

```text
final.mp4        # 可直接发布的成片
subtitles.srt    # 字幕（兼容软字幕）
subtitles.ass    # 硬字幕布局（按真实输出像素坐标）
script.json      # 分段解说词（可直接编辑）
scenes.json      # 镜头切分 + 画面分析
timeline.json    # 旁白时间轴落点
narration.wav    # 纯旁白音轨
mix.wav          # 旁白 + 原声 + BGM 混音
manifest.json    # 参数快照、质量标记与统计
review.md        # 文案与时间轴速览
qa.json          # 20 项质量检查结果
licenses.md      # BGM 授权清单
```

质量检查与精确返工：

```bash
reachreel qa output/<视频名>_<时间戳>
reachreel licenses output/<视频名>_<时间戳>

# 先看计划，再只重跑 VLM + 文案
reachreel run video.mp4 --workdir output/<视频名>_<时间戳> --only vlm,script --dry-run
reachreel run video.mp4 --workdir output/<视频名>_<时间戳> --only vlm,script
```

---

## 文档

| 文档 | 内容 |
|---|---|
| [在线文档站](https://reachpub.github.io/ReachReel/zh-CN/) | 可检索的中英双语网页文档（默认英文，`/zh-CN/` 为简体中文） |
| [文档索引](docs/README.md) | 仓库文档导航与双语维护规则 |
| [使用手册](docs/zh-CN/USER_GUIDE.md) | 安装、素材准备、命令、输出、调参、返工、常见问题 |
| [配置参考](docs/zh-CN/CONFIGURATION.md) | 所有 `config.toml` 字段、默认值、环境变量、优先级 |
| [BGM](docs/zh-CN/BGM.md) | 格式、选择、本地目录策略、授权说明 |
| [语言与音色规范](docs/zh-CN/LANGUAGES.md) | BCP 47 标签、语言维度与已支持配音音色 |
| [开发文档](docs/zh-CN/DEVELOPMENT.md) | 架构、模块、数据格式、算法、扩展点、测试 |
| [桌面版与多前端架构](docs/zh-CN/DESKTOP_ARCHITECTURE.md) | 本地服务契约、任务模型、事件协议、引擎抽象、跨平台与打包 |
| [多语言与本地化](docs/zh-CN/LOCALIZATION.md) | 代码英文、界面 i18n、文档中英方案、成片语言 |
| [术语表](docs/GLOSSARY.md) | 中英术语对照，翻译与写作必读 |
| [路线图](docs/zh-CN/ROADMAP.md) | 还需要完善什么，按优先级列出 |
| [变更记录](CHANGELOG.zh-CN.md) | 版本变化与已修复问题 |

---

## 面向开发者

```text
视频 → FFmpeg 镜头检测 → Ollama qwen3-vl 画面理解
     → 本地 LLM 生成旁白 → Kokoro-82M (MLX) 配音
     → mlx-whisper 语音边界 → BGM 闪避混音 → FFmpeg 渲染 → QA 闸门
```

```text
reachreel/
├── src/reachreel/
│   ├── cli/            # 命令行入口与子命令
│   ├── core/           # 配置、子进程/JSON 工具、共享缓存
│   ├── domain/         # 领域模型、旁白时间轴
│   ├── engines/        # VLM / TTS 引擎适配
│   ├── generation/     # 解说词生成
│   ├── i18n/           # 界面语言协商与语言包加载
│   ├── locales/        # en.toml、zh-CN.toml 界面文案
│   ├── media/          # 镜头、字幕、混音、渲染
│   ├── pipeline/       # 流水线编排、缓存、QA
│   └── prompts/        # 按语言拆分的提示词包（zh/、en/）
├── inputs/             # 本地输入（被 Git 忽略）：bgm/、fonts/、models/
├── docs/               # 双语文档（docs/en/、docs/zh-CN/）
├── tests/              # 单元测试与集成测试
├── config.example.toml
├── mkdocs.yml
└── pyproject.toml
```

---

## 当前限制

- 只支持 **Apple Silicon macOS**；TTS 与 Whisper 依赖 MLX。
- VLM 逐镜头抽关键帧理解画面，**不是分析完整视频流**，极快的镜头内动作可能描述不全。
- 解说由本地 LLM 生成，**可能过度解读画面**；正式发布前建议过一遍 `script.json`。
- 字幕按句/行对齐 Whisper 语音边界，暂不支持逐字卡拉 OK 时间轴。
- BGM 目前从 `inputs/bgm/` 随机选曲，暂不按情绪自动匹配。
- 仅导出 H.264/AAC，暂不支持 HDR、10-bit、硬件编码或竖屏智能重构图。
- 桌面图形界面、批量文件夹监听与剪映/B站/YouTube 发布集成见[路线图](docs/zh-CN/ROADMAP.md)。

---

## 许可

代码基于 Apache-2.0 许可开源。BGM 请自行确认授权；格式、选择与授权说明见 [BGM](docs/zh-CN/BGM.md)。
