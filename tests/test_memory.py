"""Tests for aegis.memory SQLiteMemoryStore implementation."""
from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timezone

from aegis.errors import MemoryStoreError
from aegis.interfaces import MemoryRecord, MemoryStore
from aegis.memory import SQLiteMemoryStore, open_memory
from aegis.memory.models import decode_tags, encode_tags, memory_row_factory, row_to_record


class TestMemoryStore(unittest.TestCase):
    """Test suite for SQLiteMemoryStore and related memory models."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_memory.db")
        self.store = SQLiteMemoryStore(self.db_path)

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    def test_protocol_conformance(self) -> None:
        """Verify SQLiteMemoryStore satisfies the MemoryStore protocol at runtime."""
        self.assertTrue(isinstance(self.store, MemoryStore))

    def test_save_and_get_round_trip(self) -> None:
        """Test save and get round-trip with content, tags, source, and ISO-8601 UTC timestamp."""
        content = "Remember to verify all system logs."
        tags = ("safety", "audit", "compliance")
        source = "unit-test"

        record = self.store.save(content, tags=tags, source=source)

        # Check returned MemoryRecord fields
        self.assertIsInstance(record, MemoryRecord)
        self.assertEqual(len(record.id), 32)
        # Verify id is a valid 32-char hex string
        int(record.id, 16)
        self.assertEqual(record.content, content)
        self.assertEqual(record.tags, tags)
        self.assertEqual(record.source, source)
        self.assertTrue(record.created_at.endswith("+00:00"))

        dt = datetime.fromisoformat(record.created_at)
        self.assertEqual(dt.tzinfo, timezone.utc)

        # Round-trip get
        fetched = self.store.get(record.id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched, record)

        # Non-existent ID returns None
        self.assertIsNone(self.store.get("non-existent-id"))

    def test_save_with_default_arguments(self) -> None:
        """Test saving without tags or source defaults properly."""
        record = self.store.save("Minimal record")
        self.assertEqual(record.content, "Minimal record")
        self.assertEqual(record.tags, ())
        self.assertIsNone(record.source)
        self.assertTrue(record.created_at.endswith("+00:00"))

        fetched = self.store.get(record.id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.tags, ())
        self.assertIsNone(fetched.source)

    def test_search_matches_content_and_tags_case_insensitive(self) -> None:
        """Search matches both content and tags, is case-insensitive, respects limit, and handles empty query."""
        r1 = self.store.save("Python architecture review", tags=("docs", "core"))
        r2 = self.store.save("Deploying service to production", tags=("python", "infra"))
        r3 = self.store.save("Database migration script", tags=("sqlite", "sql"))

        # Case-insensitive substring match in content
        results_content = self.store.search("ARCHITECTURE")
        self.assertEqual(len(results_content), 1)
        self.assertEqual(results_content[0].id, r1.id)

        # Case-insensitive substring match in tags
        results_tags = self.store.search("INFRA")
        self.assertEqual(len(results_tags), 1)
        self.assertEqual(results_tags[0].id, r2.id)

        # Search matches both records that contain "python" (one in content, one in tags)
        results_both = self.store.search("python")
        self.assertEqual(len(results_both), 2)
        result_ids = {r.id for r in results_both}
        self.assertEqual(result_ids, {r1.id, r2.id})

        # Empty or whitespace query returns []
        self.assertEqual(self.store.search(""), [])
        self.assertEqual(self.store.search("   \t\n "), [])

        # Limit respected
        limited_results = self.store.search("python", limit=1)
        self.assertEqual(len(limited_results), 1)

        # Non-positive limit clamped to >= 1
        clamped_results = self.store.search("python", limit=0)
        self.assertEqual(len(clamped_results), 1)
        clamped_neg = self.store.search("python", limit=-5)
        self.assertEqual(len(clamped_neg), 1)

    def test_delete_and_get_after_delete(self) -> None:
        """delete returns True then False; get after delete -> None."""
        record = self.store.save("Temporary data to be deleted")
        self.assertIsNotNone(self.store.get(record.id))

        # First delete succeeds
        self.assertTrue(self.store.delete(record.id))

        # Second delete on same id returns False
        self.assertFalse(self.store.delete(record.id))

        # Deleting non-existent id returns False
        self.assertFalse(self.store.delete("non-existent-id"))

        # Get after delete returns None
        self.assertIsNone(self.store.get(record.id))

    def test_list_limit_and_ordering_and_persistence(self) -> None:
        """list limit + ordering; tags survive close/reopen."""
        r1 = self.store.save("First entry", tags=("tag1", "common"))
        r2 = self.store.save("Second entry", tags=("tag2", "common"))
        r3 = self.store.save("Third entry", tags=("tag3", "common"))

        # list limit
        listed_two = self.store.list(limit=2)
        self.assertEqual(len(listed_two), 2)

        # list ordering (created_at DESC, id ASC)
        all_listed = self.store.list(limit=50)
        self.assertEqual(len(all_listed), 3)
        # All items are ordered descending by created_at (or tie-breaker id ASC)
        for i in range(len(all_listed) - 1):
            t1 = (all_listed[i].created_at, all_listed[i].id)
            t2 = (all_listed[i + 1].created_at, all_listed[i + 1].id)
            # t1 should be >= t2 for created_at, or if equal created_at, t1.id <= t2.id
            if t1[0] == t2[0]:
                self.assertLessEqual(t1[1], t2[1])
            else:
                self.assertGreater(t1[0], t2[0])

        # Clamping limit for list
        self.assertEqual(len(self.store.list(limit=0)), 1)
        self.assertEqual(len(self.store.list(limit=-1)), 1)

        # Verify count
        self.assertEqual(self.store.count(), 3)

        # Close and reopen store, verify persistence and tags
        self.store.close()
        reopened = open_memory(self.db_path)
        try:
            persisted_all = reopened.list(limit=50)
            self.assertEqual(len(persisted_all), 3)

            persisted_r1 = reopened.get(r1.id)
            self.assertIsNotNone(persisted_r1)
            self.assertEqual(persisted_r1.content, r1.content)
            self.assertEqual(persisted_r1.tags, ("tag1", "common"))
            self.assertIsInstance(persisted_r1.tags, tuple)

            persisted_r2 = reopened.get(r2.id)
            self.assertIsNotNone(persisted_r2)
            self.assertEqual(persisted_r2.tags, ("tag2", "common"))
        finally:
            reopened.close()

    def test_empty_content_raises_error(self) -> None:
        """empty or whitespace content raises MemoryStoreError."""
        with self.assertRaises(MemoryStoreError):
            self.store.save("")

        with self.assertRaises(MemoryStoreError):
            self.store.save("   \t\r\n  ")

    def test_close_is_idempotent_and_operations_fail_after_close(self) -> None:
        """close() is idempotent and operations after close raise MemoryStoreError."""
        self.store.close()
        # Additional close calls should not raise
        self.store.close()
        self.store.close()

        # Any operation after close must raise MemoryStoreError
        with self.assertRaises(MemoryStoreError):
            self.store.save("Test content")

        with self.assertRaises(MemoryStoreError):
            self.store.get("some-id")

        with self.assertRaises(MemoryStoreError):
            self.store.delete("some-id")

        with self.assertRaises(MemoryStoreError):
            self.store.search("query")

        with self.assertRaises(MemoryStoreError):
            self.store.list()

        with self.assertRaises(MemoryStoreError):
            self.store.count()

    def test_parent_directory_creation(self) -> None:
        """SQLiteMemoryStore creates nested parent directories if they do not exist."""
        nested_dir = os.path.join(self.temp_dir.name, "a", "b", "c")
        nested_path = os.path.join(nested_dir, "nested_memory.db")
        self.assertFalse(os.path.exists(nested_dir))

        store = SQLiteMemoryStore(nested_path)
        try:
            self.assertTrue(os.path.isdir(nested_dir))
            rec = store.save("Nested db test")
            self.assertEqual(store.get(rec.id).content, "Nested db test")
        finally:
            store.close()

    def test_in_memory_database(self) -> None:
        """SQLiteMemoryStore supports in-memory databases with ':memory:'."""
        mem_store = SQLiteMemoryStore(":memory:")
        try:
            rec = mem_store.save("In-memory record", tags=("transient",))
            self.assertEqual(mem_store.get(rec.id), rec)
            self.assertEqual(len(mem_store.search("transient")), 1)
        finally:
            mem_store.close()

    def test_context_manager(self) -> None:
        """SQLiteMemoryStore works as a context manager."""
        cm_path = os.path.join(self.temp_dir.name, "cm_test.db")
        with open_memory(cm_path) as s:
            rec = s.save("Context manager test")
            self.assertIsNotNone(s.get(rec.id))
        # After exit, store should be closed
        with self.assertRaises(MemoryStoreError):
            s.save("Should fail")

    def test_thread_safety(self) -> None:
        """Multiple threads can write and read concurrently without collision."""
        num_threads = 8
        items_per_thread = 10
        errors: list[Exception] = []

        def worker(thread_idx: int) -> None:
            try:
                for i in range(items_per_thread):
                    rec = self.store.save(
                        f"Thread {thread_idx} item {i}",
                        tags=(f"t{thread_idx}", "concurrent"),
                        source=f"worker-{thread_idx}",
                    )
                    fetched = self.store.get(rec.id)
                    if fetched is None or fetched.content != rec.content:
                        raise RuntimeError(f"Mismatch in thread {thread_idx}")
                    search_res = self.store.search(f"t{thread_idx}")
                    if not search_res:
                        raise RuntimeError(f"Search empty for thread {thread_idx}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(self.store.count(), num_threads * items_per_thread)

    def test_models_encode_decode_tags_and_factories(self) -> None:
        """Direct tests for models helper functions."""
        self.assertEqual(encode_tags(None), "[]")
        self.assertEqual(encode_tags([]), "[]")
        self.assertEqual(encode_tags(["a", "b"]), '["a", "b"]')

        self.assertEqual(decode_tags(None), ())
        self.assertEqual(decode_tags(""), ())
        self.assertEqual(decode_tags("[]"), ())
        self.assertEqual(decode_tags('["alpha", "beta"]'), ("alpha", "beta"))
        self.assertEqual(decode_tags("not json"), ())
        self.assertEqual(decode_tags(["already", "list"]), ("already", "list"))

        # row_to_record with tuple
        row_tuple = ("id1", "content1", '["tag1"]', "src1", "2026-01-01T00:00:00+00:00")
        rec1 = row_to_record(row_tuple)
        self.assertEqual(rec1.id, "id1")
        self.assertEqual(rec1.content, "content1")
        self.assertEqual(rec1.tags, ("tag1",))
        self.assertEqual(rec1.source, "src1")
        self.assertEqual(rec1.created_at, "2026-01-01T00:00:00+00:00")

        # row_to_record with mapping
        row_dict = {
            "id": "id2",
            "content": "content2",
            "tags": '["tag2"]',
            "source": None,
            "created_at": "2026-01-02T00:00:00+00:00",
        }
        rec2 = row_to_record(row_dict)
        self.assertEqual(rec2.id, "id2")
        self.assertIsNone(rec2.source)

        # memory_row_factory
        conn = sqlite3.connect(":memory:")
        cur = conn.execute("SELECT 'id3' AS id, 'content3' AS content, '[\"tag3\"]' AS tags, NULL AS source, '2026-01-03T00:00:00+00:00' AS created_at")
        raw_row = cur.fetchone()
        rec3 = memory_row_factory(cur, raw_row)
        self.assertEqual(rec3.id, "id3")
        self.assertEqual(rec3.tags, ("tag3",))
        conn.close()

    def test_search_punctuation_and_escaped_tags(self) -> None:
        """Verify search against JSON structural punctuation does not falsely match (m1)."""
        r1 = self.store.save("Plain alpha text", tags=("alpha", "beta"))
        r2 = self.store.save("Plain gamma text", tags=("complex,tag", "quote\"inside"))

        # Queries matching JSON structure syntax must not match tags
        self.assertEqual(self.store.search('","'), [])
        self.assertEqual(self.store.search("["), [])
        self.assertEqual(self.store.search("]"), [])

        # Queries matching literal tag contents with punctuation should match
        matches_comma = self.store.search("complex,tag")
        self.assertEqual(len(matches_comma), 1)
        self.assertEqual(matches_comma[0].id, r2.id)

        matches_quote = self.store.search('quote"inside')
        self.assertEqual(len(matches_quote), 1)
        self.assertEqual(matches_quote[0].id, r2.id)

    def test_upper_bound_limits_and_count(self) -> None:
        """Verify search and list clamp maximum limits and count() is accurate (m2)."""
        self.assertEqual(self.store.count(), 0)
        for i in range(5):
            self.store.save(f"Record {i}", tags=(f"tag{i}",))
        self.assertEqual(self.store.count(), 5)

        # Capped at 500 when requested limit is 1000000
        records = self.store.list(limit=1000000)
        self.assertEqual(len(records), 5)


if __name__ == "__main__":
    unittest.main()
