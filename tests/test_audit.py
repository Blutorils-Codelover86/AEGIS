"""Tests for structured audit logging and event round-tripping."""
from __future__ import annotations

import os
import tempfile
import threading
import unittest
from datetime import datetime, timezone

from aegis.audit import InMemoryAuditLogger, JsonlAuditLogger, read_events
from aegis.interfaces import AuditEvent, AuditLogger


class TestAuditLogger(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.log_path = os.path.join(self.temp_dir.name, "audit.jsonl")

    def test_protocol_conformance(self) -> None:
        logger = JsonlAuditLogger(self.log_path)
        logger.close()
        self.assertIsInstance(logger, AuditLogger)

        in_mem = InMemoryAuditLogger()
        self.assertIsInstance(in_mem, AuditLogger)

    def test_log_round_trip_and_timestamp_stamping(self) -> None:
        logger = JsonlAuditLogger(self.log_path)
        event1 = AuditEvent(
            event="test.event",
            actor="test-actor",
            action="sys.status",
            decision="allow",
            outcome="success",
            details={"key": "value", "count": 1},
        )
        custom_ts = "2026-10-18T10:00:00.000000+00:00"
        event2 = AuditEvent(
            event="test.event2",
            actor="user",
            action="mem.save",
            decision="allow",
            outcome="success",
            details={},
            timestamp=custom_ts,
        )

        logger.log(event1)
        logger.log(event2)
        logger.close()

        # Verify file content format (compact JSON, one line per event)
        with open(self.log_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
        self.assertEqual(len(lines), 2)
        self.assertNotIn(" ", lines[0].split('{"event":')[1].split(',"actor"')[0])

        # Read back using read_events
        loaded = read_events(self.log_path)
        self.assertEqual(len(loaded), 2)

        self.assertEqual(loaded[0].event, "test.event")
        self.assertEqual(loaded[0].actor, "test-actor")
        self.assertEqual(loaded[0].action, "sys.status")
        self.assertEqual(loaded[0].decision, "allow")
        self.assertEqual(loaded[0].outcome, "success")
        self.assertEqual(loaded[0].details, {"key": "value", "count": 1})
        self.assertTrue(loaded[0].timestamp.endswith("+00:00"))

        self.assertEqual(loaded[1].event, "test.event2")
        self.assertEqual(loaded[1].timestamp, custom_ts)

    def test_read_events_missing_file_returns_empty(self) -> None:
        nonexistent = os.path.join(self.temp_dir.name, "missing.jsonl")
        self.assertEqual(read_events(nonexistent), [])

    def test_in_memory_audit_logger(self) -> None:
        logger = InMemoryAuditLogger()
        ev = AuditEvent(event="mem.log", actor="agent")
        logger.log(ev)
        self.assertEqual(len(logger.events), 1)
        self.assertEqual(logger.events[0].event, "mem.log")
        self.assertTrue(logger.events[0].timestamp.endswith("+00:00"))
        logger.close()

    def test_thread_safety_smoke(self) -> None:
        logger = JsonlAuditLogger(self.log_path)
        num_threads = 10
        events_per_thread = 20
        total_events = num_threads * events_per_thread

        def worker(thread_idx: int) -> None:
            for i in range(events_per_thread):
                logger.log(
                    AuditEvent(
                        event=f"thread.{thread_idx}",
                        actor=f"worker-{thread_idx}",
                        action="test.concurrency",
                        details={"index": i},
                    )
                )

        threads = [
            threading.Thread(target=worker, args=(t,))
            for t in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        logger.close()

        records = read_events(self.log_path)
        self.assertEqual(len(records), total_events)
        for r in records:
            self.assertTrue(r.timestamp.endswith("+00:00"))

    def test_audit_file_permissions_0600(self) -> None:
        """Verify audit file is created with 0600 permissions."""
        import stat
        logger = JsonlAuditLogger(self.log_path)
        logger.log(AuditEvent(event="perm.test", actor="user"))
        logger.close()

        file_mode = stat.S_IMODE(os.stat(self.log_path).st_mode)
        self.assertEqual(file_mode, 0o600)

    def test_audit_cli_argv_redaction_permutations(self) -> None:
        """Verify CLI argv redaction handles all flag and positional permutations (M2)."""
        from aegis.cli.main import _redact_argv_for_audit

        # Permutation 1: memory save with options before positional content
        mem_argv1 = ["memory", "save", "--tag", "secret-tag-1", "very confidential fact", "--source", "secret-source-1"]
        redacted1 = _redact_argv_for_audit(mem_argv1)
        redacted1_str = " ".join(redacted1)
        self.assertNotIn("secret-tag-1", redacted1_str)
        self.assertNotIn("very confidential fact", redacted1_str)
        self.assertNotIn("secret-source-1", redacted1_str)
        self.assertIn("[REDACTED tag]", redacted1_str)  # secret-tag-1
        self.assertIn("[REDACTED len=22]", redacted1_str)  # very confidential fact
        self.assertIn("[REDACTED source]", redacted1_str)  # secret-source-1

        # Permutation 2: memory save with options after positional content
        mem_argv2 = ["memory", "save", "another confidential fact", "--source", "secret-source-2", "--tag", "secret-tag-2"]
        redacted2 = _redact_argv_for_audit(mem_argv2)
        redacted2_str = " ".join(redacted2)
        self.assertNotIn("secret-tag-2", redacted2_str)
        self.assertNotIn("another confidential fact", redacted2_str)
        self.assertNotIn("secret-source-2", redacted2_str)
        self.assertIn("[REDACTED len=25]", redacted2_str)
        self.assertIn("[REDACTED source]", redacted2_str)
        self.assertIn("[REDACTED tag]", redacted2_str)

        # Permutation 3: memory delete / get record ID redaction
        del_argv = ["memory", "delete", "mem-uuid-99999-private"]
        redacted_del = _redact_argv_for_audit(del_argv)
        self.assertNotIn("mem-uuid-99999-private", " ".join(redacted_del))
        self.assertIn("[REDACTED id]", " ".join(redacted_del))

        get_argv = ["memory", "get", "mem-uuid-88888-private"]
        redacted_get = _redact_argv_for_audit(get_argv)
        self.assertNotIn("mem-uuid-88888-private", " ".join(redacted_get))

        # Permutation 4: chat permutations
        chat_argv1 = ["chat", "--system", "SuperSecretSys", "--prompt", "SuperSecretPrompt"]
        redacted_chat1 = _redact_argv_for_audit(chat_argv1)
        self.assertNotIn("SuperSecretSys", " ".join(redacted_chat1))
        self.assertNotIn("SuperSecretPrompt", " ".join(redacted_chat1))

        chat_argv2 = ["chat", "--prompt", "SuperSecretPrompt", "--system", "SuperSecretSys"]
        redacted_chat2 = _redact_argv_for_audit(chat_argv2)
        self.assertNotIn("SuperSecretSys", " ".join(redacted_chat2))
        self.assertNotIn("SuperSecretPrompt", " ".join(redacted_chat2))

    def test_audit_url_query_and_credential_redaction(self) -> None:
        """Verify URL query parameters and credentials are scrubbed in audit (M2)."""
        from aegis.tools.registry import _redact_url_for_audit

        sensitive_url = "http://alice:supersecret@127.0.0.1:8080/v1/resource?token=secrettoken&api_key=privkey123#frag"
        redacted = _redact_url_for_audit(sensitive_url)
        self.assertNotIn("alice", redacted)
        self.assertNotIn("supersecret", redacted)
        self.assertNotIn("secrettoken", redacted)
        self.assertNotIn("privkey123", redacted)
        self.assertIn("[REDACTED]@127.0.0.1:8080/v1/resource", redacted)
        self.assertIn("?[REDACTED_QUERY]", redacted)
        self.assertIn("#[REDACTED_FRAGMENT]", redacted)

    def test_audit_tool_params_redaction(self) -> None:
        """Verify tool params redact content, tags, sources, ids, query strings, and unknown keys (M2)."""
        from aegis.tools.registry import _redact_params_for_audit

        tool_params = {
            "content": "Secret note to be saved in memory",
            "tags": ["secret-tag", "confidential"],
            "source": "secret-document-v1",
            "id": "mem-private-123",
            "query": "search for nuclear launch codes",
            "url": "http://user:password@localhost:5000/api?token=abc",
            "unknown_arbitrary_key": "arbitrary confidential payload",
            "limit": 10,
        }
        redacted = _redact_params_for_audit(tool_params)
        self.assertEqual(redacted["content"], "[REDACTED len=33]")
        self.assertEqual(redacted["tags"], ["[REDACTED tag]", "[REDACTED tag]"])
        self.assertEqual(redacted["source"], "[REDACTED source]")
        self.assertEqual(redacted["id"], "[REDACTED id]")
        self.assertEqual(redacted["query"], "[REDACTED query]")
        self.assertNotIn("password", redacted["url"])
        self.assertNotIn("token=abc", redacted["url"])
        self.assertEqual(redacted["unknown_arbitrary_key"], "[REDACTED len=30]")
        self.assertEqual(redacted["limit"], 10)


if __name__ == "__main__":
    unittest.main()

