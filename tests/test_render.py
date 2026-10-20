"""Unit tests for aegis.cli.render (markdown rendering and StreamBuffer)."""
from __future__ import annotations

import io
import unittest

from rich.console import Console

from aegis.cli.render import (
    StreamBuffer,
    render_markdown,
    render_plain,
    safe_width,
)


class TestRender(unittest.TestCase):
    def setUp(self) -> None:
        self.output = io.StringIO()
        self.console = Console(file=self.output, force_terminal=True, width=100)

    def test_safe_width(self) -> None:
        narrow_console = Console(file=io.StringIO(), width=30)
        self.assertEqual(safe_width(narrow_console, min_width=60), 60)

        wide_console = Console(file=io.StringIO(), width=120)
        self.assertEqual(safe_width(wide_console, min_width=60), 120)

        self.assertEqual(safe_width(None, min_width=60), 80)

    def test_render_plain(self) -> None:
        render_plain(self.console, "Raw [bold]text[/bold] without markup\n")
        val = self.output.getvalue()
        self.assertEqual(val, "Raw [bold]text[/bold] without markup\n")

    def test_render_markdown_headings(self) -> None:
        render_markdown(self.console, "# Heading Level 1\n## Heading Level 2\n")
        val = self.output.getvalue()
        self.assertIn("Heading Level 1", val)
        self.assertIn("Heading Level 2", val)

    def test_render_markdown_bold_and_italic(self) -> None:
        render_markdown(self.console, "This has **bold text** and *italic text*.\n")
        val = self.output.getvalue()
        self.assertIn("bold text", val)
        self.assertIn("italic text", val)

    def test_render_markdown_lists(self) -> None:
        render_markdown(self.console, "- Item Alpha\n- Item Beta\n- Item Gamma\n")
        val = self.output.getvalue()
        self.assertIn("Item Alpha", val)
        self.assertIn("Item Beta", val)
        self.assertIn("Item Gamma", val)

    def test_render_markdown_tables(self) -> None:
        table_md = (
            "| Column A | Column B |\n"
            "| --- | --- |\n"
            "| Row 1 Value | Row 1 Data |\n"
            "| Row 2 Value | Row 2 Data |\n"
        )
        render_markdown(self.console, table_md)
        val = self.output.getvalue()
        self.assertIn("Column A", val)
        self.assertIn("Column B", val)
        self.assertIn("Row 1 Value", val)
        self.assertIn("Row 2 Data", val)

    def test_render_markdown_code(self) -> None:
        code_md = "```python\ndef greet(name: str) -> str:\n    return f'Hello, {name}'\n```\n"
        render_markdown(self.console, code_md)
        val = self.output.getvalue()
        self.assertIn("greet", val)
        self.assertIn("Hello", val)

    def test_render_markdown_hyperlinks(self) -> None:
        link_md = "[AEGIS Project](https://github.com/project-aegis)\n"
        render_markdown(self.console, link_md)
        val = self.output.getvalue()
        self.assertIn("AEGIS Project", val)


class TestStreamBuffer(unittest.TestCase):
    def setUp(self) -> None:
        self.buf = StreamBuffer()

    def test_holds_trailing_partial_line(self) -> None:
        self.assertEqual(self.buf.feed("Hello "), "")
        self.assertEqual(self.buf.feed("world"), "")
        self.assertEqual(self.buf.feed("!\n"), "Hello world!\n")

    def test_holds_open_fenced_code_blocks(self) -> None:
        self.assertEqual(self.buf.feed("Intro text\n```python\n"), "Intro text\n")
        self.assertEqual(self.buf.feed("x = 10\n"), "")
        self.assertEqual(self.buf.feed("y = 20\n"), "")
        emitted = self.buf.feed("```\n")
        self.assertEqual(emitted, "```python\nx = 10\ny = 20\n```\n")

    def test_holds_partial_bold(self) -> None:
        self.assertEqual(self.buf.feed("Line 1\nThis is **bold "), "Line 1\n")
        self.assertEqual(self.buf.feed("text that spans\n"), "")
        self.assertEqual(self.buf.feed("across chunks** now finished.\n"), "This is **bold text that spans\nacross chunks** now finished.\n")

    def test_holds_incomplete_table_until_flush(self) -> None:
        self.assertEqual(self.buf.feed("| Head 1 | Head 2 |\n| --- | --- |\n"), "")
        self.assertEqual(self.buf.feed("| Val 1 | Val 2 |\n"), "")
        # Table must be held back
        self.assertEqual(self.buf.feed("| Val 3 | Val 4 |\n"), "")
        # Flushed at end of stream
        flushed = self.buf.flush()
        expected = (
            "| Head 1 | Head 2 |\n"
            "| --- | --- |\n"
            "| Val 1 | Val 2 |\n"
            "| Val 3 | Val 4 |\n"
        )
        self.assertEqual(flushed, expected)
        # Subsequent flush returns empty
        self.assertEqual(self.buf.flush(), "")

    def test_table_ended_by_blank_line(self) -> None:
        self.assertEqual(self.buf.feed("| A | B |\n| - | - |\n| 1 | 2 |\n"), "")
        emitted = self.buf.feed("\nSubsequent text\n")
        self.assertIn("| A | B |", emitted)
        self.assertIn("Subsequent text", emitted)

    def test_deterministic_segmentation(self) -> None:
        chunks = [
            "Start paragraph.\n\n",
            "| Header |\n| --- |\n",
            "| Row 1 |\n",
            "\nEnd paragraph.\n",
        ]
        buf1 = StreamBuffer()
        buf2 = StreamBuffer()
        res1 = [buf1.feed(c) for c in chunks] + [buf1.flush()]
        res2 = [buf2.feed(c) for c in chunks] + [buf2.flush()]
        self.assertEqual(res1, res2)


if __name__ == "__main__":
    unittest.main()
