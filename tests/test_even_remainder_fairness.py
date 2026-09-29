"""
เศษหีบของ SKU ที่แบ่งเท่ากันต้องไม่ตกคนต้นรายชื่อทุกงวด (ผลตรวจ 28 ก.ย. 2026 §4.1-9)

เดิม _distribute_even_integers ให้เศษกับช่องแรกของลิสต์ = ลำดับในทะเบียน → คนเดิมได้ทุกงวด
ผู้ใช้ตัดสิน 29 ก.ย. 2026: ใครขายสินค้านั้นมากกว่าได้ก่อน · เสมอกันให้คนที่เป้าเงินสูงกว่า
· ยังเสมอค่อยเรียงตามรหัส — ยอดรวมต่อ SKU ต้องเท่าเดิมทุกหีบ
"""

from __future__ import annotations

import os
import sys
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend import OR_engine as eng  # noqa: E402


def _case(yellows: dict, target: int, hist: dict | None = None):
    df_emp = pd.DataFrame([{"emp_id": e, "yellow_target": y} for e, y in yellows.items()])
    df_sku = pd.DataFrame([{"sku": "N1", "supervisor_target_boxes": target, "price_per_box": 100.0}])
    rows = [{"emp_id": e, "sku": "N1", "hist_boxes": b} for e, b in (hist or {}).items()]
    df_hist = pd.DataFrame(rows, columns=["emp_id", "sku", "hist_boxes"])
    return df_emp, df_sku, df_hist


def _boxes(out) -> dict:
    return {str(r.emp_id): int(r.allocated_boxes) for r in out.itertuples() if str(r.sku) == "N1"}


class TestRankRule(unittest.TestCase):
    def test_history_then_money_target_then_code(self):
        r = eng._fair_rank(["A", "B", "C", "D"], {"C": 5.0}, {"A": 100.0, "B": 900.0, "D": 900.0})
        self.assertEqual(sorted(r, key=r.get), ["C", "B", "D", "A"])

    def test_even_split_gives_remainder_to_top_ranked(self):
        rank = eng._fair_rank(["A", "B", "C"], {}, {"A": 1.0, "B": 3.0, "C": 2.0})
        self.assertEqual(eng._even_split_by_rank(7, ["A", "B", "C"], rank), {"B": 3, "C": 2, "A": 2})


class TestEngineEvenSkus(unittest.TestCase):
    """ผ่าน allocate_boxes จริง — สินค้าใหม่ทุกคนไม่เคยขาย (เสมอกันหมด)"""

    def _run(self, yellows, target, hist=None, strategy="EVEN"):
        df_emp, df_sku, df_hist = _case(yellows, target, hist)
        out = eng.allocate_boxes(
            df_emp, df_sku, df_hist, strategy=strategy, tiered_allocation=False,
            even_new_products=True, new_product_skus={"N1"},
        )
        return _boxes(out)

    def test_first_in_list_no_longer_wins_the_remainder(self):
        # E1 อยู่ต้นรายชื่อแต่เป้าเงินต่ำสุด — เดิมได้เศษ ตอนนี้ต้องไม่ได้
        got = self._run({"E1": 100.0, "E2": 500.0, "E3": 300.0}, 4)
        self.assertEqual(got, {"E1": 1, "E2": 2, "E3": 1})

    def test_total_is_unchanged(self):
        got = self._run({f"E{i}": float(i) for i in range(1, 8)}, 23)
        self.assertEqual(sum(got.values()), 23)
        self.assertLessEqual(max(got.values()) - min(got.values()), 1)

    def test_the_lp_path_follows_the_same_rule(self):
        got = self._run({"E1": 100.0, "E2": 500.0, "E3": 300.0}, 4, strategy="L3M")
        self.assertEqual(sum(got.values()), 4)
        self.assertEqual(got.get("E2"), 2, "เศษต้องตกคนเป้าเงินสูงสุดทั้งทาง LP ด้วย")


if __name__ == "__main__":
    unittest.main()
