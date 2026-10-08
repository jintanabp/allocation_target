"""
ผลตรวจ 7 ต.ค. 2026 ก5 — สำเนาแถว Target Sun ก่อนส่ง + ไฟล์คืนค่า

- ชุดแรกของงวดเขียนครั้งเดียว ส่งซ้ำกี่รอบก็ไม่ทับ
- ทุกการส่งมีสำเนาของตัวเอง ไม่ทับกัน
- ไฟล์คืนค่า = แถวในสำเนาตามจำนวนเดิม + แถวที่เราเคยส่งแต่ไม่มีในสำเนา = 0
- ไฟล์คืนค่าเป็น Excel รูปแบบนำเข้า Target Sun (คอลัมน์เดียวกับไฟล์ส่ง) · ไม่ส่งอะไรออกไป
"""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from unittest import mock

import pandas as pd

from backend.services import sent_ledger
from backend.services import ts_row_snapshots as trs

SUP = "SLZZSNAP"


def _r(sku, emp, qty, wh=""):
    return {"PRODUCTCODE": sku, "SALESTYPE": "S", "DIVISIONCODE": "D", "SALESMANCODE": emp,
            "AREACODE": "10", "PROVINCECODE": "", "WAREHOUSECODE": wh, "QUANTITYCASE": qty}


class TestRowSnapshots(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data")

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def test_first_written_once_presend_every_time(self):
        r1 = trs.save_presend_snapshot(SUP, 11, 2026, [_r("A", "E1", 10)], import_url="U", token="tok1")
        r2 = trs.save_presend_snapshot(SUP, 11, 2026, [_r("A", "E1", 7)], import_url="U", token="tok2",
                                       ledger_had_sends=True)
        self.assertEqual(r1, {"first": True, "presend": True})
        self.assertEqual(r2, {"first": False, "presend": True})
        items = trs.list_snapshots(SUP, 11, 2026)
        self.assertEqual(items[0]["id"], "first")
        self.assertEqual(items[0]["total_boxes"], 10)       # ชุดแรกไม่ถูกทับด้วยค่าหลังส่ง
        self.assertFalse(items[0]["captured_after_send"])
        self.assertEqual(len(items), 3)
        self.assertEqual({i["total_boxes"] for i in items[1:]}, {10, 7})

    def test_none_rows_not_saved(self):
        self.assertEqual(trs.save_presend_snapshot(SUP, 11, 2026, None), {"first": False, "presend": False})
        self.assertEqual(trs.list_snapshots(SUP, 11, 2026), [])

    def test_restore_rows_include_zero_for_rows_we_created(self):
        # ไฟล์ส่งมี E1 และ E2 (E2 ยังไม่มีแถวใน TS) — สำเนาครอบทั้งคู่
        trs.save_presend_snapshot(SUP, 11, 2026, [_r("A", "E1", 10, "W1")], import_url="U", token="t",
                                  emp_codes=["E1", "E2"])
        # เราส่ง: A/E1/W1=6 และสร้างแถวใหม่ A/E2/(ว่าง)=4
        sent_ledger.record_send(SUP, 11, 2026, [_r("A", "E1", 6, "W1"), _r("A", "E2", 4)],
                                token="t", send_status="ok", import_url="U")
        rows, summary = trs.build_restore_rows(SUP, 11, 2026, "first")
        got = {(r["SALESMANCODE"], r["WAREHOUSECODE"]): r["QUANTITYCASE"] for r in rows}
        self.assertEqual(got, {("E1", "W1"): 10, ("E2", ""): 0})
        self.assertEqual(summary["zeroed_rows"], 1)
        self.assertEqual(summary["restore_boxes"], 10)

    def test_send_path_hook_flags_after_send(self):
        from backend.services import targetsun_import as ti

        snap = {"raw_rows": [_r("A", "E1", 5)]}
        sent_ledger.record_send(SUP, 11, 2026, [_r("A", "E1", 5)], token="old", send_status="ok", import_url="U")
        with mock.patch.object(ti, "_current_import_url", return_value="U"):
            ti._save_presend_rows(SUP, 11, 2026, snap, user="T", token="new")
            ti._save_presend_rows(SUP, 11, 2026, None, user="T", token="x")  # อ่านไม่ได้ = ไม่เก็บ
        items = trs.list_snapshots(SUP, 11, 2026)
        self.assertEqual(len(items), 2)  # first + presend 1 ชุด
        self.assertTrue(items[0]["captured_after_send"])

    def test_employee_outside_first_snapshot_not_zeroed(self):
        """
        สำเนาแรกครอบแค่ E1 · ส่งรอบหลังแตะ F (เป้าเดิม F=8) · ledger มีคีย์ของ F
        เดิม: ไฟล์คืนค่าส่ง F=0 ทับเป้าจริง → ต้องใช้ค่าจากสำเนารอบที่ครอบ F เป็นรอบแรกแทน
        คนที่ไม่มีสำเนาไหนครอบ (G) ต้องไม่อยู่ในไฟล์ แต่ถูกแจ้งใน summary
        """
        with mock.patch.object(trs, "_now_iso", return_value="2026-11-01T00:00:00Z"):
            trs.save_presend_snapshot(SUP, 11, 2026, [_r("A", "E1", 10)], import_url="U", token="t1",
                                      emp_codes=["E1"])
        with mock.patch.object(trs, "_now_iso", return_value="2026-11-02T00:00:00Z"), \
             mock.patch.object(trs.time, "strftime", return_value="20261102_000000"):
            trs.save_presend_snapshot(SUP, 11, 2026, [_r("A", "F", 8)], import_url="U", token="t2",
                                      emp_codes=["F"])
        sent_ledger.record_send(SUP, 11, 2026, [_r("A", "E1", 6), _r("A", "F", 12), _r("A", "G", 3)],
                                token="t", send_status="ok", import_url="U")
        rows, summary = trs.build_restore_rows(SUP, 11, 2026, "first")
        got = {r["SALESMANCODE"]: r["QUANTITYCASE"] for r in rows}
        self.assertEqual(got, {"E1": 10, "F": 8})
        self.assertEqual(summary["emps_from_later_snapshots"], ["F"])
        self.assertEqual(summary["uncovered_emps"], ["G"])

    def test_bad_snap_id_rejected(self):
        with self.assertRaises(FileNotFoundError):
            trs.build_restore_rows(SUP, 11, 2026, "../../config/user_access")

    def test_restore_file_endpoint_builds_import_format_xlsx(self):
        from backend.routers import admin as admin_router
        from backend.services.lakehouse import LAKEHOUSE_CSV_COLUMNS

        trs.save_presend_snapshot(SUP, 11, 2026, [_r("A", "E1", 10, "W1")], import_url="U", token="t")
        with mock.patch.object(admin_router, "ensure_sup_in_admin_scope"), \
             mock.patch.object(admin_router, "_audit_admin") as audit:
            resp = admin_router.admin_download_restore_file(
                admin={"email": "a@x"}, sup_id=SUP, target_month=11, target_year=2026, snap_id="first",
            )
        df = pd.read_excel(io.BytesIO(resp.body), sheet_name="TGA", dtype=str)
        self.assertEqual(list(df.columns), LAKEHOUSE_CSV_COLUMNS)
        self.assertEqual(df.loc[0, "QUANTITYCASE"], "10")
        self.assertEqual(df.loc[0, "SALESMANCODE"], "E1")
        audit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
