"""
รายการ SKU ที่ต้องไปแก้เองใน Target Sun ต้องไม่หายไปพร้อมกับกล่อง

ผู้ใช้รายงาน 10 ก.ย. 2026: "พอกดปิดไปแล้วมันหายไปเลย ทำให้บางคนอาจจะแก้ไม่ครบ"
เดิมรายการนี้ขึ้นครั้งเดียวหลังส่ง ไม่มีทางเปิดดูซ้ำ — และในเส้นทาง "ยอดลงจริงไม่ตรงไฟล์"
มันถูกพูดถึงเป็นประโยคเดียวโดยไม่เคยแสดงรายการเลย

ทางที่ผู้ใช้เลือก: **แถบค้างบนหน้าผลลัพธ์ + กดเปิดกล่องเดิมซ้ำได้** (ไม่มีช่องติ๊กรายข้อ)
จะได้ไม่มีหน้าตาใหม่ให้ต้องเรียนรู้

เทสต์นี้อ่านซอร์ส frontend/app.js ตรง ๆ เพราะเป็นสถานะฝั่งเบราว์เซอร์ล้วน
(พฤติกรรมจริงยืนยันแยกต่างหากด้วยการเรนเดอร์บน Chrome headless ผ่าน file://)
"""

from __future__ import annotations

import os
import re
import sys
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

APP_JS = os.path.join(REPO, "frontend", "app.js")
INDEX_HTML = os.path.join(REPO, "frontend", "index.html")
STYLE_CSS = os.path.join(REPO, "frontend", "style.css")


class PendingTopupBannerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(APP_JS, encoding="utf-8") as f:
            cls.src = f.read()
        with open(INDEX_HTML, encoding="utf-8") as f:
            cls.html = f.read()

    def _fn(self, name, span=1400):
        i = self.src.index(name)
        return self.src[i : i + span]

    def test_the_banner_has_a_home_in_the_markup(self):
        """บทเรียนวันที่ 8 ก.ย.: ฟังก์ชันมีแต่ไม่มีที่ให้วาด = ฟีเจอร์ตายเงียบ"""
        self.assertIn('id="step3TopupBanner"', self.html)
        self.assertIn("syncPendingTopupBanner", self.src)

    def test_banner_is_redrawn_every_time_the_result_table_is_drawn(self):
        """เข้าหน้าใหม่/สลับทีม/กระจายใหม่ ต้องเห็นแถบเสมอถ้ายังมีงานค้าง"""
        i = self.src.index("function renderResult(")
        j = self.src.index("function renderResultFooter(", i)
        self.assertIn("syncPendingTopupBanner();", self.src[i:j])

    def test_it_reads_from_storage_not_from_memory(self):
        """ปิดหน้าต่างแล้วเปิดใหม่ต้องยังเห็น — ถ้าอ่านจากตัวแปรในหน้าจะหายทุกครั้งที่รีเฟรช"""
        body = self._fn("function syncPendingTopupBanner(")
        self.assertIn("_loadPendingTopup()", body)
        self.assertIn("localStorage.getItem", self._fn("function _loadPendingTopup("))

    def test_storage_key_separates_team_and_period(self):
        """ทีมอื่น/งวดอื่นต้องไม่เห็นงานค้างของทีมนี้"""
        body = self._fn("function _topupKey(", 400)
        self.assertIn("S.targetYear", body)
        self.assertIn("S.targetMonth", body)
        self.assertIn("supId", body)

    def test_saved_once_at_the_point_that_covers_every_send_exit(self):
        """
        เส้นทางหลังส่งมีสามทางออก: ส่งครบ · หยุดกลางคัน · ยอดลงจริงไม่ตรงไฟล์
        ต้องเก็บที่จุดเดียวก่อนแยกทาง ไม่งั้นบางทางจะไม่ได้เก็บ (ทางที่สามเคยไม่แสดงรายการเลย)
        """
        i = self.src.index("const pending = _mergeShortfall(")
        seg = self.src[i : i + 500]
        self.assertIn("_savePendingTopup(pending)", seg)
        self.assertIn("sentCount > 0", seg)
        # ต้องมาก่อนทางแยกทั้งสาม
        self.assertLess(
            self.src.index("_savePendingTopup(pending)"),
            self.src.index("_showPartialSendSummaryModal({"),
        )

    def test_a_clean_send_clears_the_old_list(self):
        """ส่งรอบใหม่แล้วไม่เหลืออะไรต้องแก้ = แถบเก่าต้องหาย ไม่ใช่ค้างหลอกไปเรื่อย ๆ"""
        body = self._fn("function _savePendingTopup(")
        self.assertIn("if (!items.length)", body)
        self.assertIn("localStorage.removeItem", body)

    def test_view_button_reuses_the_same_modal(self):
        """ผู้ใช้สูงวัย — ห้ามมีหน้าตาใหม่ให้เรียนรู้ ต้องเป็นกล่องเดิมเป๊ะ"""
        body = self._fn("function showPendingTopupList(")
        self.assertIn("_showShortfallModal(", body)
        self.assertIn("alreadySent: true", body)

    def test_done_button_asks_before_throwing_the_list_away(self):
        """กดพลาดแล้วเปิดกลับมาดูไม่ได้อีก — ต้องถามก่อน และบอกด้วยว่ากู้ไม่ได้"""
        body = self._fn("function confirmPendingTopupDone(")
        self.assertIn("_showInfoModal(", body)
        self.assertIn("ไม่ได้", body)
        self.assertIn("_clearPendingTopup()", body)

    def test_storage_failures_never_break_the_page(self):
        """โหมดส่วนตัว/บล็อก storage ต้องไม่ทำให้หน้าส่งพัง"""
        for fn in (
            "function _savePendingTopup(",
            "function _loadPendingTopup(",
            "function _clearPendingTopup(",
        ):
            with self.subTest(fn=fn):
                self.assertIn("catch", self._fn(fn))

    def test_stored_list_is_trimmed(self):
        """รายชื่อคนยาวเป็นร้อยไม่ได้ช่วยอะไรและกินที่เก็บ — ตัดให้พอดีกับที่กล่องแสดง"""
        body = self._fn("function _savePendingTopup(")
        self.assertTrue(re.search(r"\.slice\(0,\s*50\)", body), "จำกัดจำนวน SKU")
        self.assertTrue(re.search(r"\.slice\(0,\s*25\)", body), "จำกัดจำนวนคนต่อ SKU")

    def test_it_keeps_the_number_the_user_needs(self):
        """ยอดที่ปลายทางถืออยู่คือตัวที่ต้องใช้ตอนไปแก้มือ — เก็บไว้ด้วย ไม่ใช่แค่หีบที่ขาด"""
        body = self._fn("function _savePendingTopup(")
        self.assertIn("current_targetsun_boxes", body)
        self.assertIn("expected_boxes", body)

    def test_banner_styles_use_theme_tokens(self):
        """ใช้ตัวแปรสีของธีม ไม่ใช่สีตายตัว — ไม่งั้นโหมดมืดอ่านไม่ออก"""
        with open(STYLE_CSS, encoding="utf-8") as f:
            css = f.read()
        i = css.index(".topup-banner {")
        block = css[i : i + 500]
        self.assertIn("var(--amber-bg)", block)
        self.assertIn("var(--amber-brd)", block)


if __name__ == "__main__":
    unittest.main()
