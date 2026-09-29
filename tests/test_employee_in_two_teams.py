"""
กระจายรวมทั้งภาค: รหัสพนักงานเดียวกันใต้สองทีม = หยุดพร้อมบอกชื่อ (ผลตรวจ 28 ก.ย. 2026 §4.1-3)

เดิมเครื่องคำนวณรวมเป็นคนเดียว แถวซ้ำ ยอดเกินเป้าแล้วตีกลับ 409 แบบไม่มีคำอธิบาย
ข้อมูลปกติ (คนเดียวหลายคลังในทีมเดียว) ต้องผ่านเหมือนเดิม
"""

from __future__ import annotations

import inspect
import os
import sys
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from fastapi import HTTPException  # noqa: E402

from backend.services import optimize as opt  # noqa: E402


def _df(rows):
    return pd.DataFrame([{"emp_id": e, "supervisor_code": s, "warehouse_code": w, "yellow_target": 1.0}
                         for e, s, w in rows])


class TestEmployeeInTwoTeams(unittest.TestCase):
    def test_same_code_under_two_teams_is_stopped_with_names(self):
        with self.assertRaises(HTTPException) as ctx:
            opt._reject_employee_in_two_teams(_df([("C513", "SL351", ""), ("C513", "sl393", ""), ("C1", "SL351", "")]))
        d = ctx.exception.detail
        self.assertEqual(d["code"], "employee_in_two_teams")
        self.assertEqual(d["employees"], [{"emp_id": "C513", "teams": ["SL351", "SL393"]}])

    def test_one_person_two_warehouses_in_one_team_passes(self):
        opt._reject_employee_in_two_teams(_df([("C442", "SL397", "R408"), ("C442", "SL397", "R409")]))

    def test_rows_without_team_are_ignored(self):
        opt._reject_employee_in_two_teams(_df([("C1", "", ""), ("C1", "SL1", "")]))

    def test_only_runs_for_region_wide_targets(self):
        src = inspect.getsource(opt)
        i = src.index("_reject_employee_in_two_teams(df_all_targets)")
        self.assertIn("if summed_target:", src[i - 60:i])


if __name__ == "__main__":
    unittest.main()
