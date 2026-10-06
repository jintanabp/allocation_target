"""
ทางส่งรวดเดียว (หน้าเว็บรุ่นเก่า) ต้องจด sent ledger เป็น "unknown" ตอน 504 (OPEN_ITEMS 7.10 — ผลตรวจ 5 ต.ค. 2026)

ทาง prepare จดอยู่แล้ว (ก5) · ทางรวดเดียว raise ก่อนถึง record_send
ทุกอย่างที่ออกเน็ตถูก mock — ไม่มีการส่ง Target Sun จริง
"""

from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

import pandas as pd
from fastapi import HTTPException

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import targetsun_import as ti  # noqa: E402


class TestOneShot504Ledger(unittest.TestCase):
    def _run(self, exc, record_side_effect=None):
        df = pd.DataFrame({"SALESMANCODE": ["E1"], "PRODUCTCODE": ["A"], "QUANTITYCASE": [5]})
        req = SimpleNamespace(sup_id="SLX", target_month=11, target_year=2026,
                              upload_user_code="u", allocations=[{}])
        with mock.patch.object(ti, "targetsun_import_excel_url", return_value="http://example.invalid/x"), \
             mock.patch.object(ti, "assert_target_snapshot_is_fresh"), \
             mock.patch.object(ti, "_build_send_file", return_value=(b"x", "f.xlsx", df, 0, [], [], False)), \
             mock.patch.object(ti, "_live_target_snapshot", return_value=None), \
             mock.patch.object(ti, "_post_targetsun_multipart", side_effect=exc), \
             mock.patch.object(ti, "_file_rows", return_value=[{"k": 1}]), \
             mock.patch.object(ti.sent_ledger, "record_send", side_effect=record_side_effect) as rec:
            with self.assertRaises(HTTPException) as ctx:
                ti._import_allocations_one_shot(req)
        return ctx.exception, rec

    def test_504_recorded_as_unknown(self):
        e, rec = self._run(HTTPException(504, detail="timeout"))
        self.assertEqual(e.status_code, 504)
        rec.assert_called_once()
        self.assertEqual(rec.call_args.kwargs["send_status"], "unknown")

    def test_ledger_failure_does_not_hide_504(self):
        e, _ = self._run(HTTPException(504, detail="timeout"), record_side_effect=OSError("locked"))
        self.assertEqual(e.status_code, 504)

    def test_other_errors_not_recorded(self):
        e, rec = self._run(HTTPException(400, detail="bad"))
        self.assertEqual(e.status_code, 400)
        rec.assert_not_called()


if __name__ == "__main__":
    unittest.main()
