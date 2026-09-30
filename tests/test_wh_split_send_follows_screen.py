"""
พนักงานแยกคลัง (wh_split): หีบรายคลังบนจอต้องลงไฟล์ตรงคลังนั้น (ผู้ใช้ขอ 30 ก.ย. 2026)

เดิมตัวสร้างไฟล์ไม่สนคลังในคำขอ แตกทุกแถวตามสัดส่วนเป้าเดิม (grain) ใหม่ — จอ W1=8/W2=2
ถูกส่งเป็น 5/5 ยอดรวมคู่ถูก ด่านยอดรวม/ตรวจหลังส่งจึงมองไม่เห็น

กติกา:
  - คลังในคำขอที่มีอยู่ในเป้าปัจจุบันของคู่นั้น = ลงคลังนั้นตรง ๆ
  - คลังที่ไม่มีในเป้าปัจจุบัน = ไม่เชื่อ (กันแถวซ้อน) ไปลงแถวที่ไม่มีใครระบุ
  - แถวของคู่นั้นที่ไม่มีใครระบุ ส่ง 0 ทับ (ไม่งั้นค้างเลขเก่า เป้าเบิ้ล)
  - คนคลังเดียว / คำขอไม่ระบุคลัง = แบ่งตามสัดส่วนเดิมเหมือนก่อน

ออฟไลน์ล้วน ไม่ต่อระบบจริง
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.schemas import LakehouseUploadRequest  # noqa: E402
from backend.services import lakehouse as lh  # noqa: E402

logging.disable(logging.CRITICAL)

SUP = "SLSPLIT"


def _g(emp, sku, wh, qty, area="10"):
    return {"emp_id": emp, "sku": sku, "qty": qty, "warehouse_code": wh, "salestype": "S1",
            "divisioncode": "D1", "areacode": area, "provincecode": "P1"}


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data", exist_ok=True)
        self._p = patch.object(lh, "_enrich_emp_dimensions", side_effect=lambda df, r, **kw: df)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _grain(self, rows, sup=SUP):
        pd.DataFrame(rows).to_csv(f"data/tga_lines_{sup}_2026_08.csv", index=False)

    def _build(self, allocations):
        req = LakehouseUploadRequest(sup_id=SUP, target_month=8, target_year=2026,
                                     upload_user_code="T", allocations=allocations)
        out, *_ = lh._build_tga_upload_dataframe(req)
        return {
            (r.SALESMANCODE, r.WAREHOUSECODE, str(r.AREACODE)): int(r.QUANTITYCASE)
            for r in out.itertuples(index=False)
        }


class TestWhSplitFollowsScreen(_Base):
    def test_screen_numbers_per_warehouse_are_sent(self):
        self._grain([_g("E1", "A", "W1", 5), _g("E1", "A", "W2", 5)])
        f = self._build([
            {"emp_id": "E1", "sku": "A", "allocated_boxes": 8, "warehouse_code": "W1"},
            {"emp_id": "E1", "sku": "A", "allocated_boxes": 2, "warehouse_code": "W2"},
        ])
        self.assertEqual(f, {("E1", "W1", "10"): 8, ("E1", "W2", "10"): 2})

    def test_unlisted_warehouse_is_zeroed_not_left_stale(self):
        self._grain([_g("E1", "A", "W1", 5), _g("E1", "A", "W2", 5)])
        f = self._build([{"emp_id": "E1", "sku": "A", "allocated_boxes": 10, "warehouse_code": "W1"}])
        self.assertEqual(f, {("E1", "W1", "10"): 10, ("E1", "W2", "10"): 0})

    def test_warehouse_not_in_current_target_goes_to_unclaimed_row(self):
        self._grain([_g("E1", "A", "W1", 5), _g("E1", "A", "W2", 5)])
        f = self._build([
            {"emp_id": "E1", "sku": "A", "allocated_boxes": 4, "warehouse_code": "W1"},
            {"emp_id": "E1", "sku": "A", "allocated_boxes": 6, "warehouse_code": "W9"},
        ])
        self.assertEqual(f, {("E1", "W1", "10"): 4, ("E1", "W2", "10"): 6},
                         "W9 ไม่มีในเป้าปัจจุบัน ห้ามสร้างแถวใหม่ — ลงแถวที่ไม่มีใครระบุ")

    def test_blank_real_warehouse_in_split_employee(self):
        """คลังว่างเป็นคลังจริงค่าหนึ่ง — ว่างมาว่างไป"""
        self._grain([_g("E1", "A", "", 5), _g("E1", "A", "W2", 5)])
        f = self._build([
            {"emp_id": "E1", "sku": "A", "allocated_boxes": 7, "warehouse_code": ""},
            {"emp_id": "E1", "sku": "A", "allocated_boxes": 3, "warehouse_code": "W2"},
        ])
        self.assertEqual(f, {("E1", "", "10"): 7, ("E1", "W2", "10"): 3})

    def test_same_warehouse_several_areas_split_by_weight(self):
        self._grain([_g("E1", "A", "W1", 3, area="10"), _g("E1", "A", "W1", 1, area="20"),
                     _g("E1", "A", "W2", 5)])
        f = self._build([
            {"emp_id": "E1", "sku": "A", "allocated_boxes": 8, "warehouse_code": "W1"},
            {"emp_id": "E1", "sku": "A", "allocated_boxes": 2, "warehouse_code": "W2"},
        ])
        self.assertEqual(f, {("E1", "W1", "10"): 6, ("E1", "W1", "20"): 2, ("E1", "W2", "10"): 2})

    def test_no_warehouse_in_request_keeps_old_proportional_split(self):
        self._grain([_g("E1", "A", "W1", 3), _g("E1", "A", "W2", 1)])
        f = self._build([{"emp_id": "E1", "sku": "A", "allocated_boxes": 8}])
        self.assertEqual(f, {("E1", "W1", "10"): 6, ("E1", "W2", "10"): 2})

    def test_single_warehouse_employee_unchanged(self):
        self._grain([_g("E1", "A", "W1", 5)])
        f = self._build([{"emp_id": "E1", "sku": "A", "allocated_boxes": 9, "warehouse_code": "W1"}])
        self.assertEqual(f, {("E1", "W1", "10"): 9})

    def test_new_pair_still_blank_warehouse(self):
        self._grain([_g("E1", "A", "W1", 5)])
        f = self._build([
            {"emp_id": "E1", "sku": "A", "allocated_boxes": 5, "warehouse_code": "W1"},
            {"emp_id": "E1", "sku": "B", "allocated_boxes": 3, "warehouse_code": "W1"},
        ])
        self.assertEqual(f.get(("E1", "W1", "10")), 5)
        b_rows = [k for k, v in f.items() if v == 3]
        self.assertEqual(len(b_rows), 1)
        self.assertEqual(b_rows[0][1], "", "คู่ใหม่ (E1×B) ห้ามได้คลังจากคำขอ ต้องว่าง")

    def test_other_team_employee_in_regional_file(self):
        """รวมภาค: คนทีมอื่นที่ติดมาในไฟล์ของ SL นี้ ก็ต้องลงคลังตามจอ"""
        self._grain([_g("E1", "A", "W1", 5)])
        self._grain([_g("E9", "A", "W5", 5), _g("E9", "A", "W6", 5)], sup="SLOTHER")
        f = self._build([
            {"emp_id": "E1", "sku": "A", "allocated_boxes": 5, "warehouse_code": "W1"},
            {"emp_id": "E9", "sku": "A", "allocated_boxes": 9, "warehouse_code": "W5"},
            {"emp_id": "E9", "sku": "A", "allocated_boxes": 1, "warehouse_code": "W6"},
        ])
        self.assertEqual(f[("E9", "W5", "10")], 9)
        self.assertEqual(f[("E9", "W6", "10")], 1)


if __name__ == "__main__":
    unittest.main()
