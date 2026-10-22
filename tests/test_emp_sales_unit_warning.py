"""
QA 21 ต.ค. 2026 Q7 — คนย้ายจากทีมรถเงินสดไปทีมเครดิต (หรือกลับกัน) แถวเป้าใน Target Sun ยังเป็นหน่วยเดิม
แต่เป้าเงินขั้นที่ 2 คิดด้วยราคาหน่วยของทีม → เตือนในขั้นที่ 1 (เตือนอย่างเดียว ไม่แตะตัวเลข)
"""

from __future__ import annotations

import os
import sys
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services.employees import emp_sales_unit_mismatch_warning  # noqa: E402

EMPS = [{"emp_id": "D401", "emp_name": "หนึ่ง"}, {"emp_id": "D409", "emp_name": "ย้ายมา"}]


def _grain(rows):
    return pd.DataFrame(rows, columns=["emp_id", "sku", "salestype"])


class EmpSalesUnitWarningTest(unittest.TestCase):
    def test_moved_cash_employee_in_credit_team_is_listed(self):
        g = _grain([("D401", "A", "S"), ("D409", "A", "C"), ("D409", "B", "C")])
        w = emp_sales_unit_mismatch_warning("S", g, EMPS)
        self.assertIsNotNone(w)
        self.assertEqual(w["type"], "emp_sales_unit_mismatch")
        self.assertIn("D409 (ย้ายมา) = หน่วยรถเงินสด", w["message"])
        self.assertIn("ทีมนี้หน่วยเครดิต", w["message"])
        self.assertNotIn("D401", w["message"])

    def test_all_same_unit_no_warning(self):
        g = _grain([("D401", "A", "S"), ("D409", "A", "S1")])
        self.assertIsNone(emp_sales_unit_mismatch_warning("S", g, EMPS))

    def test_unknown_team_unit_or_missing_data_no_warning(self):
        g = _grain([("D409", "A", "C")])
        self.assertIsNone(emp_sales_unit_mismatch_warning("", g, EMPS))
        self.assertIsNone(emp_sales_unit_mismatch_warning("S", None, EMPS))
        self.assertIsNone(emp_sales_unit_mismatch_warning("S", pd.DataFrame({"emp_id": ["D409"]}), EMPS))

    def test_people_outside_team_ignored(self):
        g = _grain([("D777", "A", "C")])
        self.assertIsNone(emp_sales_unit_mismatch_warning("S", g, EMPS))


if __name__ == "__main__":
    unittest.main()
