[English](../en/LOCALIZATION.md) | **简体中文**

# 多语言与本地化

本文定义 ReachReel 的语言策略：**代码英文、界面可本地化、文档按类型分流、成片语言是独立产品能力**。

四者互不替代，混在一起做是最常见的返工原因。

相关文档：[桌面版与多前端架构](DESKTOP_ARCHITECTURE.md)、[开发文档](DEVELOPMENT.md)、[路线图](ROADMAP.md)。

---

## 1. 三层语言问题（先分开）

| 层 | 是什么 | 谁受影响 | 成本 | 何时做 |
|---|---|---|---|---|
| **L1 代码语言** | 标识符、注释、docstring、日志、错误 | 贡献者、维护者 | 低（一次性） | **现在** |
| **L2 界面语言** | CLI 帮助/输出、面板文案、错误提示 | 终端用户 | 中（脚手架 1 天，翻译 0.5 天） | **v1 之前** |
| **L3 成片语言** | 旁白/字幕/风格/配音/QA 的语言 | 终端用户 | **高**（3–5 天 + 调优） | v1 之后（P1） |

常见错误：把 L3 当成 L2 做（界面翻译成英文了，生成的旁白还是中文）；或者把 L2 拖到 L3 之后做（106 处字符串散落在 12 个文件里，后期改造要动每一个业务函数）。

---

## 2. L1：代码英文（无条件）

### 2.1 现状

```text
Python 源码中的 CJK：0 行
中文只存在于 src/reachreel/prompts/zh/ 与 src/reachreel/locales/zh-CN.toml
```

标识符、注释、日志、CLI help、错误信息已经英文化；prompt 与 fallback 文案作为语言数据放在 `prompts/<lang>/`，UI 文案放在 `locales/<lang>.toml`。

### 2.2 规则

| 对象 | 语言 | 说明 |
|---|---|---|
| 标识符、模块名、类/函数名 | 英文 | 已满足 |
| docstring、行内注释 | **英文** | 新代码强制；存量随改动顺手替换 |
| 日志消息 | **英文**（canonical） | 可 grep、可直接贴进 issue |
| 用户可见文案 | **不写死**，走 `t()` | 见 §3 |
| `locales/*.toml` | 各语言 | 唯一允许出现 CJK 的 Python 目录之外的文件 |

### 2.3 可执行的检查

CI 加一条（或 pre-commit）：

```bash
# 除 locales/、prompts/ 与测试夹具外，源码里不允许出现 CJK
! rg -n '[\p{Han}]' src/reachreel --glob '!src/reachreel/locales/**' --glob '!src/reachreel/prompts/**' --glob '!tests/fixtures/**'
```

这条规则的价值：防止新代码继续引入中文日志，让存量清理有明确的收敛终点。
**存量已清理，可以开启这条检查。**

---

## 3. L2：界面本地化

### 3.1 设计要点

1. **字符串外置**，不在业务代码里拼中文/英文；
2. **事件与错误带「代码 + 参数」**，不只是渲染好的句子（见 §3.4）；
3. **日志默认英文**，面板按客户端语言渲染；
4. 语言协商顺序：`--lang` > `REACHREEL_LANG` > `config.ui_language` > `LANG`/`LC_ALL` > `en`。

当前覆盖：CLI help、常用错误、阶段标签、dry-run、QA 报告、`review.md`、`doctor` 标签均已双语；日志保持英文 canonical。

### 3.2 模块

```python
# src/reachreel/i18n/__init__.py
def t(key: str, /, **params) -> str: ...          # 渲染当前语言文案
def set_language(value: str | None) -> str: ...   # 切换并加载语言包
def negotiate_language(explicit=None, config_language=None) -> str: ...
def supported_languages() -> tuple[str, ...]: ...
```

目录：

```text
src/reachreel/locales/
  en.toml        # canonical（新 key 先加这里）
  zh-CN.toml
```

```toml
[cli.run.help]
text = "Run the full pipeline"

[log.reuse_scenes]
text = "Reusing scene detection ({count} scenes)"

[error.ollama_unreachable]
text = "Cannot reach Ollama at {url}"
hint = "Start it with `ollama serve`, or set [llm].base_url"
```

用 TOML/JSON 字典而不是 gettext：本项目字符串量小（百级），字典文件可 diff、可 review。
当期贡献者多到需要 Poedit/Weblate 时再迁 `.po`，`t()` 调用点不用改。

### 3.3 CLI

```bash
reachreel --lang zh-CN run video.mp4
reachreel doctor --json          # 结构与语言无关，面板自己渲染
```

`argparse` 的 `help=` 全部走 `t()`；`--lang` 是全局参数（在 `ArgumentParser` 之前解析一次）。

### 3.4 事件与错误：带 code，不带成品句子

这是 [DESKTOP_ARCHITECTURE.md §5](DESKTOP_ARCHITECTURE.md) 事件协议的一条修订：

```jsonc
// ❌ 只有渲染好的句子：客户端语言与进程语言被焊死
{"type":"log","level":"warning","msg":"没有找到 BGM"}

// ✅ 代码 + 参数 + 进程语言的兜底渲染
{"type":"log","level":"warning","code":"bgm_not_found","params":{"dir":"inputs/bgm/"},"msg":"No BGM found in inputs/bgm/"}
```

- `code` + `params` 是**契约**（稳定，可测试）；
- `msg` 是当前进程语言下的渲染结果（可读性、CLI 直接用）；
- 面板优先用 `code` 查自己的 `locales/`，查不到才回退 `msg`；
- 错误同理：`{"error":{"code":"ollama_unreachable","params":{"url":...},"message":"...","hint":"..."}}`。

收益：同一台机器上，CLI 用英文、面板用中文，服务端日志保持英文，三者互不干扰。

### 3.5 工作量

| 项 | 估时 |
|---|---|
| `i18n.py` + catalog 结构 + `t()` 接入 | 0.5 天 |
| 106 处用户可见字符串外置 | 1 天 |
| `zh-CN` / `en` catalog 校对 | 0.5 天 |
| 事件/错误改造为 code+params | 0.5 天（与服务器化同批做） |
| CI key 一致性 + CJK lint | 0.5 天 |

**共约 3 天，必须在 v1 面板之前完成**。晚做的代价是每个新增 UI 元素都要改两遍。

---

## 4. 文档策略

### 4.1 结论：一份文档一种语言，目录分语言

不做逐段中英对照。同一文件里中英交替会让 diff 不可读，译文也会静默漂移。

正确做法：一份文档一种语言，独立文件，顶部放语言切换。

唯一例外：`docs/README.md` 是导航页，允许中英并列表格。

### 4.2 分流表

| 文档 | 语言 | 权威版本 |
|---|---|---|
| `README.md` / `README.zh-CN.md` | 英 / 中 | 英文 |
| `CHANGELOG.md` / `CHANGELOG.zh-CN.md` | 英 / 中 | 英文 |
| `docs/{en,zh-CN}/USER_GUIDE.md` | 英 / 中 | 中文 |
| `docs/{en,zh-CN}/CONFIGURATION.md` | 英 / 中 | 中文 |
| `docs/{en,zh-CN}/DEVELOPMENT.md` | 英 / 中 | 英文 |
| `docs/{en,zh-CN}/DESKTOP_ARCHITECTURE.md` | 英 / 中 | 英文 |
| `docs/{en,zh-CN}/ROADMAP.md` | 英 / 中 | 英文 |
| `docs/{en,zh-CN}/LOCALIZATION.md` | 英 / 中 | 英文 |
| `docs/README.md` | 中英并列 | 导航页 |
| `docs/GLOSSARY.md` | 中英对照 | 术语表 |
| `config.example.toml` 注释 | 英文 | 中文说明见配置参考 |

原则：**所有文档双语；命令、参数、路径、配置键、默认值、代码不翻译。**

### 4.3 目标结构

```text
README.md / README.zh-CN.md
CHANGELOG.md / CHANGELOG.zh-CN.md
docs/
  README.md
  GLOSSARY.md
  en/{USER_GUIDE,CONFIGURATION,DEVELOPMENT,DESKTOP_ARCHITECTURE,LOCALIZATION,ROADMAP}.md
  zh-CN/{USER_GUIDE,CONFIGURATION,DEVELOPMENT,DESKTOP_ARCHITECTURE,LOCALIZATION,ROADMAP}.md
```

**权威版本归属**（两边不一致时信谁）：

| 类别 | 权威版本 | 规则 |
|---|---|---|
| 用户文档 | **中文** | 英文为同步译文 |
| 工程文档 | **英文** | 中文为同步译文 |
| 代码字符串 | **`locales/en.toml`** | 新 key 先加英文，再补 `zh-CN.toml` |
| 提示词 | **按语言独立** | 不是翻译关系（见 §5.2） |

### 4.4 同步规则

| 规则 | 说明 |
|---|---|
| 同 PR 更新 | 改中文版必须同一 PR 改英文版，反之亦然 |
| 权威版本 | 用户文档中文权威；工程文档英文权威 |
| 命令不翻译 | 参数名、路径、默认值、配置键、代码不翻译 |
| 不追 100% 对齐 | 允许语言差异，但必须在开头注明 |
| 过期标记 | 宁可删掉过时段落，也不要留错误内容 |
| 评审 | 含双语文档的 PR 需要一名能读两种语言的人 review |

### 4.5 语言切换头

每份双语文档第一行固定为语言切换，且必须指向真实文件：

```markdown
<!-- docs/en/USER_GUIDE.md -->
**English** | [简体中文](../zh-CN/USER_GUIDE.md)

<!-- docs/zh-CN/USER_GUIDE.md -->
[English](../en/USER_GUIDE.md) | **简体中文**
```

规则：

- 当前语言用 **粗体**，不做链接；
- banner 与 `# 标题` 之间空一行；
- 不允许死链；
- `docs/README.md` 与 `docs/GLOSSARY.md` 不加 banner。

### 4.6 翻译工作流

```text
1. 先定稿权威版本
2. 机器翻译只作草稿（DeepL / GPT）
3. 人工校对：命令、参数、阈值语义、单位
4. 用 docs/GLOSSARY.md 统一术语
5. 通读渲染结果（表格、代码块、相对链接）
```

绝对不要：机器翻译直出；把命令/路径翻译掉；逐段中英对照。

### 4.7 CI 检查

```bash
# 1) 每个英文文档都有中文对应
for f in docs/en/*.md; do
  test -f "docs/zh-CN/$(basename "$f")" || { echo "missing Chinese: $f"; exit 1; }
done

# 2) 每个中文文档都有英文对应
for f in docs/zh-CN/*.md; do
  test -f "docs/en/$(basename "$f")" || { echo "missing English: $f"; exit 1; }
done

# 3) banner 存在
head -1 docs/en/USER_GUIDE.md | grep -q 'zh-CN/USER_GUIDE.md'
head -1 docs/zh-CN/USER_GUIDE.md | grep -q 'en/USER_GUIDE.md'

# 4) 相对链接可解析
lychee --offline --include-fragments '*.md' 'docs/**/*.md'

# 5) 源码 CJK（prompts/locales 除外）
! rg -n '[\p{Han}]' src/reachreel --glob '!src/reachreel/locales/**' --glob '!src/reachreel/prompts/**'
```

### 4.8 工作量

初始迁移已完成。后续每份文档的维护成本约等于一次正常文档修改。不要新增单语工程文档。

---

## 5. L3：成片语言（国际版真正的成本）

界面英文 ≠ 能出英文片子。当前实现里有 6 处硬编码中文假设：

| # | 位置 | 现状 | 影响 |
|---|---|---|---|
| 1 | `src/reachreel/prompts/zh/script.txt` | 中文旁白提示词 | 英文片需 `prompts/en/script.txt`（初版已加，待调优） |
| 2 | `src/reachreel/prompts/zh/vlm.txt` | 中文视觉提示词与中文 JSON 值 | 英文内容需 `prompts/en/vlm.txt` |
| 3 | `src/reachreel/core/config.py` `speech_chars_per_second = 3.9` | 中文「字/秒」 | 英文按字计算会严重高估；英文建议 `14.5` 字符/秒（或词/秒档案） |
| 4 | `src/reachreel/core/config.py` `subtitle_max_chars = 18` | 中文每行 18 字 | 英文需按**词**断行，每行约 38–42 字符 |
| 5 | `src/reachreel/media/subtitles.py` `_split_text`、`src/reachreel/engines/tts.py` `_split_for_tts` | 按中文标点 `。！？；，、` 切分 | 英文句子没有这些标点，切分会失效 |
| 6 | `src/reachreel/core/config.py` `subtitle_font = "PingFang SC"` | 仅中文 | 英文需 Latin 字体；混排需同时覆盖 |

另外 TTS 侧：Kokoro 有英文音色（`af_*` / `am_*`），但 G2P 需要 `misaki[en]` 而非 `misaki[zh]`。

### 5.1 语言档案（Language Profile）

把「语言相关的一切」收进一个表，而不是散在 6 个文件里：

```python
# src/reachreel/langs.py
PROFILES = {
    "zh": {"cps": 3.9, "unit": "chars", "chars_per_line": 18, "max_lines": 2,
           "break_chars": "。！？；，、", "word_wrap": False,
           "g2p": "misaki[zh]", "font": "Noto Sans SC",
           "voices": ["zf_xiaoxiao", "zm_yunxi", ...]},
    "en": {"cps": 2.6, "unit": "words", "chars_per_line": 40, "max_lines": 2,
           "break_chars": ".!?;,", "word_wrap": True,
           "g2p": "misaki[en]", "font": "Inter",
           "voices": ["af_heart", "am_michael", ...]},
}
```

配置：

```toml
content_language = "zh"  # zh | en —— 决定旁白/字幕/prompt/断句/QA 阈值
```

`content_language` 与 `ui_language` **完全独立**：界面可以是中文，成片是英文（做外贸/海外投放的用户就是这么用的）。

### 5.2 提示词包

Prompt 不翻译，**按语言分文件**，由 `content_language` 选择：

```text
src/reachreel/prompts/
  zh/{script,vlm,style}.txt
  en/{script,vlm,style}.txt
```

原因：英文解说写作规则与中文完全不同（时长按词数、hook 结构、避免第一人称套路），逐字直译的 prompt 效果必然差。`en/` 目前是初版，需要英文母语者调优。

**缓存键必须带语言**：

```python
settings_key = f"{content_language}|{model}|{PROMPT_VERSION}|{max_width}|{context}"
```

`script.json` 记录 `content_language`；语言变化时脚本会自动重生成。
`PROMPT_VERSION` 也要跟着语言包升级。

### 5.3 QA 阈值要按语言

现有 `qa_subtitle_char_warn_extra = 4`（超出 18 字 4 字就警告）是 CJK 语义。
英文按字符计不成立（一个词 8 个字符很正常），需要按「词数」或「渲染宽度」判定。
方案：QA 里把 `max_chars_limit` 换成语言档案给出的 `chars_per_line`，并在 `content_language` 变化时切换容差。

### 5.4 字体

必须同时覆盖 CJK 与 Latin：

- 方案 A（推荐）：`Noto Sans SC`（含完整 Latin），单文件子集化；
- 方案 B：`Source Han Sans` + `Inter` 双字体，ASS 里用 `FontName` 列表 + `fontsdir` 同时提供。

英文断行不能靠 libass 自动折行（会按字符断词），需要在 `build_subtitles` 里做**词级换行**，这一点必须在 L3 实现时一起做，否则英文字幕会出现单词被劈开。

### 5.5 工作量

| 项 | 估时 |
|---|---|
| `langs.py` 档案 + 全部硬编码点改造 | 1 天 |
| 提示词包拆分（zh/en）+ 英文 prompt 调优 | 1.5 天 |
| 英文断句/断行/词级换行 | 1 天 |
| `misaki[en]` 接入 + 英文音色试听调优 | 0.5 天 |
| 英文 QA 阈值 + 双语言回归素材 | 1 天 |

**约 5 天**，且需要英文母语/高水平校对者听片试听。

---

## 6. 顺序与红线

```text
P0（已完成）
  L1 代码英文化：注释/日志/CLI/错误 → 英文
  剩余 CJK 仅在 prompts/ 与 locales/
  开 CJK lint（排除 prompts/locales）

P1（与服务器化同批）
  i18n 脚手架 + 用户可见字符串外置 + zh-CN/en catalog
  事件/错误改 code+params
  文档：全部文档中英双语（初版已完成）

P2（v1 面板上线后）
  L3 内容多语言：langs.py + 提示词包 + 断行 + 字体 + QA 档案
```

### 红线

1. **不写死任何用户可见文案**（包括错误、QA 报告、`doctor` 输出）。
2. **不在 prompt 里写死语言**；prompt 必须能按 `[content].language` 选择。
3. **不做逐段中英对照**；一份文档一种语言。
4. **工程文档也保留中英两份**；英文为权威版本。
5. **不接受把中文标识符/中文日志混进新代码**——CI 会红。
6. **不把 `ui_language` 和 `content.language` 合并成一个开关**。

---

## 7. 验收

- [ ] `reachreel --lang en doctor` 与 `--lang zh-CN doctor` 输出同结构、各自语言
- [ ] 面板切语言后，历史任务日志按当前语言重新渲染（因为事件存的是 code+params）
- [ ] `en.toml` 与 `zh-CN.toml` key 完全一致（CI 断言）
- [ ] 源码除 `locales/` 与 prompt 包外无 CJK
- [ ] `[content].language = "en"` 能产出英文旁白 + 英文硬字幕，QA 通过
- [ ] `README`、`CHANGELOG`、`docs/zh-CN` 与 `docs/en` 全部文档成对互链
