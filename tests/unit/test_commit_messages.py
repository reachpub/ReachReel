import os
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.check_commit_messages import HEADER_RE, messages_in_range, validate

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class CommitMessageTests(unittest.TestCase):
    def test_accepts_conventional_headers(self):
        self.assertEqual(validate("feat(media): add scene-aware frame selection"), [])
        self.assertEqual(validate("fix: handle empty scene list"), [])
        self.assertEqual(validate("refactor(core)!: drop python 3.10"), [])
        self.assertEqual(
            validate("feat: subject\n\nbody wrapped\nover two lines\n"), []
        )

    def test_rejects_unstructured_headers(self):
        self.assertTrue(validate("add stuff"))
        self.assertTrue(validate("feat add stuff"))
        self.assertTrue(validate("feat:"))
        self.assertTrue(validate(""))
        self.assertTrue(validate("   "))

    def test_rejects_unknown_type(self):
        problems = validate("featute(x): nope")
        self.assertTrue(problems)
        self.assertIn("unknown type", " ".join(problems))

    def test_rejects_long_header(self):
        subject = "feat(core): " + "x" * 80
        self.assertTrue(any("characters" in problem for problem in validate(subject)))

    def test_rejects_trailing_period(self):
        self.assertTrue(any("period" in problem for problem in validate("fix: done.")))

    def test_rejects_body_without_blank_line(self):
        self.assertTrue(
            any("second line" in problem for problem in validate("fix: thing\nbody"))
        )

    def test_rejects_cjk(self):
        problems = validate("feat(subtitle): 白字黑描边")
        self.assertTrue(any("English" in problem for problem in problems))

    def test_accepts_git_generated_subjects(self):
        self.assertEqual(validate("Merge branch 'main' into feature"), [])
        self.assertEqual(validate('Revert "feat(subtitle): white text"'), [])
        self.assertEqual(validate("fixup! feat(subtitle): white text"), [])

    def test_header_regex_requires_space_after_colon(self):
        self.assertIsNotNone(HEADER_RE.match("feat(cli): x"))
        self.assertIsNone(HEADER_RE.match("feat(cli):x"))

    def test_range_of_a_single_commit(self):
        self.assertEqual(messages_in_range("HEAD..HEAD"), [])

    def test_hook_is_executable_and_wired(self):
        hook = PROJECT_ROOT / ".githooks" / "commit-msg"
        self.assertTrue(hook.is_file())
        self.assertTrue(os.access(hook, os.X_OK), "the commit-msg hook must be executable")
        self.assertIn("check_commit_messages.py", hook.read_text(encoding="utf-8"))

    def test_cli_reports_failures(self):
        from scripts.check_commit_messages import main

        with patch("sys.stderr"):
            self.assertEqual(main(["--file", str(self._write("feat(x): ok"))]), 0)
            self.assertEqual(main(["--file", str(self._write("nope"))]), 1)

    def _write(self, message: str) -> Path:
        import tempfile

        path = Path(tempfile.mkdtemp(prefix="reachreel-msg-")) / "msg.txt"
        path.write_text(message + "\n", encoding="utf-8")
        self.addCleanup(shutil.rmtree, path.parent, ignore_errors=True)
        return path


if __name__ == "__main__":
    unittest.main()
