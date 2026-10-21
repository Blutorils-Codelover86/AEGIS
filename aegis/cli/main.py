"""CLI entrypoint and command handlers for Project AEGIS."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Sequence

from aegis.audit import JsonlAuditLogger, read_events
from aegis.cli.animations import (
    AnimatedCaps,
    StreamIndicator,
    ThinkingIndicator,
    capabilities,
    completion,
)
from aegis.cli.render import (
    StreamBuffer,
    render_markdown,
    render_plain,
    safe_width,
)
from aegis.cli.theme import (
    header_info,
    make_console,
    print_assistant_prefix,
    print_header,
    print_user,
)
from aegis.config import AegisConfig, ensure_dirs
from aegis.conversation import Conversation
from aegis.errors import AegisError, MemoryStoreError, StopRequested
from aegis.identity import identity_system_prompt
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
        elif arg in ("--clear", "--help", "-h", "--plain", "--no-color", "--tui", "--no-memory"):
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
    # Global flags
    parser.add_argument("--plain", action="store_true", help="Plain text output without color or formatting")
    parser.add_argument("--no-color", action="store_true", help="Disable colored output")

    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # chat
    chat_parser = subparsers.add_parser("chat", help="Start chat session or single-shot prompt")
    chat_parser.add_argument("--prompt", "-p", type=str, default=None, help="Single-shot prompt")
    chat_parser.add_argument("--model", "-m", type=str, default=None, help="Model override")
    chat_parser.add_argument("--system", "-s", type=str, default=None, help="System prompt")
    chat_parser.add_argument("--no-memory", action="store_true", help="Disable memory")
    chat_parser.add_argument("--max-turns", type=int, default=None, help="Max turns in chat session")
    chat_parser.add_argument("--plain", action="store_true", default=argparse.SUPPRESS, help="Plain text output without color or formatting")
    chat_parser.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS, help="Disable colored output")
    chat_parser.add_argument("--tui", action="store_true", help="Launch interactive Textual TUI")

    # status
    status_parser = subparsers.add_parser("status", help="Show system status")
    status_parser.add_argument("--plain", action="store_true", default=argparse.SUPPRESS, help="Plain text output")
    status_parser.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS, help="Disable colored output")

    # memory
    mem_parser = subparsers.add_parser("memory", help="Memory management commands")
    mem_parser.add_argument("--plain", action="store_true", default=argparse.SUPPRESS, help="Plain text output")
    mem_parser.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS, help="Disable colored output")
    mem_sub = mem_parser.add_subparsers(dest="memory_action", required=True)

    # memory save
    mem_save = mem_sub.add_parser("save", help="Save a memory record")
    mem_save.add_argument("text", type=str, help="Memory text content")
    mem_save.add_argument("--tag", "-t", action="extend", nargs="+", default=[], help="Tag(s)")
    mem_save.add_argument("--source", "-s", type=str, default=None, help="Source metadata")
    mem_save.add_argument("--plain", action="store_true", default=argparse.SUPPRESS, help="Plain text output")
    mem_save.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS, help="Disable colored output")

    # memory search
    mem_search = mem_sub.add_parser("search", help="Search memory records")
    mem_search.add_argument("query", type=str, help="Search query")
    mem_search.add_argument("--limit", "-n", type=int, default=None, help="Maximum results")
    mem_search.add_argument("--plain", action="store_true", default=argparse.SUPPRESS, help="Plain text output")
    mem_search.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS, help="Disable colored output")

    # memory list
    mem_list = mem_sub.add_parser("list", help="List recent memory records")
    mem_list.add_argument("--limit", "-n", type=int, default=None, help="Maximum results")
    mem_list.add_argument("--plain", action="store_true", default=argparse.SUPPRESS, help="Plain text output")
    mem_list.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS, help="Disable colored output")

    # memory delete
    mem_delete = mem_sub.add_parser("delete", help="Delete a memory record")
    mem_delete.add_argument("record_id", type=str, help="Record ID to delete")
    mem_delete.add_argument("--plain", action="store_true", default=argparse.SUPPRESS, help="Plain text output")
    mem_delete.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS, help="Disable colored output")

    # stop
    stop_parser = subparsers.add_parser("stop", help="Request or clear stop signal")
    stop_parser.add_argument("--clear", action="store_true", help="Clear stop signal")
    stop_parser.add_argument("--plain", action="store_true", default=argparse.SUPPRESS, help="Plain text output")
    stop_parser.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS, help="Disable colored output")

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

        is_plain = getattr(args, "plain", False)
        is_no_color = getattr(args, "no_color", False) or ("NO_COLOR" in os.environ)
        console = make_console(plain=is_plain)
        if is_no_color:
            console.no_color = True

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
                return _handle_chat(args, provider=provider, config=config, console=console, is_plain=is_plain)
            elif args.subcommand == "status":
                return _handle_status(args, provider=provider, config=config, console=console)
            elif args.subcommand == "memory":
                return _handle_memory(args, config=config, console=console)
            elif args.subcommand == "stop":
                return _handle_stop(args, config=config, console=console)
            else:
                sys.stderr.write(f"Unknown subcommand: {args.subcommand}\n")
                return 2
        finally:
            audit.close()
    except AegisError as e:
        sys.stderr.write(f"Error: {e}\n")
        return 2


def _stream_response(
    prompt: str,
    *,
    conversation: Conversation | None = None,
    system_message: Mapping[str, str] | None = None,
    provider: ModelProvider,
    config: AegisConfig,
    model: str | None,
    system: str | None,
    stop_ctrl: StopController,
    console: Console,
    caps: AnimatedCaps,
    is_plain: bool,
) -> int:
    target_model = model or config.model
    buf = StreamBuffer()
    stream_ind = StreamIndicator(console, caps)
    thinking = ThinkingIndicator(console, caps)
    chunk_count = 0
    t0 = time.monotonic()
    first_chunk = True
    last_chunk = ""
    accumulated_chunks: list[str] = []

    try:
        with thinking:
            if hasattr(provider, "chat_stream"):
                messages: list[Mapping[str, str]] = []
                if system_message:
                    messages.append(dict(system_message))
                if conversation is not None:
                    messages.extend(conversation.messages())
                else:
                    messages.append({"role": "user", "content": prompt})
                stream_iter = provider.chat_stream(
                    messages,
                    model=target_model,
                    stop_check=stop_ctrl.check,
                )
            elif hasattr(provider, "generate_stream"):
                stream_iter = provider.generate_stream(
                    prompt,
                    system=system,
                    model=target_model,
                    stop_check=stop_ctrl.check,
                )
            else:
                try:
                    res = provider.generate(
                        prompt,
                        system=system,
                        model=target_model,
                        stop_check=stop_ctrl.check,
                    )
                except TypeError:
                    res = provider.generate(
                        prompt,
                        system=system,
                        model=target_model,
                    )
                stream_iter = iter([res])

            for chunk in stream_iter:
                if first_chunk:
                    thinking.stop()
                    first_chunk = False
                    if not is_plain and console.is_terminal and not console.no_color:
                        print_assistant_prefix(console)
                    stream_ind.start()

                chunk_count += 1
                last_chunk = chunk
                accumulated_chunks.append(chunk)
                elapsed = time.monotonic() - t0
                stream_ind.update(chunks=chunk_count, elapsed=elapsed)

                if is_plain:
                    render_plain(console, chunk)
                else:
                    ready = buf.feed(chunk)
                    if ready:
                        render_markdown(console, ready)

        thinking.stop()
        stream_ind.stop()

        if is_plain:
            if chunk_count > 0 and not last_chunk.endswith("\n"):
                console.print()
            else:
                try:
                    console.file.flush()
                except Exception:
                    pass
        else:
            remaining = buf.flush()
            if remaining:
                render_markdown(console, remaining)

        elapsed = time.monotonic() - t0
        completion(console, caps, elapsed, chunk_count)

        if conversation is not None:
            full_assistant_reply = "".join(accumulated_chunks)
            if full_assistant_reply:
                conversation.add_assistant(full_assistant_reply)

        return 0
    except KeyboardInterrupt:
        thinking.stop()
        stream_ind.stop()
        if not is_plain:
            remaining = buf.flush()
            if remaining:
                render_plain(console, remaining)
        if conversation is not None:
            conversation.discard_pending_user()
        print("\n[Stop requested by user]")
        return 130
    except StopRequested as e:
        thinking.stop()
        stream_ind.stop()
        if not is_plain:
            remaining = buf.flush()
            if remaining:
                render_plain(console, remaining)
        if conversation is not None:
            conversation.discard_pending_user()
        print(f"\n[Operation stopped: {e}]", file=sys.stderr)
        return 130


def _handle_chat(
    args: argparse.Namespace,
    *,
    provider: ModelProvider,
    config: AegisConfig,
    console: Console | None = None,
    is_plain: bool = False,
) -> int:
    if console is None:
        console = make_console(plain=is_plain)

    target_model = args.model or config.model
    system_text = identity_system_prompt(model=target_model, user_system=args.system)
    system_msg = {"role": "system", "content": system_text}

    if getattr(args, "tui", False):
        if not sys.stdout.isatty():
            sys.stderr.write("Textual TUI requires a TTY. Falling back to terminal chat.\n")
        else:
            try:
                from aegis.cli.tui import run_tui
                return run_tui(
                    config,
                    provider,
                    model=args.model,
                    system=args.system,
                    initial_prompt=args.prompt,
                    no_memory=getattr(args, "no_memory", False),
                    max_turns=getattr(args, "max_turns", None),
                )
            except ImportError as err:
                sys.stderr.write(f"Textual TUI is not installed ({err}). Falling back to terminal chat. Install with: pip install 'aegis-ai[tui]'\n")

    caps = capabilities(console, plain=is_plain)
    stop_ctrl = StopController(config.stop_file)

    if args.prompt is not None:
        try:
            stop_ctrl.check()
            if not is_plain:
                print_header(console, config, args.model)
            conv = Conversation(max_turns=1)
            conv.add_user(args.prompt)
            rc = _stream_response(
                args.prompt,
                conversation=conv,
                system_message=system_msg,
                provider=provider,
                config=config,
                model=args.model,
                system=args.system,
                stop_ctrl=stop_ctrl,
                console=console,
                caps=caps,
                is_plain=is_plain,
            )
            return rc
        except KeyboardInterrupt:
            print("\n[Stop requested by user]")
            return 130
        except StopRequested as e:
            print(f"\n[Operation stopped: {e}]", file=sys.stderr)
            return 130

    # Interactive REPL
    resolved_max_turns = args.max_turns if args.max_turns is not None else config.max_chat_turns
    conversation = Conversation(max_turns=resolved_max_turns)
    turn = 0
    if not is_plain:
        print_header(console, config, args.model)
        if console.is_terminal and not console.no_color:
            console.print(f"[aegis.muted]AEGIS Chat ({args.model or config.model}) - Ctrl+C to exit[/aegis.muted]")
        else:
            print(f"AEGIS Chat ({args.model or config.model}) - Ctrl+C to exit")
    else:
        print(f"AEGIS Chat ({args.model or config.model}) - Ctrl+C to exit")

    try:
        while turn < resolved_max_turns:
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
            conversation.add_user(stripped)
            rc = _stream_response(
                stripped,
                conversation=conversation,
                system_message=system_msg,
                provider=provider,
                config=config,
                model=args.model,
                system=args.system,
                stop_ctrl=stop_ctrl,
                console=console,
                caps=caps,
                is_plain=is_plain,
            )
            if rc != 0:
                return rc
            stop_ctrl.check()
    except KeyboardInterrupt:
        print("\n[Stop requested by user]")
        return 130

    return 0


def _handle_status(
    args: argparse.Namespace | None = None,
    *,
    provider: ModelProvider,
    config: AegisConfig,
    console: Console | None = None,
) -> int:
    if console is None:
        console = make_console()

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

    if console.no_color:
        console.print(f"Model: {config.model}")
        console.print(f"Ollama Host: {config.ollama_host}")
        console.print(f"Provider Health: {is_healthy}")
        console.print(f"Available Models: {available_models}")
        console.print(f"Memory DB: {config.memory_db} ({count} records)")
        console.print(f"Audit Log: {config.audit_log} (last event: {last_event_str})")
        console.print(f"Stop State: {stop_state}")
    else:
        console.print(f"[aegis.accent]Model:[/aegis.accent] {config.model}")
        console.print(f"[aegis.accent]Ollama Host:[/aegis.accent] {config.ollama_host}")
        health_style = "aegis.success" if is_healthy else "aegis.error"
        console.print(f"[aegis.accent]Provider Health:[/aegis.accent] [{health_style}]{is_healthy}[/{health_style}]")
        console.print(f"[aegis.accent]Available Models:[/aegis.accent] {available_models}")
        console.print(f"[aegis.accent]Memory DB:[/aegis.accent] {config.memory_db} ({count} records)")
        console.print(f"[aegis.accent]Audit Log:[/aegis.accent] {config.audit_log} (last event: {last_event_str})")
        stop_style = "aegis.error" if stop_ctrl.is_stopped() else "aegis.success"
        console.print(f"[aegis.accent]Stop State:[/aegis.accent] [{stop_style}]{stop_state}[/{stop_style}]")

    return 0 if is_healthy else 1


def _handle_memory(
    args: argparse.Namespace,
    *,
    config: AegisConfig,
    console: Console | None = None,
) -> int:
    if console is None:
        console = make_console()

    store = _get_memory_store(config.memory_db)
    try:
        if args.memory_action == "save":
            rec = store.save(args.text, tags=args.tag or (), source=args.source)
            if console.no_color:
                console.print(f"Saved memory [{rec.id}]")
            else:
                console.print(f"[aegis.success]Saved memory[/aegis.success] \\[{rec.id}]")
            return 0
        elif args.memory_action == "search":
            limit = args.limit if args.limit is not None else config.max_memory_results
            limit = min(max(1, limit), config.max_memory_results)
            records = store.search(args.query, limit=limit)
            if not records:
                console.print("No memories found.")
            else:
                for r in records:
                    tag_str = f" tags={list(r.tags)}" if r.tags else ""
                    src_str = f" source={r.source}" if r.source else ""
                    console.print(f"[{r.id}] {r.content}{tag_str}{src_str}", markup=False)
            return 0
        elif args.memory_action == "list":
            limit = args.limit if args.limit is not None else config.max_memory_results
            limit = min(max(1, limit), config.max_memory_results)
            records = store.list(limit=limit)
            if not records:
                console.print("No memories stored.")
            else:
                for r in records:
                    tag_str = f" tags={list(r.tags)}" if r.tags else ""
                    src_str = f" source={r.source}" if r.source else ""
                    console.print(f"[{r.id}] {r.content}{tag_str}{src_str}", markup=False)
            return 0
        elif args.memory_action == "delete":
            deleted = store.delete(args.record_id)
            if deleted:
                if console.no_color:
                    console.print(f"Deleted memory [{args.record_id}]")
                else:
                    console.print(f"[aegis.success]Deleted memory[/aegis.success] \\[{args.record_id}]")
            else:
                console.print(f"Memory record not found: {args.record_id}", markup=False)
            return 0
        else:
            sys.stderr.write(f"Unknown memory action: {args.memory_action}\n")
            return 2
    finally:
        store.close()


def _handle_stop(
    args: argparse.Namespace,
    *,
    config: AegisConfig,
    console: Console | None = None,
) -> int:
    if console is None:
        console = make_console()

    stop_ctrl = StopController(config.stop_file)
    if args.clear:
        stop_ctrl.clear()
        if console.no_color:
            console.print("Stop state cleared")
        else:
            console.print("[aegis.success]Stop state cleared[/aegis.success]")
    else:
        stop_ctrl.request_stop("user")
        if console.no_color:
            console.print("Stop requested")
        else:
            console.print("[aegis.warning]Stop requested[/aegis.warning]")
    return 0
