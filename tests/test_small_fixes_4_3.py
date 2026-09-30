"""
ผลตรวจ 28 ก.ย. 2026 §4.3 เรื่องเล็ก 5 จุด
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
from pydantic import ValidationError

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from fastapi import HTTPException  # noqa: E402

from backend.core.employee_filter import filter_employees_for_display, is_allocation_eligible  # noqa: E402
from backend.core.paths import target_boxes_cache_path, target_sun_cache_path  # noqa: E402
from backend.schemas import OptimizeRequest  # noqa: E402
from backend.services import optimize as opt  # noqa: E402
from backend.services import target_baseline as tb  # noqa: E402

H = pd.DataFrame([{"emp_id": "E1", "sku": "A", "hist_boxes": 12}])
EMPTY = pd.DataFrame(columns=["emp_id", "sku", "hist_boxes"])


class TestCapMultiplierValidated(unittest.TestCase):
    def test_zero_or_negative_rejected(self):
        for v in (0, -1, 0.5):
            with self.assertRaises(ValidationError, msg=v):
                OptimizeRequest(yellowTargets=[{"emp_id": "E1", "yellow_target": 1.0}], cap_multiplier=v)

    def test_normal_and_none_ok(self):
        OptimizeRequest(yellowTargets=[{"emp_id": "E1", "yellow_target": 1.0}], cap_multiplier=3.0)
        OptimizeRequest(yellowTargets=[{"emp_id": "E1", "yellow_target": 1.0}])


class TestHistWindowLabel(unittest.TestCase):
    def _pick(self, strat, h3, h6, lysm=EMPTY):
        notes: list[str] = []
        df, months = opt._hist_input_for_strategy(
            strat, h3, h6, lysm, sup_id="SLX", target_month=10, target_year=2026, fallbacks=notes
        )
        return df, months, notes

    def test_ly_falling_to_6m_says_6_months(self):
        df, months, notes = self._pick("LY", EMPTY, H)
        self.assertIs(df, H)
        self.assertEqual(months, 6, "ข้อมูล 6 เดือนต้องหารด้วย 6 ไม่ใช่ 3")

    def test_ly_falling_to_3m(self):
        _df, months, notes = self._pick("LY", H, EMPTY)
        self.assertEqual((months, notes), (3, ["LY→3M"]))

    def test_l3m_missing_uses_6m_labelled_6(self):
        df, months, notes = self._pick("L3M", EMPTY, H)
        self.assertEqual((months, notes), (6, ["3M→6M"]))

    def test_l3m_normal(self):
        self.assertEqual(self._pick("L3M", H, EMPTY)[1:], (3, []))


class TestHistSupIdsUppercase(unittest.TestCase):
    def test_owner_team_is_uppercased(self):
        import inspect

        self.assertIn("hist_sup_ids = [str(sup_id).strip().upper()] + [",
                      inspect.getsource(opt.run_optimization_service))


class TestRestoreBaseline(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data", exist_ok=True)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def test_names_kept_from_current_file(self):
        pd.DataFrame([{"sku": "A", "supervisor_target_boxes": 99, "price_per_box": 1.0,
                       "brand_name_thai": "แบรนด์เอ", "product_name_thai": "สินค้าเอ"}]).to_csv(
            target_boxes_cache_path("SLX", 10, 2026), index=False)
        base = {"skus": [{"sku": "A", "supervisor_target_boxes": 5, "price_per_box": 10}],
                "employees": [{"emp_id": "E1", "target_sun": 50.0}]}
        with patch.object(tb, "read_baseline", return_value=base):
            tb.restore_baseline_to_target_files("SLX", 10, 2026)
        df = pd.read_csv(target_boxes_cache_path("SLX", 10, 2026), dtype={"sku": str})
        self.assertEqual(int(df.loc[0, "supervisor_target_boxes"]), 5)
        self.assertEqual(df.loc[0, "brand_name_thai"], "แบรนด์เอ")
        self.assertEqual(df.loc[0, "product_name_thai"], "สินค้าเอ")
        self.assertTrue(os.path.exists(target_sun_cache_path("SLX", 10, 2026)))

    def test_no_employees_refuses_half_restore(self):
        base = {"skus": [{"sku": "A", "supervisor_target_boxes": 5}], "employees": []}
        with patch.object(tb, "read_baseline", return_value=base), self.assertRaises(HTTPException) as cm:
            tb.restore_baseline_to_target_files("SLX", 10, 2026)
        self.assertEqual(cm.exception.status_code, 409)
        self.assertFalse(os.path.exists(target_boxes_cache_path("SLX", 10, 2026)), "ต้องไม่เขียนครึ่งเดียว")


class TestBoolFromCsv(unittest.TestCase):
    def test_string_false_is_false(self):
        df = pd.DataFrame({"emp_id": ["E1", "E2", "E3"], "has_tga_rows": ["False", "True", "false"],
                           "target_sun": [10.0, 10.0, 10.0]})
        visible, hidden, _ = filter_employees_for_display(df)
        self.assertEqual(visible["emp_id"].tolist(), ["E2"])
        self.assertEqual(hidden, 2)

    def test_eligible(self):
        self.assertFalse(is_allocation_eligible("False", 10))
        self.assertTrue(is_allocation_eligible(True, 10))
        self.assertTrue(is_allocation_eligible("True", 10))


if __name__ == "__main__":
    unittest.main()
