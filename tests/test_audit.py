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


if __name__ == "__main__":
    unittest.main()
