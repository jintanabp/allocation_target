"""
ดาวน์โหลดเป้าตั้งต้นเป็น Excel (ผู้ใช้ขอ 27 ต.ค. 2026)

กล่องเป้าตั้งต้นในหน้าแอดมินโชว์แค่สรุป + รายการที่ต่าง — ไฟล์นี้ให้เห็นทั้งชุด
ข้อที่ห้ามพลาด: **อ่านอย่างเดียว** ห้ามแตะไฟล์เป้า/เป้าตั้งต้น/ผลกระจาย และไม่ออกเน็ต
"""

from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import pandas as pd
from fastapi import HTTPException

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.routers import admin as admin_router  # noqa: E402
from backend.services import target_baseline as tb  # noqa: E402

SUP, M, Y = "SL532", 11, 2026


class BaselineExcelTest(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        self._tmpdir = tempfile.mkdtemp(prefix="baseline_xlsx_")
        os.chdir(self._tmpdir)
        os.makedirs("data", exist_ok=True)
        self._old = os.environ.get("EMP_ASSIGNMENTS_JSON_PATH")
        os.environ["EMP_ASSIGNMENTS_JSON_PATH"] = os.path.join(self._tmpdir, "emp_assignments.json")

        # เป้าตั้งต้น: A=10 หีบ×100 · B=5 หีบ×20 · S041=1100 บาท · S044=0
        tb.capture_baseline_once(
            SUP, M, Y,
            pd.DataFrame([
                {"sku": "A", "supervisor_target_boxes": 10, "price_per_box": 100.0},
                {"sku": "B", "supervisor_target_boxes": 5, "price_per_box": 20.0},
            ]),
            pd.DataFrame([{"emp_id": "S041", "target_sun": 1100.0},
                          {"emp_id": "S044", "target_sun": 0.0}]),
        )
        # เป้าปัจจุบันเปลี่ยนไป: A=12 · B หาย · S041=1300
        pd.DataFrame([{"sku": "A", "supervisor_target_boxes": 12, "price_per_box": 100.0,
                       "product_name_thai": "มาม่า", "brand_name_thai": "มาม่า"}]).to_csv(
            f"data/target_boxes_{SUP}_{Y}_{M:02d}.csv", index=False)
        pd.DataFrame([{"emp_id": "S041", "target_sun": 1300.0}]).to_csv(
            f"data/target_sun_{SUP}_{Y}_{M:02d}.csv", index=False)
        pd.DataFrame([{"emp_id": "S041", "emp_name": "อธิบดี", "super_code": SUP}]).to_csv(
            f"data/emp_cache_{SUP}_{Y}_{M:02d}.csv", index=False)

    def tearDown(self):
        os.chdir(self._cwd)
        if self._old is None:
            os.environ.pop("EMP_ASSIGNMENTS_JSON_PATH", None)
        else:
            os.environ["EMP_ASSIGNMENTS_JSON_PATH"] = self._old
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _call(self, sup=SUP):
        with mock.patch.object(admin_router, "ensure_sup_in_admin_scope"), \
             mock.patch.object(admin_router, "_audit_admin"):
            return admin_router.admin_export_target_baseline_xlsx(
                admin={"email": "a@example.com"}, sup_id=sup, target_month=M, target_year=Y)

    def _sheets(self, res):
        return pd.read_excel(io.BytesIO(res.body), sheet_name=None)

    def _snapshot(self):
        out = {}
        for root, _dirs, files in os.walk("data"):
            for f in files:
                p = os.path.join(root, f)
                with open(p, "rb") as fh:
                    out[p] = fh.read()
        return out

    def test_two_sheets_with_the_whole_baseline(self):
        sheets = self._sheets(self._call())
        self.assertEqual(list(sheets), ["เป้าหีบราย SKU", "เป้าเงินรายคน"])
        sku = sheets["เป้าหีบราย SKU"].set_index("รหัสสินค้า")
        self.assertEqual(int(sku.loc["A", "เป้าตั้งต้น (หีบ)"]), 10)
        self.assertEqual(float(sku.loc["A", "เป้าตั้งต้น (บาท)"]), 1000.0)
        self.assertEqual(sku.loc["A", "ชื่อสินค้า"], "มาม่า")
        self.assertEqual(int(sku.loc["A", "เป้าปัจจุบัน (หีบ)"]), 12)
        self.assertEqual(int(sku.loc["A", "ต่าง (หีบ)"]), 2)
        self.assertIn("B", sku.index, "SKU ที่หายจากเป้าปัจจุบันต้องยังอยู่ในไฟล์เป้าตั้งต้น")
        self.assertTrue(pd.isna(sku.loc["B", "เป้าปัจจุบัน (หีบ)"]))

    def test_money_per_person(self):
        emp = self._sheets(self._call())["เป้าเงินรายคน"].astype({"รหัสพนักงาน": str}).set_index("รหัสพนักงาน")
        self.assertEqual(emp.loc["S041", "ชื่อ"], "อธิบดี")
        self.assertEqual(float(emp.loc["S041", "เป้าตั้งต้น (บาท)"]), 1100.0)
        self.assertEqual(float(emp.loc["S041", "ต่าง (บาท)"]), 200.0)
        self.assertIn("S044", emp.index)

    def test_it_changes_nothing_on_disk(self):
        before = self._snapshot()
        self._call()
        self.assertEqual(before, self._snapshot(), "ดาวน์โหลดต้องไม่เขียน/ลบไฟล์ใดใน data/")

    def test_no_baseline_is_a_404(self):
        with self.assertRaises(HTTPException) as cm:
            self._call("SL999")
        self.assertEqual(cm.exception.status_code, 404)

    def test_the_box_has_the_button(self):
        with open(os.path.join(REPO, "frontend", "app.js"), encoding="utf-8") as fh:
            js = fh.read()
        i = js.index("async function adminShowTargetBaseline(")
        block = js[i:js.index("function _baselineRowSnapsHtml", i)]
        self.assertIn('id="adminBaselineXlsxDl"', block)
        self.assertIn("/admin/target-baseline/export-xlsx?", block)


if __name__ == "__main__":
    unittest.main()
