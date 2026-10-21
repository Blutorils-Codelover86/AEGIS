"""Unit tests for the AEGIS conversation continuity engine."""
from __future__ import annotations

import unittest

from aegis.conversation import Conversation, Message, format_conversation_prompt


class TestConversation(unittest.TestCase):
    """Test conversation ordering, roles, trimming, blank handling, and clear."""

    def test_message_dataclass(self) -> None:
        """Message dataclass holds role and content, and is frozen."""
        msg = Message(role="user", content="hello")
        self.assertEqual(msg.role, "user")
        self.assertEqual(msg.content, "hello")
        with self.assertRaises((TypeError, AttributeError)):
            msg.content = "mutated"  # type: ignore[misc]

    def test_order_and_roles(self) -> None:
        """Messages preserve strict sequential ordering and role assignment."""
        conv = Conversation()
        conv.add_user("User prompt 1")
        conv.add_assistant("Assistant response 1")
        conv.add_user("User prompt 2")
        conv.add_assistant("Assistant response 2")

        msgs = conv.messages()
        self.assertEqual(len(msgs), 4)
        self.assertEqual(msgs[0], {"role": "user", "content": "User prompt 1"})
        self.assertEqual(msgs[1], {"role": "assistant", "content": "Assistant response 1"})
        self.assertEqual(msgs[2], {"role": "user", "content": "User prompt 2"})
        self.assertEqual(msgs[3], {"role": "assistant", "content": "Assistant response 2"})
        self.assertEqual(len(conv), 2)

    def test_blank_ignored(self) -> None:
        """Empty strings, whitespace-only strings, or non-strings are ignored."""
        conv = Conversation()
        conv.add_user("")
        conv.add_user("   \n\t  ")
        conv.add_assistant("")
        conv.add_assistant("   ")
        self.assertEqual(len(conv.messages()), 0)
        self.assertEqual(len(conv), 0)

        # Non-blank should be added
        conv.add_user("Valid user input")
        self.assertEqual(len(conv.messages()), 1)
        conv.add_assistant("   ")
        self.assertEqual(len(conv.messages()), 1)
        conv.add_assistant("Valid assistant response")
        self.assertEqual(len(conv.messages()), 2)
        self.assertEqual(len(conv), 1)

    def test_trim_to_max_turns(self) -> None:
        """Trimming drops the oldest completed pairs when max_turns is exceeded."""
        conv = Conversation(max_turns=2)

        # Turn 1
        conv.add_user("u1")
        conv.add_assistant("a1")
        self.assertEqual(len(conv), 1)

        # Turn 2
        conv.add_user("u2")
        conv.add_assistant("a2")
        self.assertEqual(len(conv), 2)
        self.assertEqual(
            conv.messages(),
            [
                {"role": "user", "content": "u1"},
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": "u2"},
                {"role": "assistant", "content": "a2"},
            ],
        )

        # Pending user turn (turn 3 in progress)
        conv.add_user("u3")
        self.assertEqual(len(conv), 2)
        self.assertEqual(len(conv.messages()), 5)

        # Turn 3 completes -> exceeds max_turns=2, oldest pair (u1, a1) should be dropped
        conv.add_assistant("a3")
        self.assertEqual(len(conv), 2)
        self.assertEqual(
            conv.messages(),
            [
                {"role": "user", "content": "u2"},
                {"role": "assistant", "content": "a2"},
                {"role": "user", "content": "u3"},
                {"role": "assistant", "content": "a3"},
            ],
        )

        # Turn 4
        conv.add_user("u4")
        conv.add_assistant("a4")
        self.assertEqual(len(conv), 2)
        self.assertEqual(
            conv.messages(),
            [
                {"role": "user", "content": "u3"},
                {"role": "assistant", "content": "a3"},
                {"role": "user", "content": "u4"},
                {"role": "assistant", "content": "a4"},
            ],
        )

    def test_clear(self) -> None:
        """clear() resets messages and completed turn count to 0."""
        conv = Conversation()
        conv.add_user("hello")
        conv.add_assistant("world")
        self.assertEqual(len(conv), 1)
        self.assertEqual(len(conv.messages()), 2)

        conv.clear()
        self.assertEqual(len(conv), 0)
        self.assertEqual(conv.messages(), [])

    def test_messages_immutability(self) -> None:
        """Modifying the returned messages list does not alter the conversation."""
        conv = Conversation()
        conv.add_user("orig")
        msgs = conv.messages()
        msgs.append({"role": "user", "content": "injected"})
        self.assertEqual(len(conv.messages()), 1)

    def test_discard_pending_user(self) -> None:
        """discard_pending_user removes trailing user without assistant, and no-ops otherwise."""
        conv = Conversation()

        # 1. On empty conversation: no-op
        conv.discard_pending_user()
        self.assertEqual(len(conv.messages()), 0)

        # 2. On pending user message: removes it
        conv.add_user("pending question")
        self.assertEqual(len(conv.messages()), 1)
        conv.discard_pending_user()
        self.assertEqual(len(conv.messages()), 0)

        # 3. On completed turn (user + assistant): does not remove completed messages
        conv.add_user("user turn 1")
        conv.add_assistant("assistant reply 1")
        self.assertEqual(len(conv.messages()), 2)
        conv.discard_pending_user()
        self.assertEqual(len(conv.messages()), 2)

        # 4. On completed turn + pending user: removes only the pending user
        conv.add_user("interrupted user turn")
        self.assertEqual(len(conv.messages()), 3)
        conv.discard_pending_user()
        self.assertEqual(len(conv.messages()), 2)
        self.assertEqual(
            conv.messages(),
            [
                {"role": "user", "content": "user turn 1"},
                {"role": "assistant", "content": "assistant reply 1"},
            ],
        )

        # Repeated call is safe no-op
        conv.discard_pending_user()
        self.assertEqual(len(conv.messages()), 2)

    def test_format_conversation_prompt(self) -> None:
        """format_conversation_prompt handles None, single-turn, and multi-turn properly."""
        # 1. None conversation returns raw prompt
        self.assertEqual(format_conversation_prompt(None, "hello"), "hello")

        # 2. Empty conversation returns raw prompt
        conv = Conversation()
        self.assertEqual(format_conversation_prompt(conv, "hello"), "hello")

        # 3. Single user turn matching prompt returns raw prompt
        conv.add_user("hello")
        self.assertEqual(format_conversation_prompt(conv, "hello"), "hello")

        # 4. Multi-turn conversation formats User: and Assistant: turns
        conv.add_assistant("Greetings! How can I assist you?")
        conv.add_user("Remember code: ALPHA-7")
        formatted = format_conversation_prompt(conv, "Remember code: ALPHA-7")
        expected = (
            "User: hello\n\n"
            "Assistant: Greetings! How can I assist you?\n\n"
            "User: Remember code: ALPHA-7"
        )
        self.assertEqual(formatted, expected)

        # 5. If current prompt is not yet added to conversation, appends it
        formatted2 = format_conversation_prompt(conv, "What was the code?")
        self.assertIn("Assistant: Greetings! How can I assist you?", formatted2)
        self.assertTrue(formatted2.endswith("User: What was the code?"))


if __name__ == "__main__":
    unittest.main()
