"""
รหัสพนักงานซ้ำในทีมเดียว ต้องได้ข้อความบอกสาเหตุ ไม่ใช่ 409 allocation_mismatch (OPEN_ITEMS 7.14 — ผลตรวจ 5 ต.ค. 2026)
"""

from __future__ import annotations

import inspect
import os
import sys
import unittest

import pandas as pd
from fastapi import HTTPException

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import optimize as opt  # noqa: E402


class TestDuplicateEmployeeRows(unittest.TestCase):
    def test_same_emp_same_wh_rejected(self):
        df = pd.DataFrame({"emp_id": ["E1", "E2", "E2 "], "yellow_target": [1, 2, 3],
                           "warehouse_code": [None, "W1", "W1"]})
        with self.assertRaises(HTTPException) as ctx:
            opt._reject_duplicate_employee_rows(df)
        d = ctx.exception.detail
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(d["code"], "duplicate_employee_rows")
        self.assertIn("E2", d["message"])
        self.assertEqual(d["employees"], [{"emp_id": "E2", "warehouse_code": "W1"}])

    def test_same_emp_two_warehouses_ok(self):
        df = pd.DataFrame({"emp_id": ["E1", "E1"], "yellow_target": [1, 2], "warehouse_code": ["W1", "W2"]})
        opt._reject_duplicate_employee_rows(df)

    def test_no_wh_column_duplicates_rejected(self):
        df = pd.DataFrame({"emp_id": ["E1", "E1"], "yellow_target": [1, 2]})
        with self.assertRaises(HTTPException):
            opt._reject_duplicate_employee_rows(df)

    def test_called_in_every_mode_after_two_team_check(self):
        src = inspect.getsource(opt)
        i_two = src.index("_reject_employee_in_two_teams(df_all_targets)")
        i_dup = src.index("_reject_duplicate_employee_rows(df_all_targets)")
        self.assertLess(i_two, i_dup)
        line = src[src.rfind("\n", 0, i_dup) + 1:i_dup]
        self.assertEqual(line.strip(), "", "ต้องไม่อยู่ใต้ if summed_target")
        self.assertEqual(len(line), 4)


if __name__ == "__main__":
    unittest.main()
