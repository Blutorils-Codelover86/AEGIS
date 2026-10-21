"""Conversation continuity management for Project AEGIS.

Pure standard library module. Does not depend on provider, memory, permissions, or rich.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Message:
    """A single turn message in a conversation."""

    role: str  # "user" | "assistant"
    content: str


class Conversation:
    """Manages multi-turn conversation history bounded by max_turns pairs."""

    def __init__(self, *, max_turns: int = 20) -> None:
        self.max_turns = max_turns
        self._messages: list[Message] = []

    def add_user(self, text: str) -> None:
        """Record a user message. Empty or whitespace content is ignored."""
        if not text or not isinstance(text, str) or not text.strip():
            return
        self._messages.append(Message(role="user", content=text))

    def add_assistant(self, text: str) -> None:
        """Record an assistant message and trim history if max_turns is exceeded. Blank content ignored."""
        if not text or not isinstance(text, str) or not text.strip():
            return
        self._messages.append(Message(role="assistant", content=text))
        self._trim()

    def _trim(self) -> None:
        """Bound retained pairs by dropping the oldest completed pairs first."""
        if self.max_turns < 0:
            return
        while len(self) > self.max_turns:
            idx = -1
            has_user = False
            for i, m in enumerate(self._messages):
                if m.role == "user":
                    has_user = True
                elif m.role == "assistant" and has_user:
                    idx = i
                    break
            if idx != -1:
                self._messages = self._messages[idx + 1 :]
            else:
                break

    def messages(self) -> list[dict[str, str]]:
        """Return ordered user/assistant message dicts. Never includes system prompt."""
        return [{"role": m.role, "content": m.content} for m in self._messages]

    def clear(self) -> None:
        """Clear all conversation history."""
        self._messages.clear()

    def discard_pending_user(self) -> None:
        """Remove a trailing user message if it has received no assistant reply. No-op otherwise."""
        if self._messages and self._messages[-1].role == "user":
            self._messages.pop()

    def __len__(self) -> int:
        """Return the number of completed (user, assistant) turns."""
        count = 0
        has_user = False
        for m in self._messages:
            if m.role == "user":
                has_user = True
            elif m.role == "assistant" and has_user:
                count += 1
                has_user = False
        return count


def format_conversation_prompt(conversation: Conversation | None, prompt: str) -> str:
    """Format conversation history into a unified prompt string for single-turn model providers.

    If conversation is None, empty, or contains only a single user message matching the prompt,
    the raw prompt is returned unchanged.
    Otherwise, prior exchanges and the current prompt are formatted as sequential turns:
    'User: ...\\n\\nAssistant: ...'
    """
    if conversation is None:
        return prompt
    messages = conversation.messages()
    if not messages:
        return prompt
    if len(messages) == 1 and messages[0].get("role") == "user" and messages[0].get("content") == prompt:
        return prompt
    lines: list[str] = []
    for msg in messages:
        role = "User" if msg.get("role") == "user" else "Assistant"
        lines.append(f"{role}: {msg.get('content', '')}")
    if messages[-1].get("content") != prompt:
        lines.append(f"User: {prompt}")
    return "\n\n".join(lines)
