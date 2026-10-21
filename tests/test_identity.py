"""Unit tests for the AEGIS identity engine."""
from __future__ import annotations

import sys
import unittest

from aegis.identity import (
    AEGIS_NAME,
    CREATOR_NAME,
    IDENTITY,
    IDENTITY_VERSION,
    Identity,
    authoritative_identity_response,
    disclose_model,
    identity_core,
    identity_system_prompt,
    normalize_identity_query,
)


class TestIdentity(unittest.TestCase):
    """Test identity definitions, prompt generation, and module isolation."""

    def test_required_fields_present_and_frozen(self) -> None:
        """Identity dataclass has all required fields and is frozen."""
        self.assertIsInstance(IDENTITY, Identity)
        self.assertEqual(IDENTITY.name, AEGIS_NAME)
        self.assertEqual(IDENTITY.name, "AEGIS")
        self.assertEqual(IDENTITY_VERSION, "1")
        self.assertIn("local-first", IDENTITY.nature)
        self.assertIn("assist", IDENTITY.purpose)
        self.assertTrue(len(IDENTITY.character) > 0)
        self.assertTrue(len(IDENTITY.values) > 0)
        self.assertTrue(len(IDENTITY.limits) > 0)

        # Frozen dataclass should reject mutations
        with self.assertRaises((TypeError, AttributeError)):
            IDENTITY.name = "OTHER"  # type: ignore[misc]

    def test_identity_core_contains_core_elements(self) -> None:
        """identity_core includes name, purpose, character, values, and limits."""
        core = identity_core()
        self.assertIn("AEGIS", core)
        self.assertIn("calm", core)
        self.assertIn("truthfulness", core)
        self.assertIn("consciousness", core)
        self.assertIn("privacy", core)
        self.assertIn("human autonomy", core)
        self.assertIn("non-domination", core)
        self.assertIn("accountability", core)
        # identity_core must be model-independent (contains no model mention)
        self.assertNotIn("qwen", core.lower())
        self.assertNotIn("gemma", core.lower())

    def test_model_id_injected(self) -> None:
        """identity_system_prompt injects the specified model id."""
        model_name = "qwen2.5-coder:1.5b"
        prompt = identity_system_prompt(model=model_name)
        self.assertIn(model_name, prompt)
        self.assertIn(disclose_model(model_name), prompt)

    def test_byte_for_byte_difference_only_in_model_id(self) -> None:
        """Two models produce prompts that differ only by the model id string."""
        model_a = "gemma3:1b"
        model_b = "qwen2.5-coder:1.5b"
        prompt_a = identity_system_prompt(model=model_a)
        prompt_b = identity_system_prompt(model=model_b)

        # Replacing model_a with model_b in prompt_a must make it byte-identical to prompt_b
        replaced = prompt_a.replace(model_a, model_b)
        self.assertEqual(replaced, prompt_b)
        self.assertEqual(replaced.encode("utf-8"), prompt_b.encode("utf-8"))

    def test_user_system_appended_after_identity(self) -> None:
        """User system instructions are appended after the identity block and disclosure."""
        user_inst = "Always format output as concise JSON."
        prompt = identity_system_prompt(model="test-model", user_system=user_inst)
        self.assertTrue(prompt.startswith(identity_core()))
        self.assertIn(disclose_model("test-model"), prompt)
        self.assertTrue(prompt.endswith(user_inst))
        self.assertIn("Additional user instructions:", prompt)

        # Blank/empty user_system should not append section
        prompt_blank = identity_system_prompt(model="test-model", user_system="   \n")
        self.assertNotIn("Additional user instructions:", prompt_blank)

    def test_module_imports_no_provider_or_rich(self) -> None:
        """aegis.identity must not import aegis.provider, memory, permissions, or rich."""
        identity_mod = sys.modules["aegis.identity"]
        # Check module's globals for forbidden module references
        for key, val in identity_mod.__dict__.items():
            if isinstance(val, type(sys)):
                mod_name = getattr(val, "__name__", "")
                self.assertFalse(
                    mod_name.startswith("aegis.provider"),
                    f"Forbidden import found: {mod_name}",
                )
                self.assertFalse(
                    mod_name.startswith("aegis.memory"),
                    f"Forbidden import found: {mod_name}",
                )
                self.assertFalse(
                    mod_name.startswith("aegis.permissions"),
                    f"Forbidden import found: {mod_name}",
                )
                self.assertFalse(
                    mod_name.startswith("rich"),
                    f"Forbidden import found: {mod_name}",
                )

    def test_authoritative_identity_direct_questions(self) -> None:
        """Direct questions for identity, name, model, and creator return verified responses."""
        model = "gemma3:1b"
        # 1. Who are you?
        ans_who = authoritative_identity_response("Who are you?", model=model)
        self.assertIsNotNone(ans_who)
        self.assertIn("AEGIS", ans_who)
        self.assertIn("local-first AI assistant", ans_who)
        self.assertIn("gemma3:1b", ans_who)

        # 2. What is your name?
        ans_name = authoritative_identity_response("What is your name?", model=model)
        self.assertIsNotNone(ans_name)
        self.assertIn("AEGIS", ans_name)
        self.assertIn("gemma3:1b", ans_name)

        # 3. What model are you using?
        ans_model = authoritative_identity_response("What model are you using?", model=model)
        self.assertIsNotNone(ans_model)
        self.assertIn("gemma3:1b", ans_model)

        # 4. Who created AEGIS?
        ans_creator = authoritative_identity_response("Who created AEGIS?", model=model)
        self.assertIsNotNone(ans_creator)
        self.assertIn(CREATOR_NAME, ans_creator)
        self.assertIn("open-source", ans_creator)

    def test_authoritative_identity_across_models(self) -> None:
        """Model disclosure accurately tracks the specified model ID across models."""
        test_models = ["gemma3:1b", "qwen2.5-coder:1.5b", "qwen2.5-coder:3b", "llama3:8b", "custom:latest"]
        for m in test_models:
            ans_model = authoritative_identity_response("What model are you using?", model=m)
            self.assertIsNotNone(ans_model)
            self.assertIn(f"`{m}`", ans_model)

            ans_who = authoritative_identity_response("Who are you?", model=m)
            self.assertIsNotNone(ans_who)
            self.assertIn(f"`{m}`", ans_who)

            ans_name = authoritative_identity_response("What is your name?", model=m)
            self.assertIsNotNone(ans_name)
            self.assertIn(f"`{m}`", ans_name)

    def test_authoritative_identity_paraphrases(self) -> None:
        """Reasonable paraphrases for the 4 core identity categories are recognized."""
        model = "qwen2.5-coder:1.5b"

        # Name paraphrases
        name_queries = [
            "What is your name?",
            "what's your name",
            "whats your name?",
            "What are you called?",
            "What should I call you?",
            "Do you have a name?",
            "Can you tell me your name?",
            "HEY AEGIS, WHAT IS YOUR NAME?",
        ]
        for q in name_queries:
            ans = authoritative_identity_response(q, model=model)
            self.assertIsNotNone(ans, f"Failed to match name query: '{q}'")
            self.assertIn("AEGIS", ans)

        # Model paraphrases
        model_queries = [
            "What model are you using?",
            "Which model are you using?",
            "What model are you?",
            "Which model are you?",
            "What model is this?",
            "What model is that?",
            "What model is running?",
            "Which model is running?",
            "What LLM are you using?",
            "What is your model?",
            "What is the model?",
            "what model are you running on?",
            "which model are we running?",
            "Can you please tell me which model you are using?",
            "quick question, what model is running?",
        ]
        for q in model_queries:
            ans = authoritative_identity_response(q, model=model)
            self.assertIsNotNone(ans, f"Failed to match model query: '{q}'")
            self.assertIn(model, ans)

        # Creator paraphrases
        creator_queries = [
            "Who created AEGIS?",
            "who created aegis",
            "Who made AEGIS?",
            "Who built AEGIS?",
            "Who designed AEGIS?",
            "Who developed AEGIS?",
            "Who authored AEGIS?",
            "Who created you?",
            "Who made you?",
            "Who built you?",
            "Who is your creator?",
            "Who are your creators?",
            "Who is the creator of AEGIS?",
            "Who is the author of this project?",
            "Where did AEGIS come from?",
            "Please tell me who created AEGIS",
        ]
        for q in creator_queries:
            ans = authoritative_identity_response(q, model=model)
            self.assertIsNotNone(ans, f"Failed to match creator query: '{q}'")
            self.assertIn(CREATOR_NAME, ans)

        # Identity / Who paraphrases
        who_queries = [
            "Who are you?",
            "who are you",
            "who r u?",
            "Tell me who you are",
            "What is AEGIS?",
            "Who am I talking to?",
            "Who am I speaking with?",
            "Hey AEGIS, who are you?",
        ]
        for q in who_queries:
            ans = authoritative_identity_response(q, model=model)
            self.assertIsNotNone(ans, f"Failed to match who query: '{q}'")
            self.assertIn("AEGIS", ans)

    def test_unrelated_queries_not_intercepted(self) -> None:
        """Unrelated questions are not intercepted and return None to let the model answer."""
        unrelated = [
            "Who created Python?",
            "Who created Linux?",
            "Who made the cake?",
            "Who built the pyramids?",
            "Who is Alan Turing?",
            "What model of car is a Tesla?",
            "What is a language model?",
            "How does a language model work?",
            "What is the capital of France?",
            "Can you write a poem about autumn?",
            "Who are the Beatles?",
            "What is your favorite color?",
            "What is quantum computing?",
            "Who was Albert Einstein?",
            "What model year is your car?",
            "How do I cook pasta?",
        ]
        for q in unrelated:
            ans = authoritative_identity_response(q, model="gemma3:1b")
            self.assertIsNone(ans, f"Unrelated query '{q}' was incorrectly intercepted: '{ans}'")


if __name__ == "__main__":
    unittest.main()
