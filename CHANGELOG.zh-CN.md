[English](CHANGELOG.md) | **简体中文**

# 变更记录

## [Unreleased]

### 新增

- 新增说话风格预设 `style_preset` / `--style-preset`（`oral`、`casual`、`literary`、`documentary`、`commentary`，默认 `oral` 真人口语）、自定义风格覆盖 `style` / `--style`，以及口语润色开关 `script_oral_polish` / `--oral-polish` / `--no-oral-polish`，配合更具体口语化的 VLM 看片提示词（`vf-vlm-3`）减少字幕与台词的书面腔。
- 基于 `mlx-audio` 接入 `Qwen3TTSEngine`（`tts_engine = "qwen3"` / `--tts qwen3`），支持 `CustomVoice` 预设音色（`Serena`、`Vivian`、`Uncle_Fu`、`Dylan`、`Eric`、`Ryan`、`Aiden`）加自然语言 `tts_instruct` 控制、`VoiceDesign` 音色设计，以及 `Base` 模型零样本声音克隆（`tts_ref_audio` / `tts_ref_text`）。
- 支持完整的声音与声学参数配置：支持配置文件 `[tts]` 表或顶级 `tts_*` 键、CLI 参数（`--qwen3-model`、`--tts-speed`、`--tts-instruct`、`--tts-ref-audio`、`--tts-ref-text`、`--tts-temperature`、`--tts-top-p`、`--tts-top-k`、`--tts-repetition-penalty`、`--tts-seed`、`--tts-chunk-pause`、`--tts-pitch`、`--tts-volume`）、`script.json` 片段级 `voice`/`instruct`/`speed` 覆盖，以及 Kokoro 逗号混音与 `reachreel voices` 命令。
- 支持英文旁白、英文配音与英文字幕（`en-US` 与 `en-GB` 在 `src/reachreel/languages.py` 中升级为 `supported`）。运行 `reachreel run video.mp4 --content-language en`（或 `--tts-language en-GB`）时会自动解析整套英文配置：口播估算语速（`14.5` 字符/秒）、默认 Kokoro 音色（`af_heart` / `bf_emma`）、默认 macOS `say` 音色（`Samantha` / `Daniel`）、Whisper `en` 提示、字幕行宽（`42` 字符）、拉丁字体（`Helvetica Neue`）以及软字幕轨元数据（`language=eng`）。
- 在 `pyproject.toml` 新增无 torch 的可选依赖 `tts-en`（`spacy`、`num2words`、`espeakng-loader`、`phonemizer-fork`），与 `mlx-audio` 非 transformer 的 `KokoroPipeline`（`trf=False` + `en_core_web_sm` + `EspeakFallback`）一致；同时提供中文语言组 `tts-zh`。
- `_split_for_tts` 适配英文切段（按句末/从句标点与空格切分，单段上限 250 字符，不再在 70 字符处腰斩单词），`media/subtitles.py` 支持按英文句读切分与双行均衡换行。
- `reachreel doctor` 支持 `--content-language`、`--tts-language`、`--voice` 与 `--tts`，会校验音色与语言是否一致，并按目标语言检查 G2P 依赖（含 `en_core_web_sm`）及给出可执行安装命令。
- 按 `docs/zh-CN/LANGUAGES.md` 第 6 节完成 `en-US` 与 `en-GB` 验证：Whisper 回环 CER 为 0.00%（`af_heart`、`am_michael`、`bf_emma`）和 0.51%（`bm_george`），尼亚加拉瀑布 1080p 英文全流程实跑 QA 20/20 通过（`pass=20 warn=0 fail=0`）。
- 新增中英双语 MkDocs Material 文档站（`mkdocs.yml`、`scripts/mkdocs_hooks.py`、`pyproject.toml` 中的 `docs` extra），默认英文在站点根路径、简体中文在 `/zh-CN/`，并通过 `.github/workflows/docs.yml` 在构建时从 `README.md` / `README.zh-CN.md` 复制生成 `index.md`、以严格模式构建并部署到 GitHub Pages。
- `[tool.apdev] pypi_repository = "reachreel"` 让 `apdev release` 使用 `~/.pypirc` 的 `[reachreel]` 段：本项目走自己的 PyPI 账号，其他仓库继续用默认段。需要 `apdev >= 0.2.6`；DEVELOPMENT 里写清了优先级、首次上传决定归属，以及 `TWINE_REPOSITORY` 兜底方式。
- `pyproject.toml` 补齐 PyPI 页面所需元数据：`Project-URL`（主页、仓库、文档、变更记录、Issues）、11 条 Trove 分类、关键词与作者。`twine check` 对 wheel 与 sdist 均通过；许可证按 PEP 639 渲染（`License-Expression: Apache-2.0` 与 `License-File: LICENSE`/`NOTICE`），构建元数据版本为 2.5。

### 变更

- `speech_chars_per_second`、`voice`、`say_voice`、`subtitle_max_chars` 与 `subtitle_font` 在 `Settings` 和 `config.example.toml` 中的默认值改为 `0` / 空字符串，表示「跟随解析后的语言档案」（中文默认仍解析为 `3.9`、`zf_xiaoxiao`、`Tingting`、`18`、`PingFang SC`，显式配置始终优先）。

### 修复

- `dev` extra 里其实没有列 `pre-commit`，但 `.pre-commit-config.yaml` 和文档都告诉贡献者 `uv pip install -e '.[dev]'` 会带上它。现已重新加入（`pre-commit>=3.5`）。

## [0.3.0] - 2026-11-22

### 新增

- 新增 `src/reachreel/languages.py`：按 `docs/en/LANGUAGES.md` 建立的唯一语言档案登记表，把 BCP 47 标签映射到各语言的默认值（语速、字幕行字数、字体、引擎代码、依赖 extra）。Kokoro 的 `a`/`b`/`z` 等引擎代码只存在于这里。
- 新增 `tts_language`（BCP 47，`--tts-language`）与音色/语言护栏：不支持的语言标签、或音色语言与解析结果不一致时直接报错并列出已验证标签，而不是产出一段「能过 QA 但语言错了」的配音。`reachreel doctor` 会打印解析后的 `content` / `tts` / `asr` 三个维度与引擎代码。
- `doctor` 报告解析后的 TTS 语言，并把计划中的语言标为缺失。
- `docs/en/LANGUAGES.md` 与中文版定义了语言与音色规范：只使用 BCP 47 标签、四个独立的语言维度、唯一的语言档案登记表、引擎能力契约、按语言的依赖 extra，以及声明某语言「支持」前必须通过的验证门槛。目前只有 `zh-CN` 配音通过该门槛。
- `render` 在 `output_height = 0` 且素材短边大于 1080 时给出警告：这种跑法会静默保持 4K，文件明显变大，字幕在画面上也偏小。该问题由 Niagara 4K 素材的完整实跑发现。
- VLM 阶段的代表帧去重：每帧降采样成 16×16 灰度缩略图，与上一张保留帧区别过小就丢弃。新增配置 `vlm_frame_dedup`（默认 `4.0`，`0` 关闭）与 `--vlm-dedup`；静态长镜头现在只送 1 张而不是 3 张，阈值也计入 VLM 缓存 key。
- CI 把「Python 源码无 CJK」检查拆成独立步骤，违规时直接给出文件与行号，而不是埋在完整测试输出里。
- 新增提交信息校验：`scripts/check_commit_messages.py` 通过 `commit-message` pre-commit hook 与 CI 两条路径执行，检查 push 或 PR 新增的每个提交。
- 新增 `.pre-commit-config.yaml`，把 CI 的约定检查镜像到本地（`apdev check-imports`、`apdev check-chars`、`ruff`、提交信息），用 `pre-commit install` 安装；旧的 `.githooks` 目录已移除，用过它的 clone 需要执行一次 `git config --unset core.hooksPath`。
- 新增 `scripts/check_changelog.py`，在 CI 中校验 `CHANGELOG.md` 与 `CHANGELOG.zh-CN.md` 的结构一致性：版本标题、章节顺序、标题所用语言。某章节在一侧为空会失败；每章节条目数只作提示（一条长英文条目可以合法地拆成两条中文），`--strict` 才升级为错误。它当场发现了 0.1.0 里未翻译的 `### Added` / `### Known limitations`，现已改为中文。
- 开发工具改为 `apdev`（`apdev[dev]>=0.2.5`，APCore 系列共用工具包），通过 `dev` extra 与 `[tool.apdev]` 接入。`apdev check-chars` 配 `scripts/apdev_charset.json` 取代原来的 CJK 扫描：放行全角标点、汉字仍然非法；`apdev check-imports` 守护依赖方向无环。
- 新增 `scripts/check_config_docs.py`：每个 `Settings` 字段都必须出现在 `config.example.toml` 并在两份 `CONFIGURATION.md` 中有文档；未知键报错，默认值漂移只警告（`--strict` 才失败）。它第一次运行就发现 18 个 `[qa]` 阈值在 `config.example.toml` 里缺失，现已补齐。
- `ci.yml` 拆成 `checks`（`ubuntu-latest`：提交信息、变更记录一致性、Python 源码无 CJK）与 macOS `test` 两个 job：提交信息写错会在几秒内失败，而不是等 macOS runner 分配；单元测试矩阵也不再重复跑这些检查。
- 混音 filter graph 单元测试：`mix_filter_graph()` 抽成纯函数构造侧链图，覆盖仅旁白、仅环境音、仅 BGM、双路侧链、环境音音量为 0、压低比上下限。
- `tests/integration/test_scenes.py`：只依赖 FFmpeg 的 fixture，三色片段带 2s / 4s 已知切点用于 `detect_scenes`，另有无音轨 / 单声道 / 5.1 片段断言 `probe_video` 的声道数，以及 90° / 180° / 270° 片段断言编码尺寸与显示尺寸。
- `tests/integration/test_timeline.py`：覆盖真实 WAV 上的旁白 `atempo` 适配，以及本来就能放下的时间轴不被改动。
- `tests/integration/test_smoke.py::OnlyReuseTest`：用产物哈希断言 `run --only tts`、`--only mix,render`、`--only qa` 都不会重跑前面的阶段。
- 时间轴边界单元测试：空列表、无场景、未知场景 id、旁白超长告警、以及片段短于 `tail_padding` 的情况。

- 补打 `v0.1.0`（`b8a60ae`）与 `v0.2.0`（`c9c3652`）两个 tag，并各建一个 GitHub release，附上从对应提交构建的 wheel 与 sdist。两者都标注为 retroactive，因为当时既没打 tag 也没对外发布过。

### 变更

- `asr_language` 默认改为空，含义是「跟随 `content_language` 的主语言子标签」；中文内容仍解析为 `zh`，所以对已验证的链路没有行为变化。
- 硬字幕默认改为白字 + 黑描边 + 轻微阴影（`BorderStyle=1`，`subtitle_render = "outline"`），不再使用遮住大片画面的半透明黑底色块。描边宽度约为 `subtitle_size` 的 6%，可用 `subtitle_outline` / `--subtitle-outline` 覆盖。
- `subtitle_render = "box"`（或 `--subtitle-render box`）可恢复旧观感，其内边距现在可用 `subtitle_box_padding` / `--subtitle-box-padding` 调整；`subtitle_render = "none"` 输出纯白字、无描边无阴影。已在 `render_settings` 中记录过的旧工作目录沿用原模式。
- 支持平台收窄为 Apple Silicon 上的 macOS 14+；不支持 Intel macOS，CI 固定使用当前 arm64 标签。
- `reachreel doctor` 增加主机架构检查：非 Apple Silicon 机器上的 `平台` 一项会直接判失败，而不是默默跑出很慢的结果。
- 最低 Python 版本提高到 3.11（`mlx-audio` 的硬性要求），并移除 `tomli` 回退；Python 3.10 已无法安装本包。

### 修复

- CI 改用 `macos-15`：`macos-14` 标签已下线，排队任务会报错 “The configuration 'macos-14-arm64-us-xl' is not supported”。
- `render --crf` 之前只是被解析、从未生效；现在和 `--height` / `--fps` 一样写入 `Settings.crf`，并有测试断言 manifest 的 `render_settings` 会被复用、同时 CLI 参数优先。

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

### 新增

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

### 已知限制

- 仅支持 Apple Silicon macOS；TTS 依赖 MLX。
- VLM 逐镜头看关键帧，不是完整视频理解。
- 字幕是段级/行级时间戳，不是逐字卡拉 OK。
- BGM 随机选择，不按情绪匹配。
- 尚无 GUI、批量队列与发布集成。
