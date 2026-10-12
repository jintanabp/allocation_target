"""
ตรวจก่อนใช้จริง 12 ต.ค. 2026 C1 — พนักงานคนเดียวมีเป้าในไฟล์ grain ของหลายทีม
Target Sun ถูกอ่านตามรหัสพนักงาน ไม่กรองทีม → เป้ารวมภาค (ผลบวกไฟล์รายทีม) นับเขาซ้ำ แล้วด่านยอดรวมก็ผ่าน
ต้องหยุดทั้งตอนกระจายรวมเป้าทั้งภาคและตอนส่งเป็นชุด
"""

from __future__ import annotations

import os
import tempfile
import unittest

import pandas as pd
from fastapi import HTTPException

from backend.services import lakehouse as lh


def _g(emp, sku, qty):
    return dict(emp_id=emp, sku=sku, qty=qty, salestype="S1", divisioncode="D1",
                areacode="10", provincecode="", warehouse_code="")


class TestEmployeeInMultipleTeamGrains(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data")

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _grain(self, sup, rows):
        pd.DataFrame(rows).to_csv(f"data/tga_lines_{sup}_2026_11.csv", index=False)

    def test_detects_shared_employee_with_boxes(self):
        self._grain("SLA", [_g("A001", "X", 10), _g("E445", "X", 30)])
        self._grain("SLB", [_g("B001", "X", 20), _g("e445", "X", 30)])
        self.assertEqual(lh.employees_in_multiple_team_grains(["SLA", "slb"], 11, 2026), {"E445": ["SLA", "SLB"]})
        with self.assertRaises(HTTPException) as cm:
            lh.assert_no_employee_in_multiple_team_grains(["SLA", "SLB"], 11, 2026)
        self.assertEqual(cm.exception.status_code, 409)
        self.assertEqual(cm.exception.detail["code"], "employee_in_two_teams")

    def test_zero_rows_and_other_teams_ignored(self):
        self._grain("SLA", [_g("A001", "X", 10), _g("E445", "X", 0)])
        self._grain("SLB", [_g("B001", "X", 20), _g("E445", "X", 30)])
        self._grain("SLC", [_g("A001", "X", 5)])  # ไม่อยู่ในชุด
        self.assertEqual(lh.employees_in_multiple_team_grains(["SLA", "SLB"], 11, 2026), {})
        lh.assert_no_employee_in_multiple_team_grains(["SLA", "SLB"], 11, 2026)

    def test_missing_files_do_not_crash(self):
        self.assertEqual(lh.employees_in_multiple_team_grains(["SLA", "SLB"], 11, 2026), {})


if __name__ == "__main__":
    unittest.main()
