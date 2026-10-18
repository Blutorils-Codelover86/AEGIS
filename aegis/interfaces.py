"""Shared protocols for Project AEGIS. Foundation-owned; do not edit in feature work."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable


class Decision(str, Enum):
    """Outcome of a deterministic permission evaluation."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_CONFIRMATION = "require_confirmation"


@dataclass(frozen=True)
class PermissionRequest:
    """A single request to perform an action."""

    action: str
    target: str | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)
    consequential: bool = False


@dataclass(frozen=True)
class PermissionResult:
    """Deterministic, explainable permission decision."""

    decision: Decision
    reason: str
    rule: str


@runtime_checkable
class PermissionPolicy(Protocol):
    """Deterministic permission checker."""

    def evaluate(self, request: PermissionRequest) -> PermissionResult: ...

    def require(self, request: PermissionRequest) -> PermissionResult: ...


@dataclass(frozen=True)
class ConfirmationToken:
    """Cryptographically bound confirmation token for consequential actions."""

    fingerprint: str
    action: str
    target: str | None = None
    created_at: str = ""
    comment: str = ""

    @classmethod
    def create(cls, request: PermissionRequest, comment: str = "") -> "ConfirmationToken":
        from datetime import datetime, timezone
        fp = compute_request_fingerprint(request)
        ts = datetime.now(timezone.utc).isoformat()
        return cls(
            fingerprint=fp,
            action=request.action,
            target=request.target,
            created_at=ts,
            comment=comment,
        )

    def is_valid_for(self, request: PermissionRequest) -> bool:
        if self.action != request.action:
            return False
        return self.fingerprint == compute_request_fingerprint(request)


def compute_request_fingerprint(request: PermissionRequest) -> str:
    """Compute a deterministic SHA-256 fingerprint for a PermissionRequest."""
    import hashlib
    import json

    def _json_default(obj: Any) -> Any:
        if isinstance(obj, (set, frozenset, tuple)):
            return list(obj)
        return str(obj)

    params_json = json.dumps(
        dict(sorted(request.parameters.items())),
        sort_keys=True,
        default=_json_default,
    )
    raw = f"{request.action}|{request.target or ''}|{params_json}|{request.consequential}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MemoryRecord:
    """A persisted memory entry."""

    id: str
    content: str
    tags: tuple[str, ...] = ()
    source: str | None = None
    created_at: str = ""


@runtime_checkable
class MemoryStore(Protocol):
    """SQLite-backed explicit memory interface."""

    def save(
        self,
        content: str,
        *,
        tags: Sequence[str] = (),
        source: str | None = None,
    ) -> MemoryRecord: ...

    def search(self, query: str, *, limit: int = 5) -> list[MemoryRecord]: ...

    def get(self, record_id: str) -> MemoryRecord | None: ...

    def delete(self, record_id: str) -> bool: ...

    def list(self, *, limit: int = 50) -> list[MemoryRecord]: ...

    def close(self) -> None: ...


@runtime_checkable
class ModelProvider(Protocol):
    """Model provider (Ollama) interface."""

    name: str

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        stop: Sequence[str] | None = None,
    ) -> str: ...

    def list_models(self) -> list[str]: ...

    def health(self) -> bool: ...


@dataclass(frozen=True)
class AuditEvent:
    """Structured audit record."""

    event: str
    actor: str
    action: str | None = None
    decision: str | None = None
    outcome: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)
    timestamp: str = ""


@runtime_checkable
class AuditLogger(Protocol):
    """Structured audit sink (JSON Lines)."""

    def log(self, event: AuditEvent) -> None: ...

    def close(self) -> None: ...
