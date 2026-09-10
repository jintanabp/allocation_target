"""
เป้ารวมของ SKU น้อยกว่าจำนวนคนในทีม — ต้องกระจายให้ทั่ว ไม่กองที่คนเดียว/แถวบน

ที่มา: ผลสำรวจ 53 คน (SL384) + ผลตรวจรอบ 0 ที่นับได้ 3,820 คู่ (19.5% ของคู่สินค้า×ทีม)
ตัวอย่างจริง: SL540 สินค้า 111336 เป้ารวม 4 หีบ ทีม 11 คน — 7 คนได้ 0 แน่นอน
และของเดิมไม่มีอะไรกันไม่ให้ 4 หีบไปกองที่คนเดียว

กฎที่เทสต์นี้ล็อกไว้:
  S1  เป้า < จำนวนคน → ไม่มีใครได้เกิน 1 หีบใน SKU นั้น
  S2  จำนวนคนที่ได้หีบ == เป้า (กระจายให้ทั่วจริง ไม่ใช่แค่ตัดเพดาน)
  S3  คนที่ได้ = คนที่ขาย SKU นั้นได้มากที่สุด ไม่ใช่คนแถวบนสุดของตาราง
  S4  ไม่มีประวัติเลย → ตัดสินด้วยรหัสพนักงาน (คงที่ ไม่ขึ้นกับลำดับในทะเบียน)
  S5  เป้า >= จำนวนคน → พฤติกรรมเดิม ไม่ถูกแตะ
  S6  ผลรวมต่อ SKU ยังตรงเป้าเสมอ (กฎ I1)

รันในหน่วยความจำล้วน ไม่แตะไฟล์/เน็ต/DB เหมือน test_or_engine_invariants.py
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

SKU = "111336"
STRATEGIES = ("L3M", "EVEN", "PUSH", "LP")


def make_case(n_emp: int, target_boxes: int, hist_for: dict | None = None):
    """
    ทีม n คน · SKU เดียว · เป้า target_boxes หีบ

    hist_for: {emp_id: หีบที่เคยขาย} — ไม่ใส่ = ไม่มีใครมีประวัติเลย
    รหัสพนักงานตั้งใจให้ "ลำดับในทะเบียน" กับ "ลำดับตามประวัติ" ไม่ตรงกัน
    จะได้จับได้ว่าโค้ดใช้ตัวไหนตัดสิน
    """
    emps = [f"E{i + 1:02d}" for i in range(n_emp)]
    df_emp = pd.DataFrame([{"emp_id": e, "yellow_target": 50000.0} for e in emps])
    df_sku = pd.DataFrame(
        [{"sku": SKU, "supervisor_target_boxes": target_boxes, "price_per_box": 250.0}]
    )
    hist_for = hist_for or {}
    df_hist = pd.DataFrame(
        [
            {"emp_id": e, "sku": SKU, "hist_boxes": float(hist_for.get(e, 0.0))}
            for e in emps
        ]
    )
    return df_emp, df_sku, df_hist


def boxes_by_emp(df_out: pd.DataFrame) -> dict:
    if df_out is None or df_out.empty:
        return {}
    return {
        str(r["emp_id"]).strip(): int(r["allocated_boxes"])
        for _, r in df_out.iterrows()
        if int(r["allocated_boxes"]) > 0
    }


def run(df_emp, df_sku, df_hist, strategy="L3M", **kw):
    return allocate_boxes(df_emp, df_sku, df_hist, strategy=strategy, **kw)


class SmallTargetSpreadTest(unittest.TestCase):
    def test_s1_s2_no_one_gets_more_than_one(self):
        """เป้า 4 หีบ ทีม 11 คน → 4 คนได้คนละ 1 หีบ ทุกกลยุทธ์"""
        for strategy in STRATEGIES:
            with self.subTest(strategy=strategy):
                df_emp, df_sku, df_hist = make_case(
                    11, 4, hist_for={"E09": 30, "E07": 20, "E05": 10, "E03": 5}
                )
                got = boxes_by_emp(run(df_emp, df_sku, df_hist, strategy))
                self.assertEqual(sum(got.values()), 4, f"ผลรวมต้องตรงเป้า ({strategy})")
                self.assertTrue(
                    all(v == 1 for v in got.values()),
                    f"ต้องไม่มีใครได้เกิน 1 หีบ ({strategy}) — ได้ {got}",
                )
                self.assertEqual(
                    len(got), 4, f"ต้องกระจายให้ครบ 4 คน ({strategy}) — ได้ {got}"
                )

    def test_s3_goes_to_the_best_sellers_not_the_top_rows(self):
        """คนที่ได้ต้องเป็นคนที่เคยขาย SKU นั้นได้มากสุด ไม่ใช่ E01..E03 ที่อยู่หัวตาราง"""
        winners = {"E09": 30.0, "E07": 20.0, "E05": 10.0}
        df_emp, df_sku, df_hist = make_case(11, 3, hist_for=winners)
        for strategy in ("L3M", "LP"):
            with self.subTest(strategy=strategy):
                got = boxes_by_emp(run(df_emp, df_sku, df_hist, strategy))
                self.assertEqual(
                    set(got), set(winners), f"({strategy}) ได้ {got}"
                )

    def test_s4_no_history_is_decided_by_emp_id(self):
        """ไม่มีใครมีประวัติ → ต้องได้ผลเดิมทุกครั้ง และไม่ขึ้นกับลำดับแถวในทะเบียน"""
        df_emp, df_sku, df_hist = make_case(8, 3)
        first = boxes_by_emp(run(df_emp, df_sku, df_hist, "L3M"))
        self.assertEqual(sum(first.values()), 3)
        self.assertTrue(all(v == 1 for v in first.values()), first)

        # สลับลำดับแถวในทะเบียน — ผลต้องเหมือนเดิมเป๊ะ
        df_emp_rev = df_emp.iloc[::-1].reset_index(drop=True)
        second = boxes_by_emp(run(df_emp_rev, df_sku, df_hist, "L3M"))
        self.assertEqual(first, second, "ลำดับในทะเบียนต้องไม่มีผลกับใครได้หีบ")

    def test_s5_normal_targets_untouched(self):
        """เป้า >= จำนวนคน → กฎนี้ต้องไม่ทำงาน (ยังกองตามประวัติได้ตามปกติ)"""
        df_emp, df_sku, df_hist = make_case(
            4, 100, hist_for={"E01": 50, "E02": 5, "E03": 5, "E04": 5}
        )
        got = boxes_by_emp(run(df_emp, df_sku, df_hist, "L3M"))
        self.assertEqual(sum(got.values()), 100)
        self.assertTrue(
            max(got.values()) > 1,
            f"เป้าใหญ่ต้องยังกระจายตามน้ำหนักประวัติได้ — ได้ {got}",
        )

    def test_s6_locked_cell_still_wins(self):
        """ช่องที่ผู้ใช้ล็อกไว้ต้องได้ค่าตามที่ล็อก แม้เกินเพดาน 1 หีบ"""
        df_emp, df_sku, df_hist = make_case(9, 5, hist_for={"E08": 12, "E06": 6})
        got = boxes_by_emp(
            run(
                df_emp,
                df_sku,
                df_hist,
                "L3M",
                locked_edits=[{"emp_id": "E02", "sku": SKU, "locked_boxes": 3}],
            )
        )
        self.assertEqual(got.get("E02"), 3, f"ล็อกต้องอยู่ครบ — ได้ {got}")
        self.assertEqual(sum(got.values()), 5, f"ผลรวมต้องตรงเป้า — ได้ {got}")
        others = {e: v for e, v in got.items() if e != "E02"}
        self.assertTrue(
            all(v == 1 for v in others.values()),
            f"ที่เหลือต้องคนละ 1 หีบ — ได้ {others}",
        )

    def test_s8_money_target_cannot_pile_boxes_on_one_person(self):
        """
        เคสที่ของเดิมพังจริง — คนที่เป้าเงินสูงลิ่วดูดหีบไปเกือบหมด

        LP ตัดสินด้วยส่วนต่างเงินรายคนล้วน ๆ พอ SKU เป้าเล็กเป็นทางเดียวที่จะเติมเงิน
        ให้คนที่เป้าสูง มันจึงยกไปกองที่คนนั้น · วัดกับโค้ดก่อนแก้: E01 ได้ 4 จาก 5 หีบ
        """
        emps = [f"E{i + 1:02d}" for i in range(11)]
        df_emp = pd.DataFrame(
            [
                {"emp_id": e, "yellow_target": 300000.0 if e == "E01" else 900.0}
                for e in emps
            ]
        )
        df_sku = pd.DataFrame(
            [{"sku": SKU, "supervisor_target_boxes": 5, "price_per_box": 900.0}]
        )
        df_hist = pd.DataFrame(
            [{"emp_id": e, "sku": SKU, "hist_boxes": 0.0} for e in emps]
        )
        got = boxes_by_emp(allocate_boxes(df_emp, df_sku, df_hist, strategy="L3M"))
        self.assertEqual(sum(got.values()), 5, f"ผลรวมต้องตรงเป้า — ได้ {got}")
        self.assertEqual(
            max(got.values()),
            1,
            f"เป้าเงินต้องไม่ทำให้หีบไปกองที่คนเดียว — ได้ {got}",
        )
        self.assertEqual(len(got), 5, f"ต้องได้ 5 คน คนละ 1 หีบ — ได้ {got}")

    def test_s7_target_one_box_goes_to_one_person(self):
        """เป้า 1 หีบ ทีม 6 คน → คนเดียวได้ 1 หีบ ไม่ใช่ 0 ทุกคนหรือแตกเป็นเศษ"""
        df_emp, df_sku, df_hist = make_case(6, 1, hist_for={"E04": 9})
        got = boxes_by_emp(run(df_emp, df_sku, df_hist, "L3M"))
        self.assertEqual(got, {"E04": 1}, f"ได้ {got}")


if __name__ == "__main__":
    unittest.main()
