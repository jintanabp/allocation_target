"""
ล้างแถวเป้าเก่าที่ "หลุดจากผลกระจายรอบนี้" แต่ยังค้างอยู่ใน Target Sun (ค8)

คนละกรณีกับ _clear_no_target_employees_in_tga (คนทั้งคนไม่มีแถวเลยเพราะอยู่ใน
บัญชีดำ「ไม่ต้องตั้งเป้า」) — ที่นี่คือพนักงานยังอยู่ในรอบส่งนี้จริง แต่ SKU บางตัวที่
เขาเคยมีเป้า (เห็นใน grain ของ TGA) ไม่อยู่ในรอบนี้แล้ว

ข้อห้ามสำคัญที่เทสนี้ต้องกันไว้:
  1. ส่งแบบเต็ม (ไม่มี brand_filter/sku_filter) เท่านั้นถึงจะทำงาน — ส่งบางส่วนต้อง
     no-op เด็ดขาด ไม่งั้นจะเข้าใจผิดว่า "ตั้งใจไม่เลือกส่ง" คือ "หมดเป้าแล้ว"
  2. ต้องเป็น SKU ที่อยู่ใน "จักรวาล SKU ของรอบนี้" เท่านั้น (มีแถวของ SKU นั้นอยู่ใน
     df ไม่ว่าจะเป็นของพนักงานคนไหนก็ตาม) — พนักงานทีมอื่นที่ติดมาในโหมดรวมภาค/หน่วย
     มักมี SKU อื่นในเป้า/ประวัติของทีมตัวเองที่ไม่เกี่ยวกับรอบส่งนี้เลย (บั๊กจริงที่เจอ
     ระหว่างพัฒนา — ดู test_grain_across_teams.py / test_unit_wide_allocation.py)
  3. ไม่แตะพนักงานที่หายไปทั้งคนจาก df รอบนี้ (คนละเรื่องกับ _clear_no_target_employees_in_tga)
"""

from __future__ import annotations

import logging
import os
import sys
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import lakehouse as lh  # noqa: E402

logging.disable(logging.CRITICAL)


def _grain_row(emp, sku, area="10", province="P1", wh="WH1"):
    return {
        "emp_id": emp, "sku": sku, "qty": 5, "salestype": "S1",
        "divisioncode": "D1", "areacode": area, "provincecode": province,
        "warehouse_code": wh,
    }


def _alloc_row(emp, sku, boxes=5):
    return {"emp_id": emp, "sku": sku, "allocated_boxes": boxes}


class TestClearStaleEmployeeSkuRows(unittest.TestCase):
    def test_gap_for_a_present_employee_is_cleared_with_zero(self):
        """E1 ยังอยู่ในรอบนี้ (มีแถว SKU A) แต่ SKU B (อยู่ในจักรวาลรอบนี้ผ่าน E2)
        ที่ E1 เคยมีเป้า ไม่ได้ถูกส่งให้ E1 รอบนี้ — ต้องล้างด้วย 0"""
        df = pd.DataFrame([_alloc_row("E1", "A", 5), _alloc_row("E2", "B", 3)])
        dg = pd.DataFrame([_grain_row("E1", "A"), _grain_row("E1", "B", area="20")])
        out, cleared = lh._clear_stale_employee_sku_rows_in_tga(
            df, "SLX", dg=dg, full_send=True
        )
        e1b = out[(out["emp_id"] == "E1") & (out["sku"] == "B")]
        self.assertEqual(len(e1b), 1)
        self.assertEqual(int(e1b["allocated_boxes"].iloc[0]), 0)
        self.assertEqual(e1b["areacode"].iloc[0], "20")
        self.assertEqual(cleared, 1)

    def test_partial_send_is_a_no_op(self):
        """ส่งแบบเลือกแบรนด์/สินค้าบางส่วน (full_send=False) ต้องไม่แตะอะไรเลย"""
        df = pd.DataFrame([_alloc_row("E1", "A", 5)])
        dg = pd.DataFrame([_grain_row("E1", "A"), _grain_row("E1", "B")])
        out, cleared = lh._clear_stale_employee_sku_rows_in_tga(
            df, "SLX", dg=dg, full_send=False
        )
        self.assertEqual(cleared, 0)
        self.assertEqual(len(out), 1, "ต้องไม่มีแถวเพิ่ม")

    def test_sku_outside_this_rounds_universe_is_never_touched(self):
        """
        SKU ที่ไม่มีใครเลยในรอบนี้ถูกส่ง (ไม่อยู่ใน df เลยแม้แต่แถวเดียว) ต้องไม่ถูกล้าง
        แม้พนักงานที่มี SKU นั้นใน grain จะยังอยู่ในรอบนี้ก็ตาม — ป้องกันกรณีพนักงาน
        ทีมอื่นที่ติดมาในโหมดรวมภาค มี SKU อื่นในเป้าของทีมตัวเองที่ไม่เกี่ยวกับรอบนี้
        """
        df = pd.DataFrame([_alloc_row("E1", "A", 5)])
        dg = pd.DataFrame([_grain_row("E1", "A"), _grain_row("E1", "C")])
        out, cleared = lh._clear_stale_employee_sku_rows_in_tga(
            df, "SLX", dg=dg, full_send=True
        )
        self.assertEqual(cleared, 0)
        self.assertNotIn("C", out["sku"].tolist())

    def test_employee_entirely_absent_from_the_send_is_not_touched(self):
        """คนละงานกับ _clear_no_target_employees_in_tga — ไม่ไปยุ่งกับคนที่หายทั้งคน"""
        df = pd.DataFrame([_alloc_row("E1", "A", 5)])
        dg = pd.DataFrame([_grain_row("E1", "A"), _grain_row("E9", "A")])
        out, cleared = lh._clear_stale_employee_sku_rows_in_tga(
            df, "SLX", dg=dg, full_send=True
        )
        self.assertEqual(cleared, 0)
        self.assertNotIn("E9", out["emp_id"].tolist())

    def test_pair_already_present_is_not_duplicated(self):
        df = pd.DataFrame([_alloc_row("E1", "A", 5), _alloc_row("E1", "B", 2)])
        dg = pd.DataFrame([_grain_row("E1", "A"), _grain_row("E1", "B")])
        out, cleared = lh._clear_stale_employee_sku_rows_in_tga(
            df, "SLX", dg=dg, full_send=True
        )
        self.assertEqual(cleared, 0)
        self.assertEqual(len(out), 2)

    def test_empty_grain_is_a_no_op(self):
        df = pd.DataFrame([_alloc_row("E1", "A", 5)])
        out, cleared = lh._clear_stale_employee_sku_rows_in_tga(
            df, "SLX", dg=pd.DataFrame(), full_send=True
        )
        self.assertEqual(cleared, 0)
        self.assertEqual(len(out), 1)

    def test_empty_df_is_a_no_op(self):
        dg = pd.DataFrame([_grain_row("E1", "A")])
        out, cleared = lh._clear_stale_employee_sku_rows_in_tga(
            pd.DataFrame(), "SLX", dg=dg, full_send=True
        )
        self.assertEqual(cleared, 0)
        self.assertTrue(out.empty)


class TestFullSendGatingIsWiredCorrectly(unittest.TestCase):
    """โค้ดต้องคำนวณ full_send จาก brand_filter/sku_filter จริง ไม่ใช่ hardcode True"""

    def test_source_gates_on_brand_all_and_no_sku_filter(self):
        import inspect

        src = inspect.getsource(lh._build_tga_upload_dataframe)
        self.assertIn("_clear_stale_employee_sku_rows_in_tga(", src)
        self.assertIn('(brand_filter or "ALL").upper() == "ALL" and not sku_filter', src)


if __name__ == "__main__":
    unittest.main()
