"""
ผลตรวจ 6 ต.ค. 2026 ก5 — ผู้ใช้เลือก "เตือนตอนโหลดขั้นที่ 1" (ไม่บล็อกตอนส่ง)

พนักงานที่มีชื่ออยู่ในไฟล์ของทีมอื่นด้วยในงวดเดียวกัน เป้าของเขาอาจถูกนับทั้งสองทีม
คนที่ตั้งย้ายทีมไว้ (emp_assignments) ไม่ต้องเตือน — เป็นเรื่องที่ตั้งใจ · ใช้โฟลเดอร์ชั่วคราว
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import emp_assignment_store  # noqa: E402
from backend.services.employees import emp_in_two_teams_warning  # noqa: E402


class TestTwoTeamWarning(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data")
        pd.DataFrame([{"emp_id": "E1"}, {"emp_id": "E2"}]).to_csv("data/tga_lines_SLA_2026_11.csv", index=False)
        pd.DataFrame([{"emp_id": "E2"}, {"emp_id": "E9"}]).to_csv("data/tga_lines_SLB_2026_11.csv", index=False)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def test_employee_in_another_team_is_warned(self):
        with patch.object(emp_assignment_store, "read_rows", return_value=[]):
            w = emp_in_two_teams_warning("SLA", 11, 2026, [
                {"emp_id": "E1", "emp_name": "หนึ่ง"}, {"emp_id": "E2", "emp_name": "สอง"},
            ])
        self.assertIsNotNone(w)
        self.assertEqual(w["type"], "emp_in_two_teams")
        self.assertIn("E2 (สอง) → SLB", w["message"])
        self.assertNotIn("E1", w["message"].split("ทั้งสองทีม:")[1].split("·")[0])

    def test_moved_employee_is_not_warned(self):
        with patch.object(emp_assignment_store, "read_rows", return_value=[{"emp_id": "E2", "to_sup": "SLA"}]):
            self.assertIsNone(emp_in_two_teams_warning("SLA", 11, 2026, [{"emp_id": "E2"}]))

    def test_no_other_team_means_no_warning(self):
        with patch.object(emp_assignment_store, "read_rows", return_value=[]):
            self.assertIsNone(emp_in_two_teams_warning("SLA", 11, 2026, [{"emp_id": "E1"}]))


if __name__ == "__main__":
    unittest.main()
