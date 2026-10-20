"""Textual full-screen terminal user interface for Project AEGIS."""
from __future__ import annotations

import sys
import threading
from typing import Any, Callable, Sequence

from aegis.cli.theme import ACCENT, header_info
from aegis.config import AegisConfig
from aegis.errors import AegisError, StopRequested
from aegis.interfaces import ModelProvider
from aegis.stop import StopController

try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Container, Vertical, VerticalScroll
    from textual.widgets import Footer, Header, Input, Markdown, Static
    TEXTUAL_AVAILABLE = True
except ImportError:
    TEXTUAL_AVAILABLE = False
    App = object  # type: ignore[misc,assignment]
    ComposeResult = Any  # type: ignore[misc,assignment]


TUI_CSS = """
Screen {
    background: #0f172a;
    color: #f8fafc;
}

Header {
    background: #1e293b;
    color: #3b82f6;
    text-style: bold;
    border-bottom: solid #3b82f6;
    dock: top;
}

Footer {
    background: #1e293b;
    color: #94a3b8;
    dock: bottom;
}

#chat-history {
    height: 1fr;
    padding: 1;
    overflow-y: scroll;
}

.welcome-box {
    margin: 1 0;
    padding: 1;
    background: #1e293b;
    border: solid #3b82f6;
    color: #93c5fd;
    height: auto;
}

.user-box {
    margin: 1 0;
    padding: 1;
    background: #1e293b;
    border-left: wide #60a5fa;
    height: auto;
}

.user-label {
    color: #60a5fa;
    text-style: bold;
}

.user-text {
    color: #f1f5f9;
}

.assistant-box {
    margin: 1 0;
    padding: 1;
    background: #111827;
    border-left: wide #3b82f6;
    height: auto;
}

.assistant-label {
    color: #3b82f6;
    text-style: bold;
}

.assistant-markdown {
    color: #f8fafc;
    margin-top: 1;
}

.error-box {
    margin: 1 0;
    padding: 1;
    background: #450a0a;
    border-left: wide #ef4444;
    color: #fca5a5;
    height: auto;
}

#input-container {
    dock: bottom;
    padding: 1;
    background: #1e293b;
    border-top: solid #3b82f6;
    height: auto;
}

Input {
    background: #0f172a;
    border: solid #3b82f6;
    color: #f8fafc;
}

Input:focus {
    border: double #60a5fa;
}
"""


class AegisTUI(App):
    """Full-screen Textual application for AEGIS chat."""

    CSS = TUI_CSS

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", show=True, priority=True),
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("ctrl+l", "clear_history", "Clear", show=True),
    ]

    def __init__(
        self,
        config: AegisConfig,
        provider: ModelProvider,
        *,
        model: str | None = None,
        initial_prompt: str | None = None,
    ) -> None:
        if not TEXTUAL_AVAILABLE:
            raise ImportError(
                "Textual is not installed. Please install it with: pip install 'aegis-ai[tui]'"
            )
        super().__init__()
        self.config = config
        self.provider = provider
        self.model = model or config.model
        self.initial_prompt = initial_prompt
        self.stop_ctrl = StopController(config.stop_file)
        self._stop_event = threading.Event()
        self._generating = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with VerticalScroll(id="chat-history"):
            pass
        with Vertical(id="input-container"):
            yield Input(
                placeholder="Type your message and press Enter... (Ctrl+C to quit)",
                id="chat-input",
            )
        yield Footer()

    async def on_mount(self) -> None:
        self.title = "AEGIS"
        self.sub_title = f"{self.model} · local"
        history = self.query_one("#chat-history", VerticalScroll)

        if not self.initial_prompt:
            welcome_text = (
                f"**AEGIS Terminal Assistant**\n\n"
                f"• Model: `{self.model}`\n"
                f"• Local Ollama: `{self.config.ollama_host}`\n"
                f"• Type a prompt below to begin chatting.\n"
                f"• Press **Ctrl+C** or **Ctrl+Q** to exit, **Ctrl+L** to clear."
            )
            welcome_md = Markdown(welcome_text, classes="welcome-box")
            await history.mount(welcome_md)

        input_widget = self.query_one("#chat-input", Input)
        input_widget.focus()

        if self.initial_prompt:
            await self._submit_prompt(self.initial_prompt)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text or self._generating:
            return
        event.input.value = ""
        await self._submit_prompt(text)

    async def _submit_prompt(self, prompt: str) -> None:
        self._generating = True
        history = self.query_one("#chat-history", VerticalScroll)

        # Mount User Message
        user_container = Vertical(
            Static("You", classes="user-label"),
            Static(prompt, classes="user-text"),
            classes="user-box",
        )
        await history.mount(user_container)

        # Mount Assistant Message placeholder
        asst_md = Markdown("Thinking...", classes="assistant-markdown")
        asst_container = Vertical(
            Static(f"AEGIS ({self.model})", classes="assistant-label"),
            asst_md,
            classes="assistant-box",
        )
        await history.mount(asst_container)
        history.scroll_end(animate=False)

        # Run model stream worker in background thread
        self.run_worker(lambda: self._generate_stream_task(asst_md, prompt), thread=True)

    def _generate_stream_task(self, md_widget: Markdown, prompt: str) -> None:
        accum = ""
        try:
            self._check_stop()
            if hasattr(self.provider, "generate_stream"):
                stream = self.provider.generate_stream(
                    prompt,
                    model=self.model,
                    stop_check=self._check_stop,
                )
                first = True
                for chunk in stream:
                    if self._stop_event.is_set():
                        break
                    if first:
                        accum = chunk
                        first = False
                    else:
                        accum += chunk
                    self.call_from_thread(md_widget.update, accum)
                    self.call_from_thread(self._scroll_history)
            else:
                response = self.provider.generate(
                    prompt,
                    model=self.model,
                    stop_check=self._check_stop,
                )
                accum = response
                self.call_from_thread(md_widget.update, accum)
                self.call_from_thread(self._scroll_history)
        except StopRequested as e:
            if not accum:
                accum = f"*[Generation stopped: {e}]*"
            else:
                accum += f"\n\n*[Generation stopped: {e}]*"
            self.call_from_thread(md_widget.update, accum)
        except Exception as exc:
            self.call_from_thread(self._post_error, str(exc))
        finally:
            self._generating = False
            self.call_from_thread(self._scroll_history)

    def _check_stop(self) -> None:
        if self._stop_event.is_set():
            raise StopRequested("User cancelled generation")
        self.stop_ctrl.check()

    def _scroll_history(self) -> None:
        try:
            history = self.query_one("#chat-history", VerticalScroll)
            history.scroll_end(animate=False)
        except Exception:
            pass

    def _post_error(self, message: str) -> None:
        history = self.query_one("#chat-history", VerticalScroll)
        err_widget = Static(f"Error: {message}", classes="error-box")
        history.mount(err_widget)
        self._scroll_history()

    def action_clear_history(self) -> None:
        history = self.query_one("#chat-history", VerticalScroll)
        history.remove_children()

    def action_quit(self) -> None:
        self._stop_event.set()
        self.exit(0)


def run_tui(
    config: AegisConfig,
    provider: ModelProvider,
    *,
    model: str | None = None,
    initial_prompt: str | None = None,
) -> int:
    """Launch the interactive Textual TUI.

    Raises:
        ImportError: If textual is not installed.
    """
    if not TEXTUAL_AVAILABLE:
        raise ImportError(
            "Textual is not installed. Please install it with: pip install 'aegis-ai[tui]'"
        )
    app = AegisTUI(config=config, provider=provider, model=model, initial_prompt=initial_prompt)
    app.run()
    return 0
