"""
โหมดรวมภาค/รวมหน่วย — บันทึกหลัง autoRebalance ต้องแยกทีม ไม่ปนข้ามทีม (I7)

ของจริงที่ทำให้ต้องแก้ (24 ก.ย. 2026): autoRebalance() เรียก saveDraft(true) แบบไม่เช็ค
โหมดรวมภาคเลย → queueServerAllocationSave() default sup_id=S.supId (ทีมเจ้าของที่กำลังดู
อยู่) + allocations=S.allocations (แถวของ "ทุกทีม" ที่ merge ไว้บนจอ) ทำให้ snapshot ของ
ทีมเจ้าของปนแถวของเพื่อนทีมทุกครั้งที่ autoRebalance เกลี่ยแล้วเปลี่ยนอะไรสักอย่าง — จุดที่
เรียก autoRebalance ต่อ (_doOptimize, revertResultCell, onResultEdit,
runReAllocationForSkus) บางจุดมีการเช็ค S.compositeAllocView/_regionalAggregateWritable()
อยู่แล้วก็จริง แต่เช็ค "หลัง" autoRebalance คืนค่ามาแล้ว ไม่ทันกันการบันทึกผิดที่เกิดขึ้น
ข้างในฟังก์ชันนั้นเองก่อน

ไม่มี test runner ฝั่ง JS ในโปรเจกต์นี้ จึงตรวจที่ source แบบเดียวกับ
tests/test_send_order_frontend.py — หยาบแต่จับ regression ที่สำคัญที่สุดได้
"""

from __future__ import annotations

import os
import re
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
APP_JS = os.path.join(REPO, "frontend", "app.js")

REGION_CHECK = "S.compositeAllocView && _regionalAggregateWritable()"
REGIONAL_SAVE = "queueRegionalAllocationSave("


def _read_src() -> str:
    with open(APP_JS, encoding="utf-8") as f:
        return f.read()


def _function_body(name: str) -> str:
    """
    ตัดตัวฟังก์ชันจาก app.js ด้วยการนับวงเล็บปีกกา (ไม่มี JS parser ให้ใช้) — ข้าม
    วงเล็บปีกกาใน default param (เช่น `opts = {}`) ก่อนหา body จริง ไม่งั้นเจอ `{` แรก
    ในบรรทัด signature แล้วหยุดทันที (ได้ `{}` ว่างของ default param แทน body จริง)
    """
    src = _read_src()
    m = re.search(rf"^(?:async\s+)?function {re.escape(name)}\(", src, re.MULTILINE)
    if not m:
        raise AssertionError(f"ไม่พบฟังก์ชัน {name} ใน app.js")
    paren_depth = 1
    i = m.end()
    while paren_depth > 0:
        if src[i] == "(":
            paren_depth += 1
        elif src[i] == ")":
            paren_depth -= 1
        i += 1
    start = src.index("{", i)
    depth = 0
    for j in range(start, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError(f"หาปลายฟังก์ชัน {name} ไม่เจอ")


class AutoRebalanceRoutesThroughRegionalSaveTest(unittest.TestCase):
    def setUp(self):
        self.src = _function_body("autoRebalance")

    def test_checks_region_scope_before_saving(self):
        self.assertIn(
            REGION_CHECK, self.src,
            "autoRebalance ต้องเช็คโหมดรวมภาคเองก่อนตัดสินใจว่าจะบันทึกทางไหน "
            "(ผู้เรียกที่เช็คไว้แล้ว 'หลัง' เรียกฟังก์ชันนี้ไม่ทันกันการบันทึกผิดที่เกิดในนี้)",
        )

    def test_calls_regional_save_when_in_region_scope(self):
        self.assertIn(
            REGIONAL_SAVE, self.src,
            "ต้องมีทางเรียก queueRegionalAllocationSave ในตัวฟังก์ชันเอง ไม่ใช่แค่ saveDraft เพียวๆ",
        )

    def test_region_check_precedes_the_regional_save_call(self):
        idx_check = self.src.index(REGION_CHECK)
        idx_save = self.src.index(REGIONAL_SAVE)
        self.assertLess(idx_check, idx_save, "ต้องเช็คโหมดก่อน แล้วค่อยเรียก save ตามเงื่อนไข")

    def test_still_falls_back_to_save_draft_outside_region_scope(self):
        """ต้องไม่ทิ้งพฤติกรรมเดิมสำหรับกรณีปกติ (ไม่ใช่โหมดรวมภาค)"""
        self.assertIn("saveDraft(true)", self.src)


class OtherAutoRebalanceCallersAlsoRouteCorrectlyTest(unittest.TestCase):
    """
    จุดอื่นที่เรียก autoRebalance() แล้วตามด้วย saveDraft(true) ของตัวเองอีกที (ไม่ได้พึ่ง
    แค่พฤติกรรมภายใน autoRebalance) ก็ต้องเช็คโหมดรวมภาคเองด้วย เจอ 2 จุดที่ไม่เคยเช็คเลย
    ระหว่างตรวจสอบ (revertResultCell, runReAllocationForSkus) — ไม่ใช่แค่ 3 จุดที่รายงาน
    ตรวจสอบระบบอ้างถึงตอนแรก (_doOptimize/revertAllResultCells/onResultEdit)
    """

    def _assert_routes_correctly(self, fn_name: str):
        src = _function_body(fn_name)
        self.assertIn(
            "autoRebalance(", src,
            f"{fn_name} ควรยังเรียก autoRebalance เหมือนเดิม (แค่ตรวจว่า save ถูกทางด้วย)",
        )
        idx_rebalance = src.index("autoRebalance(")
        # ต้องมีการเช็คโหมดรวมภาคอยู่ "หลัง" autoRebalance (ไม่ว่าจะมาจากใน
        # autoRebalance เองหรือโค้ดของฟังก์ชันนี้เอง) ก่อนจะไปถึง saveDraft(true) ตัวถัดไป
        after_rebalance = src[idx_rebalance:]
        self.assertIn(
            REGION_CHECK, after_rebalance,
            f"{fn_name}: ต้องมีการเช็คโหมดรวมภาคก่อนบันทึกหลัง autoRebalance",
        )

    def test_revert_result_cell(self):
        self._assert_routes_correctly("revertResultCell")

    def test_run_reallocation_for_skus(self):
        self._assert_routes_correctly("runReAllocationForSkus")


if __name__ == "__main__":
    unittest.main()
