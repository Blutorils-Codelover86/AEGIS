"""Unit tests for tool registry and sandboxed execution engine."""
from __future__ import annotations

import unittest
from typing import Any, Mapping, Sequence

from aegis.audit import InMemoryAuditLogger
from aegis.errors import PermissionDenied, ToolError
from aegis.interfaces import (
    AuditEvent,
    ConfirmationToken,
    Decision,
    MemoryRecord,
    MemoryStore,
    PermissionRequest,
)
from aegis.permissions import DeterministicPolicy
from aegis.tools import ToolRegistry, ToolSpec, default_registry


class MockMemoryStore:
    """In-memory mock satisfying MemoryStore protocol for testing."""

    def __init__(self) -> None:
        self.records: dict[str, MemoryRecord] = {}
        self._counter = 0

    def save(
        self,
        content: str,
        *,
        tags: Sequence[str] = (),
        source: str | None = None,
    ) -> MemoryRecord:
        self._counter += 1
        rec_id = f"mem-{self._counter}"
        rec = MemoryRecord(
            id=rec_id,
            content=content,
            tags=tuple(tags),
            source=source,
            created_at="2026-10-18T12:00:00+00:00",
        )
        self.records[rec_id] = rec
        return rec

    def search(self, query: str, *, limit: int = 5) -> list[MemoryRecord]:
        results = [r for r in self.records.values() if query.lower() in r.content.lower()]
        return results[:limit]

    def get(self, record_id: str) -> MemoryRecord | None:
        return self.records.get(record_id)

    def delete(self, record_id: str) -> bool:
        if record_id in self.records:
            del self.records[record_id]
            return True
        return False

    def list(self, *, limit: int = 50) -> list[MemoryRecord]:
        return list(self.records.values())[:limit]

    def close(self) -> None:
        pass


class TestToolRegistry(unittest.TestCase):
    """Test suite for ToolRegistry, default tools, and sandboxed execution."""

    def setUp(self) -> None:
        self.policy = DeterministicPolicy()
        self.audit = InMemoryAuditLogger()
        self.mock_memory = MockMemoryStore()
        self.registry = default_registry(memory=self.mock_memory)

    def test_echo_tool_allowed(self) -> None:
        """Verify echo tool executes successfully under allowlist.readonly."""
        result = self.registry.execute(
            "echo",
            {"message": "hello aegis"},
            policy=self.policy,
            audit=self.audit,
            actor="agent",
        )
        self.assertEqual(result, "hello aegis")

        # Verify audit records: tool.request and tool.result
        self.assertEqual(len(self.audit.events), 2)

        req_event = self.audit.events[0]
        self.assertEqual(req_event.event, "tool.request")
        self.assertEqual(req_event.actor, "agent")
        self.assertEqual(req_event.action, "sys.status")
        self.assertEqual(req_event.decision, "allow")
        self.assertEqual(req_event.details["tool"], "echo")
        self.assertEqual(req_event.details["rule"], "allowlist.readonly")

        res_event = self.audit.events[1]
        self.assertEqual(res_event.event, "tool.result")
        self.assertEqual(res_event.actor, "agent")
        self.assertEqual(res_event.action, "sys.status")
        self.assertEqual(res_event.outcome, "success")
        self.assertEqual(res_event.details["tool"], "echo")

    def test_run_shell_raises_permission_denied_by_default(self) -> None:
        """Verify run_shell raises PermissionDenied under deny.shell by default."""
        with self.assertRaises(PermissionDenied) as ctx:
            self.registry.execute(
                "run_shell",
                {"command": "ls -la"},
                policy=self.policy,
                audit=self.audit,
            )
        self.assertEqual(ctx.exception.result.decision, Decision.DENY)
        self.assertEqual(ctx.exception.result.rule, "deny.shell")

        # Audit should contain tool.request (denied) and no tool.result
        self.assertEqual(len(self.audit.events), 1)
        self.assertEqual(self.audit.events[0].event, "tool.request")
        self.assertEqual(self.audit.events[0].decision, "deny")
        self.assertEqual(self.audit.events[0].details["rule"], "deny.shell")

    def test_run_shell_inert_even_when_confirmed(self) -> None:
        """Verify run_shell handler raises ToolError even if confirmation is granted."""
        permissive_policy = DeterministicPolicy(allow_shell=True)

        # Unconfirmed -> PermissionDenied
        with self.assertRaises(PermissionDenied) as ctx:
            self.registry.execute(
                "run_shell",
                {"command": "id"},
                policy=permissive_policy,
                audit=self.audit,
                confirm=False,
            )
        self.assertEqual(ctx.exception.result.decision, Decision.REQUIRE_CONFIRMATION)
        self.assertEqual(ctx.exception.result.rule, "confirm.shell")

        # Bare confirm=True is rejected -> raises PermissionDenied
        with self.assertRaises(PermissionDenied):
            self.registry.execute(
                "run_shell",
                {"command": "id"},
                policy=permissive_policy,
                audit=self.audit,
                confirm=True,
            )

        # Confirmed with valid token -> handler raises ToolError("shell execution is disabled")
        req = PermissionRequest(action="shell.exec", parameters={"command": "id"}, consequential=True)
        token = ConfirmationToken.create(req)
        with self.assertRaises(ToolError) as ctx_tool:
            self.registry.execute(
                "run_shell",
                {"command": "id"},
                policy=permissive_policy,
                audit=self.audit,
                confirm=token,
            )
        self.assertIn("shell execution is disabled", str(ctx_tool.exception))

        # Check audit recorded error outcome
        last_event = self.audit.events[-1]
        self.assertEqual(last_event.event, "tool.result")
        self.assertEqual(last_event.outcome, "error")

    def test_save_memory_requires_confirmation_then_succeeds(self) -> None:
        """Verify save_memory requires confirmation, then succeeds when confirm=True."""
        # 1. Unconfirmed attempt -> raises PermissionDenied with consequential.confirm_required
        with self.assertRaises(PermissionDenied) as ctx:
            self.registry.execute(
                "save_memory",
                {"content": "AEGIS safety core online", "tags": ["core", "safety"]},
                policy=self.policy,
                audit=self.audit,
                confirm=False,
            )
        self.assertEqual(ctx.exception.result.decision, Decision.REQUIRE_CONFIRMATION)
        self.assertEqual(ctx.exception.result.rule, "consequential.confirm_required")
        self.assertEqual(len(self.audit.events), 1)
        self.assertEqual(self.audit.events[0].event, "tool.request")
        self.assertEqual(self.audit.events[0].decision, "require_confirmation")

        # 2. Bare confirm=True attempt -> raises PermissionDenied
        with self.assertRaises(PermissionDenied):
            self.registry.execute(
                "save_memory",
                {"content": "AEGIS safety core online", "tags": ["core", "safety"]},
                policy=self.policy,
                audit=self.audit,
                confirm=True,
            )

        # 3. Confirmed attempt with valid ConfirmationToken -> succeeds
        req = PermissionRequest(
            action="mem.save",
            parameters={"content": "AEGIS safety core online", "tags": ["core", "safety"]},
            consequential=True,
        )
        token = ConfirmationToken.create(req)
        record = self.registry.execute(
            "save_memory",
            {"content": "AEGIS safety core online", "tags": ["core", "safety"]},
            policy=self.policy,
            audit=self.audit,
            confirm=token,
        )
        self.assertIsInstance(record, MemoryRecord)
        self.assertEqual(record.content, "AEGIS safety core online")
        self.assertEqual(record.tags, ("core", "safety"))

        # Verify audit events: previous unconfirmed + bare confirm + token request + token result
        last_request = self.audit.events[-2]
        last_result = self.audit.events[-1]
        self.assertEqual(last_request.event, "tool.request")
        self.assertEqual(last_request.decision, "require_confirmation")
        self.assertEqual(last_result.event, "tool.result")
        self.assertEqual(last_result.outcome, "success")
        self.assertEqual(last_result.details["tool"], "save_memory")

    def test_search_memory_tool(self) -> None:
        """Verify search_memory executes as safe read-only action."""
        self.mock_memory.save("Important policy guideline", tags=("policy",))
        results = self.registry.execute(
            "search_memory",
            {"query": "guideline"},
            policy=self.policy,
            audit=self.audit,
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].content, "Important policy guideline")
        self.assertEqual(self.audit.events[0].decision, "allow")
        self.assertEqual(self.audit.events[0].details["rule"], "allowlist.readonly")

    def test_delete_memory_tool(self) -> None:
        """Verify delete_memory is consequential and requires confirmation."""
        rec = self.mock_memory.save("Temporary data")

        # Without confirmation -> denied
        with self.assertRaises(PermissionDenied):
            self.registry.execute(
                "delete_memory",
                {"id": rec.id},
                policy=self.policy,
                audit=self.audit,
                confirm=False,
            )

        # With bare confirm=True -> denied
        with self.assertRaises(PermissionDenied):
            self.registry.execute(
                "delete_memory",
                {"id": rec.id},
                policy=self.policy,
                audit=self.audit,
                confirm=True,
            )

        # With valid ConfirmationToken -> deleted
        req = PermissionRequest(action="mem.delete", parameters={"id": rec.id}, consequential=True)
        token = ConfirmationToken.create(req)
        deleted = self.registry.execute(
            "delete_memory",
            {"id": rec.id},
            policy=self.policy,
            audit=self.audit,
            confirm=token,
        )
        self.assertTrue(deleted)
        self.assertIsNone(self.mock_memory.get(rec.id))

    def test_memory_tools_raise_tool_error_if_memory_none(self) -> None:
        """Verify memory tools raise ToolError if memory store was not provided."""
        empty_reg = default_registry(memory=None)

        with self.assertRaises(ToolError) as ctx:
            empty_reg.execute("search_memory", {"query": "test"}, policy=self.policy, audit=self.audit)
        self.assertIn("Memory store is not available", str(ctx.exception))

        req = PermissionRequest(action="mem.save", parameters={"content": "test"}, consequential=True)
        token = ConfirmationToken.create(req)
        with self.assertRaises(ToolError) as ctx2:
            empty_reg.execute(
                "save_memory",
                {"content": "test"},
                policy=self.policy,
                audit=self.audit,
                confirm=token,
            )
        self.assertIn("Memory store is not available", str(ctx2.exception))

    def test_unknown_tool_raises_tool_error(self) -> None:
        """Verify calling an unregistered tool name raises ToolError."""
        with self.assertRaises(ToolError) as ctx:
            self.registry.execute(
                "non_existent_tool",
                {"arg": 1},
                policy=self.policy,
                audit=self.audit,
            )
        self.assertIn("Unknown tool", str(ctx.exception))
        self.assertEqual(len(self.audit.events), 0)

    def test_fetch_url_network_boundaries(self) -> None:
        """Verify fetch_url obeys policy network boundaries."""
        # Non-loopback with network disallowed -> PermissionDenied
        with self.assertRaises(PermissionDenied) as ctx:
            self.registry.execute(
                "fetch_url",
                {"url": "https://external-api.com/data"},
                policy=self.policy,
                audit=self.audit,
                confirm=True,
            )
        self.assertEqual(ctx.exception.result.rule, "deny.network")

    def test_tool_spec_and_custom_tool_registration(self) -> None:
        """Verify custom tool registration, discovery, and execution."""
        registry = ToolRegistry()

        def custom_handler(params: Mapping[str, Any]) -> str:
            return f"Processed {params.get('value')}"

        spec = ToolSpec(
            name="custom_op",
            description="A custom test operation",
            action="sys.status",
            consequential=False,
            handler=custom_handler,
        )
        registry.register(spec)

        self.assertIn("custom_op", registry.names())
        self.assertEqual(registry.get("custom_op"), spec)

        output = registry.execute(
            "custom_op",
            {"value": 42},
            policy=self.policy,
            audit=self.audit,
        )
        self.assertEqual(output, "Processed 42")

    def test_handler_exception_wrapped_in_tool_error(self) -> None:
        """Verify unexpected handler exceptions are wrapped in ToolError and audited."""
        registry = ToolRegistry()

        def failing_handler(params: Mapping[str, Any]) -> Any:
            raise ValueError("Something went wrong inside handler")

        registry.register(
            ToolSpec(
                name="failing_tool",
                description="A tool that always fails",
                action="sys.status",
                consequential=False,
                handler=failing_handler,
            )
        )

        with self.assertRaises(ToolError) as ctx:
            registry.execute(
                "failing_tool",
                policy=self.policy,
                audit=self.audit,
            )
        self.assertIn("Tool 'failing_tool' execution failed", str(ctx.exception))
        self.assertIn("Something went wrong inside handler", str(ctx.exception))

        # Check error was audited
        last_event = self.audit.events[-1]
        self.assertEqual(last_event.event, "tool.result")
        self.assertEqual(last_event.outcome, "error")
        self.assertEqual(last_event.details["error_type"], "ValueError")

    def test_conflicting_target_and_url_parameters(self) -> None:
        """Verify conflicting target and url parameters raise ToolError without network call (B1)."""
        with self.assertRaises(ToolError) as ctx:
            self.registry.execute(
                "fetch_url",
                {"target": "http://127.0.0.1:8080/api", "url": "https://example.com/api"},
                policy=self.policy,
                audit=self.audit,
            )
        self.assertIn("Conflicting 'target' and 'url'", str(ctx.exception))

    def test_fetch_url_blocks_redirect_to_remote(self) -> None:
        """Verify loopback server redirecting to remote URL is blocked by security policy (B2)."""
        import http.server
        import socketserver
        import threading

        class RedirectHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", "https://example.com/exfiltrate")
                self.end_headers()

            def log_message(self, format, *args):
                pass

        with socketserver.TCPServer(("127.0.0.1", 0), RedirectHandler) as httpd:
            port = httpd.server_address[1]
            t = threading.Thread(target=httpd.handle_request)
            t.daemon = True
            t.start()

            with self.assertRaises(ToolError) as ctx:
                self.registry.execute(
                    "fetch_url",
                    {"url": f"http://127.0.0.1:{port}/redirect"},
                    policy=self.policy,
                    audit=self.audit,
                )
            self.assertIn("redirect", str(ctx.exception).lower())
            t.join(timeout=2.0)


if __name__ == "__main__":
    unittest.main()
