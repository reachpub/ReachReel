[English](../en/LANGUAGES.md) | **简体中文**

# 语言与音色规范

本文定义 ReachReel 如何命名、校验和扩展语言。新增语言、音色或 TTS 引擎前请先读这里；
界面文案的多语言由 [LOCALIZATION.md](LOCALIZATION.md) 负责，是另一个问题。

## 1. 命名规则

- 所有对外的语言值使用 **BCP 47 标签**（RFC 5646），与浏览器上报的格式一致：
  `en`、`en-US`、`en-GB`、`zh-CN`、`ja-JP`、`pt-BR`。
- 输入宽容、输出规范：`en_us`、`EN-us`、`zh_CN.UTF-8` 都接受，统一规范化为
  `en-US` / `zh-CN`（主语言子标签小写，区域子标签大写）。
- **引擎内部代码不得出现在适配层之外。** Kokoro 的 `a` / `b` / `z` / `j`、
  Whisper 的 `zh` 等缩写只能存在于 `engines/` 内部，不能出现在 `config.toml`、
  CLI 参数、日志、`doctor` 输出或文档里。
- 不允许静默回退。不支持的语言直接报错并列出支持列表；悄悄退回中文，就是错语言
  配音流入成片的原因。

为什么内部代码必须藏起来：Kokoro 的单字母是为了区分美式（`a`）与英式（`b`）英语，
`en` 本身表达不了这层区别；而且它的别名表并不完整（`zh` 可用，`zh-CN` 会直接
`AssertionError`）。做映射是适配层的责任，不是用户的责任。

## 2. 四个语言维度

| 配置项 | 作用范围 | 示例 | 使用方 |
|---|---|---|---|
| `ui_language` | CLI 文案与日志 | `zh-CN` | `i18n` 语言目录 |
| `content_language` | 稿子与字幕文本 | `zh` / `en` | `prompts/<主语言>/`、字幕切分 |
| `tts_language` | 配音的发音语言 | `zh-CN` | TTS 引擎 |
| `asr_language` | Whisper 转写语言 | `zh` | `media/subtitles.py` |

规则：

1. 显式设置优先；未设置时，`tts_language` 与 `asr_language` 跟随
   `content_language` 的主语言子标签。
2. `tts_language` 必须与所选音色的语言一致。不一致按错误处理，因为音频照样会生成、
   QA 照样通过，只是听起来是错的。
3. `asr_language` 只是**转写**提示，不决定输出语言：它只影响时间对齐，因此在配音
   语言与稿子语言不同时可以单独设置。

## 3. 语言档案

唯一的登记表位于 `src/reachreel/languages.py`，每种语言是一个档案：

| 字段 | 用途 |
|---|---|
| `tag` | 规范化的 BCP 47 标签 |
| `primary` | 主语言子标签，用于选 prompt 包与传给 Whisper |
| `speech_chars_per_second` | 稿子长度目标，避免用中文标准去量英文 |
| `subtitle_max_chars` | 行字数默认值，中文约 18，空格语言约 42 |
| `font_hint` | 覆盖该文字系统的字体 |
| `default_voice` | `voice` 留空时的默认 Kokoro 音色 |
| `default_say_voice` | `say_voice` 留空时的默认 macOS `say` 调试音色 |
| `iso639_2` | 写入软字幕轨元数据的 ISO 639-2 代码（`chi`、`eng`） |
| `tts` | `{引擎: 内部代码}` |
| `requires` | 可选依赖 extra，例如 `tts-en` |

各版本的支持状态：

| 标签 | 配音 | 说明 |
|---|---|---|
| `zh-CN` | 支持 | Kokoro + `misaki[zh]`，已在真实素材上端到端验证 |
| `en-US`、`en-GB` | 支持 | Kokoro + 无 torch 的 `tts-en` extra（`spacy` + `en_core_web_sm` + `espeakng-loader` + `phonemizer-fork`）；已端到端验证（Whisper 回环 CER 0.00%–0.51%，尼亚加拉瀑布 1080p QA 20/20） |
| `ja-JP` | 计划中 | `misaki[ja]` |
| `es-ES`、`fr-FR`、`hi-IN`、`it-IT`、`pt-BR` | 计划中 | espeak-ng G2P |

不在表内的标签一律拒绝，并在错误信息里列出支持列表。

## 4. 引擎契约

每个 TTS 适配器实现：

- `supported_tags() -> list[str]`
- `map_tag(tag) -> str | None` —— 引擎内部代码，不支持时返回 `None`
- `voice_language(voice) -> str | None` —— 由音色名推出的语言标签

当 `map_tag(配置值)` 与 `voice_language(音色)` 不一致时，流水线拒绝合成，错误信息同时
给出标签与音色名。`reachreel doctor` 会打印解析后的四个维度以及每种语言将使用的后端，
让依赖问题在跑 40 分钟之前就暴露。

## 5. 依赖策略

- 按语言组提供可选 extra：`tts-zh`、`tts-en`、`tts-ja`。由 espeak-ng 支撑的语言不需要
  额外依赖。
- 默认安装保持 torch-free。`mlx-audio` 的 `KokoroPipeline` 在英文 G2P 路径固定使用
  `trf=False`（`en_core_web_sm` + `EspeakFallback`），因此 `tts-en` extra 只安装
  `spacy`、`num2words`、`espeakng-loader` 与 `phonemizer-fork`，不带入
  `spacy-curated-transformers` 与约 600 MB 的 `torch`。安装 `reachreel[tts-en]` 后
  执行一次 `python -m spacy download en_core_web_sm` 即可。
- 只有第 6 节的验证在固定版本组合上通过，才能标记为支持。一个实测反例：在 numpy 2.5 +
  `misaki[en]` 的环境里，拉入 transformer 版 spacy 模型（`en_core_web_trf`）会间歇性报
  `ValueError: Only length-1 arrays can be converted to Python scalars`，而无 transformer
  的 `en_core_web_sm` 组合在 `en-US` 与 `en-GB` 上均稳定通过。依赖状态不稳定，就不算「支持」。

## 6. 声明某语言「支持」的门槛

以下全部通过才可以，结果要写进 PR：

1. `reachreel doctor` 能解析四个维度与该语言的后端；
2. 回环验证：合成一句话与一段话，用同语言 Whisper 转写后比对；英文错误率超过 5%
   （中文超过 10%）即视为不支持；
3. 用真实素材按该语言跑通整条流水线，QA 20/20；
4. 渲染出帧检查字幕默认值（`subtitle_max_chars`、字体）是否合适；
5. 单测覆盖：档案条目、标签映射、音色/语言不匹配报错、不支持标签报错。

## 7. 第一个多语言版本的非目标

- 同一项目内混合语言旁白；
- 逐段切换语言；
- 从画面自动判断语言；
- 任意语言的音色克隆。
