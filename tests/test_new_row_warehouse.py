"""
แถวเป้าที่ "สร้างใหม่" (คู่พนักงาน×สินค้าที่ไม่เคยมีเป้าใน TGA เลย) ได้คลังว่างเสมอ

**นโยบายปัจจุบัน (22 ก.ย. 2026, ตัดสินใจโดยผู้ใช้):** "คู่ใหม่ควรได้คลังว่างไว้ก่อนดีกว่า"
แทนที่การเดาคลัง (จากแถวอื่นของพนักงานคนเดียวกัน หรือจากประวัติขาย 2 ปีที่แอป carry มา
กับ request) เพราะเดาผิดเคยสร้างความเสียหายจริงมาแล้ว (ดู
tests/test_destination_blank_warehouse_wins.py — SL380/SL530/SL525 เป้าเบิ้ล 2-4 เท่า)
ในขณะที่คลังว่างของคู่ใหม่ไม่ชนคีย์ upsert กับอะไรเลย (ไม่มีแถวอื่นของคู่นี้อยู่ก่อนให้ชน)

**ประวัติ (นโยบายเดิม ถูกแทนที่แล้ว):** ของเดิม (แก้ 11 ก.ย. 2026 จากเคส SL376/B033 —
มีเป้าที่ปลายทาง 246 แถว ใช้คลัง R082 ทุกแถว แต่แถวใหม่ที่ระบบสร้างให้เขาได้คลังว่างทั้งหมด
วัดทั้งชุด ~7,800 แถวเป็นแบบนี้ ~2,500 แถวในนั้นมีหีบ > 0) เคยแก้โดยเดาคลังจากแถวอื่นของ
พนักงานคนเดียวกันแทน — **นโยบายนั้นถูกแทนที่แล้ว** `emp_dims_from_own_grain` ยังใช้อยู่
สำหรับ dim อื่น (salestype/divisioncode/areacode/provincecode) แต่ไม่ใช้ warehouse_code
ของมันอีกต่อไป (ตัวฟังก์ชันเองยังคำนวณค่านี้ไว้เหมือนเดิม — แค่จุดเรียกใช้เลิกดึงมาใช้)

กิ่งที่ "เจอคู่ใน grain" (sub ไม่ว่าง) ไม่เกี่ยวกับไฟล์นี้ — คลังของปลายทางถูกใช้ตรง ๆ เสมอ
ไม่เดา (ทดสอบที่ tests/test_destination_blank_warehouse_wins.py) ไฟล์นี้ครอบเฉพาะกิ่ง
"ไม่เจอ → สร้างใหม่" (sub.empty) เท่านั้น
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
    """คู่ใหม่ (sub.empty) ไม่มีคลังจริงให้เชื่อ — ว่างไว้เสมอ ไม่เดาจากที่ไหนทั้งนั้น"""

    def _expand(self, alloc_wh, grain_rows):
        df_alloc = pd.DataFrame([{
            "emp_id": "B033", "sku": "NEW1", "allocated_boxes": 7,
            "warehouse_code": alloc_wh,
        }])
        out, _ = lh._expand_allocations_with_tga_grain(
            df_alloc, "SL376", 9, 2026, dg=_dg(grain_rows), infer_missing_dims=True
        )
        return out

    def test_a_new_pair_gets_a_blank_warehouse_even_when_the_persons_other_rows_agree(self):
        out = self._expand("", [_row("B033", "OLD1"), _row("B033", "OLD2")])
        self.assertEqual(len(out), 1)
        self.assertEqual(out.iloc[0]["warehouse_code"], "")
        # dim อื่น (salestype/divisioncode/areacode/provincecode) ยังเดาได้ตามเดิม —
        # นโยบาย "ว่างไว้ก่อน" ครอบเฉพาะ warehouse_code เท่านั้น
        self.assertTrue(bool(out.iloc[0]["dims_inferred"]))
        self.assertEqual(out.iloc[0]["areacode"], "10")

    def test_the_app_carried_value_is_ignored_even_when_the_persons_other_rows_agree_with_it(self):
        out = self._expand("R082", [_row("B033", "OLD1"), _row("B033", "OLD2")])
        self.assertEqual(out.iloc[0]["warehouse_code"], "")

    def test_the_app_carried_value_is_ignored_when_nothing_can_be_inferred_either(self):
        out = self._expand("G010", [_row("B033", "OLD1", wh="G010"),
                                    _row("B033", "OLD2", wh="G080")])
        self.assertEqual(out.iloc[0]["warehouse_code"], "")

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
