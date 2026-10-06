"""
โหมดหลายวิธี: ป้าย ±% เทียบประวัติต้องคิดจากหีบหลังตัวเกลี่ยเงินหลังรวมผล (OPEN_ITEMS 8.6 — ผลตรวจ 6 ต.ค. 2026)
"""

from __future__ import annotations

import inspect
import os
import sys
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import optimize as opt  # noqa: E402


def _df(boxes, pcts, statuses):
    return pd.DataFrame({
        "emp_id": ["E1", "E2", "E3", "E4"],
        "sku": ["A", "A", "B", "B"],
        "allocated_boxes": boxes,
        "baseline_boxes": [10, 10, 0, 20],
        "hist_dev_pct": pd.array(pcts, dtype=object),
        "hist_dev_status": statuses,
    })


class TestPostMergeHistDevRefresh(unittest.TestCase):
    def test_moved_cells_relabelled_unmoved_untouched(self):
        before = _df([10, 10, 5, 20], [0.0, 0.0, None, 0.0], ["ok", "ok", "", "ok"])
        after = before.copy()
        after["allocated_boxes"] = [16, 4, 5, 20]  # ตัวเกลี่ยย้าย 6 หีบจาก E2 → E1
        out = opt._refresh_hist_deviation_after_move(before, after)
        self.assertEqual(list(out["hist_dev_status"]), ["far", "far", "", "ok"])
        self.assertEqual(out.loc[0, "hist_dev_pct"], 60.0)
        self.assertEqual(out.loc[1, "hist_dev_pct"], -60.0)
        self.assertIsNone(out.loc[2, "hist_dev_pct"])
        self.assertEqual(list(out["allocated_boxes"]), [16, 4, 5, 20])

    def test_no_move_returns_same(self):
        before = _df([10, 10, 5, 20], [0.0, 0.0, None, 0.0], ["ok", "ok", "", "ok"])
        out = opt._refresh_hist_deviation_after_move(before, before.copy())
        self.assertEqual(list(out["hist_dev_status"]), ["ok", "ok", "", "ok"])

    def test_without_label_columns_noop(self):
        before = pd.DataFrame({"emp_id": ["E1"], "sku": ["A"], "allocated_boxes": [1]})
        after = before.assign(allocated_boxes=[2])
        out = opt._refresh_hist_deviation_after_move(before, after)
        self.assertEqual(list(out.columns), ["emp_id", "sku", "allocated_boxes"])

    def test_post_merge_uses_refresh(self):
        src = inspect.getsource(opt._post_merge_revenue_balance)
        self.assertIn("return _refresh_hist_deviation_after_move(df_allocation, df_out)", src)


if __name__ == "__main__":
    unittest.main()
