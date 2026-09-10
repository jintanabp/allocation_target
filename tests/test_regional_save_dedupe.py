"""
ลูปกระจายรวมภาคต้องไม่เขียนไฟล์ผลกระจายซ้ำเมื่อไม่มีอะไรเปลี่ยน

ผลตรวจรอบ 0 (docs/round0-findings-2026-09.md §7): ในชั่วโมงเดียว SL523 ถูกบันทึก
220 ครั้งโดยผู้ใช้คนเดียว · SL406/SL532 ทีมละ 154 ครั้ง — เพราะทุกครั้งที่ autosave
เด้ง ลูปจะบันทึก **ทุกทีมในภาค** ทั้งที่ผู้ใช้แก้ช่องเดียวซึ่งกระทบทีมเดียว
การบันทึกหนึ่งครั้ง = เขียนไฟล์ทั้งก้อนใหม่ (มัธยฐาน 3,230 แถวต่อทีม)

เทสต์นี้อ่านซอร์ส frontend/app.js ตรง ๆ (ท่าเดียวกับ test_no_target_employees.py)
เพราะตรรกะอยู่ฝั่งเบราว์เซอร์และผูกกับ state ของหน้าจอ
"""

from __future__ import annotations

import os
import re
import sys
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

APP_JS = os.path.join(REPO, "frontend", "app.js")


class RegionalSaveDedupeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(APP_JS, encoding="utf-8") as f:
            cls.src = f.read()
        i = cls.src.index("async function saveRegionalAllocationSnapshots(")
        cls.body = cls.src[i : i + 2600]

    def test_skips_teams_whose_rows_did_not_change(self):
        """หัวใจของข้อนี้ — ลายเซ็นตรงกับครั้งก่อน = ข้ามไปเลย ไม่ต้องยิง PUT"""
        self.assertIn("_allocFingerprint(rows, supStatus)", self.body)
        self.assertIn("_lastRegionalSaveFp.get(fpKey) === fp", self.body)
        skip = self.body.index("_lastRegionalSaveFp.get(fpKey) === fp")
        put = self.body.index("await saveServerAllocationSnapshot(")
        self.assertLess(skip, put, "ต้องเช็คก่อนยิง ไม่ใช่หลังยิง")

    def test_skipped_team_still_counts_as_saved(self):
        """ของบน server ตรงกับบนจออยู่แล้ว — ต้องไม่ไปแสดงว่าทีมนั้นยังไม่ถูกบันทึก"""
        seg = self.body[self.body.index("_lastRegionalSaveFp.get(fpKey) === fp") :][:220]
        self.assertIn("saved.push(supId)", seg)

    def test_fingerprint_is_recorded_only_after_a_successful_write(self):
        """บันทึกล้มแล้วจำว่าเขียนไปแล้ว = ข้อมูลบน server ค้างเก่าถาวรโดยไม่มีใครรู้"""
        put = self.body.index("await saveServerAllocationSnapshot(")
        setfp = self.body.index("_lastRegionalSaveFp.set(fpKey, fp)")
        self.assertLess(put, setfp, "ต้อง set หลัง await เท่านั้น")
        self.assertIn("_lastRegionalSaveFp.delete(", self.body, "catch ต้องลืมลายเซ็นทีมนั้น")

    def test_summary_refresh_only_when_something_was_written(self):
        """ข้ามทั้งภาคแล้วยังยิงโหลดสรุปการใช้งานใหม่ = ย้ายภาระไปอีกที่หนึ่งเฉย ๆ"""
        self.assertIn("if (wrote) {", self.body)

    def test_fingerprint_covers_moves_not_just_totals(self):
        """ย้ายหีบจาก A ไป B ผลรวมเท่าเดิม — ถ้าลายเซ็นดูแค่ผลรวมจะข้ามการบันทึกที่ต้องเขียนจริง"""
        i = self.src.index("function _allocFingerprint(")
        fp = self.src[i : i + 900]
        for field in ("emp_id", "sku", "warehouse_code", "allocated_boxes", "is_edited"):
            self.assertIn(field, fp, f"ลายเซ็นต้องรวม {field}")

    def test_fingerprint_key_includes_the_period(self):
        """เปลี่ยนงวดแล้วลายเซ็นของงวดก่อนต้องไม่ไปข้ามการบันทึกงวดใหม่"""
        i = self.src.index("function _regionalSaveFpKey(")
        key = self.src[i : i + 260]
        self.assertIn("S.targetYear", key)
        self.assertIn("S.targetMonth", key)

    def test_memory_is_dropped_whenever_the_server_copy_may_differ(self):
        """
        สามทางที่ของบน server อาจไม่ใช่ชุดที่เราจำไว้:
        เปลี่ยนทีม/งวด · มีคนอื่นเขียนทับ (409) · ผู้ใช้สั่งเริ่มกระจายใหม่ (ลบ snapshot)
        """
        for fn in (
            "function _clearCompositeAllocState(",
            "async function _handleSnapshotConflict(",
            "async function restartAllocation(",
        ):
            i = self.src.index(fn)
            with self.subTest(fn=fn):
                self.assertIn(
                    "_resetRegionalSaveFingerprints()",
                    self.src[i : i + 900],
                    f"{fn} ต้องล้างลายเซ็นที่จำไว้",
                )

    def test_single_team_autosave_is_untouched(self):
        """ทีมเดี่ยว: แก้ 1 ครั้ง = เขียน 1 ครั้ง อยู่แล้ว ไม่ใช่ปัญหาที่กำลังแก้"""
        i = self.src.index("function queueServerAllocationSave(")
        self.assertNotIn("_lastRegionalSaveFp", self.src[i : i + 500])

    def test_fingerprint_uses_imul_not_plain_multiply(self):
        """hash แบบ FNV ต้องคูณแบบ 32 บิต ไม่งั้นค่าเกิน 2^53 แล้วชนกันมั่ว"""
        i = self.src.index("function _allocFingerprint(")
        self.assertIn("Math.imul(", self.src[i : i + 900])
        self.assertTrue(
            re.search(r">>>\s*0", self.src[i : i + 900]),
            "ต้องบังคับเป็น unsigned 32-bit",
        )


if __name__ == "__main__":
    unittest.main()
