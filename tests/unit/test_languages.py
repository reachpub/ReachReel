import unittest

from reachreel.core.config import Settings
from reachreel.languages import (
    PLANNED,
    PROFILES,
    SUPPORTED,
    check_tts_dependencies,
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

    def test_supported_and_planned_tags(self):
        self.assertEqual(supported_tags(), ("en-GB", "en-US", "zh-CN"))
        self.assertIn("ja-JP", planned_tags())
        self.assertNotIn("en-US", planned_tags())
        self.assertNotIn("en-GB", planned_tags())

    def test_supported_english_tags_map_to_kokoro_codes(self):
        self.assertEqual(require_tts(profile_for("zh-CN"), "kokoro"), "z")
        self.assertEqual(require_tts(profile_for("en-US"), "kokoro"), "a")
        self.assertEqual(require_tts(profile_for("en-GB"), "kokoro"), "b")

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
            require_tts(profile_for("ja-JP"), "kokoro")
        self.assertIn("does not support ja-JP yet", str(ctx.exception))
        self.assertIn("en-US", str(ctx.exception))
        self.assertIn("zh-CN", str(ctx.exception))

    def test_engine_without_code_is_rejected(self):
        with self.assertRaises(ValueError):
            require_tts(profile_for("zh-CN"), "say")

    def test_voice_mismatch_explains_the_fix(self):
        with self.assertRaises(ValueError) as ctx:
            require_voice_match(profile_for("zh-CN"), "af_heart")
        message = str(ctx.exception)
        self.assertIn("af_heart", message)
        self.assertIn("en-US", message)
        self.assertIn("zh-CN", message)

        with self.assertRaises(ValueError) as ctx:
            require_voice_match(profile_for("en-US"), "zf_xiaoxiao")
        self.assertIn("zf_xiaoxiao", str(ctx.exception))

        # American vs British accent mismatch is also rejected.
        with self.assertRaises(ValueError) as ctx:
            require_voice_match(profile_for("en-GB"), "af_heart")
        self.assertIn("en-GB", str(ctx.exception))

        # A voice belonging to a planned language notes that status.
        with self.assertRaises(ValueError) as ctx:
            require_voice_match(profile_for("en-US"), "jf_alpha")
        self.assertIn("planned", str(ctx.exception))

        # Matching voices pass, including comma-blended Kokoro voices.
        require_voice_match(profile_for("zh-CN"), "zf_xiaoxiao")
        require_voice_match(profile_for("zh-CN"), "zf_xiaoxiao,zf_xiaoni")
        require_voice_match(profile_for("en-US"), "af_heart")
        require_voice_match(profile_for("en-GB"), "bf_emma")

        with self.assertRaises(ValueError):
            require_voice_match(profile_for("zh-CN"), "zf_xiaoxiao,af_heart")

    def test_language_aware_settings_defaults(self):
        zh = Settings()
        self.assertEqual(zh.resolved_speech_chars_per_second(), 3.9)
        self.assertEqual(zh.resolved_voice(), "zf_xiaoxiao")
        self.assertEqual(zh.resolved_say_voice(), "Tingting")
        self.assertEqual(zh.resolved_subtitle_max_chars(), 18)
        self.assertEqual(zh.resolved_subtitle_font(), "PingFang SC")
        self.assertEqual(zh.resolved_asr_language(), "zh")

        en_us = Settings(content_language="en")
        self.assertEqual(en_us.resolved_speech_chars_per_second(), 14.5)
        self.assertEqual(en_us.resolved_voice(), "af_heart")
        self.assertEqual(en_us.resolved_say_voice(), "Samantha")
        self.assertEqual(en_us.resolved_subtitle_max_chars(), 42)
        self.assertEqual(en_us.resolved_subtitle_font(), "Helvetica Neue")
        self.assertEqual(en_us.resolved_asr_language(), "en")

        en_gb = Settings(content_language="en", tts_language="en-GB")
        self.assertEqual(en_gb.resolved_voice(), "bf_emma")
        self.assertEqual(en_gb.resolved_say_voice(), "Daniel")

        custom = Settings(
            content_language="en",
            voice="am_michael",
            subtitle_max_chars=36,
            subtitle_font="Inter",
            speech_chars_per_second=13.0,
        )
        self.assertEqual(custom.resolved_voice(), "am_michael")
        self.assertEqual(custom.resolved_subtitle_max_chars(), 36)
        self.assertEqual(custom.resolved_subtitle_font(), "Inter")
        self.assertEqual(custom.resolved_speech_chars_per_second(), 13.0)

    def test_check_tts_dependencies_reports_missing_english_stack(self):
        en_us = profile_for("en-US")
        ok, detail = check_tts_dependencies(en_us, "kokoro", find_spec=lambda _name: object())
        self.assertTrue(ok)
        self.assertIn("en_core_web_sm", detail)

        ok, detail = check_tts_dependencies(
            en_us,
            "kokoro",
            find_spec=lambda name: None if name == "en_core_web_sm" else object(),
        )
        self.assertFalse(ok)
        self.assertIn("spacy download en_core_web_sm", detail)

        ok, detail = check_tts_dependencies(
            en_us,
            "kokoro",
            find_spec=lambda name: None if name in {"spacy", "num2words"} else object(),
        )
        self.assertFalse(ok)
        self.assertIn("tts-en", detail)

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

    def test_doctor_reports_english_axes_and_catches_voice_mismatch(self):
        from reachreel.pipeline.orchestrator import doctor_report

        en_us = doctor_report(Settings(content_language="en", ollama_url="http://127.0.0.1:1"))
        checks = {check["id"]: check for check in en_us["checks"]}
        self.assertEqual(checks["language.tts"]["value"], "en-US -> kokoro a")
        self.assertTrue(checks["language.tts"]["ok"])
        self.assertEqual(checks["language.voice"]["value"], "af_heart")

        en_gb = doctor_report(
            Settings(content_language="en", tts_language="en-GB", ollama_url="http://127.0.0.1:1")
        )
        gb_checks = {check["id"]: check for check in en_gb["checks"]}
        self.assertEqual(gb_checks["language.tts"]["value"], "en-GB -> kokoro b")
        self.assertTrue(gb_checks["language.tts"]["ok"])
        self.assertEqual(gb_checks["language.voice"]["value"], "bf_emma")

        mismatch = doctor_report(
            Settings(content_language="en", voice="zf_xiaoxiao", ollama_url="http://127.0.0.1:1")
        )
        mismatch_checks = {check["id"]: check for check in mismatch["checks"]}
        self.assertFalse(mismatch_checks["language.tts"]["ok"])
        self.assertIn("zf_xiaoxiao", mismatch_checks["language.tts"]["value"])

    def test_doctor_fails_planned_tts_language(self):
        from reachreel.pipeline.orchestrator import doctor_report

        report = doctor_report(
            Settings(tts_language="ja-JP", ollama_url="http://127.0.0.1:1")
        )
        checks = {check["id"]: check for check in report["checks"]}
        self.assertFalse(checks["language.tts"]["ok"])
        self.assertIn("planned", checks["language.tts"]["value"])
        self.assertFalse(report["ok"])

    def test_qwen3_language_and_doctor_checks(self):
        from reachreel.pipeline.orchestrator import doctor_report

        self.assertEqual(require_tts(profile_for("zh-CN"), "qwen3"), "Chinese")
        self.assertEqual(require_tts(profile_for("en-US"), "qwen3"), "English")
        zh_qwen = Settings(tts_engine="qwen3")
        self.assertEqual(zh_qwen.resolved_voice(), "Serena")
        en_qwen = Settings(content_language="en", tts_engine="qwen3")
        self.assertEqual(en_qwen.resolved_voice(), "Ryan")

        mismatch = doctor_report(
            Settings(tts_engine="qwen3", voice="zf_xiaoxiao", ollama_url="http://127.0.0.1:1")
        )
        checks = {check["id"]: check for check in mismatch["checks"]}
        self.assertFalse(checks["language.tts"]["ok"])
        self.assertIn("Kokoro", checks["language.tts"]["value"])

        missing_ref = doctor_report(
            Settings(
                tts_engine="qwen3",
                tts_ref_audio="/nonexistent/voice_sample.wav",
                ollama_url="http://127.0.0.1:1",
            )
        )
        ref_checks = {check["id"]: check for check in missing_ref["checks"]}
        self.assertFalse(ref_checks["tts.ref_audio"]["ok"])


if __name__ == "__main__":
    unittest.main()
