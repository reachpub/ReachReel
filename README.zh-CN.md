[English](README.md) | **简体中文**

# ReachReel

**把一段原始视频，在本地做成带中文解说、字幕、配音和 BGM 闪避的成片。**

```text
视频 → FFmpeg 切镜头/抽帧 → Ollama qwen3-vl 看画面 → 本地 LLM 写解说
     → Kokoro-82M (MLX) 配音 → mlx-whisper 做时间戳
     → BGM + 原声 + 旁白 侧链闪避混音 → FFmpeg 烧字幕/封装
```

没有云 API，没有按分钟计费。第一次运行会从 Hugging Face 下载 Kokoro 和 Whisper 模型，之后可以离线跑。

> 当前状态：**v0.2.0**。已在两条真实素材上跑通：
> 室内滑雪竖屏 82s、尼亚加拉瀑布横屏 93s，均输出 1080p、硬字幕、BGM 闪避。

---

## 特性

- **本地看画面**：Ollama + `qwen3-vl`，逐镜头分析地点、主体、动作、细节、情绪。
- **本地写解说**：Ollama + `qwen3`，按时间顺序写中文旁白，支持风格、背景信息、旁白密度。
- **本地配音**：Kokoro-82M / MLX，8 个中文音色，生成 48 kHz 单声道旁白。
- **本地字幕**：`mlx-whisper` 检测语音边界；字幕文字优先用解说原稿，避免 ASR 同音错字。
- **BGM 闪避**：FFmpeg `sidechaincompress`，旁白出现时自动压低 BGM 和原声。
- **硬字幕**：`ffmpeg-full` + libass，生成 `PlayRes` 与输出一致的 ASS；字号/边距按真实像素，横屏/竖屏一致。
- **阶段级命令**：`script` / `tts` / `subtitle` / `mix` / `render` / `qa`，改文案或字幕不必整条重跑；`run --only vlm,script` 精确重跑，`--dry-run` 先看计划。
- **QA 闸门**：音视频流、时长、响度/真峰值、`astats` 削波、黑帧/冻结帧（对比源片排除静态长镜头）、字幕像素超框、旁白与字幕逐句偏差；阈值可用 `[qa]` 配置。
- **可恢复**：场景分析、VLM、解说、TTS 都有缓存；改文案只重做受影响的部分。
- **失败可降级**：VLM/TTS/Whisper 会重试或回退，降级写入 `manifest.json` 的 `quality_flags`，不直接中断整条流程。
- **授权可追踪**：可选的 `inputs/bgm/manifest.json` 元数据；`reachreel licenses <workdir>` 导出 `licenses.md`。
- **跨目录缓存**：VLM 分析与 TTS 音频通过平台缓存目录（macOS 为 `~/Library/Caches/reachreel`）复用；`--no-cache` 可关闭，`reachreel cache clean --yes` 可清理。
- **字幕预设**：`--subtitle-preset douyin|reels|shorts|landscape` 套用平台安全区；每行字数与行数是硬约束。
- **脚本确认闸门**：`run --review-script` 生成 `script.md` 后停下，先改脚本再花钱配音。
- **两遍响度**：先测量混音，再线性归一到达标 LUFS；BGM 在结尾自动淡出。
- **双语 CLI**：`--lang en|zh-CN` 切换界面；`--content-language zh|en` 独立选择旁白/字幕语言与提示词包。

---

## 快速开始

### 1. 环境

- Apple Silicon Mac（M1 及以上）
- Python 3.11 – 3.12
- Homebrew
- 建议 32 GB 内存以上；16 GB 也能跑，但把 `vlm_workers` 设为 1

```bash
brew install ffmpeg ffmpeg-full

python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
```

> Homebrew 的普通 `ffmpeg` 没有 libass，不能烧硬字幕。本项目会自动优先使用
> `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg`。

### 2. 本地模型

```bash
ollama pull qwen3-vl:8b-instruct
ollama pull qwen3:30b-instruct     # 也可以换 qwen3.5:9b / qwen3:8b
```

### 3. 自检

```bash
reachreel doctor
```

### 4. 放 BGM

把 CC0 / 无版权音乐放进 `inputs/bgm/`：

```text
inputs/bgm/
  morning-light.mp3
  mountain-air.wav
```

### 5. 跑

```bash
reachreel run /path/to/video.mp4 \
  --brief "拍摄于云南雨崩，清晨从雨崩村出发，途经冰湖" \
  --style "安静、克制，像独自徒步时的自言自语"
```

输出在 `output/<视频名>_<时间戳>/`，核心文件：

```text
final.mp4        # 成片
subtitles.srt    # 字幕（软字幕/兼容）
subtitles.ass    # 硬字幕布局（PlayRes = 输出分辨率）
script.json      # 分段解说词（可直接改）
scenes.json      # 镜头 + VLM 画面分析
timeline.json    # 旁白落点
narration.wav    # 纯旁白
mix.wav          # 旁白 + 原声 + BGM
manifest.json    # 本次参数与统计
review.md        # 文案/落点速览
qa.json          # 质量检查结果
licenses.md      # BGM 授权清单
```

质量检查与返工：

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
| [文档索引](docs/README.md) | 全部文档导航 + 文档语言规则 |
| [使用手册](docs/zh-CN/USER_GUIDE.md) | 安装、素材准备、命令、输出、调参、返工、常见问题 |
| [配置参考](docs/zh-CN/CONFIGURATION.md) | 所有 `config.toml` 字段、默认值、环境变量、优先级 |
| [BGM](docs/zh-CN/BGM.md) | 格式、选择、本地目录策略、授权说明 |
| [开发文档](docs/zh-CN/DEVELOPMENT.md) | 架构、模块、数据格式、算法、扩展点、测试 |
| [桌面版与多前端架构](docs/zh-CN/DESKTOP_ARCHITECTURE.md) | 本地服务契约、任务模型、事件协议、引擎抽象、跨平台与打包 |
| [多语言与本地化](docs/zh-CN/LOCALIZATION.md) | 代码英文、界面 i18n、文档中英方案、成片语言 |
| [术语表](docs/GLOSSARY.md) | 中英术语对照，翻译与写作必读 |
| [路线图](docs/zh-CN/ROADMAP.md) | 还需要完善什么，按优先级列出 |
| [变更记录](CHANGELOG.zh-CN.md) | 版本变化与已修复问题 |

> 语言策略：全部文档中英各一份；规则见 [LOCALIZATION.md §4](docs/zh-CN/LOCALIZATION.md)。
> 全部文档双语：英文在 `docs/en/`，中文在 `docs/zh-CN/`。命令、路径、配置键不翻译。

---

## 项目结构

```text
reachreel/
├── src/reachreel/
│   ├── cli/            # 命令行入口与子命令
│   ├── core/           # 配置、子进程/JSON 工具
│   ├── domain/         # 领域模型、旁白时间轴
│   ├── engines/        # VLM / TTS 引擎适配
│   ├── generation/     # 解说词生成
│   ├── media/          # 镜头、字幕、混音、渲染
│   └── pipeline/       # 流水线编排、缓存、QA
├── inputs/             # 本地输入（被 Git 忽略）
│   ├── bgm/            # 背景音乐
│   ├── fonts/          # 预留：自定义字幕字体
│   └── models/         # 预留：本地模型文件
├── docs/               # 文档索引/使用/配置/开发/桌面架构/本地化/术语/路线图
├── tests/              # 单元测试
├── config.example.toml
└── pyproject.toml
```

---

## 当前限制

- 只支持 **Apple Silicon macOS**；TTS 依赖 MLX。
- VLM 是逐镜头看关键帧，**不是看完整视频**；镜头内动作很快时可能描述不全。
- 解说由本地 LLM 生成，**可能过度解读画面**；正式发布前建议过一遍 `script.json`。
- 字幕时间戳来自 Whisper 检测的语音边界 + 原稿文字，**不是逐字卡拉 OK 时间轴**。
- BGM 目前随机选曲，**不会根据情绪自动匹配**。
- 只做 H.264/AAC，不支持 HDR、10-bit、硬件编码、竖屏智能重构图。
- QA 是启发式检查：字幕超框用像素包围盒而非 OCR，旁白/字幕按句对齐而非逐字对齐，不做内容事实校验。
- 还没有图形界面与批量队列；面板方案已定（本地服务 + 浏览器面板，见 [桌面版架构](docs/zh-CN/DESKTOP_ARCHITECTURE.md)），但尚未实现。
- 发布集成（剪映/B站/YouTube）未做。

更完整的待办见 [路线图](docs/zh-CN/ROADMAP.md)。

---

## 许可

代码 Apache-2.0。BGM 请自行确认授权；格式、选择与授权说明见 [BGM](docs/zh-CN/BGM.md)。
