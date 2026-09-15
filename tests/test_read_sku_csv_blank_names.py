"""
_read_sku_csv (core/targets.py) เคยเติมช่องว่างทั้งตารางด้วย .fillna(0) เหมารวม —
ทำให้สินค้าที่ไม่มีชื่อภาษาอังกฤษ/ไทย (brand_name_english/product_name_english ว่าง
ในไฟล์ target_boxes_*.csv) กลายเป็นเลข 0.0 แทนที่จะเป็น "" แล้วโผล่ไปในผลกระจายหีบ
(/optimize) และ snapshot ที่บันทึกไว้จริง — พบจากการทดสอบผ่านเซิร์ฟเวอร์จริง

คอลัมน์ตัวเลข (เช่น price_per_box, supervisor_target_boxes) ต้องยังเติม 0 เหมือนเดิม —
เทสนี้กันทั้งสองทาง ไม่ใช่แค่แก้ไปทางเดียวจนพังอีกทาง
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.core.targets import _read_sku_csv, load_target_csv_for  # noqa: E402

SUP = "SLBLANKNAME"


class TestReadSkuCsvBlankNameColumns(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data", exist_ok=True)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _write_csv(self, path: str, text: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_blank_name_columns_become_empty_string_not_zero(self):
        self._write_csv(
            "data/target_boxes_test_2026_09.csv",
            "sku,price_per_box,supervisor_target_boxes,brand_name_thai,"
            "brand_name_english,product_name_thai,product_name_english\n"
            "900101,96.0,420,สาธิต A,,น้ำดื่มสาธิต,\n"
            "900102,84.0,260,สาธิต A,,น้ำดื่มสาธิต 2,\n",
        )
        df = _read_sku_csv("data/target_boxes_test_2026_09.csv")
        self.assertEqual(df["brand_name_english"].tolist(), ["", ""])
        self.assertEqual(df["product_name_english"].tolist(), ["", ""])
        self.assertTrue((df["brand_name_english"] != 0).all())
        self.assertIsInstance(df["brand_name_english"].iloc[0], str)

    def test_partially_blank_name_column_also_becomes_empty_string(self):
        """คอลัมน์ที่มีทั้งชื่อจริงและช่องว่างปนกัน (ไม่ใช่ว่างทั้งคอลัมน์) ก็ต้องไม่กลายเป็น 0"""
        self._write_csv(
            "data/target_boxes_test2_2026_09.csv",
            "sku,price_per_box,supervisor_target_boxes,brand_name_thai\n"
            "A,10.0,5,สาธิต A\n"
            "B,10.0,5,\n",
        )
        df = _read_sku_csv("data/target_boxes_test2_2026_09.csv")
        self.assertEqual(df.loc[df["sku"] == "B", "brand_name_thai"].iloc[0], "")

    def test_numeric_columns_still_fill_with_zero(self):
        self._write_csv(
            "data/target_boxes_test3_2026_09.csv",
            "sku,price_per_box,supervisor_target_boxes\nA,,5\nB,10.0,\n",
        )
        df = _read_sku_csv("data/target_boxes_test3_2026_09.csv")
        self.assertEqual(df.loc[df["sku"] == "A", "price_per_box"].iloc[0], 0)
        self.assertEqual(df.loc[df["sku"] == "B", "supervisor_target_boxes"].iloc[0], 0)

    def test_load_target_csv_for_carries_the_fix_through(self):
        self._write_csv(
            f"data/target_boxes_{SUP}_2026_09.csv",
            "sku,price_per_box,supervisor_target_boxes,brand_name_english\nA,10.0,5,\n",
        )
        df_sku, _df_sun = load_target_csv_for(SUP, 9, 2026)
        self.assertEqual(df_sku["brand_name_english"].iloc[0], "")


if __name__ == "__main__":
    unittest.main()
