"""
แถวเป้าที่ "สร้างใหม่" ต้องอยู่คลังเดียวกับเป้าอื่นของพนักงานคนนั้น

คีย์ upsert ของ Target Sun รวม WAREHOUSECODE (เจ้าของเพิ่มเข้าไป 7 ก.ย. 2026)
แถวที่คลังไม่ตรงจึงเป็น "คนละแถว" ไม่ใช่การทับ — ถ้าคู่พนักงาน×สินค้าหนึ่งมีแถวอยู่แล้ว
ที่คลัง R082 แล้วเราสร้างแถวใหม่ให้ที่คลังว่าง คู่นั้นจะมีเป้าสองที่พร้อมกัน

ของจริงที่ทำให้ต้องแก้ (ชุดพัฒนา 11 ก.ย. 2026): SL376 พนักงาน B033 มีเป้าที่ปลายทาง
246 แถว ใช้คลัง R082 ทุกแถว แต่แถวใหม่ที่ระบบสร้างให้เขาได้คลังว่างทั้งหมด
วัดทั้งชุด ~7,800 แถวเป็นแบบนี้ และ ~2,500 แถวในนั้นมีหีบ > 0 (เป้าจริง ไม่ใช่แถว 0)

กิ่งที่ "เจอคู่ใน grain" ไม่มีปัญหาอยู่แล้ว — มันหยิบ dim ทั้งชุดจากแถวปลายทาง
รวมคลังด้วย (lakehouse.py:721-728) ปัญหาอยู่เฉพาะกิ่ง "ไม่เจอ → สร้างใหม่"
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


def _row(emp, sku, qty=5, wh="R082", st="S1", div="D1", area="10", province="P1"):
    return {
        "emp_id": emp, "sku": sku, "qty": qty, "salestype": st, "divisioncode": div,
        "areacode": area, "provincecode": province, "warehouse_code": wh,
    }


def _dg(rows):
    return lh._normalize_grain_dtype(pd.DataFrame(rows))


class InferringTheWarehouseTest(unittest.TestCase):
    def test_it_is_taken_from_the_persons_other_rows(self):
        dims = lh.emp_dims_from_own_grain(_dg([_row("B033", "A"), _row("B033", "B")]))
        self.assertEqual(dims["B033"]["warehouse_code"], "R082")

    def test_a_person_selling_from_two_warehouses_is_not_guessed(self):
        """คนหนึ่งขายหลายคลังได้จริง — เดาไปจะเป็นการสร้างแถวผิดคลัง"""
        dims = lh.emp_dims_from_own_grain(
            _dg([_row("S543", "A", wh="G010"), _row("S543", "B", wh="G080")])
        )
        self.assertEqual(dims["S543"]["warehouse_code"], "")

    def test_conflicting_warehouses_do_not_throw_the_whole_person_away(self):
        """
        ต่างจาก dim ตัวอื่น: เขต/ดิวิชันขัดกัน = ทิ้งทั้งคน (สร้างแถวผิดเขตแย่กว่าไม่สร้าง)
        แต่คลังขัดกันต้องยังเดา dim ที่เหลือให้ ไม่งั้น SKU ของคนที่ขายหลายคลัง
        จะถูกตัดทิ้งไปด้วยทั้งที่เรื่องเดิมไม่ได้เกี่ยวกับคลังเลย
        """
        dims = lh.emp_dims_from_own_grain(
            _dg([_row("S543", "A", wh="G010"), _row("S543", "B", wh="G080")])
        )
        self.assertIn("S543", dims)
        self.assertEqual(dims["S543"]["salestype"], "S1")
        self.assertEqual(dims["S543"]["areacode"], "10")

    def test_a_blank_warehouse_everywhere_stays_blank(self):
        dims = lh.emp_dims_from_own_grain(_dg([_row("E9", "A", wh=""), _row("E9", "B", wh="")]))
        self.assertEqual(dims["E9"]["warehouse_code"], "")

    def test_a_missing_area_still_disqualifies_the_person(self):
        """ด่านเดิมต้องไม่หลวมลงเพราะมีคอลัมน์ใหม่เพิ่มเข้ามา"""
        dims = lh.emp_dims_from_own_grain(_dg([_row("E8", "A", area="")]))
        self.assertNotIn("E8", dims)


class TheNewRowGetsThatWarehouseTest(unittest.TestCase):
    """ปลายทางมาก่อนค่าจากฝั่งแอปเสมอ — กติกาเดียวกับกิ่งที่เจอ grain"""

    def _expand(self, alloc_wh, grain_rows):
        df_alloc = pd.DataFrame([{
            "emp_id": "B033", "sku": "NEW1", "allocated_boxes": 7,
            "warehouse_code": alloc_wh,
        }])
        out, _ = lh._expand_allocations_with_tga_grain(
            df_alloc, "SL376", 9, 2026, dg=_dg(grain_rows), infer_missing_dims=True
        )
        return out

    def test_a_new_pair_lands_in_the_same_warehouse_as_the_rest(self):
        out = self._expand("", [_row("B033", "OLD1"), _row("B033", "OLD2")])
        self.assertEqual(len(out), 1)
        self.assertEqual(out.iloc[0]["warehouse_code"], "R082")
        self.assertTrue(bool(out.iloc[0]["dims_inferred"]))

    def test_the_destination_wins_over_the_value_the_app_carried(self):
        out = self._expand("G010", [_row("B033", "OLD1"), _row("B033", "OLD2")])
        self.assertEqual(out.iloc[0]["warehouse_code"], "R082")

    def test_the_app_value_is_still_the_fallback_when_nothing_can_be_inferred(self):
        out = self._expand("G010", [_row("B033", "OLD1", wh="G010"),
                                    _row("B033", "OLD2", wh="G080")])
        self.assertEqual(out.iloc[0]["warehouse_code"], "G010")

    def test_rows_that_match_an_existing_pair_are_untouched_by_this_change(self):
        """กิ่งที่เจอ grain ต้องยังใช้คลังของแถวปลายทางเหมือนเดิม"""
        df_alloc = pd.DataFrame([{
            "emp_id": "B033", "sku": "OLD1", "allocated_boxes": 4, "warehouse_code": "G010",
        }])
        out, _ = lh._expand_allocations_with_tga_grain(
            df_alloc, "SL376", 9, 2026,
            dg=_dg([_row("B033", "OLD1", wh="R082")]), infer_missing_dims=True,
        )
        self.assertEqual(out.iloc[0]["warehouse_code"], "R082")


if __name__ == "__main__":
    unittest.main()
