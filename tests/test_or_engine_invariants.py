"""
กฎคงที่ของเครื่องกระจายหีบ (allocation invariants)

backend/OR_engine.py import แค่ pandas + logging — ไม่แตะไฟล์ ไม่แตะเน็ต ไม่แตะ DB
เทสต์ในนี้จึงรันในหน่วยความจำล้วน ปลอดภัยกับ production ทุกกรณี

กฎที่ต้องจริงเสมอไม่ว่ากลยุทธ์ไหน:
  I1  ผลรวมหีบต่อ SKU == supervisor_target_boxes
  I2  เซลล์ที่ล็อกออกมาตรงค่าที่ล็อกเป๊ะ
  I3  หีบเป็นจำนวนเต็มและไม่ติดลบ
  I4  force_min_one → ทุกคนได้ >= 1 หีบใน SKU ที่เป้า >= จำนวนคน
  I5  เซลล์ที่ baseline > 0 อยู่ในรั้วที่กำหนด
"""

from __future__ import annotations

import itertools
import logging
import math
import os
import sys
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.OR_engine import allocate_boxes  # noqa: E402
from backend.core.allocation_checks import validate_allocation_vs_targets  # noqa: E402

logging.disable(logging.CRITICAL)

STRATEGIES = ("L3M", "L6M", "LY", "EVEN", "PUSH", "LP")


def make_case(n_emp: int = 4, targets=(10, 6, 25), hist_spread: bool = True):
    """ชุดข้อมูลสังเคราะห์ — พนักงาน n คน, SKU ตามจำนวน targets"""
    emps = [f"E{i+1}" for i in range(n_emp)]
    df_emp = pd.DataFrame(
        [{"emp_id": e, "yellow_target": 50000.0 + i * 1000} for i, e in enumerate(emps)]
    )
    df_sku = pd.DataFrame(
        [
            {
                "sku": f"{100000 + i}",
                "supervisor_target_boxes": t,
                "price_per_box": 1000.0 * (i + 1),
            }
            for i, t in enumerate(targets)
        ]
    )
    rows = []
    for i, e in enumerate(emps):
        for j, _t in enumerate(targets):
            boxes = (i + 1) * (j + 2) if hist_spread else 5
            rows.append(
                {"emp_id": e, "sku": f"{100000 + j}", "hist_boxes": float(boxes)}
            )
    return df_emp, df_sku, pd.DataFrame(rows)


def sku_targets(df_sku) -> dict[str, int]:
    return {
        str(r["sku"]).strip(): int(round(float(r["supervisor_target_boxes"])))
        for _, r in df_sku.iterrows()
    }


class TestAllocationInvariants(unittest.TestCase):
    def assert_conservation(self, out, df_sku, msg=""):
        """I1 — ผลรวมต่อ SKU ต้องตรงเป้าเสมอ"""
        got = out.groupby("sku")["allocated_boxes"].sum().to_dict()
        for sku, target in sku_targets(df_sku).items():
            self.assertEqual(
                int(got.get(sku, 0)),
                target,
                f"{msg} SKU {sku}: กระจายรวม {int(got.get(sku, 0))} หีบ แต่เป้า {target} หีบ",
            )

    def test_I1_conservation_all_strategies(self):
        df_emp, df_sku, df_hist = make_case()
        for strat, tiered, fmo in itertools.product(STRATEGIES, (False, True), (False, True)):
            with self.subTest(strategy=strat, tiered=tiered, force_min_one=fmo):
                out = allocate_boxes(
                    df_emp, df_sku, df_hist,
                    strategy=strat, tiered_allocation=tiered, force_min_one=fmo,
                )
                self.assert_conservation(out, df_sku, f"[{strat} tiered={tiered} fmo={fmo}]")

    def test_I3_non_negative_integers(self):
        df_emp, df_sku, df_hist = make_case()
        for strat in STRATEGIES:
            with self.subTest(strategy=strat):
                out = allocate_boxes(df_emp, df_sku, df_hist, strategy=strat)
                vals = out["allocated_boxes"].tolist()
                self.assertTrue(all(float(v).is_integer() for v in vals), "ต้องเป็นจำนวนเต็ม")
                self.assertTrue(all(v >= 0 for v in vals), "ห้ามติดลบ")

    def test_I2_locked_cells_preserved(self):
        df_emp, df_sku, df_hist = make_case(n_emp=4, targets=(20,))
        locks = [{"emp_id": "E2", "sku": "100000", "locked_boxes": 7}]
        for strat in STRATEGIES:
            with self.subTest(strategy=strat):
                out = allocate_boxes(
                    df_emp, df_sku, df_hist, strategy=strat, locked_edits=locks
                )
                cell = out[(out.emp_id == "E2") & (out.sku == "100000")]
                self.assertEqual(int(cell["allocated_boxes"].iloc[0]), 7, "ค่าที่ล็อกต้องไม่ถูกแตะ")
                self.assert_conservation(out, df_sku, f"[{strat}]")

    def test_I4_force_min_one(self):
        # เป้า 20 หีบ / 4 คน → ทุกคนต้องได้อย่างน้อย 1
        df_emp, df_sku, df_hist = make_case(n_emp=4, targets=(20,))
        for strat in STRATEGIES:
            with self.subTest(strategy=strat):
                out = allocate_boxes(
                    df_emp, df_sku, df_hist, strategy=strat, force_min_one=True
                )
                per_emp = out[out.sku == "100000"].set_index("emp_id")["allocated_boxes"]
                for e in df_emp["emp_id"]:
                    self.assertGreaterEqual(
                        int(per_emp.get(e, 0)), 1, f"{e} ต้องได้อย่างน้อย 1 หีบ"
                    )

    def test_validator_agrees_with_engine(self):
        """ตัวตรวจของระบบต้องไม่ฟ้อง เมื่อกฎ I1 ผ่าน"""
        df_emp, df_sku, df_hist = make_case()
        for strat in STRATEGIES:
            with self.subTest(strategy=strat):
                out = allocate_boxes(df_emp, df_sku, df_hist, strategy=strat)
                self.assertEqual(validate_allocation_vs_targets(out, df_sku), [])


class TestRegressionPerFinding(unittest.TestCase):
    """เทสต์เจาะรายข้อจากผลตรวจสอบ — ชื่อเทสต์อ้างรหัสข้อ"""

    def test_F3_even_split_with_locks_and_force_min_one(self):
        """
        _enforce_even_skus_on_df: base_box ตัดสินจาก n_emps แต่ไปคูณกับ len(free_emps)

        เกินเป้าเมื่อ locked_sum + len(free_emps) > total_target
        เช่น 10 คน เป้า 10 หีบ ล็อก 3 คน คนละ 3 หีบ (รวม 9) เหลือ free 7 คน
            → 9 + 7×1 = 16 หีบ
        (เคส "ล็อก 6 คน คนละ 1" ลงตัวพอดี 6+4=10 จึงไม่เห็นอาการ)
        """
        emps = [f"E{i+1}" for i in range(10)]
        df_emp = pd.DataFrame([{"emp_id": e, "yellow_target": 10000.0} for e in emps])
        df_sku = pd.DataFrame(
            [{"sku": "900001", "supervisor_target_boxes": 10, "price_per_box": 100.0}]
        )
        df_hist = pd.DataFrame(columns=["emp_id", "sku", "hist_boxes"])
        for n_lock, per in ((3, 3), (2, 4), (4, 2), (6, 1)):
            with self.subTest(locked_emps=n_lock, boxes_each=per):
                locks = [
                    {"emp_id": e, "sku": "900001", "locked_boxes": per}
                    for e in emps[:n_lock]
                ]
                out = allocate_boxes(
                    df_emp, df_sku, df_hist,
                    strategy="EVEN", force_min_one=True, locked_edits=locks,
                    even_new_products=True, new_product_skus={"900001"},
                )
                total = int(out.loc[out.sku == "900001", "allocated_boxes"].sum())
                self.assertEqual(total, 10, f"กระจายรวม {total} หีบ แต่เป้า 10 หีบ")

    def test_F2_locks_exceeding_target_do_not_overshoot(self):
        """ล็อกรวม 15 หีบ แต่เป้า 10 → ต้องไม่ปล่อยผลที่เกินเป้าออกมาเงียบ ๆ"""
        emps = [f"E{i+1}" for i in range(3)]
        df_emp = pd.DataFrame([{"emp_id": e, "yellow_target": 10000.0} for e in emps])
        df_sku = pd.DataFrame(
            [{"sku": "900002", "supervisor_target_boxes": 10, "price_per_box": 100.0}]
        )
        df_hist = pd.DataFrame(columns=["emp_id", "sku", "hist_boxes"])
        locks = [{"emp_id": e, "sku": "900002", "locked_boxes": 5} for e in emps]
        try:
            out = allocate_boxes(
                df_emp, df_sku, df_hist, strategy="L3M", locked_edits=locks
            )
        except ValueError:
            return  # ปฏิเสธตั้งแต่ต้นทาง = พฤติกรรมที่ยอมรับได้
        total = int(out.loc[out.sku == "900002", "allocated_boxes"].sum())
        self.assertEqual(total, 10, f"กระจายรวม {total} หีบ แต่เป้า 10 หีบ")

    def test_F4_pipe_and_dash_in_emp_id_do_not_break_lp(self):
        """emp_id ของพนักงานหลายคลังมี '|' และอาจมี '-' — LP ต้องไม่ตกไป fallback"""
        df_emp = pd.DataFrame([
            {"emp_id": "E001|WH12", "yellow_target": 50000.0},
            {"emp_id": "E002|WH07", "yellow_target": 50000.0},
            {"emp_id": "A-B", "yellow_target": 40000.0},
        ])
        df_sku = pd.DataFrame([
            {"sku": "111111", "supervisor_target_boxes": 10, "price_per_box": 5000.0},
            {"sku": "222222", "supervisor_target_boxes": 6, "price_per_box": 3000.0},
        ])
        df_hist = pd.DataFrame([
            {"emp_id": "E001|WH12", "sku": "111111", "hist_boxes": 30},
            {"emp_id": "E002|WH07", "sku": "111111", "hist_boxes": 20},
            {"emp_id": "A-B", "sku": "111111", "hist_boxes": 10},
            {"emp_id": "E001|WH12", "sku": "222222", "hist_boxes": 12},
            {"emp_id": "E002|WH07", "sku": "222222", "hist_boxes": 8},
            {"emp_id": "A-B", "sku": "222222", "hist_boxes": 5},
        ])
        out = allocate_boxes(df_emp, df_sku, df_hist, strategy="L3M", tiered_allocation=False)
        self.assertFalse(
            out.attrs.get("optimization_fallback"),
            "LP ต้องแก้ได้ ไม่ตกไปใช้ proportional",
        )

    def test_F5_zero_baseline_cap_off_by_default(self):
        """
        ค่าเริ่มต้นของเพดาน baseline=0 คือ "ปิด" — ต้องไม่เผลอเปิดโดยไม่ตั้งใจ

        วัดกับข้อมูลจริง 8 ทีมแล้วพบว่าเปิดแล้วหีบย้ายที่ราว 2-3.5% ของทีม
        จึงเป็นการตัดสินใจเชิงนโยบายที่ต้องเปิดเอง ไม่ใช่ค่าตั้งต้น
        """
        from backend.OR_engine import _zero_baseline_cap_enabled

        self.assertFalse(
            _zero_baseline_cap_enabled(),
            "เพดาน baseline=0 ต้องปิดเป็นค่าเริ่มต้น (เปิดด้วย ALLOC_ZERO_BASELINE_CAP=1)",
        )

    def test_F5_zero_baseline_cell_capped_when_enabled(self):
        """
        เมื่อเปิด ALLOC_ZERO_BASELINE_CAP=1 เซลล์ที่ baseline = 0 ต้องมีเพดานจริง

        ของเดิม LP ไม่ใส่ขอบบนให้เซลล์พวกนี้เลย (`if base <= 0: continue`)
        เหลือแค่ objective เป็นตัวคุม ซึ่งแก้เมื่อไหร่ก็หลุดเมื่อนั้น
        """
        os.environ["ALLOC_ZERO_BASELINE_CAP"] = "1"
        self.addCleanup(os.environ.pop, "ALLOC_ZERO_BASELINE_CAP", None)
        emps = [f"E{i+1}" for i in range(10)]
        # E10 เป้าเงินสูงมาก → ถ้ามีรูจริง LP จะอยากโยนของแพงมาให้
        yt = [20000.0] * 9 + [900000.0]
        df_emp = pd.DataFrame(
            [{"emp_id": e, "yellow_target": t} for e, t in zip(emps, yt)]
        )
        df_sku = pd.DataFrame([
            {"sku": "CHEAP", "supervisor_target_boxes": 100, "price_per_box": 50.0},
            {"sku": "RICH", "supervisor_target_boxes": 5, "price_per_box": 90000.0},
        ])
        rows = [{"emp_id": e, "sku": "CHEAP", "hist_boxes": 10.0} for e in emps]
        # มีแค่ E1/E2 ที่เคยขาย RICH → ที่เหลือ baseline = 0
        rows += [
            {"emp_id": e, "sku": "RICH", "hist_boxes": (3.0 if e in ("E1", "E2") else 0.0)}
            for e in emps
        ]
        out = allocate_boxes(df_emp, df_sku, pd.DataFrame(rows), strategy="L3M")
        rich = out[out.sku == "RICH"].set_index("emp_id")["allocated_boxes"]
        cap = math.ceil(5 / len(emps) * 3.0)
        for e in ("E4", "E5", "E10"):
            self.assertLessEqual(
                int(rich.get(e, 0)), cap,
                f"{e} ไม่เคยขาย RICH แต่ได้ {int(rich.get(e, 0))} หีบ (เพดานควรอยู่ที่ {cap})",
            )
        self.assert_conservation_local(out, df_sku)

    def assert_conservation_local(self, out, df_sku):
        got = out.groupby("sku")["allocated_boxes"].sum().to_dict()
        for sku, target in sku_targets(df_sku).items():
            self.assertEqual(int(got.get(sku, 0)), target, f"SKU {sku} ไม่ตรงเป้า")

    def test_F8_lock_key_whitespace_tolerated(self):
        """ล็อกที่มีช่องว่างหน้า-หลังต้องยังจับคู่ได้ ไม่ใช่ถูกเมินเงียบ ๆ"""
        df_emp, df_sku, df_hist = make_case(n_emp=2, targets=(10,))
        out = allocate_boxes(
            df_emp, df_sku, df_hist, strategy="L3M",
            locked_edits=[{"emp_id": " E1 ", "sku": " 100000 ", "locked_boxes": 7}],
        )
        cell = out[(out.emp_id == "E1") & (out.sku == "100000")]
        self.assertEqual(int(cell["allocated_boxes"].iloc[0]), 7, "ล็อกถูกเมิน")

    def test_F6_non_integral_target_rounds_consistently(self):
        """เป้า 10.7 → ต้องได้ 11 ทุกทาง และตัวตรวจต้องไม่ฟ้อง"""
        df_emp, df_sku, df_hist = make_case(n_emp=2, targets=(10,))
        df_sku = df_sku.copy()
        df_sku["supervisor_target_boxes"] = 10.7
        out = allocate_boxes(df_emp, df_sku, df_hist, strategy="L3M")
        total = int(out.loc[out.sku == "100000", "allocated_boxes"].sum())
        self.assertEqual(total, 11)
        self.assertEqual(validate_allocation_vs_targets(out, df_sku), [])

    def test_F9_duplicate_sku_rows_counted_once(self):
        """SKU เดียวปรากฏสองแถว (คนละแบรนด์) — เป้าต้องไม่ถูกนับซ้ำ"""
        df_emp, _df_sku, df_hist = make_case(n_emp=3, targets=(12,))
        df_sku = pd.DataFrame([
            {"sku": "100000", "supervisor_target_boxes": 12, "price_per_box": 1000.0,
             "brand_name_thai": "แบรนด์ก"},
            {"sku": "100000", "supervisor_target_boxes": 12, "price_per_box": 1000.0,
             "brand_name_thai": "แบรนด์ข"},
        ])
        out = allocate_boxes(df_emp, df_sku, df_hist, strategy="L3M")
        total = int(out.loc[out.sku == "100000", "allocated_boxes"].sum())
        self.assertEqual(total, 12, f"SKU ซ้ำทำให้กระจาย {total} หีบ แทนที่จะเป็น 12")


class TestGreedyBalancerContract(unittest.TestCase):
    """
    F1 (ต้นเหตุ) — _greedy_revenue_balancer คืนเฉพาะแถวที่หีบ > 0

    เซลล์ที่ถูกลดเหลือ 0 จะ "หายไป" จากผลลัพธ์ ฝั่ง multi-strategy ที่ merge กลับ
    ด้วย .get(key, ค่าเดิม) จึงดึงค่าก่อนปรับกลับมา = หีบงอกจากอากาศ
    เทสต์นี้ล็อกสัญญาไว้ว่าผลลัพธ์ต้องครอบคลุมทุกเซลล์ที่ป้อนเข้าไป
    """

    def test_F1_balancer_reports_cells_it_zeroed(self):
        from backend.OR_engine import _greedy_revenue_balancer

        df_emp = pd.DataFrame([
            {"emp_id": "E1", "yellow_target": 1000000.0},
            {"emp_id": "E2", "yellow_target": 1000.0},
        ])
        df_sku = pd.DataFrame(
            [{"sku": "S1", "supervisor_target_boxes": 10, "price_per_box": 1000.0}]
        )
        df_in = pd.DataFrame([
            {"emp_id": "E1", "sku": "S1", "allocated_boxes": 9},
            {"emp_id": "E2", "sku": "S1", "allocated_boxes": 1},
        ])
        out = _greedy_revenue_balancer(
            df_in, df_emp, df_sku, base_map=None, tolerance_baht=1.0
        )
        self.assertEqual(
            int(out["allocated_boxes"].sum()), 10, "ตัวปรับสมดุลต้องไม่เปลี่ยนยอดรวม"
        )
        cells_in = {(r.emp_id, r.sku) for r in df_in.itertuples()}
        cells_out = {(r.emp_id, r.sku) for r in out.itertuples()}
        self.assertEqual(
            cells_in - cells_out, set(),
            "เซลล์ที่ถูกลดเหลือ 0 ต้องยังอยู่ในผลลัพธ์ ไม่งั้นฝั่ง merge จะดึงค่าเดิมกลับมา",
        )


class TestMultiStrategyMerge(unittest.TestCase):
    """F1 — เส้นทางหลายกลยุทธ์: _post_merge_revenue_balance ต้องไม่ทำให้หีบงอก"""

    def test_F1_post_merge_conserves_boxes(self):
        from backend.services.optimize import _post_merge_revenue_balance

        emps = [f"E{i+1}" for i in range(4)]
        df_emp = pd.DataFrame(
            [{"emp_id": e, "yellow_target": 40000.0 + i * 20000} for i, e in enumerate(emps)]
        )
        df_sku = pd.DataFrame([
            {"sku": "A1", "supervisor_target_boxes": 24, "price_per_box": 3000.0,
             "brand_name_thai": "แบรนด์ก"},
            {"sku": "A2", "supervisor_target_boxes": 18, "price_per_box": 1500.0,
             "brand_name_thai": "แบรนด์ก"},
            {"sku": "B1", "supervisor_target_boxes": 30, "price_per_box": 800.0,
             "brand_name_thai": "แบรนด์ข"},
        ])
        hist_rows = []
        for i, e in enumerate(emps):
            for j, s in enumerate(("A1", "A2", "B1")):
                hist_rows.append(
                    {"emp_id": e, "sku": s, "hist_boxes": float((i + 1) * (j + 2))}
                )
        df_hist = pd.DataFrame(hist_rows)

        # กระจายก่อน แล้วค่อยส่งเข้าตัวปรับสมดุลรวมตะกร้า (เหมือน multi-strategy run)
        df_alloc = allocate_boxes(df_emp, df_sku, df_hist, strategy="L3M")
        before = df_alloc.groupby("sku")["allocated_boxes"].sum().to_dict()

        merged = _post_merge_revenue_balance(
            df_alloc,
            df_emp,
            df_sku,
            sku_strategy={"A1": "L3M", "A2": "L3M", "B1": "L6M"},
            hist_by_strategy={"L3M": df_hist, "L6M": df_hist},
            locked_edits_data=[],
            force_min_one=False,
            cap_multiplier=None,
            even_skus=frozenset(),
            tiered_allocation=True,
            tier_pct=0.80,
            revenue_tolerance_baht=1000.0,
        )
        after = merged.groupby("sku")["allocated_boxes"].sum().to_dict()

        for sku, target in sku_targets(df_sku).items():
            self.assertEqual(
                int(after.get(sku, 0)), target,
                f"SKU {sku}: หลังปรับสมดุลได้ {int(after.get(sku, 0))} หีบ "
                f"(ก่อนปรับ {int(before.get(sku, 0))}) แต่เป้า {target} หีบ",
            )


if __name__ == "__main__":
    unittest.main()
