"""Unit tests for chat continuity and identity prompt injection across turns."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from typing import Any, Callable, Iterator, Mapping, Sequence
from unittest.mock import MagicMock, patch

from aegis.cli.main import main
from aegis.config import AegisConfig
from aegis.conversation import Conversation
from aegis.errors import StopRequested
from aegis.identity import identity_system_prompt
from aegis.interfaces import MultiTurnModelProvider


class FakeChatProvider(MultiTurnModelProvider):
    """Fake model provider implementing MultiTurnModelProvider for test verification."""

    name = "fake-chat"

    def __init__(
        self,
        responses: Sequence[Sequence[str]] | None = None,
        *,
        raise_interrupt_on_turn: int | None = None,
    ) -> None:
        self.responses = list(responses) if responses is not None else [["Assistant answer 1"], ["Assistant answer 2"]]
        self.call_history: list[dict[str, Any]] = []
        self.raise_interrupt_on_turn = raise_interrupt_on_turn
        self.turn_count = 0

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        stop: Sequence[str] | None = None,
        stop_check: Callable[[], None] | None = None,
    ) -> str:
        return "fallback"

    def generate_stream(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        stop: Sequence[str] | None = None,
        stop_check: Callable[[], None] | None = None,
    ) -> Iterator[str]:
        yield "fallback stream"

    def chat(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        model: str | None = None,
        stop: Sequence[str] | None = None,
        stop_check: Callable[[], None] | None = None,
    ) -> str:
        chunks = list(self.chat_stream(messages, model=model, stop=stop, stop_check=stop_check))
        return "".join(chunks)

    def chat_stream(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        model: str | None = None,
        stop: Sequence[str] | None = None,
        stop_check: Callable[[], None] | None = None,
    ) -> Iterator[str]:
        self.turn_count += 1
        current_turn = self.turn_count
        self.call_history.append({
            "messages": [dict(m) for m in messages],
            "model": model,
            "stop": stop,
        })

        if self.raise_interrupt_on_turn == current_turn:
            yield "partial chunk before interrupt"
            raise KeyboardInterrupt("Simulated Ctrl+C during streaming")

        idx = current_turn - 1
        chunks = self.responses[idx] if idx < len(self.responses) else ["Default answer"]
        for chunk in chunks:
            if stop_check:
                stop_check()
            yield chunk

    def list_models(self) -> list[str]:
        return ["qwen2.5-coder:1.5b", "gemma3:1b"]

    def health(self) -> bool:
        return True


class FakeLegacyStreamProvider:
    """Provider that implements only generate_stream (no chat_stream)."""

    name = "fake-legacy-stream"

    def __init__(self, responses: Sequence[str] | None = None) -> None:
        self.responses = list(responses) if responses is not None else ["Answer 1", "Answer 2"]
        self.call_history: list[dict[str, Any]] = []
        self.turn_count = 0

    def generate_stream(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        stop: Sequence[str] | None = None,
        stop_check: Callable[[], None] | None = None,
    ) -> Iterator[str]:
        self.call_history.append({
            "prompt": prompt,
            "system": system,
            "model": model,
        })
        idx = self.turn_count
        self.turn_count += 1
        resp = self.responses[idx] if idx < len(self.responses) else "Default answer"
        yield resp

    def list_models(self) -> list[str]:
        return ["qwen2.5-coder:1.5b", "gemma3:1b"]

    def health(self) -> bool:
        return True


class FakeLegacyGenerateProvider:
    """Provider that implements only generate (no streaming)."""

    name = "fake-legacy-generate"

    def __init__(self, responses: Sequence[str] | None = None) -> None:
        self.responses = list(responses) if responses is not None else ["Answer 1", "Answer 2"]
        self.call_history: list[dict[str, Any]] = []
        self.turn_count = 0

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        stop: Sequence[str] | None = None,
        stop_check: Callable[[], None] | None = None,
    ) -> str:
        self.call_history.append({
            "prompt": prompt,
            "system": system,
            "model": model,
        })
        idx = self.turn_count
        self.turn_count += 1
        return self.responses[idx] if idx < len(self.responses) else "Default answer"

    def list_models(self) -> list[str]:
        return ["qwen2.5-coder:1.5b", "gemma3:1b"]

    def health(self) -> bool:
        return True


class TestChatContinuity(unittest.TestCase):
    """Test conversation continuity and identity injection."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        base_dir = self.temp_dir.name
        self.config = AegisConfig(
            model="qwen2.5-coder:1.5b",
            ollama_host="http://127.0.0.1:11434",
            memory_db=f"{base_dir}/memory.db",
            audit_log=f"{base_dir}/audit.jsonl",
            stop_file=f"{base_dir}/STOP",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_single_shot_includes_system_and_user(self) -> None:
        """Single-shot chat invocation includes identity system prompt and user message."""
        provider = FakeChatProvider([["Hello there!"]])
        out = io.StringIO()
        with redirect_stdout(out):
            rc = main(
                ["chat", "--prompt", "Introduce yourself", "--plain"],
                provider=provider,
                config=self.config,
            )
        self.assertEqual(rc, 0)
        self.assertEqual(len(provider.call_history), 1)

        call = provider.call_history[0]
        messages = call["messages"]
        self.assertEqual(len(messages), 2)
        # Message 0: system prompt with identity and model
        self.assertEqual(messages[0]["role"], "system")
        expected_system = identity_system_prompt(model=self.config.model)
        self.assertEqual(messages[0]["content"], expected_system)
        self.assertIn("AEGIS", messages[0]["content"])
        self.assertIn(self.config.model, messages[0]["content"])

        # Message 1: user prompt
        self.assertEqual(messages[1]["role"], "user")
        self.assertEqual(messages[1]["content"], "Introduce yourself")

    def test_two_turn_interactive_continuity(self) -> None:
        """Interactive chat preserves prior turns: turn 2 sends [system, user1, assistant1, user2]."""
        provider = FakeChatProvider([["I am AEGIS."], ["I can assist you."]])
        out = io.StringIO()

        # Simulate two turns of user inputs: "Hello" then "What can you do?", then EOF
        inputs = ["Hello", "What can you do?"]
        input_iter = iter(inputs)

        def mock_input(prompt: str = "") -> str:
            try:
                return next(input_iter)
            except StopIteration:
                raise EOFError()

        with redirect_stdout(out), patch("builtins.input", side_effect=mock_input):
            rc = main(
                ["chat", "--plain"],
                provider=provider,
                config=self.config,
            )
        self.assertEqual(rc, 0)
        self.assertEqual(len(provider.call_history), 2)

        # First call: [system, user1]
        call_1 = provider.call_history[0]
        expected_system = identity_system_prompt(model=self.config.model)
        self.assertEqual(
            call_1["messages"],
            [
                {"role": "system", "content": expected_system},
                {"role": "user", "content": "Hello"},
            ],
        )

        # Second call: [system, user1, assistant1, user2]
        call_2 = provider.call_history[1]
        self.assertEqual(
            call_2["messages"],
            [
                {"role": "system", "content": expected_system},
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "I am AEGIS."},
                {"role": "user", "content": "What can you do?"},
            ],
        )

    def test_continuity_with_different_model_ids(self) -> None:
        """Different --model arguments inject their exact model id into system message."""
        for target_model in ["gemma3:1b", "qwen2.5-coder:1.5b"]:
            provider = FakeChatProvider([["Reply from model"]])
            out = io.StringIO()
            with redirect_stdout(out):
                rc = main(
                    ["chat", "--prompt", "ping", "--model", target_model, "--plain"],
                    provider=provider,
                    config=self.config,
                )
            self.assertEqual(rc, 0)
            call = provider.call_history[0]
            self.assertEqual(call["model"], target_model)
            system_msg = call["messages"][0]["content"]
            self.assertIn(target_model, system_msg)
            self.assertEqual(system_msg, identity_system_prompt(model=target_model))

    def test_partial_assistant_not_stored_on_interrupt(self) -> None:
        """When an interrupt occurs during streaming, no partial assistant turn is retained."""
        provider = FakeChatProvider(
            [["First turn ok"]],
            raise_interrupt_on_turn=1,
        )
        out = io.StringIO()
        with redirect_stdout(out):
            rc = main(
                ["chat", "--prompt", "will be interrupted", "--plain"],
                provider=provider,
                config=self.config,
            )
        self.assertEqual(rc, 130)

    def test_ollama_provider_chat_and_chat_stream_payload(self) -> None:
        """Verify OllamaProvider.chat and chat_stream format payloads to /api/chat properly."""
        from aegis.provider.ollama import OllamaProvider

        provider = OllamaProvider(host="http://127.0.0.1:11434", default_model="qwen2.5-coder:1.5b")

        mock_resp = MagicMock()
        mock_resp.readline.side_effect = [
            b'{"model":"qwen2.5-coder:1.5b","message":{"role":"assistant","content":"chunk1"},"done":false}\n',
            b'{"model":"qwen2.5-coder:1.5b","message":{"role":"assistant","content":"chunk2"},"done":true}\n',
            b"",
        ]
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.fileno.side_effect = OSError("no fileno")

        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "usr"},
        ]

        with patch.object(provider, "_cancellable_open", return_value=mock_resp):
            chunks = list(provider.chat_stream(messages))
            self.assertEqual(chunks, ["chunk1", "chunk2"])

        # Test non-streaming chat
        mock_chat_resp = MagicMock()
        mock_chat_resp.read.return_value = b'{"model":"qwen2.5-coder:1.5b","message":{"role":"assistant","content":"full answer"},"done":true}'
        mock_chat_resp.__enter__.return_value = mock_chat_resp

        with patch.object(provider, "_open", return_value=mock_chat_resp):
            res = provider.chat(messages)
            self.assertEqual(res, "full answer")

    def test_legacy_generate_stream_receives_identity_and_continuity(self) -> None:
        """Legacy provider implementing only generate_stream receives identity and conversation context."""
        provider = FakeLegacyStreamProvider(responses=["I will remember Project Chimera.", "The project is Chimera."])
        inputs = ["Remember project name: Chimera", "What was the project name?"]

        with patch("builtins.input", side_effect=inputs):
            out = io.StringIO()
            with redirect_stdout(out):
                rc = main(
                    ["chat", "--plain", "--max-turns", "2"],
                    provider=provider,
                    config=self.config,
                )
        self.assertEqual(rc, 0)
        self.assertEqual(len(provider.call_history), 2)

        # Turn 1: receives composed identity system prompt and initial prompt
        call1 = provider.call_history[0]
        self.assertIn("AEGIS", call1["system"])
        self.assertIn("qwen2.5-coder:1.5b", call1["system"])
        self.assertEqual(call1["prompt"], "Remember project name: Chimera")

        # Turn 2: receives composed identity system prompt and formatted multi-turn history
        call2 = provider.call_history[1]
        self.assertIn("AEGIS", call2["system"])
        self.assertIn("qwen2.5-coder:1.5b", call2["system"])
        self.assertIn("User: Remember project name: Chimera", call2["prompt"])
        self.assertIn("Assistant: I will remember Project Chimera.", call2["prompt"])
        self.assertIn("User: What was the project name?", call2["prompt"])

    def test_legacy_generate_receives_identity_and_continuity(self) -> None:
        """Legacy provider implementing only generate receives identity and conversation context."""
        provider = FakeLegacyGenerateProvider(responses=["I will remember Omega.", "The code is Omega."])
        inputs = ["Remember code: Omega", "What was the code?"]

        with patch("builtins.input", side_effect=inputs):
            out = io.StringIO()
            with redirect_stdout(out):
                rc = main(
                    ["chat", "--plain", "--max-turns", "2"],
                    provider=provider,
                    config=self.config,
                )
        self.assertEqual(rc, 0)
        self.assertEqual(len(provider.call_history), 2)

        # Turn 1
        call1 = provider.call_history[0]
        self.assertIn("AEGIS", call1["system"])
        self.assertEqual(call1["prompt"], "Remember code: Omega")

        # Turn 2: formatted history
        call2 = provider.call_history[1]
        self.assertIn("AEGIS", call2["system"])
        self.assertIn("User: Remember code: Omega", call2["prompt"])
        self.assertIn("Assistant: I will remember Omega.", call2["prompt"])
        self.assertIn("User: What was the code?", call2["prompt"])


if __name__ == "__main__":
    unittest.main()
