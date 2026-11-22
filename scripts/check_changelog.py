"""Keep CHANGELOG.md and CHANGELOG.zh-CN.md structurally in sync.

AGENTS.md requires every documentation change to update both languages in the
same change. The changelog is the one place where "both languages" is easy to
verify mechanically, so this script compares:

- the language banner pointing at the counterpart file;
- the version headings, in order;
- the section headings of each version, in order, through a name map;
- the number of bullets in each section.

Headings that are still English inside the Chinese file are reported, which is
how the rule catches half-translated releases.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

BANNER_PATTERN = re.compile(r"\]\(([^)]+\.md)\)")

# canonical section -> (English heading, Chinese heading)
SECTIONS: dict[str, tuple[str, str]] = {
    "added": ("Added", "新增"),
    "changed": ("Changed", "变更"),
    "fixed": ("Fixed", "修复"),
    "removed": ("Removed", "移除"),
    "deprecated": ("Deprecated", "弃用"),
    "security": ("Security", "安全"),
    "breaking": ("Breaking", "破坏性变更"),
    "validation": ("Validation", "验证"),
    "known-limitations": ("Known limitations", "已知限制"),
}
EN_NAMES = {name.lower(): key for key, (name, _) in SECTIONS.items()}
ZH_NAMES = {name.lower(): key for key, (_, name) in SECTIONS.items()}

VERSION_RE = re.compile(r"^## +(?P<title>.+?) *$")
SECTION_RE = re.compile(r"^### +(?P<title>.+?) *$")
BULLET_PREFIX = "- "


def _canonical_section(title: str, language: str) -> str | None:
    normalized = " ".join(title.strip().lower().split())
    names = EN_NAMES if language == "en" else ZH_NAMES
    return names.get(normalized)


def parse(path: Path, language: str) -> dict[str, object]:
    """Return ``{banner, versions: [(title, {section: bullet_count})], ...}``."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    banner = ""
    for line in lines:
        if not line.strip():
            continue
        match = BANNER_PATTERN.search(line)
        banner = match.group(1) if match else ""
        break

    versions: list[tuple[str, dict[str, int]]] = []
    current: dict[str, int] | None = None
    unknown_sections: list[str] = []
    wrong_language: list[tuple[str, str]] = []
    for line in lines:
        version_match = VERSION_RE.match(line)
        if version_match:
            current = {}
            versions.append((version_match.group("title").strip(), current))
            continue
        section_match = SECTION_RE.match(line)
        if section_match and current is not None:
            title = section_match.group("title").strip()
            canonical = _canonical_section(title, language)
            if canonical is None:
                other = _canonical_section(title, "zh" if language == "en" else "en")
                if other is not None:
                    expected = SECTIONS[other][0 if language == "en" else 1]
                    wrong_language.append((title, expected))
                    canonical = other
                else:
                    unknown_sections.append(title)
                    canonical = title.lower()
            current[canonical] = 0
            continue
        if current is not None and line.startswith(BULLET_PREFIX):
            sections = list(current)
            if sections:
                current[sections[-1]] += 1
    return {
        "banner": banner,
        "versions": versions,
        "unknown_sections": unknown_sections,
        "wrong_language": wrong_language,
    }


def compare(en_path: Path, zh_path: Path) -> list[str]:
    english = parse(en_path, "en")
    chinese = parse(zh_path, "zh")
    problems: list[str] = []

    for path, parsed in ((en_path, english), (zh_path, chinese)):
        for found, expected in parsed["wrong_language"]:
            problems.append(
                f"{path.name}: section '### {found}' uses the other language; "
                f"expected '### {expected}'"
            )
        if not parsed["banner"]:
            problems.append(f"{path.name}: missing the banner link to the other language version")
        if parsed["unknown_sections"]:
            problems.append(
                f"{path.name}: unknown section heading(s) {parsed['unknown_sections']}; "
                "add the spelling to SECTION_MAP in scripts/check_changelog.py"
            )

    en_versions = english["versions"]
    zh_versions = chinese["versions"]
    en_titles = [title for title, _ in en_versions]
    zh_titles = [title for title, _ in zh_versions]
    if en_titles != zh_titles:
        problems.append(
            "version headings differ:\n"
            f"  {en_path.name}: {en_titles}\n"
            f"  {zh_path.name}: {zh_titles}"
        )

    for (title, en_sections), (_, zh_sections) in zip(en_versions, zh_versions, strict=False):
        if list(en_sections) != list(zh_sections):
            problems.append(
                f"{title}: sections differ\n"
                f"  {en_path.name}: {list(en_sections)}\n"
                f"  {zh_path.name}: {list(zh_sections)}"
            )
            continue
        for section, en_count in en_sections.items():
            zh_count = zh_sections[section]
            if en_count != zh_count:
                problems.append(
                    f"{title} / {section}: {en_count} bullet(s) in {en_path.name} "
                    f"but {zh_count} in {zh_path.name}"
                )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)

    en_path = args.root / "CHANGELOG.md"
    zh_path = args.root / "CHANGELOG.zh-CN.md"
    for path in (en_path, zh_path):
        if not path.is_file():
            print(f"missing changelog: {path}", file=sys.stderr)
            return 1

    problems = compare(en_path, zh_path)
    if problems:
        print("CHANGELOG.md and CHANGELOG.zh-CN.md are out of sync:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("CHANGELOG.md and CHANGELOG.zh-CN.md are in sync")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
