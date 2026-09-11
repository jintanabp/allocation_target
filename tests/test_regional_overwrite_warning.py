"""
เตือนก่อนกระจายทั้งภาคทับทีมที่ส่งเข้า Target Sun ไปแล้ว

ผลตรวจรอบ 0 §6 เรียกเรื่องนี้ว่า "ร้ายที่สุดในผลตรวจ": มี 8 ทีมที่สถานะกลับไปเป็น
"แบบร่าง" ทั้งที่เคยส่งเข้า Target Sun แล้ว (SL523 ถูกทับหลังส่งไป 29 นาที)
แปลว่าเลขในแอปกับเลขใน Target Sun ไม่ตรงกันอยู่ โดยไม่มีใครรู้

สาเหตุ: ลูปกระจายทั้งภาคข้ามด่านกันเขียนทับโดยตั้งใจ (ไม่งั้นจะเด้งถามทีละทีมกลางลูป)
ทางแก้ที่ผลตรวจเสนอ: ตรวจก่อนเริ่มลูป แล้วให้ยืนยัน **ครั้งเดียว** พร้อมรายชื่อทีม

เทสต์นี้อ่านซอร์ส frontend/app.js ตรง ๆ (ท่าเดียวกับ test_regional_save_dedupe.py)
"""

from __future__ import annotations

import os
import sys
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

APP_JS = os.path.join(REPO, "frontend", "app.js")
STYLE_CSS = os.path.join(REPO, "frontend", "style.css")


class RegionalOverwriteWarningTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(APP_JS, encoding="utf-8") as f:
            cls.src = f.read()
        i = cls.src.index("async function openAllocScopeModal(")
        # ตัดถึงฟังก์ชันถัดไป ไม่ใช่จำนวนตัวอักษรตายตัว — โมดอลใบนี้มีคนมาเพิ่มคำเตือน
        # เรื่อย ๆ (11 ก.ย. เพิ่มบล็อกกติกาการเกลี่ย) หน้าต่างตายตัวจะเลื่อนหลุดเงียบ ๆ
        # แล้วเทสแดงทั้งที่โค้ดไม่ได้พัง
        cls.modal = cls.src[i : cls.src.index("function _lakehouseTargetSkus(", i)]

    def _fn(self, name, span=900):
        i = self.src.index(name)
        return self.src[i : i + span]

    def test_sent_teams_are_detected_by_sent_timestamp_not_status(self):
        """
        ทีมที่ส่งแล้วกลับมาแก้ต่อ สถานะจะเป็น "แบบร่าง" — ถ้าดูแค่สถานะจะพลาดทั้ง 8 ทีม
        ของจริงที่อยู่ใน Target Sun ยังทับได้อยู่ดี
        """
        body = self._fn("async function _sentToTargetSunTeams(")
        self.assertIn("target_sun_sent_at", body)
        self.assertNotIn('"sent_targetsun"', body, "ห้ามกรองด้วยสถานะอย่างเดียว")

    def test_the_two_warning_lists_do_not_overlap(self):
        """ทีมเดียวโผล่สองกล่องแล้วผู้ใช้จะไม่รู้ว่าอันไหนคืออันที่ต้องระวังจริง"""
        body = self._fn("async function _pendingReallocateTeams(")
        self.assertIn("if (it.target_sun_sent_at) return false;", body)

    def test_sent_teams_get_their_own_block_with_when_it_was_sent(self):
        """รายชื่อเปล่า ๆ ไม่พอ — ต้องบอกว่าส่งเมื่อไหร่ ผู้ใช้ถึงจะตัดสินใจได้"""
        self.assertIn("scope-modal__warn--danger", self.modal)
        i = self.modal.index("scope-modal__warn--danger")
        block = self.modal[i : i + 1200]
        self.assertIn("target_sun_sent_at", block)
        self.assertIn("_formatAllocUpdatedAt(", block)
        self.assertIn("ส่งเมื่อ", block)

    def test_it_says_what_actually_goes_wrong(self):
        """ต้องบอกผลลัพธ์จริง: ของในแอปถูกทับ แต่ Target Sun ยังเป็นของเดิม = สองฝั่งไม่ตรงกัน"""
        i = self.modal.index("scope-modal__warn--danger")
        block = self.modal[i : i + 1400]
        self.assertIn("ยังเป็นของเดิม", block)
        self.assertIn("ไม่ตรงกัน", block)

    def test_old_reassuring_line_is_gone(self):
        """
        ข้อความเดิม "ทีมที่ส่ง Target Sun แล้วจะใช้เป้าจาก Target Sun เป็นฐานใหม่"
        อ่านแล้วเหมือนไม่มีอะไรต้องห่วง ซึ่งเป็นคนละเรื่องกับความเสียหายที่เกิดขึ้นจริง
        """
        self.assertNotIn("จะใช้เป้าจาก Target Sun เป็นฐานใหม่", self.src)

    def test_run_button_is_locked_until_the_user_acknowledges(self):
        """ยืนยันครั้งเดียวก่อนเริ่มลูป — ไม่ใช่เงียบแบบเดิม และไม่ใช่ถามทีละทีมกลางลูป"""
        i = self.modal.index("allocScopeAckSent")
        wiring = self.modal[i:]
        self.assertIn("runBtn.disabled = true;", wiring)
        self.assertIn("runBtn.disabled = !ack.checked;", wiring)

    def test_no_checkbox_no_lock(self):
        """ภาคที่ยังไม่มีทีมไหนส่งเลย ต้องกดเริ่มได้ทันทีเหมือนเดิม ไม่เพิ่มขั้นตอนให้เปล่า ๆ"""
        i = self.modal.index("const ack = document.getElementById")
        self.assertIn("if (ack) {", self.modal[i : i + 200])

    def test_only_checked_when_actually_running(self):
        """เปิด modal เพื่อดู/ตั้งขอบเขตเฉย ๆ ไม่ต้องไปดึงสรุปการใช้งานมาทั้งภาค"""
        self.assertIn("const sent = run ? await _sentToTargetSunTeams() : [];", self.modal)

    def test_danger_block_has_styles_in_both_themes(self):
        """ใช้ตัวแปรสีของธีม ไม่ใช่สีตายตัว — ไม่งั้นโหมดมืดอ่านไม่ออก"""
        with open(STYLE_CSS, encoding="utf-8") as f:
            css = f.read()
        i = css.index(".scope-modal__warn--danger")
        block = css[i : i + 400]
        self.assertIn("var(--red-brd)", block)
        self.assertIn("var(--red-bg)", block)
        self.assertIn(".scope-modal__ack", css)


if __name__ == "__main__":
    unittest.main()
