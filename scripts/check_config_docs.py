"""Keep the configuration surface in sync across its four homes.

`AGENTS.md` requires a new key in `core/config.py`, `config.example.toml`, and
both `docs/{en,zh-CN}/CONFIGURATION.md`. This script reads the `Settings`
dataclass with `ast`, so it runs on a bare `python3` without installing the
package, and compares:

- every `Settings` field has a key in `config.example.toml` (hard);
- every `Settings` field is documented in both CONFIGURATION files (hard);
- no key in `config.example.toml` is absent from `Settings` (hard, catches typos);
- defaults match between `Settings` and `config.example.toml` (warning, since a
  sample file may deliberately show a tuned value; `--strict` makes it fail).

`[qa]` keys are unprefixed in TOML and documented that way, while the dataclass
names them `qa_*`, so the mapping is applied before comparing.
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
import tomllib
from pathlib import Path

# Computed at load time, never written by users.
DERIVED_FIELDS = {"bgm_root_resolved"}
QA_PREFIX = "qa_"
QA_TABLE = "qa"


def settings_fields(config_py: Path) -> dict[str, object]:
    """Return ``{field: default}`` for the ``Settings`` dataclass (AST only)."""
    tree = ast.parse(config_py.read_text(encoding="utf-8"))
    cls = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "Settings"
        ),
        None,
    )
    if cls is None:
        raise SystemExit(f"no Settings class in {config_py}")
    fields: dict[str, object] = {}
    for node in cls.body:
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        name = node.target.id
        try:
            default: object = ast.literal_eval(node.value)
        except (ValueError, TypeError):
            # default_factory or another call: keep the field, skip the value.
            default = None
        fields[name] = default
    return fields


def config_keys(example: Path) -> tuple[set[str], set[str], dict[str, object]]:
    """Return ``(top_level_keys, qa_keys, flat_values)`` from the example file."""
    data = tomllib.loads(example.read_text(encoding="utf-8"))
    top = {key for key, value in data.items() if not isinstance(value, dict)}
    qa = set(data.get(QA_TABLE) or {})
    flat: dict[str, object] = {key: data[key] for key in top}
    for key, value in (data.get(QA_TABLE) or {}).items():
        flat[f"{QA_PREFIX}{key}"] = value
    return top, qa, flat


def config_key(field: str) -> str:
    """The TOML key or docs token for a field (``[qa]`` keys lose the prefix)."""
    return field.removeprefix(QA_PREFIX)


def documented(docs: Path, field: str) -> bool:
    text = docs.read_text(encoding="utf-8")
    key = config_key(field)
    if re.search(rf"`{re.escape(key)}`", text):
        return True
    return re.search(rf"(?m)^\s*{re.escape(key)}\s*=", text) is not None


def compare(root: Path, *, strict: bool = False) -> tuple[list[str], list[str]]:
    """Return ``(problems, warnings)``."""
    fields = {
        name: default
        for name, default in settings_fields(root / "src/reachreel/core/config.py").items()
        if name not in DERIVED_FIELDS
    }
    top, qa, values = config_keys(root / "config.example.toml")
    docs = [
        root / "docs/en/CONFIGURATION.md",
        root / "docs/zh-CN/CONFIGURATION.md",
    ]

    problems: list[str] = []
    warnings: list[str] = []

    known_top = {name for name in fields if not name.startswith(QA_PREFIX)}
    known_qa = {name[len(QA_PREFIX):] for name in fields if name.startswith(QA_PREFIX)}
    for key in sorted(top - known_top):
        problems.append(f"config.example.toml: '{key}' is not a Settings field (typo?)")
    for key in sorted(qa - known_qa):
        problems.append(f"config.example.toml: '[qa] {key}' is not a Settings field (typo?)")

    for name, expected in fields.items():
        in_qa = name.startswith(QA_PREFIX)
        key = config_key(name)
        present = key in (qa if in_qa else top)
        if not present:
            location = f"[{QA_TABLE}] {key}" if in_qa else key
            problems.append(
                f"config.example.toml: missing '{location}' for Settings.{name}"
            )
        for docs_path in docs:
            if not docs_path.is_file():
                problems.append(f"missing document: {docs_path.relative_to(root)}")
            elif not documented(docs_path, name):
                problems.append(
                    f"{docs_path.relative_to(root)}: Settings.{name} "
                    f"is not documented as `{key}`"
                )

        actual = values.get(name)
        if expected is None or actual is None:
            continue
        if isinstance(expected, bool) or isinstance(actual, bool):
            same = expected == actual
        elif isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
            same = abs(float(expected) - float(actual)) < 1e-9
        else:
            same = expected == actual
        if not same:
            message = (
                f"config.example.toml: {key} = {actual!r} but Settings default is "
                f"{expected!r}"
            )
            (problems if strict else warnings).append(message)
    return problems, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--strict",
        action="store_true",
        help="also require config.example.toml to repeat the Settings defaults",
    )
    args = parser.parse_args(argv)

    problems, warnings = compare(args.root, strict=args.strict)
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if problems:
        print("configuration surface is out of sync:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(
        "configuration surface is in sync "
        "(Settings, config.example.toml, CONFIGURATION.md x2)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
