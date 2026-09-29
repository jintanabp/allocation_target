"""
ป้ายเทียบประวัติ (ok/near/far) ต้องใช้ได้แม้มี SKU ที่แบ่งเท่า (ผลตรวจ 28 ก.ย. 2026 §4.1-1)

เดิม allocate_boxes เอาผลสุดท้ายไปทับ base_map เมื่อมี SKU แบ่งเท่า + กลยุทธ์ LP
ป้ายจึงเทียบผลกับตัวเอง — ทุกช่องได้ "ok" (งวด 09/2026 เกิดครบทั้ง 55 ทีม)
แก้แล้วต้องเปลี่ยนแค่ป้าย ไม่เปลี่ยนจำนวนหีบ
"""

from __future__ import annotations

import inspect
import os
import sys
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend import OR_engine as eng  # noqa: E402


class TestLabelsCompareAgainstHistory(unittest.TestCase):
    def test_final_result_never_replaces_the_baseline(self):
        src = inspect.getsource(eng.allocate_boxes)
        self.assertNotIn("base_map = _baseline_map_from_df(df_out,", src)

    def test_label_uses_the_history_baseline_not_the_result(self):
        """เซลล์ที่ผลห่างจากฐานประวัติมาก ต้องได้ far ไม่ใช่ ok"""
        df = pd.DataFrame([{"emp_id": "E1", "sku": "A", "allocated_boxes": 20},
                           {"emp_id": "E2", "sku": "A", "allocated_boxes": 10}])
        out = eng._annotate_hist_deviation(df, {("E1", "A"): 10, ("E2", "A"): 10})
        self.assertEqual(list(out["hist_dev_status"]), ["far", "ok"])

    def test_even_skus_stay_unlabelled(self):
        df = pd.DataFrame([{"emp_id": "E1", "sku": "N", "allocated_boxes": 3}])
        out = eng._annotate_hist_deviation(df, {("E1", "N"): 1}, even_skus=frozenset({"N"}))
        self.assertEqual(list(out["hist_dev_status"]), [""])


if __name__ == "__main__":
    unittest.main()
