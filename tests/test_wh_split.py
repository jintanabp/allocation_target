"""Tests for warehouse split (emp × WH alloc rows)."""

from __future__ import annotations

import os
import sys
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services.wh_split import (  # noqa: E402
    _norm_wh,
    alloc_key,
    expand_employee_rows,
    prepare_optimize_targets,
    restore_allocation_emp_ids,
    split_hist_dataframe,
    tga_value_by_emp_wh,
    warehouses_per_emp_from_tga,
)


class TestBlankWarehouseIsNotAWarehouse(unittest.TestCase):
    """
    คลังว่าง/NaN ต้องไม่กลายเป็น "รหัสคลัง"

    บั๊กเดิม: `str(val or "").strip()` ทำให้ NaN ที่ผ่าน CSV มาเป็นสตริง "nan" (truthy)
    กลายเป็นคลังจริง → พนักงานคลังเดียวถูกแตกเป็น wh_split ปลอม เป้าเงินส่วนหนึ่งถูกโยน
    ไปคลังที่ไม่มีอยู่ แถวปลอมผ่านด่าน eligible ได้หีบจริง แล้วไหลไปถึงไฟล์ส่ง Target Sun
    """

    def test_norm_wh_treats_every_blank_form_as_empty(self):
        import numpy as np

        for blank in (None, float("nan"), np.nan, pd.NA, pd.NaT, "", "   ", "nan", "NaN", "None", "<NA>"):
            self.assertEqual(_norm_wh(blank), "", f"ควรว่างสำหรับ {blank!r}")

    def test_norm_wh_keeps_real_codes(self):
        self.assertEqual(_norm_wh("R408"), "R408")
        self.assertEqual(_norm_wh("  G010  "), "G010")

    def test_nan_warehouse_is_not_listed(self):
        import numpy as np

        df = pd.DataFrame({
            "emp_id": ["S554", "S554"],
            "sku": ["A", "B"],
            "qty": [10, 5],
            "warehouse_code": ["G010", np.nan],
        })
        self.assertEqual(warehouses_per_emp_from_tga(df), {"S554": ["G010"]})

    def test_nan_warehouse_does_not_fabricate_a_split(self):
        """เคสจริงที่เจอ (SL225): พนักงานคลังเดียวต้องไม่ถูกแตกและเป้าเงินต้องไม่ถูกหั่น"""
        import numpy as np

        df = pd.DataFrame({
            "emp_id": ["S554", "S554"],
            "sku": ["A", "B"],
            "qty": [10, 5],
            "warehouse_code": ["G010", np.nan],
        })
        rows = expand_employee_rows(
            [{"emp_id": "S554", "target_sun": 1000.0}], df, {"A": 10.0, "B": 20.0}
        )
        self.assertEqual(len(rows), 1, "ต้องไม่ถูกแตกเป็นสองแถว")
        self.assertFalse(rows[0]["wh_split"])
        self.assertEqual(rows[0]["target_sun"], 1000.0, "เป้าเงินต้องอยู่ครบ ไม่ถูกหั่นไปคลังปลอม")
        self.assertEqual(rows[0]["warehouse_code"], "G010")
        self.assertEqual(rows[0]["alloc_key"], "S554")

    def test_tga_value_without_warehouse_column(self):
        df = pd.DataFrame({"emp_id": ["E1"], "sku": ["A"], "qty": [3]})
        self.assertEqual(tga_value_by_emp_wh(df, {"A": 100.0}), {("E1", ""): 300.0})

    def test_prepare_optimize_targets_ignores_nan_warehouse(self):
        import numpy as np

        df = pd.DataFrame({
            "emp_id": ["E1", "E2"],
            "yellow_target": [100.0, 200.0],
            "warehouse_code": ["R408", np.nan],
        })
        prepared, reverse = prepare_optimize_targets(df)
        self.assertEqual(prepared["or_emp_id"].tolist(), ["E1|R408", "E2"])
        self.assertEqual(reverse["E2"], ("E2", ""), "คลัง NaN ต้องไม่กลายเป็น 'E2|nan'")


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
