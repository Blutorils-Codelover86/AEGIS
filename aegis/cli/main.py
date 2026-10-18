"""CLI entrypoint and command handlers for Project AEGIS."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Sequence

from aegis.audit import JsonlAuditLogger, read_events
from aegis.config import AegisConfig, ensure_dirs
from aegis.errors import AegisError, MemoryStoreError, StopRequested
from aegis.interfaces import AuditEvent, MemoryRecord, ModelProvider
from aegis.provider.ollama import OllamaProvider
from aegis.stop import StopController


def _redact_url_credentials(url: str) -> str:
    try:
        import urllib.parse
        parsed = urllib.parse.urlsplit(url)
        if parsed.username or parsed.password:
            netloc = parsed.hostname or ""
            if parsed.port:
                netloc = f"{netloc}:{parsed.port}"
            netloc = f"[REDACTED]@{netloc}"
            return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
    except Exception:
        pass
    return url


def _redact_argv_for_audit(argv: list[str]) -> list[str]:
    """Redact prompts, memory text, and URL credentials from CLI arguments for auditing."""
    redacted: list[str] = []
    skip_next = False
    for i, arg in enumerate(argv):
        if skip_next:
            skip_next = False
            continue
        if arg in {"--prompt", "--system"} and i + 1 < len(argv):
            redacted.append(arg)
            redacted.append(f"[REDACTED len={len(argv[i+1])}]")
            skip_next = True
        elif len(redacted) >= 2 and redacted[-2] == "memory" and redacted[-1] == "save":
            redacted.append(f"[REDACTED len={len(arg)}]")
        else:
            redacted.append(_redact_url_credentials(arg))
    return redacted


class _FallbackSQLiteMemoryStore:
    """Internal SQLite-backed memory store matching the MemoryStore protocol."""

    def __init__(self, db_path: str) -> None:
        self.db_path = os.path.abspath(os.path.expanduser(db_path))
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.Lock()
        try:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            with self._conn:
                self._conn.execute(
                    "CREATE TABLE IF NOT EXISTS memories ("
                    "id TEXT PRIMARY KEY, "
                    "content TEXT NOT NULL, "
                    "tags TEXT NOT NULL DEFAULT '[]', "
                    "source TEXT, "
                    "created_at TEXT NOT NULL"
                    ")"
                )
        except sqlite3.Error as e:
            raise MemoryStoreError(f"Failed to initialize SQLite store: {e}") from e

    def save(
        self,
        content: str,
        *,
        tags: Sequence[str] = (),
        source: str | None = None,
    ) -> MemoryRecord:
        if not content or not content.strip():
            raise MemoryStoreError("Memory content cannot be empty or whitespace")
        rec_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        tags_tuple = tuple(tags)
        tags_json = json.dumps(list(tags_tuple))
        with self._lock:
            try:
                with self._conn:
                    self._conn.execute(
                        "INSERT INTO memories (id, content, tags, source, created_at) VALUES (?, ?, ?, ?, ?)",
                        (rec_id, content, tags_json, source, now),
                    )
            except sqlite3.Error as e:
                raise MemoryStoreError(f"Failed to save memory: {e}") from e
        return MemoryRecord(
            id=rec_id,
            content=content,
            tags=tags_tuple,
            source=source,
            created_at=now,
        )

    def search(self, query: str, *, limit: int = 5) -> list[MemoryRecord]:
        q = query.strip()
        if not q:
            return []
        limit = max(1, limit)
        with self._lock:
            try:
                cur = self._conn.cursor()
                cur.execute(
                    "SELECT id, content, tags, source, created_at FROM memories "
                    "ORDER BY created_at DESC, id ASC"
                )
                rows = cur.fetchall()
            except sqlite3.Error as e:
                raise MemoryStoreError(f"Failed to search memories: {e}") from e

        results: list[MemoryRecord] = []
        q_lower = q.lower()
        for r_id, content, tags_json, source, created_at in rows:
            try:
                loaded_tags = json.loads(tags_json)
                tag_tuple = tuple(loaded_tags) if isinstance(loaded_tags, list) else ()
            except Exception:
                tag_tuple = ()
            tag_match = any(q_lower in t.lower() for t in tag_tuple)
            if q_lower in content.lower() or tag_match:
                results.append(
                    MemoryRecord(
                        id=r_id,
                        content=content,
                        tags=tag_tuple,
                        source=source,
                        created_at=created_at,
                    )
                )
                if len(results) >= limit:
                    break
        return results

    def count(self) -> int:
        with self._lock:
            try:
                cur = self._conn.cursor()
                cur.execute("SELECT COUNT(*) FROM memories")
                row = cur.fetchone()
                return int(row[0]) if row else 0
            except sqlite3.Error as e:
                raise MemoryStoreError(f"Failed to count memories: {e}") from e

    def get(self, record_id: str) -> MemoryRecord | None:
        with self._lock:
            try:
                cur = self._conn.cursor()
                cur.execute(
                    "SELECT id, content, tags, source, created_at FROM memories WHERE id = ?",
                    (record_id,),
                )
                row = cur.fetchone()
            except sqlite3.Error as e:
                raise MemoryStoreError(f"Failed to get memory: {e}") from e
        if not row:
            return None
        r_id, content, tags_json, source, created_at = row
        try:
            loaded_tags = json.loads(tags_json)
            tag_tuple = tuple(loaded_tags) if isinstance(loaded_tags, list) else ()
        except Exception:
            tag_tuple = ()
        return MemoryRecord(
            id=r_id,
            content=content,
            tags=tag_tuple,
            source=source,
            created_at=created_at,
        )

    def delete(self, record_id: str) -> bool:
        with self._lock:
            try:
                with self._conn:
                    cur = self._conn.execute("DELETE FROM memories WHERE id = ?", (record_id,))
                    return cur.rowcount > 0
            except sqlite3.Error as e:
                raise MemoryStoreError(f"Failed to delete memory: {e}") from e

    def list(self, *, limit: int = 50) -> list[MemoryRecord]:
        limit = max(1, limit)
        with self._lock:
            try:
                cur = self._conn.cursor()
                cur.execute(
                    "SELECT id, content, tags, source, created_at FROM memories "
                    "ORDER BY created_at DESC, id ASC LIMIT ?",
                    (limit,),
                )
                rows = cur.fetchall()
            except sqlite3.Error as e:
                raise MemoryStoreError(f"Failed to list memories: {e}") from e
        results: list[MemoryRecord] = []
        for r_id, content, tags_json, source, created_at in rows:
            try:
                loaded_tags = json.loads(tags_json)
                tag_tuple = tuple(loaded_tags) if isinstance(loaded_tags, list) else ()
            except Exception:
                tag_tuple = ()
            results.append(
                MemoryRecord(
                    id=r_id,
                    content=content,
                    tags=tag_tuple,
                    source=source,
                    created_at=created_at,
                )
            )
        return results

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass


def _get_memory_store(db_path: str):
    """Obtain a SQLiteMemoryStore, preferring aegis.memory if available."""
    try:
        from aegis.memory import SQLiteMemoryStore
        return SQLiteMemoryStore(db_path)
    except (ImportError, AttributeError):
        pass
    try:
        from aegis.memory.store import SQLiteMemoryStore
        return SQLiteMemoryStore(db_path)
    except (ImportError, AttributeError):
        pass
    return _FallbackSQLiteMemoryStore(db_path)


class _AegisArgumentParser(argparse.ArgumentParser):
    """Argument parser that raises SystemExit(2) on error without abnormal process exit."""

    def error(self, message: str) -> None:
        sys.stderr.write(f"usage error: {message}\n")
        raise SystemExit(2)


def _build_parser() -> _AegisArgumentParser:
    parser = _AegisArgumentParser(
        prog="aegis",
        description="Project AEGIS - Autonomous Local Assistant",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # chat
    chat_parser = subparsers.add_parser("chat", help="Start chat session or single-shot prompt")
    chat_parser.add_argument("--prompt", type=str, default=None, help="Single-shot prompt")
    chat_parser.add_argument("--model", type=str, default=None, help="Model override")
    chat_parser.add_argument("--system", type=str, default=None, help="System prompt")
    chat_parser.add_argument("--no-memory", action="store_true", help="Disable memory")
    chat_parser.add_argument("--max-turns", type=int, default=None, help="Max turns in chat session")

    # status
    subparsers.add_parser("status", help="Show system status")

    # memory
    mem_parser = subparsers.add_parser("memory", help="Memory management commands")
    mem_sub = mem_parser.add_subparsers(dest="memory_action", required=True)

    # memory save
    mem_save = mem_sub.add_parser("save", help="Save a memory record")
    mem_save.add_argument("text", type=str, help="Memory text content")
    mem_save.add_argument("--tag", "-t", action="extend", nargs="+", default=[], help="Tag(s)")
    mem_save.add_argument("--source", "-s", type=str, default=None, help="Source metadata")

    # memory search
    mem_search = mem_sub.add_parser("search", help="Search memory records")
    mem_search.add_argument("query", type=str, help="Search query")
    mem_search.add_argument("--limit", "-n", type=int, default=None, help="Maximum results")

    # memory list
    mem_list = mem_sub.add_parser("list", help="List recent memory records")
    mem_list.add_argument("--limit", "-n", type=int, default=None, help="Maximum results")

    # memory delete
    mem_delete = mem_sub.add_parser("delete", help="Delete a memory record")
    mem_delete.add_argument("record_id", type=str, help="Record ID to delete")

    # stop
    stop_parser = subparsers.add_parser("stop", help="Request or clear stop signal")
    stop_parser.add_argument("--clear", action="store_true", help="Clear stop signal")

    return parser


def main(
    argv: list[str] | None = None,
    *,
    provider: ModelProvider | None = None,
    config: AegisConfig | None = None,
) -> int:
    """Main CLI entrypoint for AEGIS."""
    try:
        if config is None:
            config = AegisConfig.load()
        ensure_dirs(config)

        effective_argv = list(sys.argv[1:] if argv is None else argv)
        if not effective_argv:
            parser = _build_parser()
            parser.print_help()
            return 2

        parser = _build_parser()
        try:
            args = parser.parse_args(effective_argv)
        except SystemExit as e:
            return int(e.code) if isinstance(e.code, int) else 2

        if provider is None:
            provider = OllamaProvider(
                config.ollama_host,
                config.model,
                config.request_timeout,
                allow_network=config.allow_network,
            )

        audit = JsonlAuditLogger(config.audit_log)
        try:
            # Audit the invocation with redaction
            action_name = args.subcommand
            if action_name == "memory" and getattr(args, "memory_action", None):
                action_name = f"memory.{args.memory_action}"
            audit.log(
                AuditEvent(
                    event="cli.command",
                    actor="user",
                    action=action_name,
                    details={"argv": _redact_argv_for_audit(effective_argv)},
                )
            )

            # Dispatch command
            if args.subcommand == "chat":
                return _handle_chat(args, provider=provider, config=config)
            elif args.subcommand == "status":
                return _handle_status(provider=provider, config=config)
            elif args.subcommand == "memory":
                return _handle_memory(args, config=config)
            elif args.subcommand == "stop":
                return _handle_stop(args, config=config)
            else:
                sys.stderr.write(f"Unknown subcommand: {args.subcommand}\n")
                return 2
        finally:
            audit.close()
    except AegisError as e:
        sys.stderr.write(f"Error: {e}\n")
        return 2


def _handle_chat(args: argparse.Namespace, *, provider: ModelProvider, config: AegisConfig) -> int:
    stop_ctrl = StopController(config.stop_file)

    if args.prompt is not None:
        try:
            stop_ctrl.check()
            try:
                answer = provider.generate(
                    args.prompt,
                    system=args.system,
                    model=args.model,
                    stop_check=stop_ctrl.check,
                )
            except TypeError:
                answer = provider.generate(
                    args.prompt,
                    system=args.system,
                    model=args.model,
                )
            print(answer)
            return 0
        except KeyboardInterrupt:
            print("\n[Stop requested by user]")
            return 130
        except StopRequested as e:
            print(f"\n[Operation stopped: {e}]", file=sys.stderr)
            return 130

    # Interactive REPL
    max_turns = args.max_turns if args.max_turns is not None else config.max_chat_turns
    turn = 0
    print(f"AEGIS Chat ({args.model or config.model}) - Ctrl+C to exit")
    try:
        while turn < max_turns:
            stop_ctrl.check()
            try:
                user_input = input("aegis> ")
            except EOFError:
                break
            except KeyboardInterrupt:
                print("\n[Stop requested by user]")
                return 130

            stripped = user_input.strip()
            if not stripped:
                continue

            turn += 1
            try:
                try:
                    response = provider.generate(
                        stripped,
                        system=args.system,
                        model=args.model,
                        stop_check=stop_ctrl.check,
                    )
                except TypeError:
                    response = provider.generate(
                        stripped,
                        system=args.system,
                        model=args.model,
                    )
            except KeyboardInterrupt:
                print("\n[Stop requested by user]")
                return 130
            except StopRequested as e:
                print(f"\n[Operation stopped: {e}]", file=sys.stderr)
                return 130

            print(response)
            stop_ctrl.check()
    except KeyboardInterrupt:
        print("\n[Stop requested by user]")
        return 130

    return 0


def _handle_status(*, provider: ModelProvider, config: AegisConfig) -> int:
    is_healthy = provider.health()
    available_models: list[str] = []
    if is_healthy:
        try:
            available_models = provider.list_models()
        except Exception:
            available_models = []

    count = 0
    if os.path.exists(config.memory_db):
        try:
            store = _get_memory_store(config.memory_db)
            try:
                if hasattr(store, "count"):
                    count = store.count()
                else:
                    count = len(store.list(limit=config.max_memory_results))
            finally:
                store.close()
        except Exception:
            count = 0

    events = read_events(config.audit_log)
    if events:
        last_event = events[-1]
        last_event_str = f"{last_event.event} at {last_event.timestamp}"
    else:
        last_event_str = "none"

    stop_ctrl = StopController(config.stop_file)
    if stop_ctrl.is_stopped():
        stop_state = f"stopped (reason: {stop_ctrl.reason()})"
    else:
        stop_state = "clear"

    print(f"Model: {config.model}")
    print(f"Ollama Host: {config.ollama_host}")
    print(f"Provider Health: {is_healthy}")
    print(f"Available Models: {available_models}")
    print(f"Memory DB: {config.memory_db} ({count} records)")
    print(f"Audit Log: {config.audit_log} (last event: {last_event_str})")
    print(f"Stop State: {stop_state}")

    return 0 if is_healthy else 1


def _handle_memory(args: argparse.Namespace, *, config: AegisConfig) -> int:
    store = _get_memory_store(config.memory_db)
    try:
        if args.memory_action == "save":
            rec = store.save(args.text, tags=args.tag or (), source=args.source)
            print(f"Saved memory [{rec.id}]")
            return 0
        elif args.memory_action == "search":
            limit = args.limit if args.limit is not None else config.max_memory_results
            limit = min(max(1, limit), config.max_memory_results)
            records = store.search(args.query, limit=limit)
            if not records:
                print("No memories found.")
            else:
                for r in records:
                    tag_str = f" tags={list(r.tags)}" if r.tags else ""
                    src_str = f" source={r.source}" if r.source else ""
                    print(f"[{r.id}] {r.content}{tag_str}{src_str}")
            return 0
        elif args.memory_action == "list":
            limit = args.limit if args.limit is not None else config.max_memory_results
            limit = min(max(1, limit), config.max_memory_results)
            records = store.list(limit=limit)
            if not records:
                print("No memories stored.")
            else:
                for r in records:
                    tag_str = f" tags={list(r.tags)}" if r.tags else ""
                    src_str = f" source={r.source}" if r.source else ""
                    print(f"[{r.id}] {r.content}{tag_str}{src_str}")
            return 0
        elif args.memory_action == "delete":
            deleted = store.delete(args.record_id)
            if deleted:
                print(f"Deleted memory [{args.record_id}]")
            else:
                print(f"Memory record not found: {args.record_id}")
            return 0
        else:
            sys.stderr.write(f"Unknown memory action: {args.memory_action}\n")
            return 2
    finally:
        store.close()


def _handle_stop(args: argparse.Namespace, *, config: AegisConfig) -> int:
    stop_ctrl = StopController(config.stop_file)
    if args.clear:
        stop_ctrl.clear()
        print("Stop state cleared")
    else:
        stop_ctrl.request_stop("user")
        print("Stop requested")
    return 0
