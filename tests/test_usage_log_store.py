"""Tests for usage log store."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import usage_log_store as logs  # noqa: E402


class TestUsageLogStore(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._orig = logs.logs_dir
        logs.logs_dir = lambda: self._tmpdir  # type: ignore[method-assign]

    def tearDown(self):
        logs.logs_dir = self._orig  # type: ignore[method-assign]

    def test_append_and_read(self):
        row = logs.append_log(
            level="error",
            email="u@example.com",
            action="test",
            message="hello",
            detail="stack",
        )
        self.assertEqual(row["level"], "error")
        self.assertTrue(row.get("entry_id"))
        items = logs.read_logs(level="error", limit=10, scan_all=True)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["message"], "hello")

    def test_acknowledge_removes_entry(self):
        row = logs.append_log(level="error", email="a@b.c", action="x", message="m1")
        eid = row["entry_id"]
        self.assertEqual(len(logs.read_logs(scan_all=True)), 1)
        n = logs.acknowledge_logs([eid])
        self.assertEqual(n, 1)
        self.assertEqual(logs.read_logs(scan_all=True), [])


if __name__ == "__main__":
    unittest.main()
