[English](CHANGELOG.md) | **简体中文**

# 变更记录

## [Unreleased]

### 变更

- 项目许可证由 MIT 改为 Apache-2.0；补充完整许可证文本和 `NOTICE` 文件。

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
