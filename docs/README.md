# Documentation

ReachReel documentation index. / ReachReel 文档索引。

Language policy / 语言策略: **all documentation is bilingual.**
（全部文档双语：`docs/zh-CN/` 与 `docs/en/`。）

- User-facing docs: Chinese is authoritative; English is kept in sync.
- Engineering docs: English is authoritative; Chinese is kept in sync.
- Commands, option names, paths, config keys, defaults, and code are never translated.
- Every document has a language banner that links to its counterpart.
- `docs/README.md` and `docs/GLOSSARY.md` are intentionally bilingual single-file references. All other docs are one language per file.

---

## Start here / 从这里开始

| Document | 中文 | English |
|---|---|---|
| Project README / 项目说明 | [README.zh-CN.md](../README.zh-CN.md) | [README.md](../README.md) |
| User Guide / 使用手册 | [zh-CN/USER_GUIDE.md](zh-CN/USER_GUIDE.md) | [en/USER_GUIDE.md](en/USER_GUIDE.md) |
| Configuration / 配置参考 | [zh-CN/CONFIGURATION.md](zh-CN/CONFIGURATION.md) | [en/CONFIGURATION.md](en/CONFIGURATION.md) |
| BGM / 背景音乐 | [zh-CN/BGM.md](zh-CN/BGM.md) | [en/BGM.md](en/BGM.md) |

## Engineering / 工程文档

| Document | 中文 | English |
|---|---|---|
| Development / 开发文档 | [zh-CN/DEVELOPMENT.md](zh-CN/DEVELOPMENT.md) | [en/DEVELOPMENT.md](en/DEVELOPMENT.md) |
| Desktop and multi-frontend architecture / 桌面版与多前端架构 | [zh-CN/DESKTOP_ARCHITECTURE.md](zh-CN/DESKTOP_ARCHITECTURE.md) | [en/DESKTOP_ARCHITECTURE.md](en/DESKTOP_ARCHITECTURE.md) |
| Localization / 多语言与本地化 | [zh-CN/LOCALIZATION.md](zh-CN/LOCALIZATION.md) | [en/LOCALIZATION.md](en/LOCALIZATION.md) |
| Language and voice specification / 语言与音色规范 | [zh-CN/LANGUAGES.md](zh-CN/LANGUAGES.md) | [en/LANGUAGES.md](en/LANGUAGES.md) |
| Roadmap / 路线图 | [zh-CN/ROADMAP.md](zh-CN/ROADMAP.md) | [en/ROADMAP.md](en/ROADMAP.md) |
| Glossary / 术语表 | [GLOSSARY.md](GLOSSARY.md) | [GLOSSARY.md](GLOSSARY.md) |
| Changelog / 变更记录 | [CHANGELOG.zh-CN.md](../CHANGELOG.zh-CN.md) | [CHANGELOG.md](../CHANGELOG.md) |

---

## Contributor rules / 贡献者规则

1. **Both languages, same change.** A change to `docs/zh-CN/<file>.md` must update `docs/en/<file>.md` in the same change, and vice versa.
2. **One language per file.** Do not interleave Chinese and English paragraphs. Only this index page is bilingual.
3. **Do not translate commands.** Keep command names, option names, paths, config keys, defaults, and code identifiers unchanged.
4. **Banners must resolve.** Every document starts with a language banner that links to the real counterpart; do not add dead links.
5. **Register first.** Add a new document to this index before writing it.
6. **Glossary first.** Use [GLOSSARY.md](GLOSSARY.md) for terminology and add new terms there before using them.

Language banner format / 语言切换行格式:

```markdown
<!-- docs/en/FILE.md -->
**English** | [简体中文](../zh-CN/FILE.md)

<!-- docs/zh-CN/FILE.md -->
[English](../en/FILE.md) | **简体中文**
```

---

## Directory layout / 目录约定

```text
docs/
  README.md                  # bilingual index
  GLOSSARY.md                # bilingual glossary
  zh-CN/
    USER_GUIDE.md
    CONFIGURATION.md
    BGM.md
    DEVELOPMENT.md
    DESKTOP_ARCHITECTURE.md
    LOCALIZATION.md
    ROADMAP.md
  en/
    USER_GUIDE.md
    CONFIGURATION.md
    BGM.md
    DEVELOPMENT.md
    DESKTOP_ARCHITECTURE.md
    LOCALIZATION.md
    ROADMAP.md
```
