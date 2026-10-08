"""
แถวคน×คลัง รับหีบได้เฉพาะสินค้าที่ Target Sun มีแถวที่คลังนั้น (ผู้ใช้ตัดสิน 8 ต.ค. 2026)

ของจริง SL509 09/2026: C442 สินค้า X มีแถวแค่ R493 เป้า 0 (R493 ไม่นำไปกระจาย) — เดิมแถว R408 ได้หีบ X ได้
แล้วตอนส่งหีบไปลง R493 = ให้เป้าคลังที่ต้นทางไม่ได้ให้ · ตอนนี้ R408 ไม่ได้ X หีบไปคนอื่นในทีม
"""

from __future__ import annotations

import os
import tempfile
import unittest

import pandas as pd

from backend.OR_engine import allocate_boxes
from backend.services.optimize import _wh_blocked_pairs


class TestWhBlockedPairsHelper(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data")
        pd.DataFrame([
            {"emp_id": "C442", "sku": "A", "qty": 10, "warehouse_code": "R408"},
            {"emp_id": "C442", "sku": "X", "qty": 0, "warehouse_code": "R493"},
            {"emp_id": "E2", "sku": "X", "qty": 5, "warehouse_code": "W9"},
        ]).to_csv("data/tga_lines_SLZZWB_2026_09.csv", index=False)
        self.df_sku = pd.DataFrame([{"sku": "A"}, {"sku": "X"}, {"sku": "NEW"}])

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def test_blocks_only_sku_without_row_at_this_warehouse(self):
        rmap = {"C442|R408": ("C442", "R408"), "E2": ("E2", "")}
        self.assertEqual(_wh_blocked_pairs(rmap, 9, 2026, self.df_sku), frozenset({("C442|R408", "X")}))

    def test_sku_at_blank_warehouse_not_blocked(self):
        # คนแยกคลังที่สินค้า B มีเป้าอยู่ที่คลังว่าง — ไม่มีแถวบนจอของคลังว่าง จึงต้องไม่ตัด
        pd.DataFrame([
            {"emp_id": "C442", "sku": "A", "qty": 10, "warehouse_code": "R408"},
            {"emp_id": "C442", "sku": "B", "qty": 4, "warehouse_code": ""},
            {"emp_id": "E2", "sku": "B", "qty": 1, "warehouse_code": "W9"},
        ]).to_csv("data/tga_lines_SLZZWB_2026_09.csv", index=False)
        rmap = {"C442|R408": ("C442", "R408"), "E2": ("E2", "")}
        df_sku = pd.DataFrame([{"sku": "A"}, {"sku": "B"}])
        self.assertEqual(_wh_blocked_pairs(rmap, 9, 2026, df_sku), frozenset())

    def test_no_split_rows_no_block(self):
        self.assertEqual(_wh_blocked_pairs({"E2": ("E2", "")}, 9, 2026, self.df_sku), frozenset())

    def test_sku_blocked_for_every_row_is_released(self):
        # รอบมีแค่ C442/R408 — ถ้าห้าม X ทั้งรอบ หีบ X ไม่มีที่ลง → ไม่ห้ามสำหรับ X
        rmap = {"C442|R408": ("C442", "R408")}
        self.assertEqual(_wh_blocked_pairs(rmap, 9, 2026, self.df_sku), frozenset())


class TestEngineRespectsBlockedPairs(unittest.TestCase):
    def test_blocked_row_gets_zero_and_total_kept(self):
        emps = pd.DataFrame([{"emp_id": e, "yellow_target": 1000.0} for e in ("C442|R408", "E2", "E3")])
        df_sku = pd.DataFrame([{"sku": "X", "supervisor_target_boxes": 6, "price_per_box": 100.0}])
        hist = pd.DataFrame([{"emp_id": "C442|R408", "sku": "X", "hist_boxes": 50.0},
                             {"emp_id": "E2", "sku": "X", "hist_boxes": 1.0},
                             {"emp_id": "E3", "sku": "X", "hist_boxes": 1.0}])
        for strategy in ("L3M", "EVEN", "LP"):
            with self.subTest(strategy=strategy):
                df = allocate_boxes(emps, df_sku, hist, strategy=strategy,
                                    wh_blocked_pairs={("C442|R408", "X")})
                got = dict(zip(df.emp_id, df.allocated_boxes))
                self.assertEqual(got.get("C442|R408", 0), 0)
                self.assertEqual(sum(got.values()), 6)
                self.assertEqual(df.attrs["never_sold_zero_pairs"], set())  # ไม่ปนกับป้าย "ไม่เคยขาย"


if __name__ == "__main__":
    unittest.main()
