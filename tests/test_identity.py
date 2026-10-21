"""Unit tests for the AEGIS identity engine."""
from __future__ import annotations

import sys
import unittest

from aegis.identity import (
    AEGIS_NAME,
    IDENTITY,
    IDENTITY_VERSION,
    Identity,
    disclose_model,
    identity_core,
    identity_system_prompt,
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


if __name__ == "__main__":
    unittest.main()
