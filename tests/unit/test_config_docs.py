import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.check_config_docs import compare, config_key, settings_fields

CONFIG_PY = '''\
from dataclasses import dataclass


@dataclass
class Settings:
    ui_language: str = "en"
    vlm_workers: int = 2
    subtitle_size: int = 48
    qa_freeze_fail_seconds: float = 8.0
    bgm_root_resolved: object = None
'''

EXAMPLE = """\
ui_language = "en"
vlm_workers = 2
subtitle_size = 48

[qa]
freeze_fail_seconds = 8.0
"""

DOC = """\
| `ui_language` | `en` | UI language |
| `vlm_workers` | `2` | Workers |
| `subtitle_size` | `48` | Size |

```toml
[qa]
freeze_fail_seconds = 8.0
```
"""


class ConfigDocsTests(unittest.TestCase):
    def _root(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="reachreel-config-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (root / "src/reachreel/core").mkdir(parents=True)
        (root / "src/reachreel/core/config.py").write_text(CONFIG_PY, encoding="utf-8")
        (root / "config.example.toml").write_text(EXAMPLE, encoding="utf-8")
        for language in ("en", "zh-CN"):
            docs = root / "docs" / language
            docs.mkdir(parents=True, exist_ok=True)
            (docs / "CONFIGURATION.md").write_text(DOC, encoding="utf-8")
        return root

    def test_synced_tree_has_no_problems(self):
        problems, warnings = compare(self._root())
        self.assertEqual(problems, [])
        self.assertEqual(warnings, [])

    def test_missing_example_key_is_a_problem(self):
        root = self._root()
        example = root / "config.example.toml"
        example.write_text(example.read_text().replace("vlm_workers = 2\n", ""))
        problems, _ = compare(root)
        self.assertTrue(any("missing 'vlm_workers'" in problem for problem in problems))

    def test_typo_in_example_is_a_problem(self):
        root = self._root()
        example = root / "config.example.toml"
        example.write_text(example.read_text().replace("vlm_workers", "vlm_wrkers"))
        problems, _ = compare(root)
        self.assertTrue(any("not a Settings field" in problem for problem in problems))

    def test_missing_documentation_is_a_problem_in_both_documents(self):
        root = self._root()
        for language in ("en", "zh-CN"):
            docs = root / "docs" / language / "CONFIGURATION.md"
            docs.write_text(docs.read_text().replace("| `subtitle_size` | `48` | Size |\n", ""))
        problems, _ = compare(root)
        self.assertEqual(sum("is not documented" in problem for problem in problems), 2)

    def test_default_mismatch_is_only_a_warning(self):
        root = self._root()
        example = root / "config.example.toml"
        example.write_text(example.read_text().replace("vlm_workers = 2", "vlm_workers = 4"))
        problems, warnings = compare(root)
        self.assertEqual(problems, [])
        self.assertTrue(any("vlm_workers" in warning for warning in warnings))

    def test_default_mismatch_fails_in_strict_mode(self):
        root = self._root()
        example = root / "config.example.toml"
        example.write_text(example.read_text().replace("vlm_workers = 2", "vlm_workers = 4"))
        problems, _ = compare(root, strict=True)
        self.assertTrue(any("Settings default is 2" in problem for problem in problems))

    def test_derived_fields_are_ignored(self):
        root = self._root()
        # The field exists in the dataclass ...
        self.assertIn(
            "bgm_root_resolved",
            settings_fields(root / "src/reachreel/core/config.py"),
        )
        # ... but is computed at load time, so it must not need a TOML key.
        problems, _ = compare(root)
        self.assertFalse(any("bgm_root_resolved" in problem for problem in problems))

    def test_qa_keys_are_unprefixed_in_toml(self):
        root = self._root()
        example = root / "config.example.toml"
        example.write_text(
            example.read_text().replace("[qa]\nfreeze_fail_seconds = 8.0\n", "[qa]\n")
        )
        problems, _ = compare(root)
        self.assertTrue(
            any("[qa] freeze_fail_seconds" in problem for problem in problems)
        )

    def test_config_key_mapping(self):
        self.assertEqual(config_key("qa_black_min_seconds"), "black_min_seconds")
        self.assertEqual(config_key("subtitle_size"), "subtitle_size")

    def test_repository_configuration_surface_is_in_sync(self):
        root = Path(__file__).resolve().parents[2]
        problems, warnings = compare(root)
        self.assertEqual(problems, [], "\n".join(problems))
        self.assertEqual(warnings, [], "\n".join(warnings))
        strict_problems, _ = compare(root, strict=True)
        self.assertEqual(
            strict_problems,
            [],
            "config.example.toml must repeat the Settings defaults:\n"
            + "\n".join(strict_problems),
        )


if __name__ == "__main__":
    unittest.main()
