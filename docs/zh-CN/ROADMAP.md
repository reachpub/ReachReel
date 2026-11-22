[English](../en/ROADMAP.md) | **简体中文**

# 路线图

本文回答两个问题：

1. 现在这个版本已经能做什么；
2. 后面还需要补什么，按什么优先级。

> 桌面面板、跨平台与引擎抽象的完整设计见 [桌面版与多前端架构](DESKTOP_ARCHITECTURE.md)。本文只记录优先级与验收标准。
> 代码英文、界面 i18n 与成片多语言的策略见 [多语言与本地化](LOCALIZATION.md)。

---

## 0. 当前状态

已经能稳定跑通：

- 本地镜头检测 + 抽帧；
- Ollama VLM 逐镜头画面理解；
- 本地 LLM 写中文旁白；
- Kokoro 中文配音；
- mlx-whisper 语音边界 + 原稿字幕；
- BGM / 原声 / 旁白侧链闪避；
- 硬字幕 / 软字幕；
- 横屏 1080p、竖屏 1080×1920；
- 缓存复用、manifest、基础 doctor、单元测试；
- 阶段级命令：`script` / `tts` / `subtitle` / `mix` / `render` / `qa`；
- QA 闸门：流/时长/响度/真峰值/削波/黑帧/冻结帧（静态长镜头已调优）/字幕像素超框/旁白与字幕逐句偏差/旁白速度；阈值可配置。

已在两条真实素材上验证：

- 82s 室内滑雪竖屏；
- 93s 尼亚加拉瀑布横屏 4K 输入 → 1080p 输出。

---

## 0.1 最近完成

- **P0-2 阶段级命令（已完成）**：`script` / `tts` / `subtitle` / `mix` / `render` / `qa`，已用 6s 素材走通 `script → tts → subtitle → mix → render → qa`；`run --only vlm,script` 与 `run --dry-run` 已可用。
- **P0-5 QA 闸门（已完成）**：`reachreel qa`，输出 `qa.json`；`run` 默认自动执行；`--strict` 警告也失败。新增字幕像素超框、旁白/字幕逐句偏差、`astats` 削波检查、`[qa]` 阈值配置、静态长镜头冻结误报调优。
- **字幕布局（已完成）**：硬字幕改为 `subtitles.ass`，`PlayRes` 等于输出分辨率，`subtitle_size` / 边距按真实像素；竖屏超框问题已修复。
- 旋转/竖屏显示分辨率修正。
- `render` 复用 `manifest.render_settings`。

---

## P0：正确性与可用性（下一步优先）

这些不解决，功能越多越容易出问题。

### P0-1 旋转/竖屏元数据完整贯通

- 现状：`probe_video` 已读取 rotation 并交换显示宽高；渲染依赖 FFmpeg 自动旋转。
- 待办：
  - 加测试：90°/270° 旋转、竖屏、方形；
  - `--height` 的语义在文档/日志中统一为“显示高度”；
  - `manifest` 同时记录编码宽高和显示宽高。

**验收**：竖屏 iPhone 素材日志显示 1080×1920，输出 1080×1920，`manifest` 一致。

### P0-2 阶段级命令（已完成）

```bash
reachreel script   output/xxx
reachreel tts      output/xxx
reachreel subtitle output/xxx
reachreel mix      output/xxx
reachreel render   output/xxx
reachreel qa       output/xxx

reachreel run video.mp4 --workdir output/xxx --only vlm,script
reachreel run video.mp4 --workdir output/xxx --only vlm,script --dry-run
```

`--only` 支持 `probe` / `vlm`（`analyze`）/ `script` / `tts` / `subtitles`（`subtitle`）/ `mix` / `render` / `qa`，按流水线顺序执行；依赖缺失时会给出下一步命令。`--dry-run` 只读取 `ffprobe` 和现有缓存状态，不调用模型、不写文件。

**验收**：改文案不必重跑 VLM；改字幕不必重跑 TTS；每步可单独重试。已通过。

### P0-3 错误处理与重试

- VLM / LLM / TTS / Whisper 都要有明确的 retry 策略和失败提示；
- 部分场景失败时，允许继续并标记 `quality_flags`；
- TTS 单段失败不能导致整条流水线崩溃；
- FFmpeg 失败时保留完整命令和 stderr。

**验收**：断网/Ollama 挂掉/模型不存在时，报错能直接指出“哪一步、哪个文件、怎么修”。

### P0-4 字幕安全区与断句

- 已完成：像素字号/边距、`PlayRes` 与输出一致的 ASS 硬字幕、字幕像素超框 QA；换行丢字已修复。
- 现有：字幕切分优先标点；时间仍按字数比例分配，可能把半句话的时间拆开。
- 待办：
  - 竖屏/横屏平台安全区预设（抖音/Reels/Shorts）；
  - 断句时间更贴合语音停顿；
  - 单条字幕不超过 2 行、每行不超过 N 字的硬约束；
  - 旧工作目录的字幕样式迁移提示。

**验收**：随机抽 20 条字幕，无丢字、无超框、无半句断开。

### P0-5 QA 闸门（已完成）

已实现：

- 成片/流/时长/音视频同步；
- 综合响度、真峰值；
- `astats` 峰值 / 满幅样本占比 / Flat factor 削波检查；
- 黑帧、冻结帧；冻结帧会对比源片运动量，静态长镜头不再误报；
- 字幕条数、每行字数、行数、时间、重叠、边界、单条时长；
- 字幕实际渲染超框检测：按真实 libass 样式渲染到黑底，用 `bbox` 测像素边距；
- 旁白与字幕逐句文本/时间偏差；
- 旁白速度、旁白结束位置；
- BGM 文件、硬/软字幕模式；
- 输出 `qa.json`；`run` 默认执行；`qa --strict` 警告也失败；
- 阈值通过 `[qa]` 配置；`qa --config` 可覆盖；`qa.json` 记录实际阈值。

**验收**：好片返回 0，坏片能定位到具体检查项；阈值调参不再改代码。

### P0-6 测试与 CI

- 场景检测 fixture；
- 字幕不丢字 property test（已完成）；
- 时间轴边界测试（已完成基础版）；
- 混音 filter graph 单元测试；
- `render` 复用 manifest 设置；
- 阶段命令 smoke test（已完成手动版）；
- QA SRT 解析/退出码测试（已完成）；新增 QA 阈值、astats 解析、字幕边距、逐句对齐、`--only` 解析测试；
- `run --only` / `--dry-run` smoke test（已完成基础版）；
- 无音轨/单声道/5.1/旋转；
- GitHub Actions：macOS runner + lint + unit tests（不跑大模型）。

**验收**：PR 必须过 lint + tests；核心路径有回归测试。

### P0-7 缓存键与跨工作目录复用

- 现在 VLM/TTS 缓存按 `workdir` 局部保存；
- 待办：
  - 视频内容 hash + 模型 + prompt 版本作为全局缓存键；
  - 可选 `~/.cache/reachreel/`；
  - 同一素材换项目目录也能复用；
  - 缓存清理命令。

### P0-8 BGM 元数据

- 现在只记录文件路径；
- 待办：
  - `inputs/bgm/manifest.json`：曲名、作者、来源 URL、许可证、时长、情绪标签；
  - 渲染时把 BGM 来源写进 `manifest.json`；
  - 发布前一键导出“素材授权清单”。

### P0-9 跨平台正确性（面板之前必须完成）

详见 [DESKTOP_ARCHITECTURE.md §9](DESKTOP_ARCHITECTURE.md)。

- `util.run` 子进程加 `encoding="utf-8"`（Windows 中文路径会 `UnicodeDecodeError`）；
- 路径改用 `platformdirs`，去掉 CWD 依赖（`.app` / `.exe` 里 `output/` 必然失败）；
- 拆 extras（`accel-mlx` / `cpu` / `gui`），剔除未使用的 `torch`（580 MB）；
- 随包分发子集化中文字体 + `fontsdir`，保证三平台字幕渲染一致、QA 不误报；
- FFmpeg 探测优先使用「应用自带」二进制；
- `doctor --json`，供面板渲染可用性矩阵。

**验收**：非 ASCII 文件名与含空格路径在三个平台都能跑到 `final.mp4`。

### P0-10 代码语言与字符串外置（面板之前必须完成）

详见 [LOCALIZATION.md](LOCALIZATION.md)。

- 代码注释/日志/CLI/错误已英文化；Python 源码 0 CJK，中文仅在 `prompts/` 与 `locales/`，可开 CI 的 CJK lint；
- `i18n.py` + `locales/{en,zh-CN}.toml`；用户可见字符串外置为 `t()`；
- 事件/错误改为「code + params + 当前语言 msg」，使 CLI 与面板语言可不同；
- 文档双语已落地：README、CHANGELOG、用户文档、工程文档全部中英两份；用户文档中文权威，工程文档英文权威。

详见 [LOCALIZATION.md §4](LOCALIZATION.md)（分流表 / 迁移步骤 / banner / 同步规则 / CI / 工作量）。

**验收**：`--lang en` 与 `--lang zh-CN` 下 `doctor` / 错误提示 / 面板文案各自正确；两份 catalog key 一致。

---

## P1：质量提升（决定成片好不好看）

### P1-1 TTS 升级

- 接入 Qwen3-TTS、IndexTTS2、Ming-3.0 TTS；
- 音色试听/选择；
- 多音字/专有名词发音词典；
- 情绪/语速按段落控制；
- 句尾停顿更自然；
- 可选声音克隆（需明确授权）。

**目标**：中文自然度明显超过 Kokoro，接近商业解说。

### P1-2 VLM 看片升级

- 不是均匀抽帧，而是按“画面变化/运动强度”选代表帧；
- 镜头内动作识别（起跳、转身、水流方向）；
- 去重：相似镜头合并描述；
- 支持视频片段输入（Qwen3-VL video / 其他本地视频模型）；
- 画面主体跟踪，减少“游客”“孩子”这种泛称。

**目标**：12 个场景的摘要不再千篇一律，能写出具体动作和空间关系。

### P1-3 写稿质量

- 事实约束：每个句子必须能追溯到某个 `scene_id` 的 VLM 证据；
- 专有名词白名单（来自 `brief`）；
- 风格预设：Vlog / 纪录片 / 吐槽 / 治愈 / 悬疑；
- 开头 3 秒 Hook；
- 段落时长预算：按 TTS 实际时长反推字数，而不是只按目标字数；
- 人工审核 gate：先出 `script.md`，确认后再配音。

### P1-4 音频质量

- 两遍 `loudnorm`（先测量再线性归一），减少动态压缩；
- BGM 结构感知闪避（前奏/副歌不同深度）；
- 原声降噪/去风噪；
- Demucs 分离人声/环境声，避免原片说话与旁白打架；
- 片尾 BGM 淡出；
- 可选“只保留环境声，去掉原片人声”。

### P1-5 字幕升级

- 逐字/逐词高亮（卡拉 OK）；
- 双语字幕；
- 字幕模板：底部条、顶部标题、人物名牌；
- 竖屏安全区预设（抖音/Reels/Shorts）；
- SRT/ASS 双输出；
- 字幕编辑器（本地 Web）。

### P1-6 渲染升级

- `h264_videotoolbox` / `hevc_videotoolbox` 硬件编码；
- 4K / 10-bit / HDR（需要 tone mapping）；
- 竖屏智能重构图（人脸/主体跟踪）；
- 片头/片尾/标题卡/进度条；
- 转场（淡入淡出、交叉溶解）；
- 封面图导出；
- 多平台母版预设（抖音/小红书/B站/YouTube）。

### P1-7 批量与项目管理

- `reachreel batch ./videos/`；
- 监听文件夹，自动处理新素材；
- 项目文件（`project.json`）保存素材、文案、样式、BGM；
- 断点续跑；
- 失败任务队列；
- 处理进度/ETA。

---

## P2：平台与产品化

### P2-1 跨平台

- Windows / Linux；
- CUDA / ROCm / CPU 后端；
- 非 Apple TTS：Piper、Kokoro ONNX、Edge-TTS（可选云）；
- 非 Apple VLM：Ollama 跨平台即可，但要测试。

### P2-2 图形界面

**已定方案：本地服务（`reachreel serve`）+ 浏览器面板**，后期用 Tauri 外壳复用同一前端。
完整的任务模型、事件协议、API 契约与 v1 范围见 [DESKTOP_ARCHITECTURE.md](DESKTOP_ARCHITECTURE.md)。
不要用 Gradio/Streamlit，也不要做时间线编辑器。

- 素材导入；
- 场景/关键帧预览；
- 文案编辑 + 重新配音；
- 字幕编辑；
- BGM 选择/试听；
- 一键渲染；
- 任务队列。

### P2-3 分发

- `pipx install reachreel`；
- Homebrew formula；
- macOS `.dmg` / 菜单栏 App；
- Docker（CPU/GPU）；
- 模型下载管理器（Kokoro/Whisper/Ollama）。

### P2-4 Provider 插件化

- `providers/` 目录；
- VLM / LLM / TTS / ASR / Renderer 接口；
- entry points 插件；
- 配置 schema 校验；
- 第三方 provider 示例。

### P2-5 发布集成

- 导出到剪映/CapCut 草稿；
- 导出到 Premiere/FCPXML；
- 直接发布到 YouTube/B站/抖音（需用户授权）；
- 生成标题/简介/标签。

---

## 不建议现在做

这些会显著增加复杂度，但当前收益低：

- 本地音乐生成模型（速度慢、质量不稳定，BGM 用 CC0 更稳）；
- 口型同步 / 数字人；
- 完整非线编辑时间线；
- 云渲染/云 TTS（违背“本地优先”）；
- 自研视频生成模型。

---

## 版本里程碑建议

### v0.2 — 可交付

- P0-1 ~ P0-4；
- 阶段级命令；
- 字幕安全区；
- 基础 QA；
- 测试补齐。

### v0.3 — 好看

- TTS 升级；
- VLM 代表帧/去重；
- 写稿风格预设 + 人工审核；
- 两遍 loudnorm；
- 字幕模板；
- 竖屏预设。

### v0.4 — 好用

- 批量/监听目录；
- 本地 Web UI；
- 项目文件；
- 模型管理；
- 跨平台初步支持。

### v1.0 — 可分发

- 插件化 provider；
- Homebrew/DMG/pipx；
- 完整文档 + 示例素材；
- 稳定的 QA/回归测试；
- 明确的许可证/素材合规流程。

---

## 贡献优先级

如果你想参与，最欢迎的方向：

1. **测试**：场景检测、字幕、时间轴、混音的回归测试；
2. **字幕**：断句、安全区、逐字高亮；
3. **TTS provider**：Qwen3-TTS / IndexTTS2 / Piper；
4. **VLM 代表帧**：运动/变化检测选帧；
5. **QA**：黑帧、冻结、削波、超框；
6. **文档**：更多真实素材的调参案例。

提交前请先跑：

```bash
uvx ruff check src/reachreel tests
python -m unittest discover -s tests -v
reachreel doctor
```
