"""Tests for warehouse split (emp × WH alloc rows)."""

from __future__ import annotations

import os
import sys
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services.wh_split import (  # noqa: E402
    alloc_key,
    expand_employee_rows,
    prepare_optimize_targets,
    restore_allocation_emp_ids,
    split_hist_dataframe,
    tga_value_by_emp_wh,
    warehouses_per_emp_from_tga,
)


class TestWhSplit(unittest.TestCase):
    def test_alloc_key_single_vs_split(self):
        self.assertEqual(alloc_key("C348", "01", wh_split=False), "C348")
        self.assertEqual(alloc_key("C348", "01", wh_split=True), "C348|01")

    def test_expand_two_warehouses(self):
        rows = [
            {
                "emp_id": "C348",
                "emp_name": "ทดสอบ",
                "target_sun": 200000.0,
                "ly_sales": 100000.0,
                "hist_avg_3m": 90000.0,
            }
        ]
        tga = pd.DataFrame(
            [
                {"emp_id": "C348", "sku": "S1", "qty": 10, "warehouse_code": "01"},
                {"emp_id": "C348", "sku": "S2", "qty": 5, "warehouse_code": "07"},
            ]
        )
        prices = {"S1": 10000.0, "S2": 10000.0}
        out = expand_employee_rows(rows, tga, prices)
        self.assertEqual(len(out), 2)
        self.assertTrue(all(r["wh_split"] for r in out))
        self.assertEqual(sum(r["target_sun"] for r in out), 200000.0)

    def test_single_wh_unchanged(self):
        rows = [{"emp_id": "E1", "target_sun": 50000.0, "ly_sales": 1.0, "hist_avg_3m": 1.0}]
        tga = pd.DataFrame([{"emp_id": "E1", "sku": "S1", "qty": 1, "warehouse_code": "03"}])
        out = expand_employee_rows(rows, tga, {"S1": 50000.0})
        self.assertEqual(len(out), 1)
        self.assertFalse(out[0]["wh_split"])

    def test_optimize_roundtrip(self):
        df = pd.DataFrame(
            [
                {"emp_id": "C348", "warehouse_code": "01", "yellow_target": 120000.0},
                {"emp_id": "C348", "warehouse_code": "07", "yellow_target": 80000.0},
            ]
        )
        prep, rev = prepare_optimize_targets(df)
        self.assertIn("C348|01", prep["or_emp_id"].tolist())
        alloc = pd.DataFrame([{"emp_id": "C348|01", "sku": "S1", "allocated_boxes": 3}])
        restored = restore_allocation_emp_ids(alloc, rev)
        self.assertEqual(restored.iloc[0]["emp_id"], "C348")
        self.assertEqual(restored.iloc[0]["warehouse_code"], "01")

    def test_split_hist(self):
        hist = pd.DataFrame([{"emp_id": "C348", "sku": "S1", "hist_boxes": 100.0, "hist_amount": 1000.0}])
        rev = {"C348|01": ("C348", "01"), "C348|07": ("C348", "07")}
        shares = {("C348", "01"): 600.0, ("C348", "07"): 400.0}
        out = split_hist_dataframe(hist, rev, shares)
        self.assertEqual(len(out), 2)
        self.assertEqual(out["emp_id"].tolist(), ["C348|01", "C348|07"])

    def test_warehouses_per_emp_ignores_blank(self):
        tga = pd.DataFrame(
            [
                {"emp_id": "C348", "sku": "S1", "qty": 1, "warehouse_code": ""},
                {"emp_id": "C348", "sku": "S2", "qty": 1, "warehouse_code": "R337"},
                {"emp_id": "C348", "sku": "S3", "qty": 1, "warehouse_code": "R360"},
            ]
        )
        wh = warehouses_per_emp_from_tga(tga)
        self.assertEqual(wh["C348"], ["R337", "R360"])

    def test_expand_c348_two_wh_codes(self):
        rows = [
            {
                "emp_id": "C348",
                "emp_name": "ทดสอบ",
                "target_sun": 200000.0,
                "ly_sales": 100000.0,
                "hist_avg_3m": 90000.0,
            }
        ]
        tga = pd.DataFrame(
            [
                {"emp_id": "C348", "sku": "S1", "qty": 10, "warehouse_code": "R337"},
                {"emp_id": "C348", "sku": "S2", "qty": 5, "warehouse_code": "R360"},
            ]
        )
        prices = {"S1": 10000.0, "S2": 10000.0}
        out = expand_employee_rows(
            rows,
            tga,
            prices,
            ly_amount_by_emp_wh={("C348", "R337"): 60000.0, ("C348", "R360"): 40000.0},
        )
        self.assertEqual(len(out), 2)
        whs = sorted(r["warehouse_code"] for r in out)
        self.assertEqual(whs, ["R337", "R360"])
        self.assertEqual(sum(r["target_sun"] for r in out), 200000.0)


if __name__ == "__main__":
    unittest.main()
