[English](CHANGELOG.md) | **简体中文**

# 变更记录

## [Unreleased]

## [0.2.0] - 2026-11-22

### 新增

- CI 工作流（`.github/workflows/ci.yml`）：在 macOS 上对 Python 3.10 和 3.12 运行 Ruff、单元测试和 `uv build`。
- 可选的集成 smoke 测试（`tests/integration/test_smoke.py`）：通过真实 CLI 用 macOS `say` 覆盖 `tts -> subtitle -> mix -> render -> qa`。
- 为 Python 3.10 增加 `tomli` 回退，用于读取 TOML 语言目录和配置。
- `manifest.json` 新增 `quality_flags`，记录不中断流程的降级（VLM 场景失败、TTS 段失败、ASR 回退、QA 警告）。
- TTS 单段重试与失败隔离；单段失败会跳过，全部失败才明确报错中断。
- VLM 和 Whisper 调用增加退避重试。
- 可选的 `inputs/bgm/manifest.json` 曲目目录；`manifest.json` 记录 `bgm_metadata`，`reachreel licenses <workdir>` 导出 `licenses.md`。
- `VideoInfo` 在显示宽高之外新增 `coded_width` / `coded_height`。
- 可选的平台缓存目录共享缓存：VLM 按视频指纹复用、TTS 按内容复用，并提供 `reachreel cache info` / `cache clean`。
- 全局 `--cache-dir` / `--no-cache` 参数。
- `subtitle_preset` 平台安全区（`douyin` / `reels` / `shorts` / `landscape`）。
- `reachreel doctor --json`，输出机器可读的环境检查结果。
- 集成 smoke 改为在中文/空格路径下运行，混入 BGM，并烧录字幕。
- `run --review-script` 在脚本阶段停下并输出 `script.md` 供人工确认。
- 两遍 `loudnorm`（`two_pass_loudnorm`，失败回退单遍）与 BGM 结尾淡出（`bgm_fade_seconds` / `--bgm-fade`）。
- 字幕切分不再拆断英文单词；时间分配在句末/逗号后加入停顿加成。

### 变更

- 项目许可证由 MIT 改为 Apache-2.0；补充完整许可证文本和 `NOTICE` 文件。
- 对带旋转的素材，`--height` 在文档和日志中统一为显示高度。
- 每行字数和行数成为硬约束；QA 文本比对会忽略空白差异。
- 混音默认先测量再线性归一，会多一次纯音频处理。
- 共享缓存默认改用平台缓存目录（macOS 为 `~/Library/Caches/reachreel`，通过 `platformdirs`）；新目录不存在时继续复用旧 `~/.cache/reachreel`。

### 修复

- `docs/en/DEVELOPMENT.md` 和 `docs/zh-CN/DEVELOPMENT.md` 的测试路径已改为与 `tests/unit/` 目录一致。
- 复用 `scenes.json` 时不再保留过期的视频元数据；旋转和编码尺寸会用最新探测结果刷新。

### 验证

- `output/ski`（1080x1920，82s）：复用已有缓存完整跑一遍，墙钟 41s；QA 20/20 通过。
- `output/niagara`（1920x1080，93s）：复用已有缓存完整跑一遍，墙钟 56s；QA 20/20 通过。
- `--review-script` 在 ski 工作目录生成 `script.md`，未重跑 VLM 或 TTS。

## [0.1.0] - 2026-11-22

### Added

- 本地视频解说流水线：
  - FFmpeg 镜头检测、抽帧；
  - Ollama `qwen3-vl` 画面理解；
  - Ollama `qwen3` 写旁白，按语言加载 prompt pack；
  - Kokoro-82M / MLX 中文配音，保留 macOS `say` 兜底；
  - `mlx-whisper` 语音边界 + 原稿字幕，输出 SRT/ASS；
  - BGM / 原声 / 旁白侧链闪避；
  - `ffmpeg-full` + libass 硬字幕；
  - H.264/AAC 成片。
- CLI：`run`、`analyze`、`script`、`tts`、`subtitle`、`mix`、`render`、`qa`、`doctor`。
- 场景、VLM、脚本、TTS 缓存可恢复；`manifest.json` 记录设置、产物与 QA。
- QA 闸门：流、时长、响度/真峰值、削波、黑帧/冻结帧、字幕布局/超框、旁白对齐。
- 双语 CLI/UI：`--lang en|zh-CN` 与 `REACHREEL_LANG`。
- 成片语言独立：`--content-language zh|en` 与 `REACHREEL_CONTENT_LANGUAGE`。
- 语言 prompt 包位于 `src/reachreel/prompts/{zh,en}`。
- 本地输入根目录 `inputs/`（被 Git 忽略）：`inputs/bgm/` 放背景音乐；`inputs/fonts/` 与 `inputs/models/` 为未来预留。
- 双语文档：`README.md` / `README.zh-CN.md`，以及 `docs/en/` 与 `docs/zh-CN/`。
- `src/` 包结构：`cli`、`core`、`domain`、`engines`、`generation`、`i18n`、`locales`、`media`、`pipeline`、`prompts`。

### Known limitations

- 仅支持 Apple Silicon macOS；TTS 依赖 MLX。
- VLM 逐镜头看关键帧，不是完整视频理解。
- 字幕是段级/行级时间戳，不是逐字卡拉 OK。
- BGM 随机选择，不按情绪匹配。
- 尚无 GUI、批量队列与发布集成。
