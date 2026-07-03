"""Smoke test for allocate_boxes — sum boxes equals SKU targets."""

from __future__ import annotations

import os
import sys
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.OR_engine import allocate_boxes  # noqa: E402


class TestOptimizeSmoke(unittest.TestCase):
    def test_allocate_boxes_sums_to_target(self):
        df_emp = pd.DataFrame([
            {"emp_id": "E1", "yellow_target": 50000.0},
            {"emp_id": "E2", "yellow_target": 50000.0},
        ])
        df_sku = pd.DataFrame([
            {"sku": "111111", "supervisor_target_boxes": 10, "price_per_box": 5000.0},
            {"sku": "222222", "supervisor_target_boxes": 6, "price_per_box": 3000.0},
        ])
        df_hist = pd.DataFrame([
            {"emp_id": "E1", "sku": "111111", "hist_boxes": 30},
            {"emp_id": "E2", "sku": "111111", "hist_boxes": 20},
            {"emp_id": "E1", "sku": "222222", "hist_boxes": 12},
            {"emp_id": "E2", "sku": "222222", "hist_boxes": 8},
        ])
        out = allocate_boxes(df_emp, df_sku, df_hist, strategy="L3M", tiered_allocation=False)
        for sku, target in [("111111", 10), ("222222", 6)]:
            got = int(out.loc[out["sku"] == sku, "allocated_boxes"].sum())
            self.assertEqual(got, target, f"sku {sku}: expected {target}, got {got}")


if __name__ == "__main__":
    unittest.main()
