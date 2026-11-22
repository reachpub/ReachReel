**English** | [简体中文](../zh-CN/LOCALIZATION.md)

# Multilingual and Localization

This document defines ReachReel's language strategy: **code in English, localizable UI, documentation split by type, and content language as a separate product capability**.

These four do not substitute for one another; mixing them is the most common cause of rework.

Related docs: [Desktop and multi-frontend architecture](DESKTOP_ARCHITECTURE.md), [Development](DEVELOPMENT.md), [Roadmap](ROADMAP.md).

---

## 1. Three layers of language problems (separate them first)

| Layer | What it is | Who is affected | Cost | When |
|---|---|---|---|---|
| **L1 Code language** | Identifiers, comments, docstrings, logs, errors | Contributors, maintainers | Low (one-time) | **Now** |
| **L2 UI language** | CLI help/output, panel copy, error messages | End users | Medium (scaffolding 1 day, translation 0.5 day) | **Before v1** |
| **L3 Content language** | Language of narration/subtitles/style/voice/QA | End users | **High** (3–5 days + tuning) | After v1 (P1) |

Common mistakes: treating L3 as L2 (the UI is translated to English, but the generated narration is still Chinese); or postponing L2 until after L3 (106 strings scattered across 12 files, so a later refactor has to touch every business function).

---

## 2. L1: code in English (unconditional)

### 2.1 Current state

```text
CJK in Python source: 0 lines
Chinese exists only in src/reachreel/prompts/zh/ and src/reachreel/locales/zh-CN.toml
```

Identifiers, comments, logs, CLI help, and errors are English; prompt and fallback copy lives in `prompts/<lang>/` as language data, and UI copy lives in `locales/<lang>.toml`.

### 2.2 Rules

| Object | Language | Notes |
|---|---|---|
| Identifiers, module names, class/function names | English | Already satisfied |
| docstrings, inline comments | **English** | Mandatory for new code; replace existing ones opportunistically when the code is touched |
| Log messages | **English** (canonical) | Greppable, can be pasted into issues directly |
| User-visible copy | **Never hardcoded**, goes through `t()` | See §3 |
| `locales/*.toml` | Per language | The only files outside the Python directories allowed to contain CJK |

### 2.3 Enforceable check

Add one CI step (or pre-commit):

```bash
# Except locales/, prompts/, and test fixtures, no CJK is allowed in source
! rg -n '[\p{Han}]' src/reachreel --glob '!src/reachreel/locales/**' --glob '!src/reachreel/prompts/**' --glob '!tests/fixtures/**'
```

The value of this rule: it prevents new code from continuing to introduce Chinese logs and gives the existing cleanup a clear convergence endpoint.
**The existing source is clean; enable this check.**

---

## 3. L2: UI localization

### 3.1 Design points

1. **Externalize strings**; do not concatenate Chinese/English in business code;
2. **Events and errors carry "code + params"**, not just a rendered sentence (see §3.4);
3. **Logs default to English**; the panel renders them in the client language;
4. Language negotiation order: `--lang` > `REACHREEL_LANG` > `config.ui_language` > `LANG`/`LC_ALL` > `en`.

Current coverage: CLI help, common errors, stage labels, dry-run output, the QA report, `review.md`, and `doctor` labels are bilingual; log messages remain canonical English.

### 3.2 Module

```python
# src/reachreel/i18n/__init__.py
def t(key: str, /, **params) -> str: ...          # render copy in the current language
def set_language(value: str | None) -> str: ...   # switch and load a catalog
def negotiate_language(explicit=None, config_language=None) -> str: ...
def supported_languages() -> tuple[str, ...]: ...
```

Directories:

```text
src/reachreel/locales/
  en.toml        # canonical (new keys go here first)
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

Use a TOML/JSON dictionary rather than gettext: this project has few strings (on the order of a hundred), and dictionary files are diffable and reviewable.
Migrate to `.po` once there are enough contributors to need Poedit/Weblate; the `t()` call sites will not need to change.

### 3.3 CLI

```bash
reachreel --lang zh-CN run video.mp4
reachreel doctor --json          # structure is language-independent; the panel renders it itself
```

All `help=` in `argparse` goes through `t()`; `--lang` is a global option (parsed once before the `ArgumentParser`).

### 3.4 Events and errors: carry a code, not a finished sentence

This is an amendment to the event protocol in [DESKTOP_ARCHITECTURE.md §5](DESKTOP_ARCHITECTURE.md):

```jsonc
// ❌ Only a rendered sentence: the client language and the process language are welded together
{"type":"log","level":"warning","msg":"No BGM found"}

// ✅ Code + params + fallback rendering in the process language
{"type":"log","level":"warning","code":"bgm_not_found","params":{"dir":"inputs/bgm/"},"msg":"No BGM found in inputs/bgm/"}
```

- `code` + `params` are the **contract** (stable, testable);
- `msg` is the rendering in the current process language (readability; the CLI uses it directly);
- the panel prefers `code` to look up its own `locales/`, and falls back to `msg` only if not found;
- errors likewise: `{"error":{"code":"ollama_unreachable","params":{"url":...},"message":"...","hint":"..."}}`.

Benefit: on the same machine, the CLI can be in English and the panel in Chinese while server logs stay in English, and the three do not interfere with one another.

### 3.5 Effort

| Item | Estimate |
|---|---|
| `i18n.py` + catalog structure + `t()` integration | 0.5 day |
| Externalize 106 user-visible strings | 1 day |
| `zh-CN` / `en` catalog proofreading | 0.5 day |
| Rework events/errors into code+params | 0.5 day (same batch as server-ification) |
| CI key consistency + CJK lint | 0.5 day |

**About 3 days in total, and it must be completed before the v1 panel**. The cost of doing it late is that every new UI element has to be changed twice.

---

## 4. Documentation strategy

### 4.1 Conclusion: one language per document, split by directory

Do not interleave Chinese and English paragraph by paragraph. Alternating languages in one file makes diffs unreadable and lets translations drift silently.

The right approach: one language per document, separate files, with a language switch at the top.

The only exception: `docs/README.md` is a navigation page and may use a side-by-side table.

### 4.2 Routing table

| Document | Language | Authoritative version |
|---|---|---|
| `README.md` / `README.zh-CN.md` | English / Chinese | English |
| `CHANGELOG.md` / `CHANGELOG.zh-CN.md` | English / Chinese | English |
| `docs/{en,zh-CN}/USER_GUIDE.md` | English / Chinese | Chinese |
| `docs/{en,zh-CN}/CONFIGURATION.md` | English / Chinese | Chinese |
| `docs/{en,zh-CN}/DEVELOPMENT.md` | English / Chinese | English |
| `docs/{en,zh-CN}/DESKTOP_ARCHITECTURE.md` | English / Chinese | English |
| `docs/{en,zh-CN}/ROADMAP.md` | English / Chinese | English |
| `docs/{en,zh-CN}/LOCALIZATION.md` | English / Chinese | English |
| `docs/README.md` | Bilingual | Navigation page |
| `docs/GLOSSARY.md` | Chinese–English | Glossary |
| `config.example.toml` comments | English | Chinese notes live in the configuration guide |

Principle: **all documentation is bilingual; commands, options, paths, config keys, defaults, and code are never translated.**

### 4.3 Target structure

```text
README.md / README.zh-CN.md
CHANGELOG.md / CHANGELOG.zh-CN.md
docs/
  README.md
  GLOSSARY.md
  en/{USER_GUIDE,CONFIGURATION,DEVELOPMENT,DESKTOP_ARCHITECTURE,LOCALIZATION,ROADMAP}.md
  zh-CN/{USER_GUIDE,CONFIGURATION,DEVELOPMENT,DESKTOP_ARCHITECTURE,LOCALIZATION,ROADMAP}.md
```

**Authoritative version ownership** (which side to trust when the two disagree):

| Category | Authoritative version | Rule |
|---|---|---|
| User docs | **Chinese** | English is a synchronized translation |
| Engineering docs | **English** | Chinese is a synchronized translation |
| Code strings | **`locales/en.toml`** | Add new keys in English first, then `zh-CN.toml` |
| Prompts | **Independent per language** | Not a translation relationship (see §5.2) |

### 4.4 Sync rules

| Rule | Description |
|---|---|
| Same PR | A change to the Chinese version must update the English version in the same PR, and vice versa |
| Authority | User docs: Chinese; engineering docs: English |
| No command translation | Option names, paths, defaults, config keys, and code are unchanged |
| No forced 100% parity | Language-specific differences are allowed, but must be noted at the top |
| Expiry | Delete outdated sections rather than keeping wrong content |
| Review | A bilingual change needs a reviewer who can read both languages |

### 4.5 Language banner

Every bilingual document starts with a language banner that links to the real counterpart:

```markdown
<!-- docs/en/USER_GUIDE.md -->
**English** | [简体中文](../zh-CN/USER_GUIDE.md)

<!-- docs/zh-CN/USER_GUIDE.md -->
[English](../en/USER_GUIDE.md) | **简体中文**
```

Rules:

- The current language is bold and not a link;
- Leave a blank line between the banner and the `#` title;
- No dead links;
- `docs/README.md` and `docs/GLOSSARY.md` do not need a banner.

### 4.6 Translation workflow

```text
1. Finalize the authoritative version
2. Machine translation is only a draft (DeepL / GPT)
3. Human review: commands, options, threshold semantics, units
4. Normalize terminology with docs/GLOSSARY.md
5. Read the rendered result: tables, code blocks, relative links
```

Never ship raw machine translation, translate command names/paths, or interleave two languages paragraph by paragraph.

### 4.7 CI checks

```bash
# 1) Every English document has a Chinese counterpart
for f in docs/en/*.md; do
  test -f "docs/zh-CN/$(basename "$f")" || { echo "missing Chinese: $f"; exit 1; }
done

# 2) Every Chinese document has an English counterpart
for f in docs/zh-CN/*.md; do
  test -f "docs/en/$(basename "$f")" || { echo "missing English: $f"; exit 1; }
done

# 3) Banners exist
head -1 docs/en/USER_GUIDE.md | grep -q 'zh-CN/USER_GUIDE.md'
head -1 docs/zh-CN/USER_GUIDE.md | grep -q 'en/USER_GUIDE.md'

# 4) Relative links resolve
lychee --offline --include-fragments '*.md' 'docs/**/*.md'

# 5) Source CJK (except prompts/locales)
! rg -n '[\p{Han}]' src/reachreel --glob '!src/reachreel/locales/**' --glob '!src/reachreel/prompts/**'
```

### 4.8 Workload

The initial migration is complete. Maintaining a document now costs about the same as a normal documentation edit. Do not add new single-language engineering documents.

---

## 5. L3: content language (the real cost of an international edition)

An English UI ≠ the ability to produce English videos. The current implementation has 6 hardcoded Chinese assumptions:

| # | Location | Current state | Impact |
|---|---|---|---|
| 1 | `src/reachreel/prompts/zh/script.txt` | Chinese narration prompt | English videos need `prompts/en/script.txt` (first draft exists, needs tuning) |
| 2 | `src/reachreel/prompts/zh/vlm.txt` | Chinese vision prompt and Chinese JSON values | English content needs `prompts/en/vlm.txt` |
| 3 | `src/reachreel/core/config.py` `speech_chars_per_second = 3.9` | Chinese "chars/second" | Counting English by character severely overestimates; use about `14.5` chars/second (or a words/second profile) |
| 4 | `src/reachreel/core/config.py` `subtitle_max_chars = 18` | 18 Chinese characters per line | English needs line breaking by **word**, around 38–42 characters per line |
| 5 | `src/reachreel/media/subtitles.py` `_split_text`, `src/reachreel/engines/tts.py` `_split_for_tts` | Split on Chinese punctuation `。！？；，、` | English sentences do not contain these punctuation marks, so splitting fails |
| 6 | `src/reachreel/core/config.py` `subtitle_font = "PingFang SC"` | Chinese only | English needs a Latin font; mixed content needs both |

Also on the TTS side: Kokoro has English voices (`af_*` / `am_*`), but G2P needs `misaki[en]` rather than `misaki[zh]`.

### 5.1 Language profile

Collect "everything language-related" into one table instead of scattering it across 6 files:

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

Configuration:

```toml
content_language = "zh"  # zh | en — decides narration/subtitles/prompt/sentence splitting/QA thresholds
```

`content_language` and `ui_language` are **completely independent**: the UI can be Chinese while the content is English (this is exactly how users doing foreign trade/overseas campaigns use it).

### 5.2 Prompt packs

Prompts are not translated; they are **split into files by language**, selected by `content_language`:

```text
src/reachreel/prompts/
  zh/{script,vlm,style}.txt
  en/{script,vlm,style}.txt
```

Reason: English narration writing rules are completely different from Chinese (duration by word count, hook structure, avoiding first-person clichés); a word-for-word translated prompt will inevitably perform worse. The `en/` pack is a first draft and needs native-speaker tuning.

**Cache keys must include the language**:

```python
settings_key = f"{content_language}|{model}|{PROMPT_VERSION}|{max_width}|{context}"
```

`script.json` records `content_language`; changing the language regenerates the script automatically.
`PROMPT_VERSION` must also be bumped along with the language pack.

### 5.3 QA thresholds must be per language

The existing `qa_subtitle_char_warn_extra = 4` (warn when 4 characters over the 18-character limit) is CJK semantics.
Counting English by character does not hold (an 8-character word is normal); judge by "word count" or "rendered width" instead.
Approach: in QA replace `max_chars_limit` with `chars_per_line` from the language profile, and switch tolerances when `content_language` changes.

### 5.4 Fonts

Must cover both CJK and Latin:

- Option A (recommended): `Noto Sans SC` (includes full Latin), subsetted as a single file;
- Option B: dual fonts `Source Han Sans` + `Inter`, provided together in ASS via a `FontName` list + `fontsdir`.

English line breaking cannot rely on libass auto-wrapping (it breaks words by character); **word-level wrapping** must be done in `build_subtitles`, and this must be done together with the L3 implementation, otherwise English subtitles will have words split apart.

### 5.5 Effort

| Item | Estimate |
|---|---|
| `langs.py` profile + rework of all hardcoded points | 1 day |
| Prompt pack split (zh/en) + English prompt tuning | 1.5 days |
| English sentence splitting/line breaking/word-level wrapping | 1 day |
| `misaki[en]` integration + English voice preview and tuning | 0.5 day |
| English QA thresholds + bilingual regression footage | 1 day |

**About 5 days**, and it requires a native English speaker / high-level proofreader to listen to the videos.

---

## 6. Order and red lines

```text
P0 (completed)
  L1 code English: comments/logs/CLI/errors → English
  Remaining CJK is only in prompts/ and locales/
  Enable CJK lint (exclude prompts/locales)

P1 (same batch as server-ification)
  i18n scaffolding + externalize user-visible strings + zh-CN/en catalog
  Rework events/errors into code+params
  Documentation: all docs bilingual (initial pass complete)

P2 (after the v1 panel ships)
  L3 content multilingual support: langs.py + prompt packs + line breaking + fonts + QA profile
```

### Red lines

1. **Never hardcode any user-visible copy** (including errors, QA reports, `doctor` output).
2. **Never hardcode the language in prompts**; prompts must be selectable via `[content].language`.
3. **No paragraph-by-paragraph Chinese/English documents**; one language per file.
4. **Engineering documents also have Chinese and English versions**; English is authoritative.
5. **Do not accept Chinese identifiers/Chinese logs in new code** — CI will fail.
6. **Do not merge `ui_language` and `content.language` into one switch**.

---

## 7. Acceptance

- [ ] `reachreel --lang en doctor` and `--lang zh-CN doctor` produce the same structure, each in its own language
- [ ] After switching the panel language, historical job logs are re-rendered in the current language (because events store code+params)
- [ ] `en.toml` and `zh-CN.toml` have exactly the same keys (CI assertion)
- [ ] The source contains no CJK outside `locales/` and the prompt packs
- [ ] `[content].language = "en"` produces English narration + English hard subtitles, and QA passes
- [ ] `README`, `CHANGELOG`, and every document under `docs/zh-CN` and `docs/en` have a linked counterpart
