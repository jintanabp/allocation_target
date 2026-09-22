"""
กติกาบังคับคลังเดียว — ต้องโชว์ในตารางผลกระจาย (ขั้นตอนคำนวณ) ล่วงหน้าด้วย ไม่ใช่รู้ตัว
ตอน "ตรวจไฟล์ก่อนส่ง" อย่างเดียว (ผู้ใช้ขอ 22 ก.ย. 2026 หลังเจอว่าตารางกระจายไม่บอกอะไรเลย)

เทสฟังก์ชัน backend/services/optimize.py::_attach_wh_pin_forced ตรง ๆ — ฟังก์ชันนี้เติม
คอลัมน์ "wh_pin_forced" (รหัสคลังที่จะถูกบังคับ หรือ "" ถ้าไม่โดน) ให้ทุกแถวผลกระจาย โดย
จับคู่ (sku, areacode, divisioncode) เหมือน lakehouse.py::_apply_warehouse_pin_rules
ทุกประการ — areacode/divisioncode มาจาก data/tga_lines_<sup>_<year>_<month>.csv
(ไฟล์เดียวกับที่ /data/employees เขียนไว้ตอนโหลดทีมในงวดเดียวกัน)

คนละเรื่องกับ tests/test_warehouse_pin_apply.py ที่เทส _apply_warehouse_pin_rules (การ
บังคับใช้จริงตอนสร้างไฟล์ส่ง) — ที่นี่แค่โชว์ "ล่วงหน้า" ให้ซุปเห็นก่อนกดส่ง ไม่แตะ
ตัวเลขกระจาย/ไฟล์ result_*.csv/Excel เลย
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import optimize as opt  # noqa: E402
from backend.services import warehouse_pin_rules_store as store  # noqa: E402

logging.disable(logging.CRITICAL)

SUP = "SLWHPINFCST"
YEAR, MONTH = 2026, 10


def _grain_row(emp, sku, area="3", div="S", wh="G001"):
    return {
        "emp_id": emp, "sku": sku, "qty": 5, "salestype": "S1",
        "divisioncode": div, "areacode": area, "provincecode": "P1",
        "warehouse_code": wh,
    }


class WhPinForcedFlagTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data", exist_ok=True)
        self._old_env = os.environ.get("WAREHOUSE_PIN_RULES_PATH")
        os.environ["WAREHOUSE_PIN_RULES_PATH"] = os.path.join(self._tmp.name, "rules.json")

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("WAREHOUSE_PIN_RULES_PATH", None)
        else:
            os.environ["WAREHOUSE_PIN_RULES_PATH"] = self._old_env
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _write_grain(self, rows):
        pd.DataFrame(rows).to_csv(f"data/tga_lines_{SUP}_{YEAR}_{MONTH:02d}.csv", index=False)

    def _rule(self, skus, area="3", div="S", wh="G010", section="702"):
        store.write_rules([
            {"section": section, "skus": skus, "areacode": area, "divisioncode": div, "warehouse_code": wh}
        ])

    def test_no_rules_configured_is_a_cheap_no_op(self):
        self._write_grain([_grain_row("E1", "SKU1")])
        df = pd.DataFrame([{"emp_id": "E1", "sku": "SKU1", "allocated_boxes": 10}])
        out = opt._attach_wh_pin_forced(df, SUP, MONTH, YEAR)
        self.assertEqual(out["wh_pin_forced"].tolist(), [""])

    def test_matching_rows_get_forced_warehouse_others_stay_blank(self):
        self._rule(skus=["SKU1"], area="3", div="S", wh="G010")
        self._write_grain([
            _grain_row("E1", "SKU1", area="3", div="S"),   # ตรงกติกา
            _grain_row("E1", "SKU2", area="3", div="S"),   # SKU ไม่ตรงกติกา
            _grain_row("E2", "SKU1", area="5", div="S"),   # ภาคไม่ตรงกติกา
        ])
        df = pd.DataFrame([
            {"emp_id": "E1", "sku": "SKU1", "allocated_boxes": 10},
            {"emp_id": "E1", "sku": "SKU2", "allocated_boxes": 4},
            {"emp_id": "E2", "sku": "SKU1", "allocated_boxes": 6},
        ])
        out = opt._attach_wh_pin_forced(df, SUP, MONTH, YEAR)
        got = dict(zip(zip(out["emp_id"], out["sku"]), out["wh_pin_forced"]))
        self.assertEqual(got[("E1", "SKU1")], "G010")
        self.assertEqual(got[("E1", "SKU2")], "")
        self.assertEqual(got[("E2", "SKU1")], "")

    def test_wh_split_employee_both_warehouse_rows_flagged_the_same(self):
        """พนักงานคลังแตก (2 แถวต่อ SKU เดียวกัน คนละ warehouse_code) ต้องได้ wh_pin_forced
        เดียวกันทั้งคู่ — สอดคล้องกับความหมายจริงคือ "งวดนี้ SKU ตัวนี้จะไปรวมที่คลังเดียว"
        ไม่ว่าตารางบนจอจะยังโชว์แบ่งคลังตามประวัติแบบไหนก็ตาม"""
        self._rule(skus=["SKU1"], area="3", div="S", wh="G010")
        self._write_grain([_grain_row("E1", "SKU1", area="3", div="S")])
        df = pd.DataFrame([
            {"emp_id": "E1", "sku": "SKU1", "allocated_boxes": 6, "warehouse_code": "R082"},
            {"emp_id": "E1", "sku": "SKU1", "allocated_boxes": 4, "warehouse_code": "G002"},
        ])
        out = opt._attach_wh_pin_forced(df, SUP, MONTH, YEAR)
        self.assertEqual(out["wh_pin_forced"].tolist(), ["G010", "G010"])

    def test_missing_grain_file_is_graceful(self):
        df = pd.DataFrame([{"emp_id": "E1", "sku": "SKU1", "allocated_boxes": 10}])
        out = opt._attach_wh_pin_forced(df, SUP, MONTH, YEAR)
        self.assertEqual(out["wh_pin_forced"].tolist(), [""])

    def test_empty_df_is_returned_as_is(self):
        df = pd.DataFrame(columns=["emp_id", "sku", "allocated_boxes"])
        out = opt._attach_wh_pin_forced(df, SUP, MONTH, YEAR)
        self.assertTrue(out.empty)


if __name__ == "__main__":
    unittest.main()
