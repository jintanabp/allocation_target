"""
§4.1-8 สินค้าใหม่ที่แบ่งเท่า แต่มีคนเคยขายใน 12 เดือน ต้องทำตาม I9

_proportional และ LP ตัดคนไม่เคยขายออกจากสินค้าแบ่งเท่าถูกแล้ว แต่ขั้นสุดท้าย
_enforce_even_skus_on_df แบ่งใหม่ให้ทุกคน ทับผลนั้นทิ้ง
"""

from __future__ import annotations

import logging
import os
import sys
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.OR_engine import _enforce_even_skus_on_df, allocate_boxes  # noqa: E402

logging.disable(logging.CRITICAL)

EMPS = ["E1", "E2", "E3", "E4"]


def _run(target, sold, force_min_one=False):
    df_emp = pd.DataFrame({"emp_id": EMPS, "yellow_target": [1000.0] * 4})
    df_sku = pd.DataFrame([
        {"sku": "N", "supervisor_target_boxes": target, "price_per_box": 100.0},   # สินค้าใหม่
        {"sku": "B", "supervisor_target_boxes": 40, "price_per_box": 100.0},
    ])
    df_hist = pd.DataFrame([{"emp_id": e, "sku": "B", "hist_boxes": 30} for e in EMPS])
    df_sold = pd.DataFrame([{"emp_id": e, "sku": s, "hist_boxes": b} for e, s, b in sold])
    out = allocate_boxes(df_emp, df_sku, df_hist, strategy="L3M", force_min_one=force_min_one,
                         even_new_products=True, new_product_skus={"N"}, df_sold_12m=df_sold)
    n = out[out["sku"] == "N"]
    return {e: int(n.loc[n["emp_id"] == e, "allocated_boxes"].sum()) for e in EMPS}


# ความจุคนเคยขาย = 60/12 = 5 หีบ/เดือน → เป้า 6 ไม่เกิน 5 เท่า = กติกาตัดคนไม่เคยขาย
SOLD = [("E1", "N", 30), ("E2", "N", 30)] + [(e, "B", 60) for e in EMPS]


class TestNewProductEvenRespectsNeverSold(unittest.TestCase):
    def test_only_sellers_share_the_new_product(self):
        self.assertEqual(_run(6, SOLD), {"E1": 3, "E2": 3, "E3": 0, "E4": 0})

    def test_force_min_one_still_wins(self):
        got = _run(8, SOLD, force_min_one=True)
        self.assertEqual((got["E3"], got["E4"]), (1, 1))
        self.assertEqual(got["E1"] + got["E2"], 6)
        self.assertEqual(sum(got.values()), 8)

    def test_push_target_is_even_for_everyone(self):
        """เป้าเกิน 5 เท่าของความจุคนเคยขาย = สินค้าดันเป้า → เฉลี่ยทุกคน (ข้อยกเว้น 2)"""
        sold = [("E1", "N", 12)] + [(e, "B", 60) for e in EMPS]
        self.assertEqual(_run(40, sold), {e: 10 for e in EMPS})

    def test_without_rule_still_even_for_all(self):
        df_emp = pd.DataFrame({"emp_id": EMPS, "yellow_target": [1.0] * 4})
        df_sku = pd.DataFrame([{"sku": "N", "supervisor_target_boxes": 8, "price_per_box": 1.0}])
        out = _enforce_even_skus_on_df(pd.DataFrame(), frozenset({"N"}), df_emp, df_sku, {}, False)
        self.assertEqual(dict(zip(out["emp_id"], out["allocated_boxes"])), {e: 2 for e in EMPS})


if __name__ == "__main__":
    unittest.main()
