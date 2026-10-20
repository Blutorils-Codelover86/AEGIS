"""Unit tests for the optional Textual TUI."""
from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from typing import Iterator, Sequence
from unittest.mock import MagicMock, patch

from aegis.config import AegisConfig
from aegis.errors import StopRequested
from aegis.interfaces import ModelProvider

try:
    import textual
    from textual.containers import VerticalScroll
    from textual.widgets import Input, Markdown, Static
    from aegis.cli.tui import AegisTUI, TEXTUAL_AVAILABLE, run_tui
except ImportError:
    TEXTUAL_AVAILABLE = False


class FakeStreamingProvider:
    """Mock provider with streaming support."""

    name = "fake-streaming"

    def __init__(self, chunks: Sequence[str] | None = None, *, raise_stop: bool = False) -> None:
        self.chunks = list(chunks) if chunks is not None else ["Chunk 1", " Chunk 2"]
        self.raise_stop = raise_stop
        self.calls: list[str] = []

    def generate(self, prompt: str, **kwargs) -> str:
        self.calls.append(prompt)
        return "".join(self.chunks)

    def generate_stream(
        self, prompt: str, *, stop_check=None, **kwargs
    ) -> Iterator[str]:
        self.calls.append(prompt)
        for chunk in self.chunks:
            if stop_check is not None:
                stop_check()
            if self.raise_stop:
                raise StopRequested("Interrupted by test")
            yield chunk

    def list_models(self) -> list[str]:
        return ["fake-model:latest"]

    def health(self) -> bool:
        return True


class TestTUIFallbackAndGuard(unittest.TestCase):
    """Tests for import guards and CLI fallback paths that run regardless of Textual."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = self.temp_dir.name
        self.config = AegisConfig(
            model="test-model:1b",
            ollama_host="http://127.0.0.1:11434",
            memory_db=os.path.join(self.base_dir, "memory.db"),
            audit_log=os.path.join(self.base_dir, "audit.jsonl"),
            stop_file=os.path.join(self.base_dir, "STOP"),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_run_tui_raises_when_textual_missing(self) -> None:
        """run_tui raises ImportError with install instruction when Textual is missing."""
        with patch("aegis.cli.tui.TEXTUAL_AVAILABLE", False):
            provider = FakeStreamingProvider()
            with self.assertRaises(ImportError) as ctx:
                run_tui(self.config, provider)
            self.assertIn("pip install 'aegis-ai[tui]'", str(ctx.exception))

    def test_cli_fallback_when_not_tty(self) -> None:
        """When stdout is not a TTY, --tui prints fallback warning and uses Rich streaming."""
        from aegis.cli.main import main

        provider = FakeStreamingProvider(["Echoed: test"])
        out = io.StringIO()
        err = io.StringIO()

        with patch("sys.stdout.isatty", return_value=False):
            with redirect_stdout(out), redirect_stderr(err):
                rc = main(
                    ["chat", "--tui", "--prompt", "Hello TTY test"],
                    config=self.config,
                    provider=provider,
                )

        self.assertEqual(rc, 0)
        self.assertIn("Textual TUI requires a TTY", err.getvalue())
        self.assertIn("Echoed: test", out.getvalue())

    def test_cli_fallback_when_import_error(self) -> None:
        """When TUI import fails, CLI warns on stderr and falls back to Rich streaming."""
        from aegis.cli.main import main

        provider = FakeStreamingProvider(["Streaming reply"])
        out = io.StringIO()
        err = io.StringIO()
        out.isatty = lambda: True  # type: ignore[assignment]

        with redirect_stdout(out), redirect_stderr(err):
            with patch("aegis.cli.tui.run_tui", side_effect=ImportError("Textual is not installed")):
                rc = main(
                    ["chat", "--tui", "--prompt", "Fallback import test"],
                    config=self.config,
                    provider=provider,
                )

        self.assertEqual(rc, 0)
        self.assertIn("Textual TUI is not installed", err.getvalue())
        self.assertIn("Streaming reply", out.getvalue())


@unittest.skipUnless(TEXTUAL_AVAILABLE, "Textual is not installed")
class TestTUIPilot(unittest.IsolatedAsyncioTestCase):
    """Textual pilot headless interaction tests."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = self.temp_dir.name
        self.config = AegisConfig(
            model="test-model:1b",
            ollama_host="http://127.0.0.1:11434",
            memory_db=os.path.join(self.base_dir, "memory.db"),
            audit_log=os.path.join(self.base_dir, "audit.jsonl"),
            stop_file=os.path.join(self.base_dir, "STOP"),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_app_construction(self) -> None:
        """App constructs properly with model, provider, config and bindings."""
        provider = FakeStreamingProvider()
        app = AegisTUI(self.config, provider, model="custom:3b")
        self.assertEqual(app.model, "custom:3b")
        self.assertEqual(app.config, self.config)
        self.assertEqual(app.provider, provider)

        bindings = [b.key for b in app.BINDINGS]
        self.assertIn("ctrl+c", bindings)
        self.assertIn("ctrl+q", bindings)

    async def test_pilot_streaming_round_trip(self) -> None:
        """Using App.run_test(), verify fake streaming provider appends the assistant message."""
        provider = FakeStreamingProvider(["Hello ", "from ", "**AEGIS** ", "pilot!"])
        app = AegisTUI(self.config, provider, model="test-model:1b")

        async with app.run_test() as pilot:
            inp = app.query_one("#chat-input", Input)
            inp.value = "Tell me about yourself"
            inp.focus()
            await pilot.press("enter")
            # Wait for worker thread to stream chunks and UI to refresh
            await pilot.pause(0.25)

            history = app.query_one("#chat-history", VerticalScroll)
            md_widgets = list(history.query(Markdown))
            self.assertTrue(len(md_widgets) >= 1)
            last_md = md_widgets[-1]
            self.assertEqual(last_md.source, "Hello from **AEGIS** pilot!")

            # Verify user label and prompt were mounted
            user_texts = [str(s.content) for s in history.query(Static) if "Tell me about yourself" in str(getattr(s, "content", ""))]
            self.assertTrue(len(user_texts) >= 1)

    async def test_pilot_initial_prompt(self) -> None:
        """Initial prompt supplied at launch triggers generation on mount."""
        provider = FakeStreamingProvider(["Initial ", "prompt ", "response."])
        app = AegisTUI(
            self.config,
            provider,
            model="test-model:1b",
            initial_prompt="Run initial prompt",
        )

        async with app.run_test() as pilot:
            await pilot.pause(0.25)
            history = app.query_one("#chat-history", VerticalScroll)
            md_widgets = list(history.query(Markdown))
            self.assertTrue(len(md_widgets) >= 1)
            last_md = md_widgets[-1]
            self.assertEqual(last_md.source, "Initial prompt response.")

    async def test_pilot_stop_handling(self) -> None:
        """StopRequested during generation is handled gracefully in UI."""
        provider = FakeStreamingProvider(raise_stop=True)
        app = AegisTUI(self.config, provider, initial_prompt="Interrupt me")

        async with app.run_test() as pilot:
            await pilot.pause(0.25)
            history = app.query_one("#chat-history", VerticalScroll)
            md_widgets = list(history.query(Markdown))
            self.assertTrue(len(md_widgets) >= 1)
            last_md = md_widgets[-1]
            self.assertIn("Generation stopped", last_md.source)
