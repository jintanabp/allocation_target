"""
เพดาน "เป้าน้อยกว่าจำนวนคน = คนละไม่เกิน 1 หีบ" ต้องนับเฉพาะคนที่มีสิทธิ์รับ (พบ 29 ก.ย. 2026)

เดิมนับทั้งทีม รวมคนที่กติกาไม่เคยขายตัดเป็น 0 — ทีม 6 คน เป้า 3 หีบ มีคนเคยขายคนเดียว
คนนั้นได้ 1 หีบ อีก 2 หีบไม่มีที่ลง ยอดขาดเป้า (I1) และ LP ถอยไป fallback ทั้งทีม
"""

from __future__ import annotations

import os
import sys
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend import OR_engine as eng  # noqa: E402

EMPS = [f"E{i}" for i in range(1, 7)]


def _case():
    df_emp = pd.DataFrame({"emp_id": EMPS, "yellow_target": [10_000.0] * 6})
    df_sku = pd.DataFrame([
        {"sku": "A", "supervisor_target_boxes": 3, "price_per_box": 100.0},     # ขายคนเดียว
        {"sku": "B", "supervisor_target_boxes": 540, "price_per_box": 100.0},   # ขายทุกคน
    ])
    hist = [{"emp_id": e, "sku": "B", "hist_boxes": 270.0} for e in EMPS]
    hist.append({"emp_id": "E6", "sku": "A", "hist_boxes": 24.0})  # 2 หีบ/เดือน ไม่ถึงเกณฑ์ดันเป้า
    df_hist = pd.DataFrame(hist)
    return df_emp, df_sku, df_hist, df_hist.copy()


class TestOneEachCountsEligible(unittest.TestCase):
    def test_eligible_count_skips_zeroed_people(self):
        zp = {(e, "A") for e in EMPS[:5]}
        self.assertEqual(eng._eligible_emp_count(EMPS, "A", zp), 1)
        self.assertEqual(eng._eligible_emp_count(EMPS, "B", zp), 6)
        self.assertEqual(eng._eligible_emp_count(EMPS, "A", None), 6)

    def test_target_met_when_only_one_person_ever_sold(self):
        df_emp, df_sku, df_hist, sold = _case()
        for strat in ("L3M", "EVEN"):
            out = eng.allocate_boxes(df_emp, df_sku, df_hist, strategy=strat,
                                     tiered_allocation=True, df_sold_12m=sold)
            got = out.groupby("sku")["allocated_boxes"].sum().to_dict()
            self.assertEqual(got.get("A"), 3, strat)
            self.assertEqual(got.get("B"), 540, strat)
            a = {k: v for k, v in out[out["sku"] == "A"].set_index("emp_id")["allocated_boxes"].items() if v}
            self.assertEqual(a, {"E6": 3}, strat)

    def test_proportional_fallback_also_meets_target(self):
        df_emp, df_sku, df_hist, _ = _case()
        zp = {(e, "A") for e in EMPS[:5]}
        out = eng._proportional(df_emp, df_sku, df_hist, "L3M", zero_pairs=zp)
        self.assertEqual(int(out[out["sku"] == "A"]["allocated_boxes"].sum()), 3)

    def test_without_rule_one_each_still_applies(self):
        df_emp, df_sku, df_hist, _ = _case()
        out = eng.allocate_boxes(df_emp, df_sku, df_hist, strategy="L3M", tiered_allocation=True)
        a = out[out["sku"] == "A"]["allocated_boxes"]
        self.assertEqual(int(a.sum()), 3)
        self.assertLessEqual(int(a.max()), 1)


if __name__ == "__main__":
    unittest.main()
