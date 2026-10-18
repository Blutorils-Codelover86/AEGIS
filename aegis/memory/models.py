"""Data conversion and model helpers for AEGIS memory store."""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Mapping, Sequence

from aegis.interfaces import MemoryRecord


def encode_tags(tags: Sequence[str] | None) -> str:
    """Encode a sequence of tags into a JSON array string."""
    if not tags:
        return "[]"
    return json.dumps([str(t) for t in tags], ensure_ascii=False)


def decode_tags(raw: str | Sequence[str] | None) -> tuple[str, ...]:
    """Decode a JSON array string into a tuple of string tags."""
    if not raw:
        return ()
    if isinstance(raw, (list, tuple)):
        return tuple(str(t) for t in raw)
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return tuple(str(x) for x in data)
        return ()
    except Exception:
        return ()


def row_to_record(row: sqlite3.Row | tuple[Any, ...] | Mapping[str, Any]) -> MemoryRecord:
    """Convert a database row or mapping to a MemoryRecord."""
    if isinstance(row, sqlite3.Row) or isinstance(row, dict):
        return MemoryRecord(
            id=str(row["id"]),
            content=str(row["content"]),
            tags=decode_tags(row["tags"]),
            source=str(row["source"]) if row["source"] is not None else None,
            created_at=str(row["created_at"]),
        )
    return MemoryRecord(
        id=str(row[0]),
        content=str(row[1]),
        tags=decode_tags(row[2]),
        source=str(row[3]) if row[3] is not None else None,
        created_at=str(row[4]),
    )


def memory_row_factory(cursor: sqlite3.Cursor, row: tuple[Any, ...]) -> MemoryRecord:
    """Row factory to directly construct MemoryRecord instances from a cursor."""
    col_names = [col[0] for col in cursor.description]
    data = dict(zip(col_names, row))
    return row_to_record(data)
