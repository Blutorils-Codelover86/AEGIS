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
        netloc = parsed.hostname or ""
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        if parsed.username or parsed.password:
            netloc = f"[REDACTED]@{netloc}"
        query = "[REDACTED_QUERY]" if parsed.query else ""
        fragment = "[REDACTED_FRAGMENT]" if parsed.fragment else ""
        return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, query, fragment))
    except Exception:
        pass
    return url


def _redact_argv_for_audit(argv: list[str]) -> list[str]:
    """Strict metadata allowlist for auditing CLI invocations.

    Safe commands and flag names are preserved.
    Sensitive payloads (prompt, system, memory text, tags, sources, queries, IDs)
    are redacted regardless of option/position ordering.
    """
    if not argv:
        return []

    redacted: list[str] = []
    i = 0
    subcommand: str | None = None
    memory_action: str | None = None

    while i < len(argv):
        arg = argv[i]

        if subcommand is None and arg in ("chat", "status", "memory", "stop"):
            subcommand = arg
            redacted.append(arg)
            i += 1
            continue

        if subcommand == "memory" and memory_action is None and arg in ("save", "search", "list", "delete"):
            memory_action = arg
            redacted.append(arg)
            i += 1
            continue

        if arg in ("--prompt", "-p"):
            redacted.append(arg)
            if i + 1 < len(argv):
                val = argv[i + 1]
                redacted.append(f"[REDACTED len={len(val)}]")
                i += 2
                continue
        elif arg in ("--system", "-s"):
            redacted.append(arg)
            if i + 1 < len(argv):
                val = argv[i + 1]
                redacted.append(f"[REDACTED len={len(val)}]")
                i += 2
                continue
        elif arg in ("--tag", "-t"):
            redacted.append(arg)
            if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
                redacted.append("[REDACTED tag]")
                i += 2
            else:
                i += 1
            continue
        elif arg in ("--source",):
            redacted.append(arg)
            if i + 1 < len(argv):
                redacted.append("[REDACTED source]")
                i += 2
                continue
        elif arg in ("--limit", "-n", "--turns", "--timeout"):
            redacted.append(arg)
            if i + 1 < len(argv):
                val = argv[i + 1]
                redacted.append(val if val.isdigit() else "[REDACTED]")
                i += 2
                continue
        elif arg in ("--model", "-m"):
            redacted.append(arg)
            if i + 1 < len(argv):
                redacted.append(argv[i + 1])
                i += 2
                continue
        elif arg in ("--clear", "--help", "-h"):
            redacted.append(arg)
            i += 1
            continue
        elif arg.startswith("-"):
            redacted.append(arg)
            i += 1
            continue
        else:
            if subcommand == "memory":
                if memory_action == "save":
                    redacted.append(f"[REDACTED len={len(arg)}]")
                elif memory_action == "search":
                    redacted.append("[REDACTED query]")
                elif memory_action == "delete":
                    redacted.append("[REDACTED id]")
                else:
                    redacted.append("[REDACTED]")
            elif subcommand == "chat":
                redacted.append(f"[REDACTED len={len(arg)}]")
            else:
                redacted.append(_redact_url_credentials(arg))
            i += 1

    return redacted


def _get_memory_store(db_path: str):
    """Obtain a SQLiteMemoryStore."""
    from aegis.memory import SQLiteMemoryStore
    return SQLiteMemoryStore(db_path)


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
