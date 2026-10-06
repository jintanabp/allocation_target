"""
ปุ่ม「ตั้งเป้าตามประวัติ」ใน Step 2 (5 ต.ค. 2026)

ผู้ใช้บางกลุ่มไม่อยากตั้งเป้าเงินเอง — กดปุ่มแล้วเป้ารายคนกลายเป็นสัดส่วนของยอดขาย
ย้อนหลัง (3/6/12 เดือน หรือเดือนเดียวกันปีที่แล้ว) ยอดรวมยังเท่าเดิม แล้ว Step 3 กระจายหีบ
ตามปกติ ขั้นตอนของแอปไม่เปลี่ยน

คณิตการแบ่งยอด (ปัดสตางค์ ผลรวมตรงพอดี) เทสต์ด้วย node ที่ tests/logic.test.js
ตรงนี้ตรวจข้อมูลที่ backend ส่งให้ และกติกาฝั่งหน้าจอที่ผู้ใช้ตัดสินไว้
"""

from __future__ import annotations

import os
import sys
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services.employees import avg_monthly_amount_by_emp  # noqa: E402
from backend.services.wh_split import expand_employee_rows  # noqa: E402


class TestAvgMonthlyAmount(unittest.TestCase):
    def test_sums_per_emp_then_divides_by_months(self):
        df = pd.DataFrame({
            "emp_id": ["E1", "E1 ", "E2"],
            "sku": ["A", "B", "A"],
            "hist_boxes": [1, 1, 1],
            "hist_amount": [600.0, 600.0, 120.0],
        })
        self.assertEqual(avg_monthly_amount_by_emp(df, 6), {"E1": 200.0, "E2": 20.0})
        self.assertEqual(avg_monthly_amount_by_emp(df, 12), {"E1": 100.0, "E2": 10.0})

    def test_empty_or_missing_is_empty(self):
        self.assertEqual(avg_monthly_amount_by_emp(None, 6), {})
        self.assertEqual(avg_monthly_amount_by_emp(pd.DataFrame(), 6), {})
        self.assertEqual(avg_monthly_amount_by_emp(pd.DataFrame({"emp_id": ["E1"]}), 6), {})

    def test_bad_amount_counts_as_zero(self):
        df = pd.DataFrame({"emp_id": ["E1", "E1"], "hist_amount": ["x", 300]})
        self.assertEqual(avg_monthly_amount_by_emp(df, 3), {"E1": 100.0})


class TestWarehouseSplitKeepsTotals(unittest.TestCase):
    """พนักงานหลายคลังถูกแตกแถว — ค่าเฉลี่ย 6/12 เดือนต้องแตกตามด้วย และผลรวมต่อคนต้องเท่าเดิม"""

    def test_6m_12m_split_and_sum_back(self):
        tga = pd.DataFrame({
            "emp_id": ["E1", "E1"],
            "sku": ["A", "A"],
            "qty": [30, 10],
            "warehouse_code": ["W1", "W2"],
        })
        rows = [{
            "emp_id": "E1", "target_sun": 4000.0, "ly_sales": 100.0,
            "hist_avg_3m": 400.0, "hist_avg_6m": 600.0, "hist_avg_12m": 1200.0,
        }]
        out = expand_employee_rows(rows, tga, {"A": 100.0})
        self.assertEqual(len(out), 2)
        self.assertAlmostEqual(sum(r["hist_avg_6m"] for r in out), 600.0, places=2)
        self.assertAlmostEqual(sum(r["hist_avg_12m"] for r in out), 1200.0, places=2)
        by_wh = {r["warehouse_code"]: r for r in out}
        self.assertAlmostEqual(by_wh["W1"]["hist_avg_6m"], 450.0, places=2)

    def test_6m_split_uses_own_warehouse_weights(self):
        """7.15: คลังที่ 3 เดือนหลังไม่มียอด แต่ 6/12 เดือนมี ต้องไม่ได้ 0 (เดิม = "ไม่มีประวัติ")"""
        tga = pd.DataFrame({"emp_id": ["E1", "E1"], "sku": ["A", "A"], "qty": [30, 10],
                            "warehouse_code": ["W1", "W2"]})
        rows = [{"emp_id": "E1", "target_sun": 4000.0, "ly_sales": 0.0,
                 "hist_avg_3m": 400.0, "hist_avg_6m": 600.0, "hist_avg_12m": 1200.0}]
        avg3 = {("E1", "W1"): 400.0, ("E1", "W2"): 0.0}
        avg6 = {("E1", "W1"): 300.0, ("E1", "W2"): 100.0}
        old = {r["warehouse_code"]: r for r in expand_employee_rows(
            rows, tga, {"A": 100.0}, avg3_amount_by_emp_wh=avg3)}
        self.assertEqual(old["W2"]["hist_avg_6m"], 0.0)  # พฤติกรรมเดิมเมื่อไม่มีตัวเลขแยกคลัง 6 เดือน
        new = {r["warehouse_code"]: r for r in expand_employee_rows(
            rows, tga, {"A": 100.0}, avg3_amount_by_emp_wh=avg3,
            avg6_amount_by_emp_wh=avg6, avg12_amount_by_emp_wh={})}
        self.assertAlmostEqual(new["W1"]["hist_avg_6m"], 450.0, places=2)
        self.assertAlmostEqual(new["W2"]["hist_avg_6m"], 150.0, places=2)
        # 12 เดือนไม่มีตัวเลขแยกคลัง → ถอยไปใช้น้ำหนัก 3 เดือน ผลรวมยังตรง
        self.assertAlmostEqual(new["W1"]["hist_avg_12m"] + new["W2"]["hist_avg_12m"], 1200.0, places=2)
        self.assertAlmostEqual(new["W1"]["hist_avg_12m"], 1200.0, places=2)


class TestHistFillModeSurvivesReload(unittest.TestCase):
    """7.15: ร่าง/ผลบน server จำโหมด「ตั้งตามประวัติ」 — โหลดกลับแล้วป้ายเตือนไม่หาย"""

    def test_store_keeps_known_source_only(self):
        from backend.services import allocation_store as st

        base = {"sup_id": "SLX", "target_month": 11, "target_year": 2026, "allocations": []}
        norm = st._validate_body
        self.assertEqual(norm({**base, "yellow_source": "6m"}).get("yellow_source"), "6m")
        self.assertNotIn("yellow_source", norm({**base, "yellow_source": "evil"}))
        self.assertNotIn("yellow_source", norm(base))

    def test_frontend_saves_and_restores(self):
        with open(os.path.join(REPO, "frontend", "app.js"), encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn("yellowSource: S.yellowSource || null", js)
        self.assertIn("_restoreHistFillMode(draftData.yellowSource)", js)
        self.assertIn("_restoreHistFillMode(snap.yellow_source)", js)
        self.assertIn("body.yellow_source = S.yellowSource || null", js)
        # ต้องคืนหลังล้างเสมอ (ล้างเหตุผลอัตโนมัติก่อน แล้วค่อยคืนโหมด)
        for tag in ("draftData.yellowSource", "snap.yellow_source"):
            i = js.index(f"_restoreHistFillMode({tag})")
            self.assertGreater(i, js.rindex("_clearHistFillMode();", 0, i))
            self.assertLess(i - js.rindex("_clearHistFillMode();", 0, i), 60)


class TestDemoHasHistoryWindows(unittest.TestCase):
    def test_demo_rows_carry_6m_12m(self):
        from backend.services.demo_data import demo_employees

        for e in demo_employees():
            self.assertGreater(e["hist_avg_6m"], 0)
            self.assertGreater(e["hist_avg_12m"], 0)


class TestFrontendRules(unittest.TestCase):
    """กติกาที่ผู้ใช้ตัดสินไว้ — ตรวจจากซอร์สเพราะเป็นโค้ดหน้าจอ"""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO, "frontend", "app.js"), encoding="utf-8") as fh:
            cls.js = fh.read()
        with open(os.path.join(REPO, "frontend", "index.html"), encoding="utf-8") as fh:
            cls.html = fh.read()

    # ตรรกะการแบ่ง/คนไม่มีประวัติ/คลิกไม่ล็อก/รวมภาค → tests/js/hist_fill.test.js · ทางรีเซ็ต/เป้าสด/ผลตรวจเป้าที่มาช้า
    # → tests/js/step2_reset_and_drift.test.js — รันฟังก์ชันจริงทั้งคู่ (8.5)
    # ที่เหลือข้างล่างคือสิ่งที่ตรวจด้วยการรันไม่ได้ง่าย ๆ (ปุ่มใน HTML, ชื่อฟิลด์, ลำดับใน runOptimization)

    def test_four_buttons_exist(self):
        for key in ("3m", "6m", "12m", "ly"):
            self.assertIn(f"fillYellowFromHistory('{key}')", self.html)
        self.assertIn('id="step2HistFillNote"', self.html)

    def test_sources_map_to_backend_fields(self):
        for field in ("hist_avg_3m", "hist_avg_6m", "hist_avg_12m", "ly_sales"):
            self.assertIn(f'field: "{field}"', self.js)

    def test_run_asks_before_allocating_with_zero_rows(self):
        i = self.js.index("async function runOptimization")
        body = self.js[i: i + 4000]
        self.assertIn("_histFillNoHistoryNames()", body)
        self.assertIn("_confirmDialog", body)

    def test_reason_and_bui_reset_when_team_or_period_changes(self):
        self.assertIn("S._step2CtxKey !== _step2Ctx", self.js)
        i = self.js.index("S._step2CtxKey !== _step2Ctx")
        self.assertIn('S.negGrowthReason = "";', self.js[i: i + 200])
        self.assertIn("S.buiDeductions = {};", self.js[i: i + 200])


class TestCalendarYearCacheMerge(unittest.TestCase):
    """ผลตรวจ 5 ต.ค. 2026 ข้อ 7: โหลดงวดอื่นต้องไม่ลบสินค้าของงวดก่อนออกจากแคชยอดรายปี"""

    def test_other_period_skus_survive_and_fetched_skus_are_replaced(self):
        import tempfile

        from backend.core.allocation_checks import skus_no_sales_cy_ly
        from backend.services.employees import _merge_calendar_year_cache

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "hist_cy.csv")
            # งวด 10 โหลดก่อน: A ขายได้, B ขายได้
            pd.DataFrame([
                {"emp_id": "E1", "sku": "A", "hist_boxes": 5, "hist_amount": 500},
                {"emp_id": "E1", "sku": "B", "hist_boxes": 2, "hist_amount": 200},
            ]).to_csv(path, index=False)
            # งวด 11 ดึงแค่ B กับ C — B ไม่มียอดแล้ว, C มียอด
            fresh = pd.DataFrame([{"emp_id": "E1", "sku": "C", "hist_boxes": 1, "hist_amount": 100}])
            out = _merge_calendar_year_cache(path, fresh, {"B", "C"})
            self.assertEqual(sorted(out["sku"].astype(str)), ["A", "C"])
            out.to_csv(path, index=False)

            from unittest.mock import patch

            with patch("backend.core.allocation_checks.hist_calendar_year_cache_path", return_value=path):
                # กลับไปทำงวด 10: A ต้องไม่กลายเป็น "สินค้าใหม่"
                self.assertEqual(skus_no_sales_cy_ly("SLX", 2026, ["A", "B"]), {"B"})

    def test_unreadable_old_file_uses_fresh_only(self):
        import tempfile

        from backend.services.employees import _merge_calendar_year_cache

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "hist_cy.csv")
            with open(path, "wb") as f:
                f.write(b"\xff\xfe\x00broken")
            fresh = pd.DataFrame([{"emp_id": "E1", "sku": "C", "hist_boxes": 1, "hist_amount": 100}])
            out = _merge_calendar_year_cache(path, fresh, {"C"})
            self.assertEqual(list(out["sku"]), ["C"])


if __name__ == "__main__":
    unittest.main()
