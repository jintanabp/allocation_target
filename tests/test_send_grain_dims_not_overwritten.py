"""
ผลตรวจ 7 ต.ค. 2026 ก4 — แถวที่มาจาก grain (แถวจริงใน Target Sun) ห้ามถูกเติม dim จาก Fabric ทับ

เดิม: มีแถวใดในไฟล์ขาด SALESTYPE/DIVISION/AREA (เช่น พนักงานที่ไม่มีแถวเป้าเลย) → ทั้งไฟล์ไปเติมจาก
Fabric (MAX ต่อคน) แล้ว PROVINCECODE ที่ "ว่างจริง" ใน Target Sun ถูกเติมเป็นจังหวัดของแถวอื่น
→ คีย์ upsert เปลี่ยน = แถวใหม่ซ้อนแถวเดิม เป้าเบิ้ล (แบบเดียวกับ SL380 แต่ผ่านจังหวัด)
และคู่ใหม่หีบ 0 ที่ได้ dim จาก Fabric กลายเป็นแถว 0 ใหม่ใน Target Sun
"""

from __future__ import annotations

import logging
import os
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from backend.schemas import LakehouseUploadRequest
from backend.services import lakehouse as lh

logging.disable(logging.CRITICAL)
SUP = "SLZZPROV"


def _g(emp, sku, qty, prov=""):
    return dict(emp_id=emp, sku=sku, qty=qty, salestype="S1", divisioncode="D1", areacode="10",
                provincecode=prov, warehouse_code="")


class _FakeFabric:
    def get_tga_lakehouse_dims_by_emp_sku(self, e, s):
        return pd.DataFrame()

    def get_tga_lakehouse_dims_by_emp(self, emps):
        return pd.DataFrame([
            dict(emp_id=e, salestype="S1", divisioncode="D1", areacode="10", provincecode="P9", warehouse_code="R404")
            for e in ("E1", "E2")
        ])


class TestGrainDimsNotOverwritten(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data")
        pd.DataFrame([_g("E1", "SKU1", 5, ""), _g("E1", "SKU2", 3, "P9")]).to_csv(
            f"data/tga_lines_{SUP}_2026_10.csv", index=False
        )

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _build(self, allocs):
        req = LakehouseUploadRequest(sup_id=SUP, target_month=10, target_year=2026, upload_user_code="T",
                                     allocations=allocs, allow_new_targetsun_rows=True)
        with patch.object(lh, "FabricDAXConnector", return_value=_FakeFabric()):
            out, *_ = lh._build_tga_upload_dataframe(req, drop_incomplete_rows=True)
        return out

    def test_blank_province_stays_blank_when_other_row_needs_fabric(self):
        out = self._build([
            dict(emp_id="E1", sku="SKU1", allocated_boxes=5, warehouse_code=""),
            dict(emp_id="E1", sku="SKU2", allocated_boxes=3, warehouse_code=""),
            dict(emp_id="E2", sku="SKU1", allocated_boxes=0, warehouse_code=""),
            dict(emp_id="E2", sku="SKU2", allocated_boxes=0, warehouse_code=""),
        ])
        e1 = out[out.SALESMANCODE == "E1"].set_index("PRODUCTCODE")
        self.assertEqual(e1.loc["SKU1", "PROVINCECODE"], "")
        self.assertEqual(e1.loc["SKU2", "PROVINCECODE"], "P9")
        # คู่ใหม่หีบ 0 ไม่ต้องสร้างแถวเปล่าใน Target Sun
        self.assertTrue(out[out.SALESMANCODE == "E2"].empty)
        self.assertEqual(len(out), 2)


if __name__ == "__main__":
    unittest.main()
