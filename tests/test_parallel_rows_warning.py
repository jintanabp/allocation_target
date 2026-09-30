"""
คำเตือน "แถวใหม่ซ้อนคู่เดิม" + ห้ามเดาคลังตอนส่ง (ผู้ใช้ขอ 30 ก.ย. 2026)

คีย์ upsert ของ Target Sun รวม WAREHOUSECODE — คู่พนักงาน×สินค้าที่มีแถวคลัง A อยู่แล้ว
ถ้าไฟล์ส่งคลัง B มา จะได้แถวใหม่ซ้อน เป้าเบิ้ล (SL453/SL380) ตัวนับ「ส่วนเกิน」มองไม่เห็น
เพราะแถวนี้อยู่ใน「คาดแถวใหม่」ด้วย

กติกาคลัง: ว่างมาว่างไป มีรหัสไหนมาส่งรหัสนั้น — ไม่มี grain ให้รู้คลังจริง = ไม่ส่ง

ทุกอย่าง mock / ออฟไลน์ ห้ามต่อ Target Sun จริง
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from fastapi import HTTPException  # noqa: E402

from backend.schemas import LakehouseUploadRequest  # noqa: E402
from backend.services import lakehouse as lh  # noqa: E402
from backend.services.send_alerts import row_count_alert  # noqa: E402

logging.disable(logging.CRITICAL)


def _key(sku, emp, wh):
    return lh._live_target_row_key({
        "PRODUCTCODE": sku, "SALESMANCODE": emp, "SALESTYPE": "0", "DIVISIONCODE": "B",
        "AREACODE": "1", "PROVINCECODE": "10", "WAREHOUSECODE": wh,
    })


class TestParallelRows(unittest.TestCase):
    def test_new_warehouse_for_existing_pair_is_flagged(self):
        old = _key("X", "E1", "")
        new = _key("X", "E1", "R001")
        out = lh.parallel_rows_of_existing_pairs({old}, {new}, {old: 7, new: 5})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["emp_id"], "E1")
        self.assertEqual(out[0]["sku"], "X")
        self.assertEqual(out[0]["old_warehouses"], [""])
        self.assertEqual(out[0]["new_warehouse"], "R001")
        self.assertEqual(out[0]["old_boxes"], 7)

    def test_old_row_also_in_file_is_not_flagged(self):
        """กติกาบังคับคลังส่ง 0 ทับคลังเก่าไปด้วย = ยอดคุมได้ ไม่ใช่เป้าเบิ้ล"""
        old = _key("X", "E1", "R001")
        new = _key("X", "E1", "R002")
        self.assertEqual(lh.parallel_rows_of_existing_pairs({old}, {old, new}), [])

    def test_brand_new_pair_is_not_flagged(self):
        other = _key("Y", "E1", "R001")
        new = _key("X", "E1", "")
        self.assertEqual(lh.parallel_rows_of_existing_pairs({other}, {other, new}), [])

    def test_same_key_is_overwrite_not_flagged(self):
        k = _key("X", "E1", "R001")
        self.assertEqual(lh.parallel_rows_of_existing_pairs({k}, {k}), [])

    def test_verify_marks_not_ok_even_when_counts_reconcile(self):
        old = _key("X", "E1", "")
        new = _key("X", "E1", "R001")
        before = {"row_count": 1, "keys": {old}}
        after = {"row_count": 2, "keys": {old, new}, "qty_by_key": {old: 7, new: 5}}
        with patch.object(lh, "_live_target_snapshot", return_value=after):
            res = lh.verify_row_count_after_send(
                "SLA", 8, 2026, emp_codes=["E1"], before_snapshot=before,
                file_keys={new}, file_qty_by_key={new: 5},
            )
        self.assertEqual(res["unexpected_extra_rows"], 0, "จำนวนแถวตรงที่คาดพอดี")
        self.assertEqual(res["parallel_rows_count"], 1)
        self.assertFalse(res["ok"], "แต่คู่นั้นมีเป้าสองก้อน ต้องไม่นับว่าปกติ")

    def test_alert_text_mentions_parallel_rows(self):
        a = row_count_alert({
            "checked": True, "ok": False, "before_count": 1, "after_count": 2,
            "expected_new_rows": 1, "unexpected_extra_rows": 0, "parallel_rows_count": 1,
            "parallel_rows_sample": [{"emp_id": "E1", "sku": "X", "old_warehouses": [""],
                                      "new_warehouse": "R001"}],
        })
        self.assertEqual(a["status"], "mismatch")
        self.assertIn("ซ้อนคู่เดิม", a["text"])
        self.assertIn("ว่าง", a["text"])
        self.assertIn("R001", a["text"])


SUP = "SLWH"


class TestNoWarehouseGuessOnSend(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data", exist_ok=True)
        pd.DataFrame([{"sku": "A", "supervisor_target_boxes": 10, "price_per_box": 1.0}]).to_csv(
            f"data/target_boxes_{SUP}_2026_08.csv", index=False
        )
        # กันเส้นทางเติม dim วิ่งไป Fabric (ห้ามมี network ในเทส)
        self._patch = patch.object(
            lh, "_enrich_emp_dimensions", side_effect=lambda df, rows_raw, **kw: df
        )
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _req(self):
        return LakehouseUploadRequest(
            sup_id=SUP, target_month=8, target_year=2026, upload_user_code="TESTER",
            allocations=[{"emp_id": "E1", "sku": "A", "allocated_boxes": 10,
                          "warehouse_code": "R999"}],
        )

    def test_send_without_grain_is_blocked(self):
        with self.assertRaises(HTTPException) as cm:
            lh._build_tga_upload_dataframe(self._req(), drop_incomplete_rows=True)
        self.assertEqual(cm.exception.status_code, 409)
        self.assertEqual(cm.exception.detail["code"], "grain_missing")

    def _write_grain(self, wh):
        pd.DataFrame([{
            "emp_id": "E1", "sku": "A", "qty": 5, "salestype": "S1", "divisioncode": "D1",
            "areacode": "10", "provincecode": "P1", "warehouse_code": wh,
        }]).to_csv(f"data/tga_lines_{SUP}_2026_08.csv", index=False)

    def test_blank_warehouse_in_targetsun_is_sent_blank(self):
        """ว่างมา = ว่างไป แม้คำขอจะมีคลังติดมา"""
        self._write_grain("")
        out, *_ = lh._build_tga_upload_dataframe(self._req(), drop_incomplete_rows=True)
        self.assertEqual(out["WAREHOUSECODE"].tolist(), [""])

    def test_existing_warehouse_code_is_sent_as_is(self):
        """มีรหัสไหนมา = ส่งรหัสนั้น ไม่ใช่คลังจากคำขอ"""
        self._write_grain("R001")
        out, *_ = lh._build_tga_upload_dataframe(self._req(), drop_incomplete_rows=True)
        self.assertEqual(out["WAREHOUSECODE"].tolist(), ["R001"])


if __name__ == "__main__":
    unittest.main()
