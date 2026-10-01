"""
แคตตาล็อกสินค้าหน้าแอดมิน (/admin/sku-links/catalog) ใช้ราคาตั้งแบบเดียวกับหน้าทีม
(กลุ่ม 4 จากออดิต 26 ส.ค. 2026 — แก้ 1 ต.ค. 2026)

เดิมใช้ราคาเฉลี่ยจากยอดขายเดือนก่อน (Amount÷Qty ปนเครดิต+เงินสด) ทั้งที่ดึงราคาตั้งมาแล้ว
ตอนนี้: ราคาตั้งเครดิต ณ วันที่ 1 ของงวด · ยอดขายเฉลี่ยเป็นตัวถอยเฉพาะ SKU ที่ไม่มีราคาตั้ง
และยิงคิวรียอดขายเฉพาะตอนจำเป็น — Fabric ปลอมทั้งหมด ไม่ต่อระบบจริง
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.routers import admin as admin_router  # noqa: E402

DEV = {"email": "dev@example.test", "is_admin": True, "is_marketing": False,
       "role": "dev", "admin_scope": None}


class _FakeFabric:
    sales_calls: list = []

    def get_tga_period_sku_targets(self, month, year):
        return pd.DataFrame([{"sku": "A1", "target_boxes": 10}, {"sku": "B2", "target_boxes": 4}])

    def get_product_info(self, sku_list=None, target_year=None, target_month=None):
        return pd.DataFrame([
            {"sku": "A1", "brand": "X", "credit_unit_price": 352.0, "cash_unit_price": 340.0},
            {"sku": "B2", "brand": "Y", "credit_unit_price": 0.0, "cash_unit_price": 0.0},
        ])

    def get_latest_price_per_box_by_sku(self, month, year, sku_list):
        type(self).sales_calls.append(list(sku_list))
        return pd.DataFrame([{"sku": s, "price_per_box": 99.0} for s in sku_list])


class TestCatalogUsesListPrice(unittest.TestCase):
    def _run(self):
        _FakeFabric.sales_calls = []
        with patch.object(admin_router, "FabricDAXConnector", _FakeFabric), \
             patch.object(admin_router, "read_links", return_value=[]):
            out = admin_router.sku_link_catalog(year=2026, month=10, super_code=None, user=DEV)
        return {r["sku"]: r for r in out["skus"]}

    def test_list_price_used_when_present(self):
        rows = self._run()
        self.assertEqual(rows["A1"]["price_per_box"], 352.0)
        self.assertFalse(rows["A1"]["price_from_sales_history"])

    def test_sales_average_only_for_missing_list_price(self):
        rows = self._run()
        self.assertEqual(rows["B2"]["price_per_box"], 99.0)
        self.assertTrue(rows["B2"]["price_from_sales_history"])
        self.assertEqual(_FakeFabric.sales_calls, [["B2"]], "ยิงยอดขายเฉพาะ SKU ที่ไม่มีราคาตั้ง")


if __name__ == "__main__":
    unittest.main()
