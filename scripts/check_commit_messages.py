"""Commit message validator for the Conventional Commits rule in AGENTS.md.

Two entry points share the same rules:

- ``--file`` is what the local ``.githooks/commit-msg`` hook calls;
- ``--range`` is what CI calls on the commits a push or pull request adds.

Kept as a standalone script instead of a runtime module because it is
development tooling: it never ships in the wheel.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

TYPES = (
    "build",
    "chore",
    "ci",
    "docs",
    "feat",
    "fix",
    "perf",
    "refactor",
    "revert",
    "style",
    "test",
)
MAX_HEADER = 72
# Git-generated subjects that are not hand-written Conventional Commits.
GENERATED_PREFIXES = ('Merge ', 'Revert "', "fixup! ", "squash! ", "amend! ")

HEADER_RE = re.compile(
    r"^(?P<type>[a-z]+)(?:\((?P<scope>[^()\s]+)\))?(?P<breaking>!)?: (?P<subject>\S.*)$"
)
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

EXAMPLE = "feat(media): add scene-aware frame selection"


def validate(message: str) -> list[str]:
    """Return human-readable problems; an empty list means the message is fine."""
    lines = message.replace("\r\n", "\n").strip("\n").split("\n")
    if not lines or not lines[0].strip():
        return ["the commit message is empty"]

    header = lines[0].strip()
    problems: list[str] = []
    if header.startswith(GENERATED_PREFIXES):
        return []

    match = HEADER_RE.match(header)
    if match is None:
        problems.append(
            f"header '{header}' must look like 'type(scope): subject' "
            f"(allowed types: {', '.join(TYPES)}), for example: {EXAMPLE}"
        )
    else:
        commit_type = match.group("type")
        if commit_type not in TYPES:
            problems.append(
                f"unknown type '{commit_type}'; use one of: {', '.join(TYPES)}"
            )
        if len(header) > MAX_HEADER:
            problems.append(
                f"header is {len(header)} characters, the limit is {MAX_HEADER}"
            )
        if match.group("subject").rstrip().endswith("."):
            problems.append("the subject must not end with a period")
        if len(lines) > 1 and lines[1].strip():
            problems.append("leave the second line empty before the body")

    cjk = [f"  line {index}: {line}" for index, line in enumerate(lines, 1) if CJK_RE.search(line)]
    if cjk:
        problems.append(
            "commit messages must be English; CJK characters found:\n" + "\n".join(cjk)
        )
    return problems


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout


def messages_in_range(rev_range: str) -> list[tuple[str, str]]:
    """Return ``(sha, message)`` for every non-merge commit in ``rev_range``.

    A range whose left side is the all-zero sha (first push of a branch) falls
    back to ``HEAD~1..HEAD`` so the check still runs.
    """
    left = rev_range.split("..", 1)[0] if ".." in rev_range else ""
    if not left or set(left) <= {"0"}:
        rev_range = "HEAD~1..HEAD"
    shas = _git("rev-list", "--no-merges", rev_range).split()
    return [(sha, _git("log", "-1", "--format=%B", sha)) for sha in shas]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", type=Path, help="commit message file (commit-msg hook)")
    group.add_argument("--range", help="git revision range, for example origin/main..HEAD")
    args = parser.parse_args(argv)

    if args.file is not None:
        entries = [(str(args.file), args.file.read_text(encoding="utf-8", errors="replace"))]
    else:
        entries = messages_in_range(args.range)

    failures = 0
    for label, message in entries:
        problems = validate(message)
        if not problems:
            continue
        failures += 1
        subject = message.strip().split("\n")[0]
        print(f"invalid commit message: {label}\n  {subject}", file=sys.stderr)
        for problem in problems:
            for line in problem.splitlines():
                print(f"  - {line.strip()}", file=sys.stderr)
        print(file=sys.stderr)

    if failures:
        print(
            f"{failures} commit message(s) rejected. Rule: {EXAMPLE}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
