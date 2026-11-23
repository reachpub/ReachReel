"""MkDocs hooks for ReachReel's bilingual documentation tree."""
from __future__ import annotations

import re

_BANNER_RE = re.compile(
    r"^(?:\*\*English\*\*\s*\|\s*\[简体中文\]\([^)]+\)|\[English\]\([^)]+\)\s*\|\s*\*\*简体中文\*\*)\s*\n+"
)


def on_page_markdown(markdown: str, **_kwargs: object) -> str:
    """Strip the GitHub-only top language banner; Material renders its own switcher."""
    return _BANNER_RE.sub("", markdown, count=1)
