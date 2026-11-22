import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.check_changelog import compare, main, parse

EN = """**English** | [简体中文](CHANGELOG.zh-CN.md)

# Changelog

## [Unreleased]

### Added

- one
- two

## [0.2.0] - 2026-01-01

### Fixed

- three
"""

ZH = """[English](CHANGELOG.md) | **简体中文**

# 变更记录

## [Unreleased]

### 新增

- 一
- 二

## [0.2.0] - 2026-01-01

### 修复

- 三
"""


class ChangelogSyncTests(unittest.TestCase):
    def _pair(self, english: str = EN, chinese: str = ZH) -> tuple[Path, Path]:
        root = Path(tempfile.mkdtemp(prefix="reachreel-changelog-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        en_path = root / "CHANGELOG.md"
        zh_path = root / "CHANGELOG.zh-CN.md"
        en_path.write_text(english, encoding="utf-8")
        zh_path.write_text(chinese, encoding="utf-8")
        return en_path, zh_path

    def test_synced_pair_has_no_problems(self):
        en_path, zh_path = self._pair()
        self.assertEqual(compare(en_path, zh_path), [])
        self.assertEqual(main(["--root", str(en_path.parent)]), 0)

    def test_parse_reports_banner_and_counts(self):
        en_path, _ = self._pair()
        parsed = parse(en_path, "en")
        self.assertEqual(parsed["banner"], "CHANGELOG.zh-CN.md")
        self.assertEqual(
            parsed["versions"],
            [("[Unreleased]", {"added": 2}), ("[0.2.0] - 2026-01-01", {"fixed": 1})],
        )

    def test_bullet_count_mismatch_is_reported(self):
        en_path, zh_path = self._pair(chinese=ZH.replace("- 二\n", ""))
        problems = compare(en_path, zh_path)
        self.assertTrue(any("added" in problem and "bullet" in problem for problem in problems))

    def test_missing_version_in_one_file(self):
        zh_path_missing = ZH.split("## [0.2.0]")[0]
        en_path, zh_path = self._pair(chinese=zh_path_missing)
        problems = compare(en_path, zh_path)
        self.assertTrue(any("version headings differ" in problem for problem in problems))

    def test_section_order_mismatch(self):
        swapped = (
            ZH.replace("### 新增", "### TMP")
            .replace("### 修复", "### 新增")
            .replace("### TMP", "### 修复")
        )
        en_path, zh_path = self._pair(chinese=swapped)
        problems = compare(en_path, zh_path)
        self.assertTrue(any("sections differ" in problem for problem in problems))

    def test_english_heading_in_chinese_file(self):
        en_path, zh_path = self._pair(
            chinese=ZH.replace("### 新增", "### Added")
        )
        problems = compare(en_path, zh_path)
        self.assertTrue(
            any("uses the other language" in problem for problem in problems), problems
        )

    def test_unknown_section_heading(self):
        en_path, zh_path = self._pair(
            english=EN.replace("### Added", "### Whatever"), chinese=ZH.replace("### 新增", "### 随便")
        )
        problems = compare(en_path, zh_path)
        self.assertEqual(sum("unknown section heading" in problem for problem in problems), 2)

    def test_missing_banner(self):
        en_path, zh_path = self._pair(english=EN.replace("**English** | [简体中文](CHANGELOG.zh-CN.md)", "# Changelog"))
        problems = compare(en_path, zh_path)
        self.assertTrue(any("banner" in problem for problem in problems))

    def test_repository_changelogs_are_in_sync(self):
        root = Path(__file__).resolve().parents[2]
        self.assertEqual(compare(root / "CHANGELOG.md", root / "CHANGELOG.zh-CN.md"), [])

    def test_missing_file_exits_non_zero(self):
        root = Path(tempfile.mkdtemp(prefix="reachreel-changelog-empty-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        self.assertEqual(main(["--root", str(root)]), 1)


if __name__ == "__main__":
    unittest.main()
