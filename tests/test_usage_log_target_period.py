"""
กรอง log ตาม "งวดของเป้า" ไม่ใช่ "วันที่เขียน log" (ผลตรวจ 5 ต.ค. 2026 ข้อ 6)

เดิม: ส่งงวด 11/2026 ในวันที่ 5 ต.ค. → ไฟล์ log ชื่อ usage_2026-10-05 → หน้าประวัติการส่ง
งวด 11 ขึ้น「ยังไม่เคยส่ง」ส่วนหน้างวด 10 เห็นการส่งของงวด 11 · ใช้โฟลเดอร์ log ชั่วคราว
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import usage_log_store as uls  # noqa: E402


class TestLogTargetPeriod(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("USAGE_LOGS_DIR")
        os.environ["USAGE_LOGS_DIR"] = self._tmp.name

    def tearDown(self):
        if self._old is None:
            os.environ.pop("USAGE_LOGS_DIR", None)
        else:
            os.environ["USAGE_LOGS_DIR"] = self._old
        self._tmp.cleanup()

    def _write(self, day: str, rows: list[dict]) -> None:
        with open(os.path.join(self._tmp.name, f"usage_{day}.jsonl"), "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def test_send_for_next_period_shows_under_that_period(self):
        self._write("2026-10-05", [
            {"ts": "2026-10-05T03:00:00Z", "action": "send_targetsun", "sup_id": "SL1",
             "target_month": 11, "target_year": 2026, "message": "ส่งงวด 11"},
            {"ts": "2026-10-05T04:00:00Z", "action": "send_targetsun", "sup_id": "SL1",
             "target_month": 10, "target_year": 2026, "message": "ส่งงวด 10"},
        ])
        nov = uls.read_logs(target_year=2026, target_month=11, action="send_targetsun", sup_id="SL1")
        oct_ = uls.read_logs(target_year=2026, target_month=10, action="send_targetsun", sup_id="SL1")
        self.assertEqual([r["message"] for r in nov], ["ส่งงวด 11"])
        self.assertEqual([r["message"] for r in oct_], ["ส่งงวด 10"])

    def test_rows_without_period_fall_back_to_file_month(self):
        self._write("2026-10-02", [{"ts": "2026-10-02T01:00:00Z", "action": "login", "sup_id": "SL1"}])
        self.assertEqual(len(uls.read_logs(target_year=2026, target_month=10)), 1)
        self.assertEqual(len(uls.read_logs(target_year=2026, target_month=11)), 0)

    def test_send_written_in_previous_year_is_found(self):
        self._write("2025-12-20", [{"ts": "2025-12-20T01:00:00Z", "action": "send_targetsun",
                                    "sup_id": "SL1", "target_month": 1, "target_year": 2026}])
        self.assertEqual(len(uls.read_logs(target_year=2026, target_month=1, action="send_targetsun")), 1)


if __name__ == "__main__":
    unittest.main()
