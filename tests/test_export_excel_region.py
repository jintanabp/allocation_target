"""
ปุ่มดาวน์โหลด Excel ต้องให้ตัวเลขชุดเดียวกับ Excel ที่ขั้นกระจายสร้าง

ขั้นกระจายแก้ไปแล้วว่าโหมดรวมภาคต้องใช้ "ไฟล์เป้ารวม" เป็นแหล่งของแถวเป้าหีบ
(optimize._excel_target_boxes_path) แต่ปุ่มดาวน์โหลดยังชี้ไฟล์ของทีมเดียวเสมอ
Excel สองไฟล์ของรอบเดียวกันจึงไม่ตรงกัน: ไฟล์จากปุ่มนี้เอาเป้าของทีมเดียวมาคู่กับ
หีบของทั้งภาค ดูเหมือนกระจายเกินเป้ามหาศาล · และการเติมราคา/ชื่อสินค้าก็อ่านจาก
ไฟล์ทีมเดียว SKU ที่ทีมนั้นไม่มีจึงได้ราคา 0 มูลค่ารวมในไฟล์เลยต่ำกว่าจริงเป็นก้อน
"""

import os
import shutil
import tempfile
import unittest

import pandas as pd

from backend.core.paths import (
    target_boxes_cache_path,
    target_boxes_union_cache_path,
)
from backend.services.exporting import _export_target_boxes_path

MONTH, YEAR = 9, 2026
SUP = "SL397"


class TestExportTargetBoxesSource(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        self._tmpdir = tempfile.mkdtemp(prefix="export_region_")
        os.makedirs(os.path.join(self._tmpdir, "data"), exist_ok=True)
        os.chdir(self._tmpdir)

    def tearDown(self):
        os.chdir(self._cwd)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _write(self, path: str, boxes: int) -> str:
        pd.DataFrame([{
            "sku": "734046", "price_per_box": 352.0,
            "supervisor_target_boxes": boxes,
        }]).to_csv(path, index=False)
        return path

    def test_falls_back_to_own_team_file_when_there_is_no_union(self):
        own = self._write(target_boxes_cache_path(SUP, MONTH, YEAR), 100)
        self.assertEqual(_export_target_boxes_path(SUP, MONTH, YEAR), own)

    def test_uses_the_union_file_after_a_region_wide_run(self):
        self._write(target_boxes_cache_path(SUP, MONTH, YEAR), 100)
        union = self._write(target_boxes_union_cache_path(SUP, MONTH, YEAR), 450)
        os.utime(union, (os.path.getmtime(union) + 60,) * 2)
        self.assertEqual(_export_target_boxes_path(SUP, MONTH, YEAR), union)

    def test_a_later_single_team_run_wins_again(self):
        """กระจายรวมภาคแล้วกลับมากระจายทีมเดียว ไฟล์รวมภาคเก่าต้องไม่ถูกหยิบมาใช้อีก"""
        union = self._write(target_boxes_union_cache_path(SUP, MONTH, YEAR), 450)
        own = self._write(target_boxes_cache_path(SUP, MONTH, YEAR), 100)
        os.utime(own, (os.path.getmtime(union) + 60,) * 2)
        self.assertEqual(_export_target_boxes_path(SUP, MONTH, YEAR), own)

    def test_without_a_period_it_is_the_old_behaviour(self):
        path = _export_target_boxes_path(SUP, None, None)
        self.assertTrue(path.endswith("target_boxes.csv"), path)


if __name__ == "__main__":
    unittest.main()
