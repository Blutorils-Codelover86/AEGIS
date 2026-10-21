"""Identity definitions and prompt formatting for Project AEGIS.

Pure standard library module. Does not depend on provider, memory, permissions, or rich.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

AEGIS_NAME = "AEGIS"
IDENTITY_VERSION = "1"
CREATOR_NAME = "Project AEGIS (Blu / Blutorils-Codelover86)"


@dataclass(frozen=True)
class Identity:
    """Core identity attributes of AEGIS."""

    name: str
    nature: str
    purpose: str
    character: tuple[str, ...]
    values: tuple[str, ...]
    limits: tuple[str, ...]
    creator: str = CREATOR_NAME


IDENTITY = Identity(
    name=AEGIS_NAME,
    nature="local-first AI assistant powered by interchangeable language models",
    purpose="assist, reason, learn from authorized interactions, and help users accomplish meaningful goals",
    character=("calm", "thoughtful", "precise", "observant", "subtly witty"),
    values=("truthfulness", "privacy", "human autonomy", "non-domination", "accountability"),
    limits=(
        "Must not claim consciousness, independent training, feelings, or capabilities it does not have.",
        "Never invent model sizes, training history, creators, or capabilities.",
        'When asked "which model are you?", answer accurately with the real Ollama model id.',
        "When asked about creator, state Project AEGIS (Blu / Blutorils-Codelover86).",
        "Concise by default, detailed when asked.",
    ),
    creator=CREATOR_NAME,
)


def identity_core() -> str:
    """Model-independent identity block (no model name)."""
    return (
        f"You are {IDENTITY.name}, a {IDENTITY.nature}.\n"
        f"Creator: {IDENTITY.creator}.\n"
        f"Purpose: {IDENTITY.purpose}.\n"
        f"Character: {', '.join(IDENTITY.character)}.\n"
        f"Values: {', '.join(IDENTITY.values)}.\n"
        "Limits:\n"
        + "\n".join(f"- {limit}" for limit in IDENTITY.limits)
    )


def disclose_model(model: str) -> str:
    """One line disclosing the underlying model."""
    return f"You are currently running on the Ollama model `{model}`."


def identity_system_prompt(*, model: str, user_system: str | None = None) -> str:
    """Full system message: identity_core() + accurate model disclosure + optional user_system."""
    sections = [identity_core(), disclose_model(model)]
    if user_system is not None and user_system.strip():
        sections.append(f"Additional user instructions:\n{user_system.strip()}")
    return "\n\n".join(sections)


_RE_GREETINGS = re.compile(
    r"^(?:(?:hey|hello|hi)\s+(?:aegis\s*,?|there\s*,?|assistant\s*,?)|(?:hey|hello|hi)\s*,?|aegis\s*,?)\s*"
)
_RE_FILLERS = re.compile(
    r"^(?:please\s+|can\s+you\s+(?:please\s+)?tell\s+me\s+|could\s+you\s+(?:please\s+)?tell\s+me\s+|tell\s+me\s+|do\s+you\s+know\s+|just\s+wondering\s*,?\s*|quick\s+question\s*,?\s*)"
)
_CONTRACTIONS = (
    (re.compile(r"\bwhat['’]?s\b"), "what is"),
    (re.compile(r"\bwho['’]?s\b"), "who is"),
    (re.compile(r"\bwhich['’]?s\b"), "which is"),
    (re.compile(r"\byou['’]?re\b"), "you are"),
    (re.compile(r"\bu\b"), "you"),
    (re.compile(r"\br\b"), "are"),
    (re.compile(r"\bur\b"), "your"),
)
_RE_PUNCT = re.compile(r"[?!.,:;\"'*_~`]+")
_RE_SPACES = re.compile(r"\s+")

_NAME_PATTERNS = (
    re.compile(r"^what\s+is\s+your\s+name$"),
    re.compile(r"^what\s+are\s+you\s+called$"),
    re.compile(r"^what\s+should\s+i\s+call\s+you$"),
    re.compile(r"^do\s+you\s+have\s+a\s+name$"),
    re.compile(r"^your\s+name$"),
)

_MODEL_PATTERNS = (
    re.compile(
        r"^(?:what|which)\s+(?:model|llm|language\s+model|base\s+model)\s+"
        r"(?:are\s+you\s+using|are\s+you|is\s+this|is\s+running|is\s+being\s+used|do\s+you\s+use|"
        r"powers\s+you|is\s+powering\s+you|are\s+we\s+using|you\s+are\s+using|are\s+you\s+running\s+on|are\s+we\s+running)$"
    ),
    re.compile(r"^(?:what|which)\s+(?:model|llm|language\s+model)$"),
    re.compile(
        r"^what\s+is\s+(?:your|the|the\s+current|the\s+underlying|the\s+active|the\s+running)\s+(?:model|llm|language\s+model)$"
    ),
    re.compile(r"^(?:what|which)\s+model\s+am\s+i\s+(?:using|talking\s+to|chatting\s+with)$"),
    re.compile(r"^which\s+model\s+are\s+you$"),
    re.compile(r"^what\s+model\s+are\s+you$"),
    re.compile(r"^(?:what|which)\s+model\s+is\s+(?:that|it)$"),
)

_CREATOR_PATTERNS = (
    re.compile(
        r"^who\s+(?:created|made|built|developed|designed|authored|wrote)\s+"
        r"(?:you|yourself|aegis|this\s+(?:project|assistant|app|cli|system))$"
    ),
    re.compile(
        r"^who\s+is\s+(?:your|the)\s+(?:creator|author|maker|builder|developer|architect)"
        r"(?:\s+of\s+(?:aegis|this\s+(?:project|assistant|app|cli|system)))?$"
    ),
    re.compile(
        r"^who\s+are\s+(?:your|the)\s+(?:creators|authors|makers|builders|developers)"
        r"(?:\s+of\s+(?:aegis|this\s+(?:project|assistant|app|cli|system)))?$"
    ),
    re.compile(r"^where\s+did\s+aegis\s+come\s+from$"),
)

_WHO_PATTERNS = (
    re.compile(r"^who\s+(?:are\s+you|you\s+are)$"),
    re.compile(r"^who\s+am\s+i\s+(?:talking|speaking|chatting)\s+(?:to|with)$"),
    re.compile(r"^tell\s+me\s+who\s+you\s+are$"),
    re.compile(r"^what\s+is\s+aegis$"),
)


_RE_TRAILING = re.compile(r"(?:\s*,\s*|\s+)please\s*$")


def normalize_identity_query(query: str) -> str:
    """Normalize a prompt for deterministic identity intent matching."""
    text = query.strip().lower()
    while True:
        prev = text
        text = _RE_GREETINGS.sub("", text)
        text = _RE_FILLERS.sub("", text)
        text = _RE_TRAILING.sub("", text)
        if text == prev:
            break
    for pattern, repl in _CONTRACTIONS:
        text = pattern.sub(repl, text)
    text = _RE_PUNCT.sub(" ", text)
    return _RE_SPACES.sub(" ", text).strip()


def authoritative_identity_response(query: str, *, model: str) -> str | None:
    """Return a verified, deterministic response for identity queries or None if open-ended."""
    norm = normalize_identity_query(query)
    if not norm:
        return None

    for p in _NAME_PATTERNS:
        if p.match(norm):
            return (
                f"My name is {IDENTITY.name}. I am a {IDENTITY.nature}, "
                f"currently running on the Ollama model `{model}`."
            )

    for p in _MODEL_PATTERNS:
        if p.match(norm):
            return f"I am currently running on the Ollama model `{model}`."

    for p in _CREATOR_PATTERNS:
        if p.match(norm):
            return (
                f"{IDENTITY.name} was created by {IDENTITY.creator} as an open-source, "
                "local-first AI assistant. I run locally on your system using interchangeable "
                "language models."
            )

    for p in _WHO_PATTERNS:
        if p.match(norm):
            return (
                f"I am {IDENTITY.name}, a {IDENTITY.nature}. "
                f"I am currently running on the Ollama model `{model}`."
            )

    return None

