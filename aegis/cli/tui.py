"""Textual full-screen terminal user interface for Project AEGIS."""
from __future__ import annotations

import sys
import threading
from typing import Any, Callable, Sequence

from aegis.cli.render import StreamBuffer
from aegis.cli.theme import ACCENT, header_info
from aegis.config import AegisConfig
from aegis.conversation import Conversation
from aegis.errors import AegisError, StopRequested
from aegis.identity import identity_system_prompt
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


if TEXTUAL_AVAILABLE:
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

.assistant-tail {
    color: #94a3b8;
    margin-top: 0;
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
            system: str | None = None,
            initial_prompt: str | None = None,
            no_memory: bool = False,
            max_turns: int | None = None,
        ) -> None:
            super().__init__()
            self.config = config
            self.provider = provider
            self.model = model or config.model
            self.system = system
            self.initial_prompt = initial_prompt
            self.no_memory = no_memory
            self.stop_ctrl = StopController(config.stop_file)
            self._stop_event = threading.Event()
            self._generating = False

            resolved_max_turns = max_turns if max_turns is not None else config.max_chat_turns
            self.conversation = Conversation(max_turns=resolved_max_turns)
            self.system_prompt_str = identity_system_prompt(model=self.model, user_system=self.system)
            self.system_message = {"role": "system", "content": self.system_prompt_str}

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
                mem_status = "disabled (--no-memory)" if self.no_memory else "safe local SQLite (explicit commands)"
                welcome_text = (
                    f"**AEGIS Terminal Assistant**\n\n"
                    f"• Model: `{self.model}`\n"
                    f"• Provider: `{self.config.ollama_host}`\n"
                    f"• Memory: `{mem_status}`\n"
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
            self.conversation.add_user(prompt)
            history = self.query_one("#chat-history", VerticalScroll)

            # Mount User Message
            user_container = Vertical(
                Static("You", classes="user-label"),
                Static(prompt, classes="user-text"),
                classes="user-box",
            )
            await history.mount(user_container)

            # Mount Assistant Message container with stable Markdown widget + pending tail
            asst_md = Markdown("", classes="assistant-markdown")
            asst_tail = Static("Thinking...", classes="assistant-tail")
            asst_container = Vertical(
                Static(f"AEGIS ({self.model})", classes="assistant-label"),
                asst_md,
                asst_tail,
                classes="assistant-box",
            )
            await history.mount(asst_container)
            history.scroll_end(animate=False)

            # Run model stream worker in background thread
            self.run_worker(lambda: self._generate_stream_task(asst_md, asst_tail, prompt), thread=True)

        def _generate_stream_task(self, md_widget: Markdown, tail_widget: Static, prompt: str) -> None:
            buf = StreamBuffer()
            accum_stable = ""
            first = True
            try:
                self._check_stop()
                if hasattr(self.provider, "chat_stream"):
                    messages = [self.system_message] + self.conversation.messages()
                    stream = self.provider.chat_stream(
                        messages,
                        model=self.model,
                        stop_check=self._check_stop,
                    )
                    for chunk in stream:
                        if self._stop_event.is_set():
                            break
                        if first:
                            first = False
                            self.call_from_thread(tail_widget.update, "")

                        ready = buf.feed(chunk)
                        if ready:
                            accum_stable += ready
                            self.call_from_thread(md_widget.update, accum_stable)
                        pending = buf.pending
                        self.call_from_thread(tail_widget.update, pending)
                        self.call_from_thread(self._scroll_history)
                elif hasattr(self.provider, "generate_stream"):
                    stream = self.provider.generate_stream(
                        prompt,
                        system=self.system,
                        model=self.model,
                        stop_check=self._check_stop,
                    )
                    for chunk in stream:
                        if self._stop_event.is_set():
                            break
                        if first:
                            first = False
                            self.call_from_thread(tail_widget.update, "")

                        ready = buf.feed(chunk)
                        if ready:
                            accum_stable += ready
                            self.call_from_thread(md_widget.update, accum_stable)
                        # Display pending tail as literal text
                        pending = buf.pending
                        self.call_from_thread(tail_widget.update, pending)
                        self.call_from_thread(self._scroll_history)
                else:
                    response = self.provider.generate(
                        prompt,
                        system=self.system,
                        model=self.model,
                        stop_check=self._check_stop,
                    )
                    accum_stable = response
                    self.call_from_thread(md_widget.update, accum_stable)
                    self.call_from_thread(tail_widget.update, "")
                    self.call_from_thread(self._scroll_history)

                # End of stream: flush any remaining buffer to stable markdown
                remaining = buf.flush()
                if remaining:
                    accum_stable += remaining
                    self.call_from_thread(md_widget.update, accum_stable)
                self.call_from_thread(tail_widget.update, "")
                self.call_from_thread(self._scroll_history)

                if accum_stable:
                    self.conversation.add_assistant(accum_stable)
            except StopRequested as e:
                if self.conversation._messages and self.conversation._messages[-1].role == "user":
                    self.conversation._messages.pop()
                remaining = buf.flush()
                if remaining:
                    accum_stable += remaining
                stop_msg = f"*[Generation stopped: {e}]*"
                if not accum_stable:
                    accum_stable = stop_msg
                else:
                    accum_stable += f"\n\n{stop_msg}"
                self.call_from_thread(md_widget.update, accum_stable)
                self.call_from_thread(tail_widget.update, "")
            except Exception as exc:
                if self.conversation._messages and self.conversation._messages[-1].role == "user":
                    self.conversation._messages.pop()
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
            self.conversation.clear()
            history = self.query_one("#chat-history", VerticalScroll)
            history.remove_children()

        def action_quit(self) -> None:
            self._stop_event.set()
            self.exit(0)

else:
    TUI_CSS = ""
    AegisTUI = None  # type: ignore[assignment,misc]


def run_tui(
    config: AegisConfig,
    provider: ModelProvider,
    *,
    model: str | None = None,
    system: str | None = None,
    initial_prompt: str | None = None,
    no_memory: bool = False,
    max_turns: int | None = None,
) -> int:
    """Launch the interactive Textual TUI.

    Raises:
        ImportError: If textual is not installed.
    """
    if not TEXTUAL_AVAILABLE or AegisTUI is None:
        raise ImportError(
            'Textual is not installed. Install with: pip install "aegis-ai[tui]"'
        )
    app = AegisTUI(
        config=config,
        provider=provider,
        model=model,
        system=system,
        initial_prompt=initial_prompt,
        no_memory=no_memory,
        max_turns=max_turns,
    )
    app.run()
    return 0
