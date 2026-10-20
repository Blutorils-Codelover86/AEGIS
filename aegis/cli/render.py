"""Safe markdown rendering and stream buffering for Project AEGIS."""
from __future__ import annotations

import re
from typing import Any

from rich.console import Console
from rich.markdown import Markdown

MIN_SAFE_WIDTH = 10


def safe_width(console: Console | None = None, min_width: int = MIN_SAFE_WIDTH) -> int:
    """Return console width respecting narrow terminals down to min_width."""
    if console is None:
        return 80
    w = getattr(console, "width", 80) or 80
    return max(w, min_width)


def _has_unescaped_pipe(line: str) -> bool:
    """Check whether a line contains an unescaped pipe character outside inline code."""
    # Strip closed inline code spans first so code like `a | b` does not trigger table logic
    cleaned = re.sub(r"`[^`]*`", "", line)
    return bool(re.search(r"(?<!\\)(?:\\\\)*\|", cleaned))


def _is_table_separator(line: str) -> bool:
    """Check whether a line is a markdown table delimiter row (e.g. |---|---| or --- | ---)."""
    s = line.strip()
    if not s or not _has_unescaped_pipe(s):
        return False
    return bool(re.match(r"^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?$", s))


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
        fence_char = ""
        fence_len = 0
        in_table = False

        for i, line in enumerate(lines):
            # 1. Code fence handling
            if not in_fence:
                fence_match = re.match(r"^[ ]{0,3}(`{3,}|~{3,})", line)
                if fence_match:
                    in_fence = True
                    fence_char = fence_match.group(1)[0]
                    fence_len = len(fence_match.group(1))
                    if in_table:
                        in_table = False
            else:
                # Inside fence: close only on line with >= opening run of same char and only trailing whitespace
                close_match = re.match(rf"^[ ]{{0,3}}\{fence_char}{{{fence_len},}}\s*$", line)
                if close_match:
                    in_fence = False
                    fence_char = ""
                    fence_len = 0

            # 2. Table handling (only outside code fences)
            if not in_fence:
                if in_table:
                    # Inside active table: continues as long as line is non-blank and has unescaped pipe
                    if not line.strip() or not _has_unescaped_pipe(line):
                        in_table = False
                else:
                    # Potential table header or line with pipe
                    if _has_unescaped_pipe(line):
                        if i + 1 < len(lines):
                            next_line = lines[i + 1]
                            if _is_table_separator(next_line):
                                in_table = True
                            else:
                                in_table = False
                        else:
                            # Next line is not yet in the buffer; hold back this line
                            break

            # 3. Check safe point (outside fences and tables, with closed bold)
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

    @property
    def pending(self) -> str:
        """Return the un-emitted trailing buffer content."""
        return self._buffer


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
