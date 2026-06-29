"""Tests for employee visibility filter rules."""

from __future__ import annotations

import os
import sys
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.core.employee_filter import (  # noqa: E402
    drop_van_employees,
    filter_employees_for_display,
    is_allocation_eligible,
    is_van_employee_id,
)


class TestVanEmployeeFilter(unittest.TestCase):
    def test_is_van_code(self):
        self.assertTrue(is_van_employee_id("V303"))
        self.assertTrue(is_van_employee_id("v100"))
        self.assertFalse(is_van_employee_id("S343"))

    def test_drop_van(self):
        df = pd.DataFrame(
            [
                {"emp_id": "S343", "emp_name": "A"},
                {"emp_id": "V303", "emp_name": "B"},
            ]
        )
        out, n = drop_van_employees(df)
        self.assertEqual(n, 1)
        self.assertEqual(list(out["emp_id"]), ["S343"])

    def test_allocation_eligible(self):
        self.assertTrue(is_allocation_eligible(True, 100.0))
        self.assertFalse(is_allocation_eligible(False, 100.0))
        self.assertFalse(is_allocation_eligible(True, 0.0))
        self.assertFalse(is_allocation_eligible(False, 0.0))


class TestEmployeeTargetFilter(unittest.TestCase):
    def test_enrich_allocation_flags(self):
        from backend.services.employees import _enrich_employee_allocation_flags

        rows = [
            {"emp_id": "A", "has_tga_rows": True, "target_sun": 100.0},
            {"emp_id": "B", "has_tga_rows": False, "target_sun": 0.0, "ly_sales": 50000},
        ]
        out = _enrich_employee_allocation_flags(rows)
        self.assertTrue(out[0]["allocation_eligible"])
        self.assertTrue(out[0]["include_in_allocation"])
        self.assertFalse(out[1]["allocation_eligible"])
        self.assertFalse(out[1]["include_in_allocation"])
        self.assertTrue(out[1]["view_only"])

    def test_keeps_only_with_target(self):
        df = pd.DataFrame(
            [
                {"emp_id": "A", "has_tga_rows": True, "target_sun": 100.0},
                {"emp_id": "B", "has_tga_rows": False, "target_sun": 0.0},
                {"emp_id": "C", "has_tga_rows": True, "target_sun": 0.0},
            ]
        )
        out, hidden, ly_only = filter_employees_for_display(df)
        self.assertEqual(list(out["emp_id"]), ["A"])
        self.assertEqual(hidden, 2)
        self.assertEqual(ly_only, 0)

    def test_keeps_ly_without_target(self):
        df = pd.DataFrame(
            [
                {"emp_id": "A", "has_tga_rows": True, "target_sun": 100.0},
                {"emp_id": "B", "has_tga_rows": False, "target_sun": 0.0},
            ]
        )
        out, hidden, ly_only = filter_employees_for_display(
            df, ly_sales_by_emp={"B": 50000.0}
        )
        self.assertEqual(sorted(out["emp_id"].tolist()), ["A", "B"])
        self.assertEqual(hidden, 0)
        self.assertEqual(ly_only, 1)

    def test_empty_when_none_qualify(self):
        df = pd.DataFrame(
            [{"emp_id": "B", "has_tga_rows": False, "target_sun": 0.0}]
        )
        out, hidden, ly_only = filter_employees_for_display(df)
        self.assertTrue(out.empty)
        self.assertEqual(hidden, 1)
        self.assertEqual(ly_only, 0)


if __name__ == "__main__":
    unittest.main()
