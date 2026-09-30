"""
§4.1-6 โหมดหลายกลยุทธ์ร่วมกับ history_only หรือกลุ่มที่มูลค่า 0 ต้องไม่ได้ 409

เดิม: เป้าเงินแต่ละคน × สัดส่วนมูลค่ากลุ่ม แล้วตัดคนที่ ≤ 0 ทิ้ง
  - history_only เป้าเงิน 0 ทุกคน → ตัดหมดทุกกลุ่ม
  - กลุ่มราคา 0 → สัดส่วน 0 → ข้ามทั้งกลุ่ม สินค้าไม่มีหีบ → ด่าน I1 ตีกลับ 409
"""

from __future__ import annotations

import inspect
import logging
import os
import sys
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.OR_engine import allocate_boxes  # noqa: E402
from backend.services import optimize as opt  # noqa: E402

logging.disable(logging.CRITICAL)

EMP = pd.DataFrame({"emp_id": ["E1", "E2", "E3"], "yellow_target": [3000.0, 2000.0, 1000.0]})


class TestStrategyGroupTargets(unittest.TestCase):
    def test_normal_group_scales_by_value_share(self):
        df, ho = opt._strategy_group_targets(EMP, 250.0, 1000.0, history_only=False)
        self.assertFalse(ho)
        self.assertEqual(df["yellow_target"].tolist(), [750.0, 500.0, 250.0])

    def test_history_only_keeps_everyone(self):
        zero = EMP.assign(yellow_target=0.0)
        df, ho = opt._strategy_group_targets(zero, 250.0, 1000.0, history_only=True)
        self.assertTrue(ho)
        self.assertEqual(df["emp_id"].tolist(), ["E1", "E2", "E3"])

    def test_zero_value_group_keeps_everyone_and_uses_history(self):
        df, ho = opt._strategy_group_targets(EMP, 0.0, 1000.0, history_only=False)
        self.assertTrue(ho, "ราคา 0 = เป้าเงินไม่มีความหมายกับกลุ่มนี้ ต้องกระจายตามประวัติ")
        self.assertEqual(len(df), 3)

    def test_zero_value_group_still_fills_its_target(self):
        """สินค้าราคา 0 ต้องได้หีบครบเป้า (I1) ไม่ใช่ถูกข้าม"""
        df, ho = opt._strategy_group_targets(EMP, 0.0, 1000.0, history_only=False)
        sku = pd.DataFrame([{"sku": "Z", "supervisor_target_boxes": 12, "price_per_box": 0.0}])
        hist = pd.DataFrame([{"emp_id": e, "sku": "Z", "hist_boxes": h} for e, h in (("E1", 6), ("E2", 3), ("E3", 3))])
        out = allocate_boxes(df, sku, hist, strategy="L3M", history_only=ho)
        self.assertEqual(int(out["allocated_boxes"].sum()), 12)
        self.assertEqual(dict(zip(out["emp_id"], out["allocated_boxes"])), {"E1": 6, "E2": 3, "E3": 3})

    def test_service_uses_the_helper_and_its_history_flag(self):
        src = inspect.getsource(opt.run_optimization_service)
        self.assertIn("_strategy_group_targets(", src)
        self.assertIn("history_only=grp_history_only", src)
        self.assertNotIn('df_targets_grp["yellow_target"] * share', src)


if __name__ == "__main__":
    unittest.main()
