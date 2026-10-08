"""
คนแยกคลัง: สินค้าที่ Target Sun ไม่มีแถวที่คลังของแถวบนจอ → บอกว่าตอนส่งจะลงคลังไหน (ผู้ใช้ยืนยัน 8 ต.ค. 2026)

ของจริง SL509 งวด 09/2026: C442 มี R408 (96 สินค้า) และ R493 (17 สินค้า เป้า 0 ทุกแถว → ไม่นำไปกระจาย)
ขั้นที่ 3 แสดง C442 แค่ R408 แต่ถ้าได้หีบของ 17 สินค้านั้น ตอนส่งจะลงแถว R493 (ตาม Target Sun ไม่สร้างแถวใหม่)
"""

from __future__ import annotations

import unittest

import pandas as pd

from backend.services.wh_split import expand_employee_rows


def _g(sku, wh, qty):
    return {"emp_id": "C442", "sku": sku, "warehouse_code": wh, "qty": qty}


class TestWhRedirectSkus(unittest.TestCase):
    def test_redirect_listed_per_warehouse_row(self):
        grain = pd.DataFrame([_g("A", "R408", 10), _g("B", "R408", 5), _g("X", "R493", 0), _g("Y", "R493", 0)])
        rows = expand_employee_rows(
            [{"emp_id": "C442", "target_sun": 1500.0}], grain, {"A": 100.0, "B": 100.0, "X": 50.0, "Y": 50.0}
        )
        by_wh = {r["warehouse_code"]: r for r in rows}
        self.assertEqual(by_wh["R408"]["wh_redirect_skus"], {"X": ["R493"], "Y": ["R493"]})
        self.assertEqual(by_wh["R493"]["wh_redirect_skus"], {"A": ["R408"], "B": ["R408"]})

    def test_sku_in_both_warehouses_not_redirected(self):
        grain = pd.DataFrame([_g("A", "R408", 10), _g("A", "R493", 2), _g("B", "R493", 1)])
        rows = expand_employee_rows([{"emp_id": "C442", "target_sun": 1000.0}], grain, {"A": 100.0, "B": 100.0})
        r408 = next(r for r in rows if r["warehouse_code"] == "R408")
        self.assertEqual(r408["wh_redirect_skus"], {"B": ["R493"]})

    def test_single_warehouse_employee_has_no_field(self):
        grain = pd.DataFrame([_g("A", "R408", 10)])
        rows = expand_employee_rows([{"emp_id": "C442", "target_sun": 1000.0}], grain, {"A": 100.0})
        self.assertNotIn("wh_redirect_skus", rows[0])


if __name__ == "__main__":
    unittest.main()
