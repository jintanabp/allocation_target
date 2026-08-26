"""
คู่พนักงาน×สินค้าที่ยังไม่มีเป้าใน Target Sun ต้องถูก "ส่งไปสร้างแถวใหม่" ไม่ใช่ตัดทิ้ง

Target Sun รองรับ insert อยู่แล้ว (targetsun-importTargetSalesmanNextFromExcel.md)
แต่ระบบเราตัดแถวที่ไม่มี SALESTYPE/DIVISIONCODE/AREACODE ออกก่อนสร้างไฟล์ เพราะ dim
พวกนั้นลอกมาจาก "แถวเป้าที่มีอยู่แล้ว" ของคู่นั้น — สินค้าที่พนักงานไม่เคยมีเป้าจึงไม่มี
ให้ลอก · ผลคือหีบที่กระจายไปแล้วหายจากเป้าจริง แล้วผู้ใช้ต้องไปนั่งเพิ่มเองทีละแถว

เดิมเปิด flag ให้เฉพาะโหมด "กระจายรวมทั้งหน่วย" (S.unitWideOwnerSup) แต่เคสเดียวกัน
เกิดกับทีมเดียวด้วย — สินค้าใหม่ หรือสินค้าที่คนนั้นยังไม่เคยมีเป้าในงวดนั้น
"""

from __future__ import annotations

import logging
import os
import re
import sys
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.schemas import LakehouseUploadRequest  # noqa: E402
from backend.services import lakehouse as lh  # noqa: E402

logging.disable(logging.CRITICAL)


def _read(rel: str) -> str:
    with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
        return fh.read()


APP = _read("frontend/app.js")


class TestFrontendAlwaysAsksForNewRows(unittest.TestCase):
    def test_flag_is_not_tied_to_the_unit_wide_mode_anymore(self):
        m = re.search(r"allow_new_targetsun_rows:\s*([^,\n]+)", APP)
        self.assertIsNotNone(m, "ไม่พบการส่ง allow_new_targetsun_rows จากหน้าเว็บ")
        value = m.group(1).strip()
        self.assertEqual(
            value, "true",
            "ต้องเปิดทุกกรณี — ผูกกับ S.unitWideOwnerSup ทำให้ทีมเดียวยังโดนตัดแถวทิ้ง",
        )

    def test_the_default_in_the_schema_stays_closed(self):
        """ผู้เรียกอื่น (สคริปต์/เทส) ต้องไม่สร้างแถวใหม่โดยไม่ตั้งใจ"""
        req = LakehouseUploadRequest(
            sup_id="SL397", target_month=9, target_year=2026, upload_user_code="T",
            allocations=[{"emp_id": "E1", "sku": "A", "allocated_boxes": 1}],
        )
        self.assertFalse(req.allow_new_targetsun_rows)


class TestInferenceOnlyFromTheSameEmployee(unittest.TestCase):
    """
    ตัวกันความผิดพลาด: เดา dim ได้เฉพาะจากแถวของพนักงานคนเดียวกัน และเฉพาะเมื่อ
    ทุกแถวของคนนั้นตรงกันหมด — สร้างแถวผิดเขตใน Oracle แย่กว่าไม่สร้าง
    """

    def _grain(self, rows: list[dict]) -> pd.DataFrame:
        return pd.DataFrame(rows)

    def test_infers_when_every_row_of_that_employee_agrees(self):
        dg = self._grain([
            {"emp_id": "E1", "sku": "A", "salestype": "S", "divisioncode": "B",
             "areacode": "01", "provincecode": "P1"},
            {"emp_id": "E1", "sku": "B", "salestype": "S", "divisioncode": "B",
             "areacode": "01", "provincecode": "P1"},
        ])
        dims = lh.emp_dims_from_own_grain(dg)
        self.assertIn("E1", dims)
        self.assertEqual(dims["E1"]["provincecode"], "P1")

    def test_does_not_infer_when_the_employee_sells_in_two_areas(self):
        dg = self._grain([
            {"emp_id": "E1", "sku": "A", "salestype": "S", "divisioncode": "B",
             "areacode": "01", "provincecode": "P1"},
            {"emp_id": "E1", "sku": "B", "salestype": "S", "divisioncode": "B",
             "areacode": "02", "provincecode": "P2"},
        ])
        self.assertNotIn(
            "E1", lh.emp_dims_from_own_grain(dg),
            "ขัดกันเอง = ห้ามเดา ปล่อยให้ถูกตัดตามเดิมดีกว่าสร้างแถวผิดเขต",
        )

    def test_an_employee_with_no_rows_at_all_is_never_invented(self):
        dg = self._grain([
            {"emp_id": "E1", "sku": "A", "salestype": "S", "divisioncode": "B",
             "areacode": "01", "provincecode": "P1"},
        ])
        self.assertNotIn("E2", lh.emp_dims_from_own_grain(dg))

    def test_dims_never_come_from_another_employee(self):
        dg = self._grain([
            {"emp_id": "E1", "sku": "A", "salestype": "S", "divisioncode": "B",
             "areacode": "01", "provincecode": "P1"},
            {"emp_id": "E2", "sku": "A", "salestype": "C", "divisioncode": "S",
             "areacode": "09", "provincecode": "P9"},
        ])
        dims = lh.emp_dims_from_own_grain(dg)
        self.assertEqual(dims["E1"]["areacode"], "01")
        self.assertEqual(dims["E2"]["areacode"], "09")


class TestBlankColumnIsNotAConflict(unittest.TestCase):
    """
    คอลัมน์ที่ว่างทุกแถว = "ไม่มีค่า" ไม่ใช่ "ขัดกัน"

    เจอของจริง (งวด 09/2026): พนักงานทั้ง 65 คนมี PROVINCECODE ว่าง ตัวเดา dim จึง
    ทิ้งทุกคน = ฟีเจอร์นี้ตายสนิทมาตลอดโดยไม่มีใครรู้ · ผลคือ 409 คู่พนักงานxสินค้า
    ถูกตัดตอนส่งรวมภาค ทั้งที่ salestype/divisioncode/areacode ตรงกันหมดทุกแถว
    """

    def _dg(self, rows: list[dict]) -> pd.DataFrame:
        return pd.DataFrame(rows)

    def test_blank_province_does_not_throw_the_employee_away(self):
        dg = self._dg([
            {"emp_id": "S302", "sku": "A", "salestype": "S", "divisioncode": "S",
             "areacode": "3", "provincecode": ""},
            {"emp_id": "S302", "sku": "B", "salestype": "S", "divisioncode": "S",
             "areacode": "3", "provincecode": ""},
        ])
        dims = lh.emp_dims_from_own_grain(dg)
        self.assertIn("S302", dims, "province ว่างไม่ใช่เหตุให้ทิ้งทั้งคน")
        self.assertEqual(dims["S302"]["areacode"], "3")
        self.assertEqual(dims["S302"]["provincecode"], "")

    def test_a_real_disagreement_is_still_a_conflict(self):
        dg = self._dg([
            {"emp_id": "S302", "sku": "A", "salestype": "S", "divisioncode": "S",
             "areacode": "3", "provincecode": "P1"},
            {"emp_id": "S302", "sku": "B", "salestype": "S", "divisioncode": "S",
             "areacode": "3", "provincecode": "P2"},
        ])
        self.assertNotIn(
            "S302", lh.emp_dims_from_own_grain(dg),
            "ค่าต่างกันจริงยังต้องถือว่าขัดกัน ห้ามเดา",
        )

    def test_some_rows_blank_and_some_filled_is_not_a_conflict(self):
        """แถวเก่าไม่ได้กรอกจังหวัด แถวใหม่กรอก — ไม่ใช่ความขัดแย้ง"""
        dg = self._dg([
            {"emp_id": "S302", "sku": "A", "salestype": "S", "divisioncode": "S",
             "areacode": "3", "provincecode": ""},
            {"emp_id": "S302", "sku": "B", "salestype": "S", "divisioncode": "S",
             "areacode": "3", "provincecode": "P1"},
        ])
        dims = lh.emp_dims_from_own_grain(dg)
        self.assertEqual(dims["S302"]["provincecode"], "P1")

    def test_a_missing_key_column_still_disqualifies(self):
        """SALESTYPE/DIVISIONCODE/AREACODE เป็นคีย์ upsert — ขาดตัวใดก็ส่งไม่ได้อยู่ดี"""
        for missing in ("salestype", "divisioncode", "areacode"):
            row = {"emp_id": "S302", "sku": "A", "salestype": "S",
                   "divisioncode": "S", "areacode": "3", "provincecode": "P1"}
            row[missing] = ""
            with self.subTest(missing=missing):
                self.assertNotIn("S302", lh.emp_dims_from_own_grain(self._dg([row])))


if __name__ == "__main__":
    unittest.main()
