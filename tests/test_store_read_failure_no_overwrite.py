"""
อ่านไฟล์พลาดครั้งเดียว ต้องไม่เขียนทับข้อมูลเดิมจนหาย (OPEN_ITEMS 7.2 / 7.3 — ผลตรวจ 5 ต.ค. 2026)

7.2 feedback_store: เดิมอ่านพลาด → ถือว่าว่าง → append แล้วเขียนทับ เหลือข้อความเดียว
ใช้โฟลเดอร์ชั่วคราว ไม่แตะ data/ จริง
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import feedback_store as fs  # noqa: E402


class TestFeedbackNotWipedOnReadError(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(os.environ, {"FEEDBACK_DIR": self._tmp.name})
        self._env.start()
        for i in range(3):
            fs.append_entry(email=f"u{i}@x.test", message=f"ข้อความ {i}")

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def test_transient_read_error_refuses_to_write(self):
        real_open = open
        calls = {"n": 0}

        def flaky(path, *a, **k):
            if str(path).endswith("feedback.json") and "r" in (a[0] if a else k.get("mode", "r")) and calls["n"] < 5:
                calls["n"] += 1
                raise PermissionError("locked by antivirus")
            return real_open(path, *a, **k)

        with patch("builtins.open", side_effect=flaky), patch.object(fs.time, "sleep"):
            with self.assertRaises(fs.FeedbackUnreadable):
                fs.append_entry(email="new@x.test", message="ใหม่")
        self.assertEqual(len(fs.read_doc()["items"]), 3, "ข้อความเดิมต้องอยู่ครบ")

    def test_corrupt_file_is_kept_aside(self):
        path = fs.feedback_json_path()
        with open(path, "w", encoding="utf-8") as f:
            f.write("{broken")
        fs.append_entry(email="new@x.test", message="ใหม่")
        self.assertTrue(any(n.startswith("feedback.json.corrupt-") for n in os.listdir(self._tmp.name)))
        self.assertEqual(len(fs.read_doc()["items"]), 1)


class TestSnapshotNotResetOnReadError(unittest.TestCase):
    """7.3: อ่าน snapshot พลาดครั้งเดียว ต้องไม่ทำให้ version กลับเป็น 1 และไม่ลบเวลาที่ส่ง Target Sun"""

    def setUp(self):
        from backend.services import allocation_store as st

        self.st = st
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(os.environ, {"ALLOCATIONS_DATA_DIR": self._tmp.name})
        self._env.start()
        body = {"sup_id": "SL1", "target_month": 11, "target_year": 2026, "status": "sent_targetsun",
                "allocations": [], "yellow": {}}
        for _ in range(4):
            st.write_snapshot(dict(body))

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def test_transient_read_error_refuses_to_write(self):
        st = self.st
        real_open = open

        def flaky(path, *a, **k):
            mode = a[0] if a else k.get("mode", "r")
            if str(path).endswith("SL1_2026_11.json") and "r" in mode:
                raise PermissionError("locked")
            return real_open(path, *a, **k)

        with patch("builtins.open", side_effect=flaky), patch.object(st.time, "sleep"):
            with self.assertRaises(st.SnapshotUnreadable):
                st.write_snapshot({"sup_id": "SL1", "target_month": 11, "target_year": 2026,
                                   "status": "draft", "allocations": [], "yellow": {}})
        snap = st.read_snapshot("SL1", 11, 2026)
        self.assertEqual(snap["version"], 4)
        self.assertTrue(snap.get("target_sun_sent_at"))

    def test_corrupt_snapshot_is_kept_aside(self):
        st = self.st
        path = st.allocation_snapshot_path("SL1", 11, 2026)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{broken")
        st.write_snapshot({"sup_id": "SL1", "target_month": 11, "target_year": 2026,
                           "status": "draft", "allocations": [], "yellow": {}})
        self.assertTrue(any(".corrupt-" in n for n in os.listdir(self._tmp.name)))


if __name__ == "__main__":
    unittest.main()
