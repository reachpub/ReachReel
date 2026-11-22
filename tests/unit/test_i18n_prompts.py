import re
import tomllib
import unittest
from importlib import resources
from pathlib import Path

from reachreel.core.config import Settings
from reachreel.domain.models import Scene
from reachreel.generation.script import _scene_table
from reachreel.i18n import set_language, t
from reachreel.prompts import load_content, load_prompt
from scripts.check_python_language import find_offenders


def _flatten(data: dict, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    for key, value in data.items():
        full = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            keys.update(_flatten(value, full))
        else:
            keys.add(full)
    return keys


class I18nPromptTests(unittest.TestCase):
    def test_catalogs_have_identical_keys(self):
        def keys(language: str) -> set[str]:
            path = resources.files("reachreel") / "locales" / f"{language}.toml"
            return _flatten(tomllib.loads(path.read_text(encoding="utf-8")))

        self.assertEqual(keys("en"), keys("zh-CN"))

    def test_prompt_packs_exist_for_both_languages(self):
        for language in ("zh", "en"):
            for name in ("vlm", "script", "style"):
                prompt = load_prompt(language, name)
                self.assertTrue(prompt.strip(), f"{language}/{name} is empty")

    def test_english_prompts_have_no_chinese(self):
        cjk = re.compile(r"[\u4e00-\u9fff]")
        for name in ("vlm", "script", "style"):
            self.assertIsNone(cjk.search(load_prompt("en", name)), name)

    def test_ui_language_switch(self):
        set_language("zh-CN")
        self.assertEqual(t("cli.run"), "完整跑一遍")
        set_language("en")
        self.assertEqual(t("cli.run"), "Run the full pipeline")

    def test_settings_normalize_languages(self):
        settings = Settings.load(
            overrides={"ui_language": "zh_CN.UTF-8", "content_language": "EN"}
        )
        self.assertEqual(settings.ui_language, "zh-CN")
        self.assertEqual(settings.content_language, "en")

    def test_content_catalog_keys_match(self):
        self.assertEqual(set(load_content("en")), set(load_content("zh")))

    def test_no_cjk_in_python_source(self):
        # Same implementation as the CI step: scripts/check_python_language.py.
        root = Path(__file__).resolve().parents[2] / "src" / "reachreel"
        self.assertEqual(
            find_offenders(root),
            [],
            "CJK in Python source; keep localized text in locales/*.toml "
            "or prompts/*.toml (run: python3 scripts/check_python_language.py)",
        )

    def test_english_scene_table_uses_english_labels(self):
        table = _scene_table(
            [Scene(id=1, start=0, end=3, summary="A boat approaches the falls", place="Niagara")],
            "en",
        )
        self.assertIn("Place:", table)
        self.assertIn("Description:", table)


if __name__ == "__main__":
    unittest.main()
