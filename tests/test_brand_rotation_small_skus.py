"""
หมุนหีบ SKU เป้าน้อยกว่าจำนวนคนภายในแบรนด์ (ผู้ใช้เลือก 7 ต.ค. 2026)

ปัญหาเดิม (จำลองผ่านตัวกระจายจริง): ทีม 6 คน แบรนด์กู๊ดเอจ 5 SKU SKU ละ 3 หีบ
→ E1–E3 ได้ทุก SKU · E4–E6 ได้ 0 ทั้งแบรนด์ ทุกวิธี (แม้ประวัติเท่ากัน — ตัดสินเสมอด้วยรหัส)
"""

from __future__ import annotations

import unittest

import pandas as pd

from backend.OR_engine import _rotate_small_skus_in_brand, allocate_boxes

EMPS = [f"E{i}" for i in range(1, 7)]


def _sku_df(skus, boxes=3, brand="กู๊ดเอจ", price=100.0):
    return pd.DataFrame([
        {"sku": s, "supervisor_target_boxes": boxes, "price_per_box": price, "brand_name_thai": brand}
        for s in skus
    ])


def _emp_df(yellow=1500.0):
    return pd.DataFrame([{"emp_id": e, "yellow_target": yellow} for e in EMPS])


def _hist(skus):
    return pd.DataFrame([
        {"emp_id": e, "sku": s, "hist_boxes": 10 - i * 0.5} for i, e in enumerate(EMPS) for s in skus
    ])


def _matrix(df):
    out = {}
    for e, s, b in zip(df["emp_id"], df["sku"], df["allocated_boxes"]):
        out[(str(e), str(s))] = out.get((str(e), str(s)), 0) + int(b)
    return out


class TestBrandRotationEngine(unittest.TestCase):
    SKUS = ["GA1", "GA2", "GA3", "GA4", "GA5"]

    def _run(self, strategy):
        return allocate_boxes(_emp_df(), _sku_df(self.SKUS), _hist(self.SKUS), strategy=strategy)

    def test_everyone_gets_some_of_the_brand(self):
        for strategy in ("L3M", "L6M", "EVEN", "LP"):
            with self.subTest(strategy=strategy):
                df = self._run(strategy)
                m = _matrix(df)
                for s in self.SKUS:
                    self.assertEqual(sum(m.get((e, s), 0) for e in EMPS), 3)     # ยอดต่อ SKU เท่าเดิม
                    self.assertTrue(all(m.get((e, s), 0) <= 1 for e in EMPS))  # คนละไม่เกิน 1
                per_emp = [sum(m.get((e, s), 0) for s in self.SKUS) for e in EMPS]
                self.assertTrue(all(n >= 2 for n in per_emp), per_emp)          # ทุกคนได้ขาย 2–3 SKU
                self.assertEqual(sum(per_emp), 15)

    def test_can_be_turned_off(self):
        df = allocate_boxes(_emp_df(), _sku_df(self.SKUS), _hist(self.SKUS), strategy="L3M",
                            rotate_small_in_brand=False)
        m = _matrix(df)
        self.assertEqual(sum(m.get(("E6", s), 0) for s in self.SKUS), 0)  # พฤติกรรมเดิม (ไว้เทียบ)


class TestBrandRotationRules(unittest.TestCase):
    def _out(self, cells):
        return pd.DataFrame([{"emp_id": e, "sku": s, "allocated_boxes": b} for (e, s), b in cells.items()])

    def _top3(self, skus):
        return {(e, s): 1 for s in skus for e in EMPS[:3]}

    def test_locked_cells_untouched(self):
        skus = ["A", "B"]
        cells = self._top3(skus)
        locked = {("E1", "B"): 1}
        out, st = _rotate_small_skus_in_brand(self._out(cells), _emp_df(), _sku_df(skus), locked_map=locked)
        m = _matrix(out)
        self.assertEqual(m.get(("E1", "B")), 1)
        for s in skus:
            self.assertEqual(sum(m.get((e, s), 0) for e in EMPS), 3)
        self.assertGreater(st["moved_boxes"], 0)

    def test_never_sold_pairs_not_given_boxes(self):
        skus = ["A", "B"]
        zero = {("E5", "B"), ("E6", "B")}  # B มีคนมีสิทธิ์ 4 คน (> 3 หีบ) จึงยังเป็น SKU เป้าน้อย
        out, _ = _rotate_small_skus_in_brand(self._out(self._top3(skus)), _emp_df(), _sku_df(skus), zero_pairs=zero)
        m = _matrix(out)
        self.assertTrue(all(m.get((e, "B"), 0) == 0 for e in ("E5", "E6")))
        self.assertEqual(sum(m.get((e, s), 0) for e in EMPS for s in skus), 6)
        for e in ("E4", "E5", "E6"):  # ทุกคนได้ขายแบรนด์ — E5/E6 ได้ที่ A เพราะ B ห้าม
            self.assertGreaterEqual(m.get((e, "A"), 0) + m.get((e, "B"), 0), 1)

    def test_single_small_sku_brand_unchanged(self):
        cells = self._top3(["A"])
        out, st = _rotate_small_skus_in_brand(self._out(cells), _emp_df(), _sku_df(["A"]))
        self.assertEqual(st["moved_boxes"], 0)
        self.assertEqual(_matrix(out), cells)

    def test_sku_with_cell_above_one_untouched(self):
        cells = self._top3(["A"]) | {("E1", "B"): 2, ("E2", "B"): 1}
        out, st = _rotate_small_skus_in_brand(self._out(cells), _emp_df(), _sku_df(["A", "B"]))
        self.assertEqual(st["moved_boxes"], 0)  # เหลือ SKU เป้าน้อยแบบ 0/1 แค่ตัวเดียว

    def test_no_brand_column_no_change(self):
        cells = self._top3(["A", "B"])
        df_sku = _sku_df(["A", "B"]).drop(columns=["brand_name_thai"])
        out, st = _rotate_small_skus_in_brand(self._out(cells), _emp_df(), df_sku)
        self.assertEqual(st["moved_boxes"], 0)

    def test_different_brands_not_mixed(self):
        df_sku = pd.concat([_sku_df(["A"], brand="X"), _sku_df(["B"], brand="Y")], ignore_index=True)
        cells = self._top3(["A", "B"])
        out, st = _rotate_small_skus_in_brand(self._out(cells), _emp_df(), df_sku)
        self.assertEqual(st["moved_boxes"], 0)  # แบรนด์ละ 1 SKU — ไม่หมุนข้ามแบรนด์


if __name__ == "__main__":
    unittest.main()
