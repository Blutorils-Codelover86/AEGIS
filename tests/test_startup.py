"""Unit tests for the AEGIS startup subsystem and presentation modes."""
from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from typing import Any, Callable, Iterator, Mapping, Sequence
from unittest.mock import MagicMock, patch

from aegis.cli.main import _build_parser, _handle_chat, main
from aegis.cli.startup import CheckState, StartupCheck, StartupReport, run_startup
from aegis.cli.theme import make_console
from aegis.config import AegisConfig
from aegis.interfaces import ModelProvider, MultiTurnModelProvider


class FakeStartupProvider(MultiTurnModelProvider):
    """Mock provider for startup diagnostic tests."""

    name = "fake-startup"

    def __init__(
        self,
        *,
        is_healthy: bool = True,
        models: Sequence[str] | None = None,
        last_error_msg: str | None = None,
    ) -> None:
        self.is_healthy = is_healthy
        self.models_list = list(models) if models is not None else ["qwen2.5-coder:1.5b", "gemma3:1b"]
        self.last_error_msg = last_error_msg

    def generate(self, prompt: str, **kwargs: Any) -> str:
        return "fake response"

    def generate_stream(self, prompt: str, **kwargs: Any) -> Iterator[str]:
        yield "fake stream"

    def chat(self, messages: Sequence[Mapping[str, str]], **kwargs: Any) -> str:
        return "fake chat response"

    def chat_stream(self, messages: Sequence[Mapping[str, str]], **kwargs: Any) -> Iterator[str]:
        yield "fake chat stream"

    def health(self) -> bool:
        return self.is_healthy

    def list_models(self) -> list[str]:
        return list(self.models_list)

    def last_error(self) -> str | None:
        return self.last_error_msg


class TestStartup(unittest.TestCase):
    """Test startup subsystem checks, diagnostic report, and presentation modes."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = self.temp_dir.name
        self.db_path = os.path.join(self.base_dir, "memory.db")
        self.audit_path = os.path.join(self.base_dir, "audit.jsonl")
        self.stop_path = os.path.join(self.base_dir, "STOP")
        self.config = AegisConfig(
            model="qwen2.5-coder:1.5b",
            ollama_host="http://127.0.0.1:11434",
            memory_db=self.db_path,
            audit_log=self.audit_path,
            stop_file=self.stop_path,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_health_false_not_online_or_ready(self) -> None:
        """When provider.health() is False, Ollama is UNAVAILABLE and model is SKIPPED (never READY)."""
        provider = FakeStartupProvider(is_healthy=False, last_error_msg="Connection refused")
        console = make_console(plain=True)
        report = run_startup(
            console,
            self.config,
            provider,
            model="qwen2.5-coder:1.5b",
            plain=True,
            animation=False,
        )

        self.assertFalse(report.ollama_available)
        self.assertFalse(report.model_available)

        checks_by_key = {c.key: c for c in report.checks}
        self.assertEqual(checks_by_key["ollama"].state, CheckState.UNAVAILABLE)
        self.assertFalse(checks_by_key["ollama"].ok)
        self.assertIn("Connection refused", checks_by_key["ollama"].detail)

        # Model must be SKIPPED, never READY
        self.assertEqual(checks_by_key["model"].state, CheckState.SKIPPED)
        self.assertFalse(checks_by_key["model"].ok)
        self.assertNotEqual(checks_by_key["model"].state, CheckState.READY)

    def test_missing_model_not_ready(self) -> None:
        """When requested model is not in provider.list_models(), model check is DEGRADED (not READY)."""
        provider = FakeStartupProvider(models=["other-model:latest"])
        console = make_console(plain=True)
        report = run_startup(
            console,
            self.config,
            provider,
            model="qwen2.5-coder:1.5b",
            plain=True,
            animation=False,
        )

        self.assertTrue(report.ollama_available)
        self.assertFalse(report.model_available)

        checks_by_key = {c.key: c for c in report.checks}
        self.assertEqual(checks_by_key["model"].state, CheckState.DEGRADED)
        self.assertFalse(checks_by_key["model"].ok)
        self.assertIn("qwen2.5-coder:1.5b", checks_by_key["model"].detail)

    def test_bad_memory_path_not_ready(self) -> None:
        """When memory database cannot be accessed/created, memory check is UNAVAILABLE (not READY)."""
        bad_config = AegisConfig(
            model="qwen2.5-coder:1.5b",
            ollama_host="http://127.0.0.1:11434",
            memory_db="/proc/nonexistent_invalid_path/mem.db",
            audit_log=self.audit_path,
            stop_file=self.stop_path,
        )
        provider = FakeStartupProvider()
        console = make_console(plain=True)
        report = run_startup(
            console,
            bad_config,
            provider,
            model="qwen2.5-coder:1.5b",
            plain=True,
            animation=False,
        )

        self.assertFalse(report.memory_available)
        checks_by_key = {c.key: c for c in report.checks}
        self.assertEqual(checks_by_key["memory"].state, CheckState.UNAVAILABLE)
        self.assertFalse(checks_by_key["memory"].ok)
        self.assertTrue(len(checks_by_key["memory"].detail) > 0)

    def test_stop_file_stopped(self) -> None:
        """When stop file exists, stop check is STOPPED and report.stop_requested is True."""
        with open(self.stop_path, "w", encoding="utf-8") as f:
            f.write("STOP requested")

        provider = FakeStartupProvider()
        console = make_console(plain=True)
        report = run_startup(
            console,
            self.config,
            provider,
            model="qwen2.5-coder:1.5b",
            plain=True,
            animation=False,
        )

        self.assertTrue(report.stop_requested)
        checks_by_key = {c.key: c for c in report.checks}
        self.assertEqual(checks_by_key["stop"].state, CheckState.STOPPED)
        self.assertFalse(checks_by_key["stop"].ok)

    def test_fast_animated_elapsed_under_budget(self) -> None:
        """Animated startup completes within the budget (< 2.5s)."""
        provider = FakeStartupProvider()
        out = io.StringIO()
        console = make_console(force_terminal=True, file=out)
        console.no_color = False

        report = run_startup(
            console,
            self.config,
            provider,
            model="qwen2.5-coder:1.5b",
            plain=False,
            animation=True,
            env={},
        )
        self.assertGreaterEqual(report.elapsed, 1.0)
        self.assertLess(report.elapsed, 2.5)
        # Should include shield emblem in animated terminal mode
        self.assertIn("AEGIS", out.getvalue())
        self.assertIn(".---.", out.getvalue())

    def test_plain_zero_esc_bytes(self) -> None:
        """Plain mode startup produces status lines with zero ANSI escape codes."""
        provider = FakeStartupProvider()
        out = io.StringIO()
        console = make_console(plain=True, file=out)

        run_startup(
            console,
            self.config,
            provider,
            model="qwen2.5-coder:1.5b",
            plain=True,
            animation=False,
        )
        output = out.getvalue()
        self.assertNotIn("\x1b", output)
        self.assertIn("[ready] identity:", output)
        self.assertIn("[ready] memory:", output)
        self.assertIn("[ready] permissions:", output)
        self.assertIn("[ready] stop:", output)
        self.assertIn("[ready] ollama:", output)
        self.assertIn("[ready] model:", output)

    def test_non_tty_success_no_emblem(self) -> None:
        """Non-TTY invocation with successful checks outputs nothing to stdout."""
        provider = FakeStartupProvider()
        out = io.StringIO()
        err = io.StringIO()
        console = make_console(force_terminal=False, file=out)

        with redirect_stderr(err):
            report = run_startup(
                console,
                self.config,
                provider,
                model="qwen2.5-coder:1.5b",
                plain=False,
                animation=True,
            )

        self.assertEqual(out.getvalue(), "")
        self.assertEqual(err.getvalue(), "")

    def test_non_tty_failure_emits_to_stderr(self) -> None:
        """Non-TTY invocation with failed checks writes warnings to stderr."""
        provider = FakeStartupProvider(is_healthy=False, last_error_msg="Connection timed out")
        out = io.StringIO()
        err = io.StringIO()
        console = make_console(force_terminal=False, file=out)

        with redirect_stderr(err):
            report = run_startup(
                console,
                self.config,
                provider,
                model="qwen2.5-coder:1.5b",
                plain=False,
                animation=True,
            )

        self.assertEqual(out.getvalue(), "")
        self.assertIn("aegis warning: ollama is unavailable", err.getvalue())

    def test_animation_false_no_emblem(self) -> None:
        """When animation=False, the shield emblem is omitted."""
        provider = FakeStartupProvider()
        out = io.StringIO()
        console = make_console(force_terminal=True, file=out)

        run_startup(
            console,
            self.config,
            provider,
            model="qwen2.5-coder:1.5b",
            plain=False,
            animation=False,
        )
        self.assertNotIn(".---.", out.getvalue())
        self.assertNotIn("interchangeable model runtime", out.getvalue())


class TestStartupCLIRegression(unittest.TestCase):
    """Test CLI argument parsing and process execution integration."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = self.temp_dir.name
        self.db_path = os.path.join(self.base_dir, "memory.db")
        self.audit_path = os.path.join(self.base_dir, "audit.jsonl")
        self.stop_path = os.path.join(self.base_dir, "STOP")
        self.config = AegisConfig(
            model="qwen2.5-coder:1.5b",
            ollama_host="http://127.0.0.1:11434",
            memory_db=self.db_path,
            audit_log=self.audit_path,
            stop_file=self.stop_path,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_no_animation_parses_globally_and_on_chat(self) -> None:
        """--no-animation is accepted globally and as a chat/status subcommand flag."""
        parser = _build_parser()

        args1 = parser.parse_args(["--no-animation", "chat", "--prompt", "hi"])
        self.assertTrue(getattr(args1, "no_animation", False))

        args2 = parser.parse_args(["chat", "--no-animation", "--prompt", "hi"])
        self.assertTrue(getattr(args2, "no_animation", False))

        args3 = parser.parse_args(["status", "--no-animation"])
        self.assertTrue(getattr(args3, "no_animation", False))

    def test_plain_startup_zero_ansi_through_cli(self) -> None:
        """aegis chat --prompt ... --plain produces zero ANSI escape bytes from end to end."""
        provider = FakeStartupProvider()
        out = io.StringIO()
        with redirect_stdout(out):
            rc = main(
                ["chat", "--prompt", "hello test", "--plain"],
                provider=provider,
                config=self.config,
            )
        self.assertEqual(rc, 0)
        raw_output = out.getvalue()
        self.assertNotIn("\x1b", raw_output)

    def test_startup_called_once_across_two_turn_session(self) -> None:
        """run_startup is called exactly once across a multi-turn interactive session."""
        provider = FakeStartupProvider()
        inputs = ["Turn 1", "Turn 2"]
        input_iter = iter(inputs)

        def mock_input(prompt: str = "") -> str:
            try:
                return next(input_iter)
            except StopIteration:
                raise EOFError()

        out = io.StringIO()
        with patch("aegis.cli.main.run_startup", wraps=run_startup) as mock_startup:
            with redirect_stdout(out), patch("builtins.input", side_effect=mock_input):
                rc = main(
                    ["chat", "--plain"],
                    provider=provider,
                    config=self.config,
                )
            self.assertEqual(rc, 0)
            self.assertEqual(mock_startup.call_count, 1)

    def test_header_single_shot_non_plain(self) -> None:
        """Header appears exactly once in non-plain single-shot chat."""
        provider = FakeStartupProvider()
        out = io.StringIO()
        console = make_console(force_terminal=True, file=out)
        args = _build_parser().parse_args(["chat", "--prompt", "hi", "--no-animation"])

        rc = _handle_chat(args, provider=provider, config=self.config, console=console, is_plain=False)
        self.assertEqual(rc, 0)
        output = out.getvalue()
        self.assertEqual(output.count("AEGIS · qwen2.5-coder:1.5b · local"), 1)

    def test_header_interactive_non_plain(self) -> None:
        """Header appears exactly once in non-plain interactive chat."""
        provider = FakeStartupProvider()
        out = io.StringIO()
        console = make_console(force_terminal=True, file=out)
        args = _build_parser().parse_args(["chat", "--no-animation", "--max-turns", "1"])

        with patch("builtins.input", side_effect=EOFError):
            rc = _handle_chat(args, provider=provider, config=self.config, console=console, is_plain=False)
        self.assertEqual(rc, 0)
        output = out.getvalue()
        self.assertEqual(output.count("AEGIS · qwen2.5-coder:1.5b · local"), 1)

    def test_header_absent_in_plain_mode(self) -> None:
        """Header is absent in plain mode for single-shot and interactive chat."""
        provider = FakeStartupProvider()

        # Single-shot
        out1 = io.StringIO()
        console1 = make_console(plain=True, file=out1)
        args1 = _build_parser().parse_args(["chat", "--prompt", "hi", "--plain", "--no-animation"])
        rc1 = _handle_chat(args1, provider=provider, config=self.config, console=console1, is_plain=True)
        self.assertEqual(rc1, 0)
        self.assertEqual(out1.getvalue().count("AEGIS · qwen2.5-coder:1.5b · local"), 0)

        # Interactive
        out2 = io.StringIO()
        console2 = make_console(plain=True, file=out2)
        args2 = _build_parser().parse_args(["chat", "--plain", "--no-animation", "--max-turns", "1"])
        with patch("builtins.input", side_effect=EOFError):
            rc2 = _handle_chat(args2, provider=provider, config=self.config, console=console2, is_plain=True)
        self.assertEqual(rc2, 0)
        self.assertEqual(out2.getvalue().count("AEGIS · qwen2.5-coder:1.5b · local"), 0)


if __name__ == "__main__":
    unittest.main()
