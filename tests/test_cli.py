"""Unit tests for the AEGIS CLI."""
from __future__ import annotations

import io
import os
import re
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from typing import Sequence
from unittest.mock import patch

from aegis.audit import read_events
from aegis.cli.main import main
from aegis.config import AegisConfig
from aegis.errors import ProviderError
from aegis.stop import StopController


class FakeProvider:
    name: str = "fake"

    def __init__(
        self,
        *,
        response: str = "Fake model output",
        models: list[str] | None = None,
        is_healthy: bool = True,
        raise_on_generate: Exception | None = None,
    ) -> None:
        self.response = response
        self.models = models if models is not None else ["fake-model:latest"]
        self.is_healthy = is_healthy
        self.raise_on_generate = raise_on_generate
        self.calls: list[dict] = []

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        stop: Sequence[str] | None = None,
    ) -> str:
        self.calls.append({"prompt": prompt, "system": system, "model": model, "stop": stop})
        if self.raise_on_generate is not None:
            raise self.raise_on_generate
        return self.response

    def list_models(self) -> list[str]:
        return list(self.models)

    def health(self) -> bool:
        return self.is_healthy


class TestCLI(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = self.temp_dir.name
        self.db_path = os.path.join(self.base_dir, "memory.db")
        self.audit_path = os.path.join(self.base_dir, "audit.jsonl")
        self.stop_path = os.path.join(self.base_dir, "STOP")
        self.config = AegisConfig(
            model="test-model:1b",
            ollama_host="http://127.0.0.1:11434",
            memory_db=self.db_path,
            audit_log=self.audit_path,
            stop_file=self.stop_path,
            max_chat_turns=3,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_memory_workflow(self) -> None:
        # Save memory
        out = io.StringIO()
        with redirect_stdout(out):
            rc = main(
                [
                    "memory",
                    "save",
                    "Remember this critical detail",
                    "--tag",
                    "important",
                    "project",
                    "--source",
                    "test",
                ],
                config=self.config,
            )
        self.assertEqual(rc, 0)
        save_out = out.getvalue()
        self.assertIn("Saved memory", save_out)
        match = re.search(r"\[([a-f0-9]+)\]", save_out)
        self.assertIsNotNone(match)
        rec_id = match.group(1)

        # Search memory
        out = io.StringIO()
        with redirect_stdout(out):
            rc = main(["memory", "search", "critical"], config=self.config)
        self.assertEqual(rc, 0)
        self.assertIn(rec_id, out.getvalue())
        self.assertIn("Remember this critical detail", out.getvalue())

        # List memory
        out = io.StringIO()
        with redirect_stdout(out):
            rc = main(["memory", "list", "--limit", "10"], config=self.config)
        self.assertEqual(rc, 0)
        self.assertIn(rec_id, out.getvalue())

        # Delete memory
        out = io.StringIO()
        with redirect_stdout(out):
            rc = main(["memory", "delete", rec_id], config=self.config)
        self.assertEqual(rc, 0)
        self.assertIn(f"Deleted memory [{rec_id}]", out.getvalue())

        # Search again should find nothing
        out = io.StringIO()
        with redirect_stdout(out):
            rc = main(["memory", "search", "critical"], config=self.config)
        self.assertEqual(rc, 0)
        self.assertIn("No memories found", out.getvalue())

        # Check audit log recorded cli.command events
        events = read_events(self.audit_path)
        self.assertGreaterEqual(len(events), 4)
        for ev in events:
            self.assertEqual(ev.event, "cli.command")
            self.assertEqual(ev.actor, "user")

    def test_chat_prompt_single_shot(self) -> None:
        fake = FakeProvider(response="I am AEGIS AI assistant")
        out = io.StringIO()
        with redirect_stdout(out):
            rc = main(
                ["chat", "--prompt", "hi", "--system", "Be helpful"],
                provider=fake,
                config=self.config,
            )
        self.assertEqual(rc, 0)
        self.assertIn("I am AEGIS AI assistant", out.getvalue())
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(fake.calls[0]["prompt"], "hi")
        self.assertEqual(fake.calls[0]["system"], "Be helpful")

    def test_status_healthy_returns_zero(self) -> None:
        fake = FakeProvider(models=["model-a", "model-b"], is_healthy=True)
        out = io.StringIO()
        with redirect_stdout(out):
            rc = main(["status"], provider=fake, config=self.config)
        self.assertEqual(rc, 0)
        output = out.getvalue()
        self.assertIn("Model: test-model:1b", output)
        self.assertIn("Ollama Host: http://127.0.0.1:11434", output)
        self.assertIn("Provider Health: True", output)
        self.assertIn("model-a", output)
        self.assertIn("Memory DB:", output)
        self.assertIn("Audit Log:", output)
        self.assertIn("Stop State: clear", output)

    def test_status_unhealthy_returns_one(self) -> None:
        fake = FakeProvider(is_healthy=False)
        out = io.StringIO()
        with redirect_stdout(out):
            rc = main(["status"], provider=fake, config=self.config)
        self.assertEqual(rc, 1)
        output = out.getvalue()
        self.assertIn("Provider Health: False", output)

    def test_stop_and_clear(self) -> None:
        stop_ctrl = StopController(self.stop_path)
        self.assertFalse(stop_ctrl.is_stopped())

        # Request stop
        out = io.StringIO()
        with redirect_stdout(out):
            rc = main(["stop"], config=self.config)
        self.assertEqual(rc, 0)
        self.assertTrue(stop_ctrl.is_stopped())
        self.assertEqual(stop_ctrl.reason(), "user")

        # Clear stop
        out = io.StringIO()
        with redirect_stdout(out):
            rc = main(["stop", "--clear"], config=self.config)
        self.assertEqual(rc, 0)
        self.assertFalse(stop_ctrl.is_stopped())

    def test_chat_interactive_turns(self) -> None:
        fake = FakeProvider(response="Turn response")
        inputs = ["first query", "second query"]

        with patch("builtins.input", side_effect=inputs):
            out = io.StringIO()
            with redirect_stdout(out):
                rc = main(["chat", "--max-turns", "2"], provider=fake, config=self.config)
        self.assertEqual(rc, 0)
        self.assertEqual(len(fake.calls), 2)
        self.assertIn("Turn response", out.getvalue())

    def test_chat_keyboard_interrupt(self) -> None:
        fake = FakeProvider()
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            out = io.StringIO()
            with redirect_stdout(out):
                rc = main(["chat"], provider=fake, config=self.config)
        self.assertEqual(rc, 130)
        self.assertIn("Stop requested by user", out.getvalue())

    def test_aegis_error_handled(self) -> None:
        fake = FakeProvider(raise_on_generate=ProviderError("Connection refused by Ollama"))
        err = io.StringIO()
        with redirect_stderr(err):
            rc = main(["chat", "--prompt", "hi"], provider=fake, config=self.config)
        self.assertEqual(rc, 2)
        self.assertIn("Connection refused by Ollama", err.getvalue())

    def test_no_args_returns_two(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            rc = main([], config=self.config)
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
