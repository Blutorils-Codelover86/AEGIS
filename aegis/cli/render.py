"""Safe markdown rendering and stream buffering for Project AEGIS."""
from __future__ import annotations

import re
from typing import Any

from rich.console import Console
from rich.markdown import Markdown

MIN_SAFE_WIDTH = 60


def safe_width(console: Console | None = None, min_width: int = MIN_SAFE_WIDTH) -> int:
    """Clamp console width to a safe minimum so tables and code blocks do not break."""
    if console is None:
        return max(80, min_width)
    w = getattr(console, "width", 80) or 80
    return max(w, min_width)


def _is_table_line(line: str) -> bool:
    """Check whether a line is part of a markdown table."""
    s = line.strip()
    if not s:
        return False
    if s.startswith("|"):
        return True
    if "|" in s and re.match(r"^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?$", s):
        return True
    if s.count("|") >= 2:
        return True
    return False


def _has_unclosed_bold(text: str) -> bool:
    """Check whether text contains unclosed bold markdown delimiters outside code."""
    cleaned = text.replace(r"\*\*", "").replace(r"\_\_", "")
    # Remove inline code spans
    cleaned = re.sub(r"`[^`]*`", "", cleaned)
    # Remove closed code fences
    cleaned = re.sub(r"```.*?```", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"~~~.*?~~~", "", cleaned, flags=re.DOTALL)
    stars = cleaned.count("**")
    underscores = cleaned.count("__")
    return (stars % 2 != 0) or (underscores % 2 != 0)


class StreamBuffer:
    """Safe incremental segmentation buffer for streaming markdown chunks.

    Holds trailing partial lines, open fenced code blocks, and incomplete tables.
    Never emits a half-open fence or half table row.
    Deterministic across identical chunk sequences.
    """

    def __init__(self) -> None:
        self._buffer: str = ""

    def feed(self, chunk: str) -> str:
        """Feed a new chunk and return the safe, stable prefix to render as Markdown."""
        self._buffer += chunk
        if not self._buffer:
            return ""

        last_nl = self._buffer.rfind("\n")
        if last_nl == -1:
            return ""

        candidate = self._buffer[: last_nl + 1]
        remainder = self._buffer[last_nl + 1 :]

        lines = candidate.splitlines(keepends=True)
        if not lines:
            return ""

        safe_index = 0
        in_fence = False
        fence_marker = ""
        in_table = False

        for i, line in enumerate(lines):
            stripped = line.strip()
            # Fenced code block handling
            if not in_fence:
                if stripped.startswith("```"):
                    in_fence = True
                    fence_marker = "```"
                elif stripped.startswith("~~~"):
                    in_fence = True
                    fence_marker = "~~~"
            else:
                if stripped.startswith(fence_marker):
                    in_fence = False
                    fence_marker = ""

            # Markdown table handling (only outside code fences)
            if not in_fence:
                if _is_table_line(line):
                    in_table = True
                else:
                    in_table = False

            # Check safe point (outside fences and tables, with closed bold)
            if not in_fence and not in_table:
                sub_text = "".join(lines[: i + 1])
                if not _has_unclosed_bold(sub_text):
                    safe_index = i + 1

        if safe_index == 0:
            return ""

        emitted = "".join(lines[:safe_index])
        self._buffer = "".join(lines[safe_index:]) + remainder
        return emitted

    def flush(self) -> str:
        """Flush and return everything remaining at the end of the stream."""
        res = self._buffer
        self._buffer = ""
        return res


def render_markdown(console: Console, text: str) -> None:
    """Render markdown text via rich.markdown.Markdown."""
    if not text:
        return
    w = safe_width(console)
    md = Markdown(text, hyperlinks=True)
    console.print(md, width=w)


def render_plain(console: Console, text: str) -> None:
    """Render raw text without markup or style interpretation."""
    if not text:
        return
    console.print(text, highlight=False, markup=False, end="")
