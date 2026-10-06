"""
สินค้าราคา 0 ต้องกระจายตามประวัติ ไม่ไปตกคนที่ไม่เคยขาย (OPEN_ITEMS 7.5 — ผลตรวจ 5 ต.ค. 2026)

เดิมเทอม "อยู่ใกล้ประวัติ" ของ LP ถูกคูณด้วยราคา → ราคา 0 = LP ไม่สนที่ไป
จำลอง: เป้า Z=100 ประวัติ E1=60 E2=40 · เดิมได้ E3=13–20 ทั้งที่ E3 ไม่เคยขาย
"""

from __future__ import annotations

import logging
import os
import sys
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.OR_engine import allocate_boxes  # noqa: E402

logging.disable(logging.CRITICAL)


class TestZeroPriceSku(unittest.TestCase):
    def _run(self, tiered):
        emp = pd.DataFrame({"emp_id": ["E1", "E2", "E3", "E4"], "yellow_target": [10000, 8000, 6000, 6000]})
        sku = pd.DataFrame([
            {"sku": "Z", "supervisor_target_boxes": 100, "price_per_box": 0.0},
            {"sku": "P", "supervisor_target_boxes": 300, "price_per_box": 100.0},
        ])
        hist = pd.DataFrame(
            [{"emp_id": "E1", "sku": "Z", "hist_boxes": 60}, {"emp_id": "E2", "sku": "Z", "hist_boxes": 40}]
            + [{"emp_id": e, "sku": "P", "hist_boxes": h} for e, h in zip(["E1", "E2", "E3", "E4"], [100, 80, 60, 60])]
        )
        out = allocate_boxes(emp, sku, hist, strategy="L3M", tiered_allocation=tiered)
        return out[out.sku == "Z"].set_index("emp_id")["allocated_boxes"].to_dict(), out

    def test_follows_history(self):
        for tiered in (True, False):
            with self.subTest(tiered=tiered):
                z, out = self._run(tiered)
                self.assertEqual(sum(z.values()), 100)
                self.assertEqual(z.get("E3", 0) + z.get("E4", 0), 0, f"ไม่เคยขายต้องได้ 0: {z}")
                self.assertEqual(int(out[out.sku == "P"]["allocated_boxes"].sum()), 300)


if __name__ == "__main__":
    unittest.main()
