import unittest

from reachreel.core.config import Settings
from reachreel.languages import (
    PLANNED,
    PROFILES,
    SUPPORTED,
    normalize_tag,
    planned_tags,
    profile_for,
    require_tts,
    require_voice_match,
    resolve_asr_language,
    resolve_tts_language,
    supported_tags,
    voice_language,
)


class LanguageRegistryTests(unittest.TestCase):
    def test_normalizes_bcp47_input(self):
        self.assertEqual(normalize_tag("en"), "en-US")
        self.assertEqual(normalize_tag("EN-us"), "en-US")
        self.assertEqual(normalize_tag("zh_cn.UTF-8"), "zh-CN")
        self.assertEqual(normalize_tag("en-GB"), "en-GB")
        self.assertEqual(normalize_tag("pt-br"), "pt-BR")
        # An unknown region falls back to the default regional profile.
        self.assertEqual(normalize_tag("zh-Hans"), "zh-CN")
        self.assertIsNone(normalize_tag("de"))
        self.assertIsNone(normalize_tag(""))
        self.assertIsNone(normalize_tag(None))

    def test_profiles_are_consistent(self):
        for tag, profile in PROFILES.items():
            self.assertEqual(tag, profile.tag)
            self.assertIn(profile.status, (SUPPORTED, PLANNED))
            self.assertEqual(profile.primary, tag.split("-")[0])
            self.assertGreater(profile.speech_chars_per_second, 0)
            self.assertGreater(profile.subtitle_max_chars, 0)
            self.assertTrue(profile.font_hint)
            if profile.status == SUPPORTED:
                self.assertTrue(profile.tts, f"{tag} needs an engine code")

    def test_only_chinese_is_supported_today(self):
        self.assertEqual(supported_tags(), ("zh-CN",))
        self.assertIn("en-US", planned_tags())
        self.assertIn("ja-JP", planned_tags())

    def test_voice_language_from_name(self):
        self.assertEqual(voice_language("af_heart"), "en-US")
        self.assertEqual(voice_language("bm_george"), "en-GB")
        self.assertEqual(voice_language("zf_xiaoxiao"), "zh-CN")
        self.assertEqual(voice_language("jf_alpha"), "ja-JP")
        self.assertIsNone(voice_language("Samantha"))  # macOS voice, not Kokoro
        self.assertIsNone(voice_language(""))

    def test_tts_language_resolution_order(self):
        # explicit > content language > voice
        self.assertEqual(resolve_tts_language(tts_language="zh-CN", content_language="en").tag, "zh-CN")
        self.assertEqual(resolve_tts_language(content_language="en", voice="zf_xiaoxiao").tag, "en-US")
        self.assertEqual(resolve_tts_language(voice="zf_xiaoxiao").tag, "zh-CN")

    def test_unknown_tag_lists_supported_values(self):
        with self.assertRaises(ValueError) as ctx:
            resolve_tts_language(tts_language="de")
        self.assertIn("zh-CN", str(ctx.exception))
        self.assertIn("de", str(ctx.exception))

    def test_planned_language_is_rejected_for_tts(self):
        with self.assertRaises(ValueError) as ctx:
            require_tts(profile_for("en-US"), "kokoro")
        self.assertIn("does not support en-US yet", str(ctx.exception))
        self.assertIn("misaki[en]", str(ctx.exception))

    def test_engine_without_code_is_rejected(self):
        with self.assertRaises(ValueError):
            require_tts(profile_for("zh-CN"), "say")

    def test_voice_mismatch_explains_the_fix(self):
        with self.assertRaises(ValueError) as ctx:
            require_voice_match(profile_for("zh-CN"), "af_heart")
        message = str(ctx.exception)
        self.assertIn("af_heart", message)
        self.assertIn("en-US", message)
        self.assertIn("planned", message)
        # The matching voice passes.
        require_voice_match(profile_for("zh-CN"), "zf_xiaoxiao")

    def test_asr_language_follows_content(self):
        self.assertEqual(resolve_asr_language("", "zh"), "zh")
        self.assertEqual(resolve_asr_language("", "en"), "en")
        self.assertEqual(resolve_asr_language("ja", "zh"), "ja")

    def test_settings_normalize_both_language_keys(self):
        settings = Settings.load(overrides={"tts_language": "EN-us", "asr_language": ""})
        self.assertEqual(settings.tts_language, "en-US")
        self.assertEqual(settings.asr_language, "")
        # An unknown tag is kept as typed so the engine can name it in the error.
        settings = Settings.load(overrides={"tts_language": "de"})
        self.assertEqual(settings.tts_language, "de")

    def test_doctor_reports_the_resolved_tts_language(self):
        from reachreel.pipeline.orchestrator import doctor_report

        report = doctor_report(Settings(ollama_url="http://127.0.0.1:1"))
        checks = {check["id"]: check for check in report["checks"]}
        self.assertIn("language.tts", checks)
        self.assertEqual(checks["language.tts"]["value"], "zh-CN -> kokoro z")
        self.assertTrue(checks["language.tts"]["ok"])

    def test_doctor_fails_planned_tts_language(self):
        from reachreel.pipeline.orchestrator import doctor_report

        report = doctor_report(
            Settings(content_language="en", ollama_url="http://127.0.0.1:1")
        )
        checks = {check["id"]: check for check in report["checks"]}
        self.assertFalse(checks["language.tts"]["ok"])
        self.assertIn("planned", checks["language.tts"]["value"])
        self.assertFalse(report["ok"])


if __name__ == "__main__":
    unittest.main()
