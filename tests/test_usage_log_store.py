"""Tests for usage log store."""

from __future__ import annotations

import os
import sys
import shutil
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
        shutil.rmtree(self._tmpdir, ignore_errors=True)

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

    def test_logs_are_append_only(self):
        """
        log เป็นบันทึกการใช้งานถาวร ไว้ตรวจย้อนหลังว่าใครส่ง Target Sun เมื่อไหร่
        เดิมมี acknowledge_logs ให้แอดมินกดลบทีละรายการ — ถอดออกแล้ว ห้ามใส่กลับ
        """
        self.assertFalse(
            hasattr(logs, "acknowledge_logs"),
            "acknowledge_logs ถูกถอดออกแล้ว — การลบ log ทำให้ตาม monitor ย้อนหลังไม่ได้",
        )
        logs.append_log(level="info", email="a@b.c", action="send_targetsun", message="m1")
        logs.append_log(level="error", email="a@b.c", action="x", message="m2")
        self.assertEqual(len(logs.read_logs(scan_all=True)), 2)

    def test_send_targetsun_is_readable_as_info(self):
        logs.append_log(
            level="info",
            email="sup@x.com",
            sup_id="SL330",
            action="send_targetsun",
            message="ส่งเข้า Target Sun สำเร็จ",
            detail="งวด 2026-07 · ส่ง 100 แถว",
        )
        items = logs.read_logs(level="info", limit=10, scan_all=True)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["action"], "send_targetsun")
        self.assertEqual(items[0]["sup_id"], "SL330")


if __name__ == "__main__":
    unittest.main()
