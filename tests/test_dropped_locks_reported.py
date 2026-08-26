"""
ล็อกที่เครื่องคำนวณไม่ได้ใช้ ต้องถูกบอกกลับไปให้หน้าจอรู้

หน้าจอ (_mergeLockedEditsIntoAllocs) เอาล็อกทุกตัวยัดกลับเข้าผลลัพธ์เสมอ ตัวไหน
ไม่มีแถวรองรับก็สร้างแถวใหม่ให้ · ส่วน server ตัดล็อกของพนักงานที่ไม่เข้าเกณฑ์
(เป้าเงิน 0 / ถูกคัดออก) และของ SKU ที่ไม่อยู่ในเป้ารอบนั้นทิ้งเงียบ ๆ ล็อกพวกนี้จึง
กลับเข้าไปในผลฝั่งเบราว์เซอร์ ทำให้ยอดต่อ SKU เกินเป้า แล้วตัวเกลี่ยอัตโนมัติไปหัก
จากคนอื่นแทน — ยอดรวมดูตรงเป้า แต่ตัวเลขรายคนไม่ใช่สิ่งที่ server คำนวณ
และไม่มีอะไรบนจอบอกผู้ใช้เลยว่าค่าที่ล็อกไว้ไม่ได้ถูกใช้
"""

from __future__ import annotations

import inspect
import logging
import os
import sys
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import optimize as opt  # noqa: E402

logging.disable(logging.CRITICAL)


class TestDroppedLocksAreReported(unittest.TestCase):
    def setUp(self):
        self.src = inspect.getsource(opt.run_optimization_service)

    def test_response_carries_dropped_locks(self):
        self.assertIn('"dropped_locks": dropped_locks', self.src)

    def test_both_drop_reasons_are_labelled(self):
        """ผู้ใช้ต้องอ่านออกว่าล็อกหายเพราะอะไร ไม่ใช่แค่ 'หายไป'"""
        self.assertIn('"reason": "employee_not_eligible"', self.src)
        self.assertIn('"reason": "sku_not_in_target"', self.src)

    def test_dropped_locks_keep_the_original_employee_key(self):
        """
        emp_id ที่ส่งเข้าเครื่องคำนวณถูกรวมกับรหัสคลังแล้ว (_lock_or_emp_id)
        ถ้าไม่ส่งคีย์ตัวเดิมกลับไป หน้าจอจะจับคู่ล็อกไม่เจอแล้วยัดกลับเหมือนเดิม
        """
        self.assertIn('"orig_emp_id"', self.src)
        self.assertIn('"warehouse_code"', self.src)

    def test_locks_of_ineligible_employees_never_reach_the_engine(self):
        head = self.src.split("dropped_locks", 1)[0]
        self.assertIn("eligible_set", head + self.src)
        i_drop = self.src.index('"employee_not_eligible"')
        i_append = self.src.index("locked_edits_data.append")
        self.assertLess(i_drop, i_append, "ต้องคัดออกก่อนจะใส่เข้ารายการที่ส่งให้เครื่องคำนวณ")

    def test_sku_filter_uses_the_final_target_frame(self):
        """
        เป้าหีบถูกกรองมาหลายชั้น (หีบ 0 / only_skus) — ต้องเทียบกับ df_sku ตัวสุดท้าย
        ไม่ใช่ตัวก่อนกรอง ไม่งั้นล็อกของ SKU ที่ถูกตัดออกจะยังหลุดผ่านไปได้
        """
        self.assertIn('_sku_in_target = {str(r["sku"]).strip() for _, r in df_sku.iterrows()}', self.src)
        self.assertLess(
            self.src.index('df_sku[df_sku["sku"].isin(set(only_skus))]'),
            self.src.index("_sku_in_target"),
        )


if __name__ == "__main__":
    unittest.main()
