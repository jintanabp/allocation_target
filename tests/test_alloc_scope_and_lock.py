"""
ขอบเขตการกระจาย (modal) + "คลิกช่องแล้วล็อกค่า" — ตรวจจากซอร์สหน้าเว็บ

ทั้งสองเรื่องเป็นตรรกะฝั่ง browser ที่เทส Python รันจริงไม่ได้ แต่ "พังเงียบ" ได้ง่าย:
  - ตัวเลือกขอบเขตย้ายจากเรดิโอในการ์ดไปเป็น modal ถ้าใครเผลอเอา
    `input[name="allocScope"]` กลับมา หน้าเว็บจะมีสองที่ให้ตั้งค่าและไม่ตรงกัน
  - เดิมคลิกช่องแล้วออกโดยไม่เปลี่ยนเลข = ไม่ล็อก ตัวเกลี่ยจึงกวาดเลขที่ตั้งใจคงไว้
    ทิ้งตอนไปแก้ช่องอื่น ถ้ามีใคร revert สาขานี้กลับ ผู้ใช้จะไม่มีทางบอกระบบว่า
    "ช่องนี้ห้ามขยับ" อีกเลย
"""
from __future__ import annotations

import os
import re
import sys
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)


def _read(rel: str) -> str:
    with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
        return fh.read()


APP = _read("frontend/app.js")
HTML = _read("frontend/index.html")


class TestAllocScopeIsAModal(unittest.TestCase):
    def test_the_inline_radio_group_is_gone(self):
        self.assertNotIn('name="allocScope"', HTML)

    def test_the_card_only_shows_the_current_choice(self):
        self.assertIn('id="allocScopeValue"', HTML)
        self.assertIn('id="allocScopeChangeBtn"', HTML)

    def test_the_change_button_uses_a_listener_not_inline_onclick(self):
        """กติกา boy-scout: template ที่แตะรอบนี้ต้องเลิก onclick= ใน HTML"""
        self.assertNotIn('onclick="openAllocScopeModal()"', HTML)
        self.assertIn('changeBtn.addEventListener("click"', APP)

    def test_scope_comes_from_state_not_the_dom(self):
        """อ่านจาก S.allocScope — DOM ไม่ใช่แหล่งความจริงอีกแล้ว"""
        fn = _func_source(APP, "_selectedAllocScope")
        self.assertIn("S.allocScope", fn)
        self.assertNotIn("querySelector", fn)

    def test_scope_resets_on_every_data_load(self):
        """ไม่จำข้ามงวด — งวดใหม่ต้องเริ่มที่ 'แยกตามทีม' เสมอ"""
        self.assertIn('S.allocScope = "team";', APP)

    def test_running_the_allocation_opens_the_modal_first(self):
        fn = _func_source(APP, "runOptimization")
        self.assertIn("openAllocScopeModal({ run: true })", fn)

    def test_the_overwrite_warning_merged_into_the_same_modal(self):
        """เดิมเป็น modal สองใบซ้อนกัน — ผู้ใช้กดผ่านใบแรกโดยไม่อ่าน"""
        self.assertNotIn("_confirmRegionalReallocateIfNeeded", APP)
        self.assertIn("_pendingReallocateTeams", APP)

    def test_owner_team_picker_is_gone(self):
        """เป้าไม่ได้อยู่ที่ทีมใดทีมหนึ่งแล้ว — บวกรวมทั้งภาค"""
        self.assertNotIn("allocScopeOwner", HTML)
        self.assertNotIn("_allocScopeOwnerSup", APP)

    def test_unit_mode_sends_every_team_as_the_target_pool(self):
        fn = _func_source(APP, "_doOptimize")
        self.assertIn("target_sup_ids: supOrder", fn)
        self.assertIn("peer_sup_ids: supOrder", fn)


class TestClickToLockCell(unittest.TestCase):
    def test_clicking_without_changing_the_number_locks_it(self):
        fn = _func_source(APP, "onResultEdit")
        self.assertIn("val === prev && !wasEdited", fn)
        self.assertIn("alloc.is_edited = true", fn)
        self.assertNotIn(
            'el.classList.remove("is-edited");\n    return;',
            fn,
            "สาขาเดิมที่ 'ไม่ถือว่าแก้' ต้องไม่กลับมา",
        )

    def test_the_locked_cell_gets_its_unlock_button_without_a_full_render(self):
        self.assertIn("function _syncCellRevertButton", APP)
        self.assertIn("_syncCellRevertButton(el, a);", APP)

    def test_locking_is_persisted(self):
        self.assertIn("function _persistAfterCellLock", APP)
        self.assertIn("_persistAfterCellLock();", APP)

    def test_locked_cells_are_sent_as_locked_edits_on_recalculation(self):
        """สายเดิม: is_edited -> _collectLockedEdits -> locked_edits ใน payload"""
        fn = _func_source(APP, "_collectLockedEdits")
        self.assertIn("a.is_edited", fn)
        self.assertIn("locked_boxes", fn)


def _func_source(src: str, name: str) -> str:
    """ตัวฟังก์ชันตั้งแต่ `function name(` จนถึงปีกกาปิดที่คอลัมน์ 0"""
    m = re.search(rf"^(?:async )?function {re.escape(name)}\(", src, re.M)
    assert m, f"ไม่พบฟังก์ชัน {name} ใน app.js"
    end = src.find("\n}\n", m.start())
    assert end > 0, f"หาปลายฟังก์ชัน {name} ไม่เจอ"
    return src[m.start():end + 3]


if __name__ == "__main__":
    unittest.main()
