"""
โหมดหลายกลยุทธ์: ตัวเกลี่ยเงินหลังรวมผลต้องไม่ทำลายการแบ่งเท่าของ SKU no_seller/push_target
(ผลตรวจ 28 ก.ย. 2026 §4.1-2)

เดิม even_skus ของขั้นนี้มีแค่สินค้าใหม่ และ baseline ของรั้วไม่รู้จัก zero_pairs (I9)
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

EMPS = ["E1", "E2", "E3", "E4"]


class TestMultiStrategyNeverSoldEven(unittest.TestCase):
    # 8.5 (6 ต.ค. 2026): สองข้อนี้เคยค้นข้อความในโค้ด — ตอนนี้รันฟังก์ชันจริง + ตัวเกลี่ยจริง
    def test_never_sold_even_skus_join_the_post_merge_even_set(self):
        got = opt._post_merge_even_skus(
            frozenset({"NEW"}),
            {"N1": {"reason": "no_seller"}, "N2": {"reason": "push_target"},
             "N3": {"reason": "zeroed"}, "N4": None},
            {},
        )
        self.assertEqual(got, frozenset({"NEW", "N1", "N2"}))

    def test_brands_set_to_even_or_push_join_the_post_merge_even_set(self):
        """ผลตรวจ 5 ต.ค. 2026 ข้อ 5: แบรนด์ที่เลือก EVEN/PUSH ต้องไม่ถูกตัวเกลี่ยเงินย้ายหีบ"""
        got = opt._post_merge_even_skus(
            frozenset(), {}, {" X1 ": "EVEN", "X2": "push", "X3": "L3M", "X4": "LY"},
        )
        self.assertEqual(got, frozenset({"X1", "X2"}))

    def test_even_brand_survives_post_merge_balance_end_to_end(self):
        """ตัวอย่างจากผลตรวจ 5 ต.ค.: EVEN 50/50/50/50 ต้องไม่กลายเป็น 100/33/33/34 หลังเกลี่ยเงิน"""
        df_emp = pd.DataFrame({"emp_id": EMPS, "yellow_target": [90000.0, 30000.0, 30000.0, 30000.0]})
        df_sku = pd.DataFrame([
            {"sku": "X1", "supervisor_target_boxes": 200, "price_per_box": 800.0},
            {"sku": "Y1", "supervisor_target_boxes": 40, "price_per_box": 100.0},
        ])
        hist = pd.DataFrame([{"emp_id": e, "sku": s, "hist_boxes": 10.0} for e in EMPS for s in ("X1", "Y1")])
        alloc = pd.DataFrame(
            [{"emp_id": e, "sku": "X1", "allocated_boxes": 50} for e in EMPS]
            + [{"emp_id": e, "sku": "Y1", "allocated_boxes": 10} for e in EMPS]
        )
        strat = {"X1": "EVEN", "Y1": "L3M"}
        kw = dict(sku_strategy=strat, hist_by_strategy={"EVEN": hist, "L3M": hist}, locked_edits_data=[],
                  force_min_one=False, cap_multiplier=None, tiered_allocation=True, tier_pct=0.8,
                  revenue_tolerance_baht=1000.0, zero_pairs=set())
        protected = opt._post_merge_revenue_balance(
            alloc, df_emp, df_sku, even_skus=opt._post_merge_even_skus(frozenset(), {}, strat), **kw)
        self.assertEqual(protected[protected["sku"] == "X1"]["allocated_boxes"].tolist(), [50, 50, 50, 50])
        # ถ้าไม่กันไว้ ตัวเกลี่ยย้ายหีบ X1 ตามเงินจริง (พิสูจน์ว่าเทสนี้จับได้)
        unprotected = opt._post_merge_revenue_balance(alloc, df_emp, df_sku, even_skus=frozenset(), **kw)
        self.assertNotEqual(unprotected[unprotected["sku"] == "X1"]["allocated_boxes"].tolist(), [50, 50, 50, 50])
        for df in (protected, unprotected):
            self.assertEqual(int(df[df["sku"] == "X1"]["allocated_boxes"].sum()), 200)

    def test_run_optimize_uses_the_helper(self):
        src = inspect.getsource(opt)
        i = src.index("df_allocation = _post_merge_revenue_balance(")
        self.assertIn("_post_merge_even_skus(", src[i - 400:i])

    def test_base_map_respects_zero_pairs(self):
        df_emp = pd.DataFrame({"emp_id": EMPS, "yellow_target": [1000.0] * 4})
        df_sku = pd.DataFrame([{"sku": "A", "supervisor_target_boxes": 8, "price_per_box": 100.0}])
        hist = pd.DataFrame([{"emp_id": e, "sku": "A", "hist_boxes": 3.0} for e in EMPS[:2]])
        zp = {("E3", "A"), ("E4", "A")}
        bm = opt._build_multi_strategy_base_map(
            df_emp, df_sku, {"A": "L3M"}, {"L3M": hist}, force_min_one=False,
            locked_map={}, cap_multiplier=None, even_skus=frozenset(), zero_pairs=zp,
        )
        self.assertEqual(bm.get(("E3", "A"), 0), 0)
        self.assertEqual(bm.get(("E4", "A"), 0), 0)
        self.assertEqual(bm.get(("E1", "A"), 0) + bm.get(("E2", "A"), 0), 8)

    def test_even_sku_untouched_by_post_merge_balance(self):
        df_emp = pd.DataFrame({"emp_id": EMPS, "yellow_target": [4000.0, 1000.0, 1000.0, 1000.0]})
        df_sku = pd.DataFrame([
            {"sku": "N", "supervisor_target_boxes": 40, "price_per_box": 100.0},   # ไม่มีใครเคยขาย
            {"sku": "B", "supervisor_target_boxes": 30, "price_per_box": 100.0},
        ])
        hist = pd.DataFrame([{"emp_id": e, "sku": "B", "hist_boxes": 20.0} for e in EMPS])
        alloc = pd.DataFrame(
            [{"emp_id": e, "sku": "N", "allocated_boxes": 10} for e in EMPS]
            + [{"emp_id": e, "sku": "B", "allocated_boxes": b} for e, b in zip(EMPS, [8, 8, 7, 7])]
        )
        out = opt._post_merge_revenue_balance(
            alloc, df_emp, df_sku, sku_strategy={"N": "L3M", "B": "L6M"},
            hist_by_strategy={"L3M": hist, "L6M": hist}, locked_edits_data=[],
            force_min_one=False, cap_multiplier=None, even_skus=frozenset({"N"}),
            tiered_allocation=True, tier_pct=0.8, revenue_tolerance_baht=1000.0, zero_pairs=set(),
        )
        n = out[out["sku"] == "N"]["allocated_boxes"].tolist()
        self.assertEqual(sorted(n), [10, 10, 10, 10])
        self.assertEqual(int(out[out["sku"] == "B"]["allocated_boxes"].sum()), 30)


if __name__ == "__main__":
    unittest.main()
