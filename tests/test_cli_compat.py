"""Compatibility tests for Project AEGIS CLI (Phase A)."""
from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from aegis.cli.main import main
from aegis.config import AegisConfig


class FakeProvider:
    name: str = "fake"

    def __init__(self, response: str = "Test response") -> None:
        self.response = response
        self.calls: list[dict] = []

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        stop_check: object = None,
        **kwargs: object,
    ) -> str:
        self.calls.append({"prompt": prompt, "system": system, "model": model})
        if stop_check is not None and callable(stop_check):
            stop_check()
        return self.response

    def health(self) -> bool:
        return True

    def list_models(self) -> list[str]:
        return ["fake-model:latest"]


class TestCLICompat(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config = AegisConfig(
            model="default-model:1b",
            ollama_host="http://127.0.0.1:11434",
            memory_db=f"{self.temp_dir.name}/memory.db",
            audit_log=f"{self.temp_dir.name}/audit.jsonl",
            stop_file=f"{self.temp_dir.name}/STOP",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_all_four_commands_preserved(self) -> None:
        fake = FakeProvider()

        # 1. status
        out = io.StringIO()
        with redirect_stdout(out):
            rc = main(["status", "--plain"], provider=fake, config=self.config)
        self.assertEqual(rc, 0)
        self.assertIn("Model: default-model:1b", out.getvalue())

        # 2. memory
        out = io.StringIO()
        with redirect_stdout(out):
            rc = main(["memory", "list", "--plain"], config=self.config)
        self.assertEqual(rc, 0)

        # 3. chat
        out = io.StringIO()
        with redirect_stdout(out):
            rc = main(["chat", "--prompt", "hello", "--plain"], provider=fake, config=self.config)
        self.assertEqual(rc, 0)
        self.assertIn("Test response", out.getvalue())

        # 4. stop
        out = io.StringIO()
        with redirect_stdout(out):
            rc = main(["stop", "--plain"], config=self.config)
        self.assertEqual(rc, 0)
        self.assertIn("Stop requested", out.getvalue())

    def test_model_gemma3_forwarded_to_provider(self) -> None:
        fake = FakeProvider()
        out = io.StringIO()
        with redirect_stdout(out):
            rc = main(
                ["chat", "--prompt", "hi", "--model", "gemma3:4b", "--plain"],
                provider=fake,
                config=self.config,
            )
        self.assertEqual(rc, 0)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(fake.calls[0]["model"], "gemma3:4b")

    def test_plain_has_no_ansi(self) -> None:
        fake = FakeProvider(response="# Heading\n- Item 1\n**bold text**")
        out = io.StringIO()
        with redirect_stdout(out):
            rc = main(
                ["chat", "--prompt", "format me", "--plain"],
                provider=fake,
                config=self.config,
            )
        self.assertEqual(rc, 0)
        output = out.getvalue()
        # Verify no ANSI escape sequence (e.g. \033 or \x1b)
        self.assertNotIn("\033", output)
        self.assertNotIn("\x1b", output)
        self.assertIn("# Heading", output)
        self.assertIn("**bold text**", output)

    def test_no_color_env_honored(self) -> None:
        fake = FakeProvider(response="Plain response with NO_COLOR")
        with patch.dict(os.environ, {"NO_COLOR": "1"}):
            out = io.StringIO()
            with redirect_stdout(out):
                rc = main(
                    ["chat", "--prompt", "hello"],
                    provider=fake,
                    config=self.config,
                )
            self.assertEqual(rc, 0)
            output = out.getvalue()
            self.assertNotIn("\033", output)
            self.assertNotIn("\x1b", output)
            self.assertIn("Plain response with NO_COLOR", output)

    def test_ctrl_c_clean_exit_code_130(self) -> None:
        fake = FakeProvider()
        with patch.object(fake, "generate", side_effect=KeyboardInterrupt):
            out = io.StringIO()
            with redirect_stdout(out):
                rc = main(
                    ["chat", "--prompt", "interrupt me"],
                    provider=fake,
                    config=self.config,
                )
            self.assertEqual(rc, 130)
            self.assertIn("Stop requested by user", out.getvalue())

    def test_help_flags(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            rc = main(["--help"], config=self.config)
        self.assertEqual(rc, 0)
        self.assertIn("usage: aegis", out.getvalue())

        out_chat = io.StringIO()
        with redirect_stdout(out_chat):
            rc = main(["chat", "--help"], config=self.config)
        self.assertEqual(rc, 0)
        self.assertIn("--plain", out_chat.getvalue())
        self.assertIn("--model", out_chat.getvalue())

    def test_global_flags_before_command(self) -> None:
        """Global --plain and --no-color placed before subcommand must not be overwritten by subparser."""
        fake = FakeProvider(response="# Heading\n- Item 1\n**bold text**")

        # 1. aegis --plain chat
        out = io.StringIO()
        with redirect_stdout(out):
            rc = main(["--plain", "chat", "--prompt", "test prompt"], provider=fake, config=self.config)
        self.assertEqual(rc, 0)
        self.assertNotIn("\033", out.getvalue())
        self.assertNotIn("\x1b", out.getvalue())
        self.assertIn("# Heading", out.getvalue())

        # 2. aegis --no-color chat
        out = io.StringIO()
        with redirect_stdout(out):
            rc = main(["--no-color", "chat", "--prompt", "test prompt"], provider=fake, config=self.config)
        self.assertEqual(rc, 0)
        self.assertNotIn("\033", out.getvalue())
        self.assertNotIn("\x1b", out.getvalue())

        # 3. Nested memory commands: root, parent, and subparser placements
        for argv in [
            ["--plain", "memory", "list"],
            ["memory", "--plain", "list"],
            ["memory", "list", "--plain"],
        ]:
            out = io.StringIO()
            with redirect_stdout(out):
                rc = main(argv, config=self.config)
            self.assertEqual(rc, 0, f"Failed for {argv}")
            self.assertNotIn("\033", out.getvalue())

    def test_narrow_terminal_width_columns_20(self) -> None:
        """Running with narrow terminal COLUMNS=20 does not crash or raise layout exceptions."""
        fake = FakeProvider(response="Header | Value\n---|---\nRow 1 | 12345\n\n```python\nprint(1)\n```")
        out = io.StringIO()
        with patch.dict(os.environ, {"COLUMNS": "20"}):
            with redirect_stdout(out):
                rc = main(["chat", "--prompt", "test narrow"], provider=fake, config=self.config)
        self.assertEqual(rc, 0)
        self.assertTrue(len(out.getvalue()) > 0)

    def test_interrupted_stream_flushes_partial_buffer(self) -> None:
        """On KeyboardInterrupt during streaming, un-emitted buffer is flushed as literal text and rc is 130."""
        class InterruptingProvider:
            name = "interrupting"
            def generate_stream(self, prompt, **kwargs):
                yield "First paragraph.\n\n"
                yield "Partial **unclosed bold text"
                raise KeyboardInterrupt()
            def health(self):
                return True
            def list_models(self):
                return ["test:latest"]

        provider = InterruptingProvider()
        out = io.StringIO()
        with redirect_stdout(out):
            rc = main(["chat", "--prompt", "stream partial"], provider=provider, config=self.config)

        self.assertEqual(rc, 130)
        val = out.getvalue()
        # Ensure partial text was kept on screen
        self.assertIn("Partial **unclosed bold text", val)
        self.assertIn("Stop requested by user", val)


if __name__ == "__main__":
    unittest.main()
