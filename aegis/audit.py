"""Structured audit logging implementation for Project AEGIS."""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, replace
from datetime import datetime, timezone

from aegis.interfaces import AuditEvent


class JsonlAuditLogger:
    """Thread-safe JSON Lines structured audit logger."""

    def __init__(self, path: str) -> None:
        self.path = os.path.abspath(os.path.expanduser(path))
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.Lock()
        self._file = open(self.path, "a", encoding="utf-8")

    def log(self, event: AuditEvent) -> None:
        with self._lock:
            if not event.timestamp:
                event = replace(event, timestamp=datetime.now(timezone.utc).isoformat())
            data = asdict(event)
            line = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
            if self._file and not self._file.closed:
                self._file.write(line + "\n")
                self._file.flush()
            else:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")

    def close(self) -> None:
        with self._lock:
            if self._file and not self._file.closed:
                self._file.flush()
                self._file.close()


def read_events(path: str) -> list[AuditEvent]:
    """Parse a JSONL audit file back into AuditEvent instances."""
    resolved_path = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(resolved_path):
        return []

    events: list[AuditEvent] = []
    with open(resolved_path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            data = json.loads(stripped)
            events.append(
                AuditEvent(
                    event=data["event"],
                    actor=data["actor"],
                    action=data.get("action"),
                    decision=data.get("decision"),
                    outcome=data.get("outcome"),
                    details=data.get("details") or {},
                    timestamp=data.get("timestamp", ""),
                )
            )
    return events


class InMemoryAuditLogger:
    """In-memory audit sink for testing."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []
        self._lock = threading.Lock()

    def log(self, event: AuditEvent) -> None:
        with self._lock:
            if not event.timestamp:
                event = replace(event, timestamp=datetime.now(timezone.utc).isoformat())
            self.events.append(event)

    def close(self) -> None:
        pass
