"""Tests for StopController halting mechanism."""
from __future__ import annotations

import os
import tempfile
import unittest

from aegis.errors import StopRequested
from aegis.stop import StopController


class TestStopController(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.stop_path = os.path.join(self.temp_dir.name, "STOP")
        self.controller = StopController(self.stop_path)

    def test_initial_state(self) -> None:
        self.assertFalse(self.controller.is_stopped())
        self.assertIsNone(self.controller.reason())
        # check() should be a no-op when not stopped
        self.controller.check()

    def test_request_stop_default_and_check(self) -> None:
        self.controller.request_stop()
        self.assertTrue(self.controller.is_stopped())
        self.assertEqual(self.controller.reason(), "user")
        with self.assertRaises(StopRequested):
            self.controller.check()

    def test_request_stop_custom_reason(self) -> None:
        self.controller.request_stop("emergency halt by operator")
        self.assertTrue(self.controller.is_stopped())
        self.assertEqual(self.controller.reason(), "emergency halt by operator")
        with self.assertRaises(StopRequested) as ctx:
            self.controller.check()
        self.assertIn("emergency halt by operator", str(ctx.exception))

    def test_clear_stop(self) -> None:
        self.controller.request_stop("temp stop")
        self.assertTrue(self.controller.is_stopped())

        self.controller.clear()
        self.assertFalse(self.controller.is_stopped())
        self.assertIsNone(self.controller.reason())
        self.controller.check()

        # Repeated clear should not error
        self.controller.clear()

    def test_request_stop_creates_parent_directory(self) -> None:
        nested_stop = os.path.join(self.temp_dir.name, "nested", "deep", "STOP")
        ctrl = StopController(nested_stop)
        ctrl.request_stop("nested stop")
        self.assertTrue(ctrl.is_stopped())
        self.assertEqual(ctrl.reason(), "nested stop")


if __name__ == "__main__":
    unittest.main()
