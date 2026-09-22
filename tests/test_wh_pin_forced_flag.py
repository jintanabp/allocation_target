"""
กติกาบังคับคลังเดียว — ต้องมีผลจริงตั้งแต่ตอนกดกระจาย (Step 3) ไม่ใช่รู้ตัวแค่ตอน "ตรวจ
ไฟล์ก่อนส่ง" อย่างเดียว (ผู้ใช้ขอ 22 ก.ย. 2026 สามรอบในวันเดียว — รอบแรกขอแค่ป้ายเตือน
ล่วงหน้า รอบสองขอให้บังคับจริงตั้งแต่ตอนกระจายเลย รอบสามขยายให้ครอบคนคลังเดียวด้วย
หลังเจอเคสจริง SL225/S543/SKU 411140 ที่คลังเดียว (ไม่เคยแตก) ไม่ถูกแตะเพราะรอบสองจำกัด
ไว้แค่คนคลังแตกเท่านั้น)

เทสฟังก์ชัน backend/services/optimize.py::_apply_wh_pin_preview ตรง ๆ — ฟังก์ชันนี้:
1. รวมคลังจริงให้ทุกแถวที่ (sku, areacode, divisioncode) ตรงกติกา ไม่ว่าจะเคยคลังแตก
   หรือไม่ก็ตาม — คลังแตก (≥2 แถว) เรียก lakehouse.py::_apply_warehouse_pin_rules ตัวเดียว
   กับตอนส่งจริงเป๊ะ ๆ (รวมเป็นแถวใหม่+ล้างแถวเก่าเป็น 0) ส่วนคลังเดียว (1 แถว) แก้
   warehouse_code ในแถวเดิมตรง ๆ (ไม่ต้องสร้างแถวใหม่ ไม่มีปัญหา upsert key แบบตอนส่งจริง)
   ผลคือ Step 3 เห็น "ตรงกับสิ่งที่จะเกิดตอนส่งจริง" เสมอ
2. เติมคอลัมน์ "wh_pin_forced" (รหัสคลังที่ถูกบังคับ หรือ "" ถ้าไม่โดน) จากผลที่รวมแล้ว
   ให้หัว SKU ในตาราง Step 3 โชว์ป้าย 🔒 ได้

จับคู่ (sku, areacode, divisioncode) เหมือน lakehouse.py::_apply_warehouse_pin_rules
ทุกประการ — areacode/divisioncode มาจาก data/tga_lines_<sup>_<year>_<month>.csv (ไฟล์
เดียวกับที่ /data/employees เขียนไว้ตอนโหลดทีมในงวดเดียวกัน) ไม่ต้องต่อ Fabric เพิ่ม

คนละเรื่องกับ tests/test_warehouse_pin_apply.py ที่เทส _apply_warehouse_pin_rules ตรง ๆ
(เทสนั้นครอบคลุมตรรกะการรวมคลังเองอยู่แล้ว — ที่นี่เน้นว่า optimize.py เรียกมันถูกจังหวะ/
ถูกคอลัมน์ และไม่กระทบไฟล์ result_*.csv/Excel)
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


class WhPinPreviewTest(unittest.TestCase):
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
        df = pd.DataFrame([{"emp_id": "E1", "sku": "SKU1", "allocated_boxes": 10, "warehouse_code": "G001"}])
        out = opt._apply_wh_pin_preview(df, SUP, MONTH, YEAR)
        self.assertEqual(out["wh_pin_forced"].tolist(), [""])
        self.assertEqual(out["allocated_boxes"].tolist(), [10])
        self.assertEqual(out["warehouse_code"].tolist(), ["G001"])

    def test_matching_rows_get_forced_warehouse_others_stay_blank(self):
        self._rule(skus=["SKU1"], area="3", div="S", wh="G010")
        self._write_grain([
            _grain_row("E1", "SKU1", area="3", div="S"),   # ตรงกติกา
            _grain_row("E1", "SKU2", area="3", div="S"),   # SKU ไม่ตรงกติกา
            _grain_row("E2", "SKU1", area="5", div="S"),   # ภาคไม่ตรงกติกา
        ])
        df = pd.DataFrame([
            {"emp_id": "E1", "sku": "SKU1", "allocated_boxes": 10, "warehouse_code": "G010"},
            {"emp_id": "E1", "sku": "SKU2", "allocated_boxes": 4, "warehouse_code": "R082"},
            {"emp_id": "E2", "sku": "SKU1", "allocated_boxes": 6, "warehouse_code": "R303"},
        ])
        out = opt._apply_wh_pin_preview(df, SUP, MONTH, YEAR)
        got = dict(zip(zip(out["emp_id"], out["sku"]), out["wh_pin_forced"]))
        self.assertEqual(got[("E1", "SKU1")], "G010")
        self.assertEqual(got[("E1", "SKU2")], "")
        self.assertEqual(got[("E2", "SKU1")], "")
        # E2/SKU1 ไม่ตรงกติกา (ภาคไม่ตรง) — คลัง/หีบต้องไม่ถูกแตะ
        e2 = out[(out["emp_id"] == "E2") & (out["sku"] == "SKU1")].iloc[0]
        self.assertEqual(e2["warehouse_code"], "R303")
        self.assertEqual(int(e2["allocated_boxes"]), 6)

    def test_split_warehouse_employee_gets_actually_consolidated_not_just_labeled(self):
        """ผู้ใช้ขอ 22 ก.ย. 2026 รอบสอง: "พอกดกระจาย มันก็จะไม่เอาหีบไปลงที่สินค้าในคลัง
        นั้นแล้ว" — พนักงานคลังแตก (2 แถวคนละคลัง ไม่ใช่คลังปักหมุดเลยสักแถว) ต้องเห็น
        หีบรวมเป็นแถวเดียวที่คลังปักหมุดจริง ๆ ในผลกระจาย ไม่ใช่แค่ติดป้ายเตือนเฉย ๆ"""
        self._rule(skus=["SKU1"], area="3", div="S", wh="G010")
        self._write_grain([_grain_row("E1", "SKU1", area="3", div="S")])
        df = pd.DataFrame([
            {"emp_id": "E1", "sku": "SKU1", "allocated_boxes": 6, "warehouse_code": "R082", "hist_avg": 4.0},
            {"emp_id": "E1", "sku": "SKU1", "allocated_boxes": 4, "warehouse_code": "G002", "hist_avg": 2.0},
        ])
        out = opt._apply_wh_pin_preview(df, SUP, MONTH, YEAR)
        self.assertEqual(int(out["allocated_boxes"].sum()), 10)  # ผลรวมยังตรงเป้าเดิม (I1)
        old_wh_rows = out[out["warehouse_code"].isin(["R082", "G002"])]
        self.assertTrue((old_wh_rows["allocated_boxes"] == 0).all())
        pinned = out[out["warehouse_code"] == "G010"]
        self.assertEqual(len(pinned), 1)
        self.assertEqual(int(pinned.iloc[0]["allocated_boxes"]), 10)
        self.assertEqual(pinned.iloc[0]["wh_pin_forced"], "G010")
        # คอลัมน์ประวัติเทียบเคียง (hist_avg) ต้องรวมข้ามแถวไปด้วย ไม่ใช่ก็อปแค่แถวแรก
        self.assertAlmostEqual(float(pinned.iloc[0]["hist_avg"]), 6.0)

    def test_single_warehouse_employee_gets_switched_in_place_too(self):
        """พนักงานคลังเดียว (ไม่เคยแตก) แต่คลังนั้นไม่ใช่คลังปักหมุด — ต้องถูกสลับคลังให้ตรง
        กติกาด้วยเหมือนกัน ไม่ใช่แค่คนคลังแตก (เจอเคสจริง SL225/S543/411140 22 ก.ย. 2026:
        S543 มีคลังเดียวคือ G080 ไม่เคยแตก แต่กติกาไม่ทำงานเพราะรอบแรกจำกัดไว้แค่คลังแตก)
        แก้ในแถวเดิมตรง ๆ ไม่สร้างแถวใหม่ (ไม่มีปัญหา upsert key แบบตอนส่งจริง)"""
        self._rule(skus=["SKU1"], area="3", div="S", wh="G010")
        self._write_grain([_grain_row("E1", "SKU1", area="3", div="S")])
        df = pd.DataFrame([
            {"emp_id": "E1", "sku": "SKU1", "allocated_boxes": 8, "warehouse_code": "R082"},
        ])
        out = opt._apply_wh_pin_preview(df, SUP, MONTH, YEAR)
        self.assertEqual(len(out), 1)
        self.assertEqual(out.iloc[0]["warehouse_code"], "G010")
        self.assertEqual(int(out.iloc[0]["allocated_boxes"]), 8)
        self.assertEqual(out.iloc[0]["wh_pin_forced"], "G010")

    def test_single_warehouse_employee_already_on_pinned_warehouse_is_a_no_op(self):
        self._rule(skus=["SKU1"], area="3", div="S", wh="G010")
        self._write_grain([_grain_row("E1", "SKU1", area="3", div="S")])
        df = pd.DataFrame([
            {"emp_id": "E1", "sku": "SKU1", "allocated_boxes": 8, "warehouse_code": "G010"},
        ])
        out = opt._apply_wh_pin_preview(df, SUP, MONTH, YEAR)
        self.assertEqual(len(out), 1)
        self.assertEqual(out.iloc[0]["warehouse_code"], "G010")
        self.assertEqual(int(out.iloc[0]["allocated_boxes"]), 8)

    def test_missing_grain_file_is_graceful(self):
        df = pd.DataFrame([{"emp_id": "E1", "sku": "SKU1", "allocated_boxes": 10, "warehouse_code": "G001"}])
        out = opt._apply_wh_pin_preview(df, SUP, MONTH, YEAR)
        self.assertEqual(out["wh_pin_forced"].tolist(), [""])
        self.assertEqual(int(out["allocated_boxes"].sum()), 10)

    def test_empty_df_is_returned_as_is(self):
        df = pd.DataFrame(columns=["emp_id", "sku", "allocated_boxes"])
        out = opt._apply_wh_pin_preview(df, SUP, MONTH, YEAR)
        self.assertTrue(out.empty)


if __name__ == "__main__":
    unittest.main()
