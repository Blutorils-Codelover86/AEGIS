"""Exception hierarchy for Project AEGIS."""
from __future__ import annotations

from aegis.interfaces import PermissionResult


class AegisError(Exception):
    """Base class for all AEGIS errors."""


class ConfigError(AegisError):
    """Invalid or unreadable configuration."""


class ProviderError(AegisError):
    """Model provider (Ollama) failure."""


class MemoryStoreError(AegisError):
    """SQLite memory failure."""


class ToolError(AegisError):
    """Tool execution failure."""


class StopRequested(AegisError):
    """Raised when an in-flight operation observes a stop request."""


class PermissionDenied(AegisError):
    """Raised when a permission decision is not ALLOW."""

    def __init__(self, result: PermissionResult) -> None:
        self.result = result
        super().__init__(f"{result.decision.value}: {result.reason} (rule={result.rule})")
