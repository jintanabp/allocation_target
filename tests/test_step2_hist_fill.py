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

    def test_four_buttons_exist(self):
        for key in ("3m", "6m", "12m", "ly"):
            self.assertIn(f"fillYellowFromHistory('{key}')", self.html)
        self.assertIn('id="step2HistFillNote"', self.html)

    def test_sources_map_to_backend_fields(self):
        for field in ("hist_avg_3m", "hist_avg_6m", "hist_avg_12m", "ly_sales"):
            self.assertIn(f'field: "{field}"', self.js)

    def test_no_history_is_not_auto_filled(self):
        """ผู้ใช้เลือก (5 ต.ค. 2026): คนไม่มีประวัติได้ 0 แล้วกรอกเอง — ห้ามแบ่งเท่าให้"""
        i = self.js.index("async function fillYellowFromHistory")
        body = self.js[i: i + 3000]
        self.assertIn("AppLogic.shareTotalByWeights", body)
        self.assertNotIn("spreadIncrease", body)

    def test_run_asks_before_allocating_with_zero_rows(self):
        i = self.js.index("async function runOptimization")
        body = self.js[i: i + 4000]
        self.assertIn("_histFillNoHistoryNames()", body)
        self.assertIn("_confirmDialog", body)

    def test_every_reset_path_clears_the_source(self):
        # รีเซ็ต Target Sun / โหลดข้อมูลใหม่ / เริ่มใหม่ / กู้ snapshot / กู้ร่าง
        self.assertGreaterEqual(self.js.count("S.yellowSource = null;"), 5)


if __name__ == "__main__":
    unittest.main()
