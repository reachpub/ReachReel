"""Reject CJK characters in Python sources, as required by AGENTS.md.

Kept as a script so CI can run it without installing the package (no venv, no
imports) and so the unit test and the CI step share one implementation.
Localized copy belongs in `src/reachreel/locales/*.toml` and
`src/reachreel/prompts/*.toml`, which this script never scans.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Ideographs only. Full-width punctuation such as "。！？；，" is language
# processing data (split points, ending punctuation), not localized copy, so it
# stays allowed — the same rule the unit test has always used.
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def find_offenders(root: Path) -> list[str]:
    """Return ``path:line: source`` for every Python line containing CJK."""
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if CJK_RE.search(line):
                offenders.append(f"{path}:{lineno}: {line.strip()}")
    return offenders


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("src") / "reachreel",
        help="package directory to scan",
    )
    args = parser.parse_args(argv)

    if not args.root.is_dir():
        print(f"not a directory: {args.root}", file=sys.stderr)
        return 1

    offenders = find_offenders(args.root)
    scanned = sum(1 for _ in args.root.rglob("*.py"))
    if offenders:
        print(
            "CJK characters found in Python source; move the text to "
            "locales/*.toml or prompts/*.toml:",
            file=sys.stderr,
        )
        for offender in offenders:
            print(f"  {offender}", file=sys.stderr)
        return 1
    print(f"OK: {scanned} Python files are free of CJK characters")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
