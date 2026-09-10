"""
กติกา "หน่วยไม่เคยขายสินค้านั้น = เป้า 0" — กฎที่ผู้ใช้เคาะไว้ 10 ก.ย. 2026

  1. คู่ (พนักงาน × สินค้า) ที่ไม่เคยขายกันใน 12 เดือนล่าสุด -> ไม่ได้รับหีบ
  2. ถ้าทั้งทีมไม่มีใครเคยขาย SKU นั้นเลย -> เฉลี่ยทุกคน ไม่ใช่กองที่คนเดียว
  3. ถ้าเป้าใหญ่เกิน push_multiple เท่าของประวัติรวมของคนที่เคยขาย -> เฉลี่ยทุกคน
     (สินค้าดันเป้า — ประวัติใช้ตัดสินไม่ได้)

ที่มาของข้อ 3 คือของจริง: SL531 สินค้า 351320 เป้า 2,052 หีบ ทีม 5 คน มีคนเคยขาย
คนเดียว ประวัติ 1 หีบ · ถ้าไม่มีข้อนี้ หีบทั้งก้อนจะตกที่คนเดียว

และกฎเดิมต้องไม่พัง: ผลรวมต่อ SKU ยังตรงเป้าเสมอ (I1) · ช่องที่ล็อกไว้ไม่ถูกแตะ (I2)

รันในหน่วยความจำล้วน ไม่แตะไฟล์/เน็ต/DB
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

STRATEGIES = ("L3M", "EVEN", "PUSH")


def team(n=6):
    return [f"E{i + 1:02d}" for i in range(n)]


def make(emps, targets: dict, hist: dict, sold12: dict, yellow=None):
    """
    hist   = {(emp, sku): หีบเฉลี่ยที่ใช้กระจาย}
    sold12 = {(emp, sku): ยอดรวม 12 เดือน}  — คู่ที่ไม่อยู่ในนี้คือ "ไม่เคยขาย"
    """
    df_emp = pd.DataFrame(
        [{"emp_id": e, "yellow_target": (yellow or {}).get(e, 50000.0)} for e in emps]
    )
    df_sku = pd.DataFrame(
        [
            {"sku": s, "supervisor_target_boxes": t, "price_per_box": 100.0}
            for s, t in targets.items()
        ]
    )
    df_hist = pd.DataFrame(
        [{"emp_id": e, "sku": s, "hist_boxes": float(v)} for (e, s), v in hist.items()]
    ) if hist else pd.DataFrame(columns=["emp_id", "sku", "hist_boxes"])
    df_12 = pd.DataFrame(
        [{"emp_id": e, "sku": s, "hist_boxes": float(v)} for (e, s), v in sold12.items()]
    ) if sold12 else pd.DataFrame(columns=["emp_id", "sku", "hist_boxes"])
    return df_emp, df_sku, df_hist, df_12


def boxes(df, sku=None):
    out = {}
    for _, r in df.iterrows():
        if sku and str(r["sku"]).strip() != sku:
            continue
        b = int(r["allocated_boxes"])
        if b:
            out[str(r["emp_id"]).strip()] = b
    return out


class NeverSoldZeroTest(unittest.TestCase):
    def test_only_people_who_sold_it_get_boxes(self):
        """กฎข้อ 1 — คนที่ไม่เคยขายในรอบปีต้องได้ 0 ทุกกลยุทธ์"""
        emps = team(6)
        # 12 เดือนรวม 600 หีบ = เดือนละ 50 · สองคนรวม 100 = เท่ากับเป้าพอดี
        # (ถ้าตั้งน้อยกว่านี้จะเข้าเงื่อนไข "สินค้าดันเป้า" แล้วถูกเฉลี่ยแทน)
        sellers = {"E02": 600.0, "E05": 600.0}
        for strategy in STRATEGIES:
            with self.subTest(strategy=strategy):
                df_emp, df_sku, df_hist, df_12 = make(
                    emps,
                    {"A": 100},
                    {(e, "A"): 5.0 for e in emps},
                    {(e, "A"): v for e, v in sellers.items()},
                )
                got = boxes(
                    allocate_boxes(
                        df_emp, df_sku, df_hist, strategy=strategy, df_sold_12m=df_12
                    ),
                    "A",
                )
                self.assertEqual(set(got), set(sellers), f"({strategy}) ได้ {got}")
                self.assertEqual(sum(got.values()), 100, "ผลรวมต้องยังตรงเป้า (I1)")

    def test_last_year_same_month_still_counts_as_sold(self):
        """
        กันสินค้าเทศกาล — ผู้ใช้เลือกนิยาม 12 เดือน ไม่ใช่ 3 เดือน ด้วยเหตุผลนี้
        คนที่ขายเฉพาะช่วงเทศกาลปีที่แล้วต้องยังได้เป้า แม้ 3 เดือนล่าสุดเป็น 0
        """
        emps = team(4)
        df_emp, df_sku, df_hist, df_12 = make(
            emps,
            {"A": 80},
            {(e, "A"): 0.0 for e in emps},          # 3 เดือนล่าสุดไม่มีใครขายเลย
            {("E03", "A"): 240.0, ("E01", "A"): 120.0},  # แต่ปีที่แล้วสองคนนี้ขาย
        )
        got = boxes(allocate_boxes(df_emp, df_sku, df_hist, df_sold_12m=df_12), "A")
        self.assertEqual(set(got), {"E01", "E03"}, f"ได้ {got}")
        self.assertEqual(sum(got.values()), 80)

    def test_nobody_ever_sold_it_means_split_evenly(self):
        """
        กฎข้อ 2 — ผู้ใช้ย้ำว่า "ไม่ใช่ไปลงคนเดียว"
        (วัดจากงวด 09/2026 เจอเคสนี้ครบทั้ง 55 ทีม จึงไม่ใช่กรณีหายาก)
        """
        emps = team(4)
        df_emp, df_sku, df_hist, df_12 = make(
            emps,
            {"A": 80},
            {(e, "A"): 0.0 for e in emps},
            # ทีมขายของอื่นอยู่ แต่ไม่มีใครเคยขาย A เลย — ต้องต่างจาก "ยังไม่มีข้อมูล"
            # (ตารางว่างทั้งใบแปลว่ายังโหลดไม่มา ห้ามตีความว่าไม่เคยขาย)
            {("E01", "ของอื่น"): 120.0},
            yellow={"E01": 900000.0},             # เป้าเงินเบี้ยว — เดิมจะดูดไปคนเดียว
        )
        got = boxes(allocate_boxes(df_emp, df_sku, df_hist, df_sold_12m=df_12), "A")
        self.assertEqual(sum(got.values()), 80)
        self.assertEqual(len(got), 4, f"ต้องได้ครบทุกคน — ได้ {got}")
        self.assertEqual(set(got.values()), {20}, f"ต้องเฉลี่ยเท่ากัน — ได้ {got}")

    def test_pushed_target_is_split_evenly_not_dumped_on_the_one_seller(self):
        """
        กฎข้อ 3 — เคสจริงของ SL531: เป้า 2,052 หีบ ทีม 5 คน คนเคยขายคนเดียว ประวัติ 1 หีบ
        ถ้าไม่มีกฎนี้ คนเดียวจะได้ 2,052 หีบ ซึ่งไม่มีใครยอมรับได้
        """
        emps = team(5)
        df_emp, df_sku, df_hist, df_12 = make(
            emps,
            {"A": 2052},
            {(e, "A"): 0.0 for e in emps},
            {("E02", "A"): 12.0},        # 12 เดือนรวม 12 หีบ = เดือนละ 1
        )
        got = boxes(allocate_boxes(df_emp, df_sku, df_hist, df_sold_12m=df_12), "A")
        self.assertEqual(sum(got.values()), 2052)
        self.assertEqual(len(got), 5, f"ต้องกระจายทั้งทีม — ได้ {got}")
        self.assertLessEqual(
            max(got.values()) - min(got.values()), 1, f"ต้องเฉลี่ยเท่า ๆ กัน — ได้ {got}"
        )

    def test_a_normal_target_is_not_treated_as_a_push(self):
        """เกณฑ์ 5 เท่าต้องไม่ไปแตะของปกติ — เป้าพอ ๆ กับประวัติต้องใช้กติกาข้อ 1 ตามเดิม"""
        emps = team(5)
        df_emp, df_sku, df_hist, df_12 = make(
            emps,
            {"A": 100},
            {(e, "A"): 5.0 for e in emps},
            {("E02", "A"): 600.0, ("E04", "A"): 600.0},   # เดือนละ 50 x 2 = เป้า 100 พอดี
        )
        got = boxes(allocate_boxes(df_emp, df_sku, df_hist, df_sold_12m=df_12), "A")
        self.assertEqual(set(got), {"E02", "E04"}, f"ได้ {got}")

    def test_threshold_is_configurable(self):
        """push_multiple ปรับได้ — ตั้งสูงขึ้นแปลว่ายอมให้คนที่เคยขายรับหนักขึ้น"""
        emps = team(5)
        args = dict(
            targets={"A": 500},
            hist={(e, "A"): 0.0 for e in emps},
            sold12={("E02", "A"): 120.0},   # เดือนละ 10 -> เป้าเป็น 50 เท่า
        )
        df_emp, df_sku, df_hist, df_12 = make(emps, **args)
        even = boxes(allocate_boxes(df_emp, df_sku, df_hist, df_sold_12m=df_12), "A")
        self.assertEqual(len(even), 5, "เกณฑ์ 5 (ค่าเริ่มต้น) -> เฉลี่ย")

        df_emp, df_sku, df_hist, df_12 = make(emps, **args)
        strict = boxes(
            allocate_boxes(
                df_emp, df_sku, df_hist, df_sold_12m=df_12, push_multiple=100.0
            ),
            "A",
        )
        self.assertEqual(set(strict), {"E02"}, "เกณฑ์ 100 -> ยังบังคับข้อ 1 ตามเดิม")

    def test_locked_cell_wins_over_the_rule(self):
        """กฎ I2 — ช่องที่ผู้ใช้ล็อกไว้คือเจตนาที่ชัดเจนกว่ากติกาอัตโนมัติ"""
        emps = team(5)
        df_emp, df_sku, df_hist, df_12 = make(
            emps,
            {"A": 100},
            {(e, "A"): 5.0 for e in emps},
            {("E02", "A"): 600.0, ("E03", "A"): 600.0},
        )
        got = boxes(
            allocate_boxes(
                df_emp,
                df_sku,
                df_hist,
                df_sold_12m=df_12,
                locked_edits=[{"emp_id": "E05", "sku": "A", "locked_boxes": 9}],
            ),
            "A",
        )
        self.assertEqual(got.get("E05"), 9, f"ล็อกต้องอยู่ครบ — ได้ {got}")
        self.assertEqual(sum(got.values()), 100)

    def test_force_min_one_still_gives_everyone_one_box(self):
        """
        ผู้ใช้ติ๊ก "ทุกคนอย่างน้อย 1 หีบ" = สั่งชัดกว่ากติกาอัตโนมัติ
        คนไม่เคยขายจึงได้พอดี 1 หีบ ไม่ใช่ 0 และไม่ใช่มากกว่านั้น
        """
        emps = team(5)
        df_emp, df_sku, df_hist, df_12 = make(
            emps,
            {"A": 100},
            {(e, "A"): 5.0 for e in emps},
            {("E02", "A"): 600.0},
        )
        got = boxes(
            allocate_boxes(
                df_emp, df_sku, df_hist, df_sold_12m=df_12, force_min_one=True
            ),
            "A",
        )
        self.assertEqual(sum(got.values()), 100)
        for e in emps:
            if e != "E02":
                self.assertEqual(got.get(e), 1, f"{e} ต้องได้พอดี 1 หีบ — ได้ {got}")

    def test_rule_is_off_when_there_is_no_twelve_month_data(self):
        """
        ไม่มีข้อมูล = ไม่รู้ ต้องไม่ใช่ "ไม่เคยขาย"
        ถ้าเผลอตีความผิด ทีมที่แคชยังไม่มาจะถูกตัดเป้าเกือบทั้งทีมในคลิกเดียว
        """
        emps = team(5)
        for label, frame in (
            ("ไม่ได้ส่งมาเลย", None),
            ("ตารางว่างทั้งใบ", pd.DataFrame(columns=["emp_id", "sku", "hist_boxes"])),
        ):
            with self.subTest(กรณี=label):
                df_emp, df_sku, df_hist, _ = make(
                    emps, {"A": 100}, {(e, "A"): 5.0 for e in emps}, {}
                )
                got = boxes(
                    allocate_boxes(df_emp, df_sku, df_hist, df_sold_12m=frame), "A"
                )
                self.assertEqual(sum(got.values()), 100)
                self.assertGreater(len(got), 1, f"({label}) ต้องกระจายตามปกติ — ได้ {got}")

    def test_plan_is_reported_back_for_the_screen(self):
        """หน้าจอต้องบอกผู้ใช้ได้ว่าเกิดอะไรขึ้น — ผลลัพธ์จึงต้องแนบสรุปกลับมา"""
        emps = team(5)
        df_emp, df_sku, df_hist, df_12 = make(
            emps,
            {"A": 100, "B": 60},
            {(e, s): 5.0 for e in emps for s in ("A", "B")},
            {("E02", "A"): 600.0, ("E03", "A"): 600.0},   # B ไม่มีใครเคยขาย
        )
        out = allocate_boxes(df_emp, df_sku, df_hist, df_sold_12m=df_12)
        summary = out.attrs.get("never_sold_summary") or {}
        self.assertEqual(summary.get("A", {}).get("reason"), "zeroed")
        self.assertEqual(summary.get("B", {}).get("reason"), "no_seller")
        self.assertTrue(out.attrs.get("never_sold_zero_pairs"))


class TellsTheUserWhatHappened(unittest.TestCase):
    """
    ผู้ใช้ตกลงกติกาโดยมีเงื่อนไขว่า "อาจจะต้องมีการแจ้งบอก"
    โดยเฉพาะข้อ 3 ที่คนเคยขายจะรับหนักขึ้น — ถ้าเลขเปลี่ยนแล้วไม่มีใครอธิบาย
    ซุปจะนึกว่าระบบคำนวณผิด แล้วกลับไปแก้มือเหมือนเดิม ซึ่งสวนทางกับเป้าหมายทั้งหมด
    """

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO, "frontend", "app.js"), encoding="utf-8") as f:
            cls.js = f.read()
        with open(
            os.path.join(REPO, "backend", "services", "optimize.py"), encoding="utf-8"
        ) as f:
            cls.py = f.read()

    def test_backend_sends_the_summary_back(self):
        self.assertIn('"never_sold_summary": never_sold_summary_all', self.py)

    def test_both_paths_read_it(self):
        """ทีมเดียวกับรวมภาคเป็นคนละเส้นทาง — พลาดเส้นไหนเส้นนั้นจะเงียบ"""
        self.assertIn("S.neverSoldSummary =", self.js)
        i = self.js.index("const neverSold = {}")
        self.assertIn("never_sold_summary", self.js[i : i + 500], "เส้นรวมภาคต้องรวมทุกทีม")
        self.assertIn("S.neverSoldSummary = neverSold;", self.js)

    def test_the_panel_actually_shows_it(self):
        """บทเรียน 8 ก.ย. — มีฟังก์ชันแต่ไม่มีใครเรียก = ฟีเจอร์ตายเงียบ"""
        i = self.js.index("function syncStep3ReviewNotes(")
        j = self.js.index("function syncStep3TieredNote(", i)
        self.assertIn("_neverSoldReviewLines()", self.js[i:j])

    def test_it_flags_only_the_concentrated_ones(self):
        """
        บอกทุก SKU ที่กติกาทำงาน = ท่วมจนไม่มีใครอ่าน
        ที่ต้องเข้าไปดูจริงคือ SKU ที่เป้าไปกองที่คนเคยขาย 1-2 คน
        """
        i = self.js.index("function _neverSoldReviewLines(")
        body = self.js[i : i + 2600]
        self.assertIn("x.sellers <= 2", body)
        self.assertIn("slice(0, 5)", body, "ยาวเกินต้องตัด ไม่งั้นแผงยาวเป็นหางว่าว")
        self.assertIn("b.boxes - a.boxes", body, "เรียงจากก้อนใหญ่ก่อน")

    def test_it_separates_the_two_kinds_of_even_split(self):
        """'ทีมไม่เคยขาย' กับ 'เป้าใหญ่เกินไป' คนละเรื่อง ผู้ใช้ต้องแยกออก"""
        i = self.js.index("function _neverSoldReviewLines(")
        body = self.js[i : i + 2600]
        self.assertIn("no_seller", body)
        self.assertIn("push_target", body)
        self.assertIn("ทั้งทีมไม่เคยขาย", body)
        self.assertIn("เป้าใหญ่กว่าที่ทีมเคยขายมาก", body)


if __name__ == "__main__":
    unittest.main()
