"""
ผลตรวจ 7 ต.ค. 2026 ก6 — ปรับราคาขัดกัน (โหลดรวมภาค) ต้องไม่ให้เงินกับคลังที่ไม่มีแถวใน SKU นั้น

C442 มี R408 (SKU X 10 หีบ) และ R493 (ไม่นำไปกระจายเป้า เพราะเป้าเงิน 0)
ราคา X 100 → 110 · เดิม R408=1100 แต่ R493=100 (ได้ส่วนต่างทั้งคนซ้ำ) แล้วกลายเป็นคลังที่กระจายได้
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

import pandas as pd

from backend.services import employees as em


def _run(grain_rows, employees, fixes):
    payload = {
        "_source_sup_id": "SL509",
        "skus": [{"sku": "X", "price_per_box": 100.0}, {"sku": "Y", "price_per_box": 50.0}],
        "employees": employees,
    }
    with tempfile.TemporaryDirectory() as tmp, \
         mock.patch.object(em, "_tga_qty_by_emp_sku", return_value=pd.DataFrame(grain_rows)), \
         mock.patch.object(em, "target_boxes_cache_path", return_value=os.path.join(tmp, "a.csv")), \
         mock.patch.object(em, "target_sun_cache_path", return_value=os.path.join(tmp, "b.csv")), \
         mock.patch.object(em, "atomic_write_csv", lambda *a, **k: None):
        em._apply_price_fix_to_payload(payload, fixes, 10, 2026)
    return {(e["emp_id"], e.get("warehouse_code")): e["target_sun"] for e in payload["employees"]}


class TestPriceFixPerWarehouse(unittest.TestCase):
    def test_warehouse_without_rows_gets_nothing(self):
        out = _run(
            [dict(emp_id="C442", sku="X", qty=10.0, warehouse_code="R408"),
             dict(emp_id="C442", sku="Y", qty=0.0, warehouse_code="R493")],
            [{"emp_id": "C442", "warehouse_code": "R408", "wh_split": True, "target_sun": 1000.0},
             {"emp_id": "C442", "warehouse_code": "R493", "wh_split": True, "target_sun": 0.0}],
            {"X": (100.0, 110.0, False)},
        )
        self.assertEqual(out[("C442", "R408")], 1100.0)
        self.assertEqual(out[("C442", "R493")], 0.0)

    def test_blank_warehouse_row_not_double_counted(self):
        # คลัง W1 10 หีบ + คลังว่าง 10 หีบ ราคา 100→110 → ส่วนต่างรวม 200 ไม่ใช่ 400
        out = _run(
            [dict(emp_id="C1", sku="X", qty=10.0, warehouse_code="W1"),
             dict(emp_id="C1", sku="X", qty=10.0, warehouse_code="")],
            [{"emp_id": "C1", "warehouse_code": "W1", "wh_split": True, "target_sun": 1000.0},
             {"emp_id": "C1", "warehouse_code": "", "wh_split": True, "target_sun": 1000.0}],
            {"X": (100.0, 110.0, False)},
        )
        self.assertEqual(out[("C1", "W1")], 1100.0)
        self.assertEqual(out[("C1", "")], 1100.0)

    def test_delta_of_unlisted_warehouse_not_lost(self):
        # แถวเป้าดิบอยู่คลัง R999 ที่ไม่มีบนจอ — ส่วนต่างต้องไม่หาย (ลงแถวที่เงินมากสุด)
        out = _run(
            [dict(emp_id="C442", sku="X", qty=10.0, warehouse_code="R999")],
            [{"emp_id": "C442", "warehouse_code": "R408", "wh_split": True, "target_sun": 500.0},
             {"emp_id": "C442", "warehouse_code": "R493", "wh_split": True, "target_sun": 0.0}],
            {"X": (100.0, 110.0, False)},
        )
        self.assertEqual(out[("C442", "R408")] + out[("C442", "R493")], 600.0)
        self.assertEqual(out[("C442", "R493")], 0.0)


if __name__ == "__main__":
    unittest.main()
