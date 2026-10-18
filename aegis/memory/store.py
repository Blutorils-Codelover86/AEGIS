"""SQLite-backed memory store implementation for Project AEGIS."""
from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from aegis.errors import MemoryStoreError
from aegis.interfaces import MemoryRecord, MemoryStore
from aegis.memory.models import encode_tags, row_to_record


class SQLiteMemoryStore:
    """Thread-safe SQLite-backed explicit memory store."""

    _SCHEMA = (
        "CREATE TABLE IF NOT EXISTS memories ("
        "id TEXT PRIMARY KEY, "
        "content TEXT NOT NULL, "
        "tags TEXT NOT NULL DEFAULT '[]', "
        "source TEXT, "
        "created_at TEXT NOT NULL"
        ")"
    )

    def __init__(self, db_path: str | Path) -> None:
        self._raw_path = str(db_path)
        if self._raw_path != ":memory:":
            resolved = os.path.abspath(os.path.expanduser(self._raw_path))
            parent = os.path.dirname(resolved)
            if parent:
                try:
                    os.makedirs(parent, exist_ok=True)
                except OSError as e:
                    raise MemoryStoreError(f"Failed to create parent directories for database: {e}") from e
            self._db_path = resolved
        else:
            self._db_path = ":memory:"

        self._lock = threading.Lock()
        with self._lock:
            try:
                self._conn: sqlite3.Connection | None = sqlite3.connect(
                    self._db_path,
                    check_same_thread=False,
                )
                self._conn.row_factory = sqlite3.Row
                with self._conn:
                    self._conn.execute(self._SCHEMA)
            except sqlite3.Error as e:
                raise MemoryStoreError(f"Failed to initialize SQLite database at '{self._db_path}': {e}") from e

    def _ensure_open(self) -> sqlite3.Connection:
        if self._conn is None:
            raise MemoryStoreError("Database connection is closed.")
        return self._conn

    def save(
        self,
        content: str,
        *,
        tags: Sequence[str] = (),
        source: str | None = None,
    ) -> MemoryRecord:
        """Save a new memory record."""
        if not isinstance(content, str) or not content.strip():
            raise MemoryStoreError("Memory content cannot be empty or whitespace only.")

        record_id = uuid.uuid4().hex
        tags_tuple = tuple(str(t) for t in (tags or ()))
        tags_json = encode_tags(tags_tuple)
        created_at = datetime.now(timezone.utc).isoformat()
        src = str(source) if source is not None else None

        with self._lock:
            conn = self._ensure_open()
            try:
                with conn:
                    conn.execute(
                        "INSERT INTO memories (id, content, tags, source, created_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (record_id, content, tags_json, src, created_at),
                    )
            except sqlite3.Error as e:
                raise MemoryStoreError(f"Failed to save memory record: {e}") from e

        return MemoryRecord(
            id=record_id,
            content=content,
            tags=tags_tuple,
            source=src,
            created_at=created_at,
        )

    def search(self, query: str, *, limit: int = 5) -> list[MemoryRecord]:
        """Search memories case-insensitively by content and tags JSON."""
        if not isinstance(query, str) or not query.strip():
            return []

        clamped_limit = max(1, int(limit))
        with self._lock:
            conn = self._ensure_open()
            try:
                cur = conn.execute(
                    "SELECT id, content, tags, source, created_at FROM memories "
                    "WHERE instr(lower(content), lower(?)) > 0 "
                    "   OR instr(lower(tags), lower(?)) > 0 "
                    "ORDER BY created_at DESC, id ASC "
                    "LIMIT ?",
                    (query, query, clamped_limit),
                )
                rows = cur.fetchall()
                return [row_to_record(row) for row in rows]
            except sqlite3.Error as e:
                raise MemoryStoreError(f"Failed to search memories: {e}") from e

    def get(self, record_id: str) -> MemoryRecord | None:
        """Retrieve a single memory record by ID."""
        with self._lock:
            conn = self._ensure_open()
            try:
                cur = conn.execute(
                    "SELECT id, content, tags, source, created_at FROM memories WHERE id = ?",
                    (str(record_id),),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                return row_to_record(row)
            except sqlite3.Error as e:
                raise MemoryStoreError(f"Failed to get memory record '{record_id}': {e}") from e

    def delete(self, record_id: str) -> bool:
        """Delete a memory record by ID. Return True if a row was removed."""
        with self._lock:
            conn = self._ensure_open()
            try:
                with conn:
                    cur = conn.execute(
                        "DELETE FROM memories WHERE id = ?",
                        (str(record_id),),
                    )
                return cur.rowcount > 0
            except sqlite3.Error as e:
                raise MemoryStoreError(f"Failed to delete memory record '{record_id}': {e}") from e

    def list(self, *, limit: int = 50) -> list[MemoryRecord]:
        """List memories ordered by created_at DESC, id ASC."""
        clamped_limit = max(1, int(limit))
        with self._lock:
            conn = self._ensure_open()
            try:
                cur = conn.execute(
                    "SELECT id, content, tags, source, created_at FROM memories "
                    "ORDER BY created_at DESC, id ASC "
                    "LIMIT ?",
                    (clamped_limit,),
                )
                rows = cur.fetchall()
                return [row_to_record(row) for row in rows]
            except sqlite3.Error as e:
                raise MemoryStoreError(f"Failed to list memories: {e}") from e

    def count(self) -> int:
        """Return the total number of stored memory records."""
        with self._lock:
            conn = self._ensure_open()
            try:
                cur = conn.execute("SELECT count(*) FROM memories")
                row = cur.fetchone()
                return int(row[0]) if row else 0
            except sqlite3.Error as e:
                raise MemoryStoreError(f"Failed to count memories: {e}") from e

    def close(self) -> None:
        """Close the database connection idempotently."""
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except sqlite3.Error as e:
                    raise MemoryStoreError(f"Failed to close SQLite connection: {e}") from e
                finally:
                    self._conn = None

    def __enter__(self) -> SQLiteMemoryStore:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.close()


def open_memory(path: str | Path) -> SQLiteMemoryStore:
    """Open and return a SQLiteMemoryStore at path."""
    return SQLiteMemoryStore(path)
