"""
history_only=True — โหมดทดลอง "กระจายจากประวัติ ไม่ต้องตั้งเป้าเงิน" (แผน
majestic-twirling-lynx) ข้ามชั้นเงินทั้งหมด (_lp_optimize + _greedy_revenue_balancer)
กระจายด้วย _proportional ตรง ๆ ตามสัดส่วนประวัติล้วน ๆ — yellow_target ที่ส่งมาต้อง
ถูกละเว้นโดยสมบูรณ์ไม่ว่าค่าจะเป็นอะไร

ค่าเริ่มต้น history_only=False ต้องให้ผลเหมือนเดิมทุกประการ (ยืนยันแยกด้วย
scripts/golden_allocation.py compare ก่อน commit ด้วย —ดูแผน)
"""

from __future__ import annotations

import os
import sys
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.OR_engine import allocate_boxes  # noqa: E402
from backend.schemas import OptimizeRequest  # noqa: E402


def _sku(rows):
    return pd.DataFrame(rows)


def _hist(rows):
    return pd.DataFrame(rows)


class TestHistoryOnlyIgnoresYellowTarget(unittest.TestCase):
    """yellow_target ต้องไม่มีผลต่อผลลัพธ์เลยเมื่อ history_only=True"""

    def _inputs(self):
        df_sku = _sku([
            {"sku": "A", "supervisor_target_boxes": 10, "price_per_box": 100.0},
            {"sku": "B", "supervisor_target_boxes": 6, "price_per_box": 50.0},
        ])
        df_hist = _hist([
            {"emp_id": "E1", "sku": "A", "hist_boxes": 30},
            {"emp_id": "E2", "sku": "A", "hist_boxes": 10},
            {"emp_id": "E1", "sku": "B", "hist_boxes": 5},
            {"emp_id": "E2", "sku": "B", "hist_boxes": 15},
        ])
        return df_sku, df_hist

    def test_wildly_different_yellow_target_gives_identical_result(self):
        df_sku, df_hist = self._inputs()
        df_emp_low = pd.DataFrame(
            [{"emp_id": "E1", "yellow_target": 1.0}, {"emp_id": "E2", "yellow_target": 1.0}]
        )
        df_emp_skewed = pd.DataFrame(
            [{"emp_id": "E1", "yellow_target": 999_999.0}, {"emp_id": "E2", "yellow_target": 1.0}]
        )
        for strat in ("L3M", "L6M", "LY", "LP"):
            out_low = allocate_boxes(
                df_emp_low, df_sku, df_hist, strategy=strat, history_only=True
            )
            out_skewed = allocate_boxes(
                df_emp_skewed, df_sku, df_hist, strategy=strat, history_only=True
            )
            a = out_low.set_index(["emp_id", "sku"])["allocated_boxes"].sort_index()
            b = out_skewed.set_index(["emp_id", "sku"])["allocated_boxes"].sort_index()
            pd.testing.assert_series_equal(a, b, check_names=False, obj=f"strategy={strat}")

    def test_history_only_changes_the_answer_vs_normal_mode_when_targets_are_skewed(self):
        """
        ต้องพิสูจน์ว่า flag นี้ "ทำอะไรจริง" ไม่ใช่แค่ไม่ error — เทียบกับโหมดปกติ

        ใช้ทีมใหญ่ขึ้น (5 คน) เพราะ LP มีรั้ว ±20% ยึดกับ baseline ประวัติเสมอ
        (ดู _DEFAULT_HIST_BAND_PCT) ทีมเล็ก 2 คนบางเคสปัดเศษได้เลขเดียวกันพอดีทั้งที่
        อยู่กันคนละโหมด ต้องให้มีที่ให้ LP ขยับได้จริงถึงจะเห็นผลต่าง
        """
        df_sku = _sku([{"sku": "A", "supervisor_target_boxes": 100, "price_per_box": 100.0}])
        emps = [f"E{i}" for i in range(1, 6)]
        df_hist = _hist([{"emp_id": e, "sku": "A", "hist_boxes": 20} for e in emps])
        df_emp = pd.DataFrame(
            [{"emp_id": "E1", "yellow_target": 999_999.0}]
            + [{"emp_id": e, "yellow_target": 1.0} for e in emps[1:]]
        )
        out_normal = allocate_boxes(df_emp, df_sku, df_hist, strategy="L3M", history_only=False)
        out_history_only = allocate_boxes(df_emp, df_sku, df_hist, strategy="L3M", history_only=True)
        normal_e1 = int(out_normal.loc[out_normal["emp_id"] == "E1", "allocated_boxes"].sum())
        ho_e1 = int(out_history_only.loc[out_history_only["emp_id"] == "E1", "allocated_boxes"].sum())
        self.assertNotEqual(
            normal_e1, ho_e1,
            "เป้าเงินเบี้ยวขนาดนี้ต้องทำให้สองโหมดกระจายต่างกัน ไม่งั้น flag ไม่มีผลจริง",
        )


class TestHistoryOnlyInvariantsHold(unittest.TestCase):
    def setUp(self):
        self.df_sku = _sku([
            {"sku": "A", "supervisor_target_boxes": 13, "price_per_box": 100.0},
            {"sku": "B", "supervisor_target_boxes": 7, "price_per_box": 50.0},
        ])
        self.df_hist = _hist([
            {"emp_id": "E1", "sku": "A", "hist_boxes": 30},
            {"emp_id": "E2", "sku": "A", "hist_boxes": 10},
            {"emp_id": "E1", "sku": "B", "hist_boxes": 5},
            {"emp_id": "E2", "sku": "B", "hist_boxes": 15},
        ])
        self.df_emp = pd.DataFrame(
            [{"emp_id": "E1", "yellow_target": 0.0}, {"emp_id": "E2", "yellow_target": 0.0}]
        )

    def test_i1_sum_per_sku_still_matches_target_exactly(self):
        out = allocate_boxes(
            self.df_emp, self.df_sku, self.df_hist, strategy="L3M", history_only=True
        )
        for sku, target in [("A", 13), ("B", 7)]:
            got = int(out.loc[out["sku"] == sku, "allocated_boxes"].sum())
            self.assertEqual(got, target, f"sku {sku}: expected {target}, got {got}")

    def test_locked_edits_are_still_honored(self):
        out = allocate_boxes(
            self.df_emp, self.df_sku, self.df_hist, strategy="L3M", history_only=True,
            locked_edits=[{"emp_id": "E1", "sku": "A", "locked_boxes": 4}],
        )
        locked = out[(out["emp_id"] == "E1") & (out["sku"] == "A")]["allocated_boxes"]
        self.assertEqual(int(locked.iloc[0]), 4)
        self.assertEqual(int(out.loc[out["sku"] == "A", "allocated_boxes"].sum()), 13)

    def test_never_sold_zero_rule_still_applies(self):
        # ยอด 12 เดือนของ E2 ต้องสูงพอไม่ให้ชน push_multiple (default 5x/เดือน) ไม่งั้น
        # SKU จะถูกตีความเป็น "สินค้าดันเป้า" (เฉลี่ยทุกคน) แทนกติกาไม่เคยขาย=เป้า 0 ปกติ
        df_sold_12m = pd.DataFrame([{"emp_id": "E2", "sku": "A", "hist_boxes": 120}])  # E1 ไม่เคยขาย A
        out = allocate_boxes(
            self.df_emp, self.df_sku, self.df_hist, strategy="L3M", history_only=True,
            df_sold_12m=df_sold_12m,
        )
        e1_a = out[(out["emp_id"] == "E1") & (out["sku"] == "A")]["allocated_boxes"]
        self.assertEqual(int(e1_a.iloc[0]), 0)
        self.assertEqual(int(out.loc[out["sku"] == "A", "allocated_boxes"].sum()), 13)


class TestHistoryOnlyDoesNotAffectAlreadyHistoryDrivenStrategies(unittest.TestCase):
    """EVEN/PUSH ข้าม LP อยู่แล้วโดยดีไซน์เดิม — history_only ต้องไม่เปลี่ยนอะไรเลยสำหรับสองตัวนี้"""

    def test_push_strategy_unaffected_by_the_flag(self):
        df_sku = _sku([{"sku": "A", "supervisor_target_boxes": 10, "price_per_box": 100.0}])
        df_hist = _hist([
            {"emp_id": "E1", "sku": "A", "hist_boxes": 30},
            {"emp_id": "E2", "sku": "A", "hist_boxes": 10},
        ])
        df_emp = pd.DataFrame(
            [{"emp_id": "E1", "yellow_target": 50000.0}, {"emp_id": "E2", "yellow_target": 50000.0}]
        )
        out_false = allocate_boxes(df_emp, df_sku, df_hist, strategy="PUSH", history_only=False)
        out_true = allocate_boxes(df_emp, df_sku, df_hist, strategy="PUSH", history_only=True)
        a = out_false.set_index(["emp_id", "sku"])["allocated_boxes"].sort_index()
        b = out_true.set_index(["emp_id", "sku"])["allocated_boxes"].sort_index()
        pd.testing.assert_series_equal(a, b, check_names=False)


class TestHistoryOnlySchemaAndWiring(unittest.TestCase):
    def test_schema_defaults_to_false(self):
        req = OptimizeRequest(yellowTargets=[{"emp_id": "E1", "yellow_target": 1.0}])
        self.assertFalse(req.history_only)

    def test_schema_accepts_true(self):
        req = OptimizeRequest(
            yellowTargets=[{"emp_id": "E1", "yellow_target": 1.0}], history_only=True
        )
        self.assertTrue(req.history_only)

    def test_service_passes_history_only_to_both_allocate_boxes_call_sites(self):
        import inspect

        from backend.services import optimize as opt

        src = inspect.getsource(opt.run_optimization_service)
        self.assertEqual(src.count("history_only=bool(req.history_only)"), 2)

    def test_post_merge_revenue_balance_is_skipped_in_history_only_mode(self):
        import inspect

        from backend.services import optimize as opt

        src = inspect.getsource(opt.run_optimization_service)
        self.assertIn(
            "req.tiered_allocation and not req.history_only", src
        )

    def test_yellow_target_gate_is_not_applied_in_history_only_mode(self):
        """
        พบระหว่างทดสอบมือ (sandbox): ด่านเดิม "ไม่มีพนักงานที่มีเป้าเงิน > 0" กรองคนออก
        ก่อนถึง history_only เสียอีก — ทีมที่ไม่เคยตั้งเป้าเงินเลย (ทุกคน 0) กระจายไม่ได้
        ทั้งที่ history_only ตั้งใจให้ไม่สนใจเป้าเงินอยู่แล้ว ต้องข้ามด่านนี้ด้วย
        """
        import inspect

        from backend.services import optimize as opt

        src = inspect.getsource(opt.run_optimization_service)
        self.assertIn("df_emp_targets = df_all_targets.copy()", src)
        self.assertIn('getattr(req, "history_only", False)', src)


if __name__ == "__main__":
    unittest.main()
