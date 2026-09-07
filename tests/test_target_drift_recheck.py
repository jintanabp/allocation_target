"""
ตรวจเป้าซ้ำตอนกลับมาที่แท็บ — ตรวจจากซอร์สหน้าเว็บ

เป็นตรรกะฝั่ง browser ที่เทส Python รันจริงไม่ได้ แต่พังเงียบได้ง่ายสองทาง:

  1. **ถูกถอดออก** — คนที่เกลี่ยเป้าทั้งภาคเปิดหน้าค้างไว้ทีละหลายชั่วโมง ถ้าไม่มีตัวนี้
     เขาจะรู้ว่าเป้าขยับก็ตอนกดส่งแล้วโดน 409 ซึ่งตอนนั้นกระจายหีบข้ามซุปไปหมดแล้ว
  2. **ถูกเปลี่ยนเป็น polling** — ตรวจแต่ละครั้งต้องอ่าน Target Sun ทีละทีม
     (ภาคหนึ่งมีได้ถึงสิบกว่าทีม) การยิงเป็นรอบจะกลายเป็นสิบกว่าคำขอต่อรอบต่อคน
     ไปซ้ำเติมเสียงร้องเรียนเรื่องความเร็ว 6 เสียง (12%) ที่ยังไม่มีงานแก้

กติกาที่ต้องคงไว้: ยิงเฉพาะ "ตอนกลับมาดูจริง ๆ" และต้องเว้นระยะ ไม่ใช่ทุกครั้งที่สลับแท็บ
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


def _fn_body(name: str) -> str:
    """เนื้อในฟังก์ชันแบบหยาบ ๆ — พอสำหรับตรวจว่ามีอะไรอยู่ข้างใน"""
    i = APP.index(f"function {name}(")
    return APP[i : i + 1200]


class TestRecheckOnReturnExists(unittest.TestCase):
    def test_the_installer_is_defined(self):
        self.assertIn("function _installDriftRecheckOnReturn(", APP)

    def test_it_listens_for_visibilitychange(self):
        body = _fn_body("_installDriftRecheckOnReturn")
        self.assertIn('addEventListener("visibilitychange"', body)

    def test_it_runs_the_existing_silent_check_not_a_new_one(self):
        """ใช้ตัวเดิมซ้ำ — อย่าสร้างเส้นทางตรวจเป้าเส้นที่สอง"""
        body = _fn_body("_installDriftRecheckOnReturn")
        self.assertIn("checkTargetSunDrift({ silent: true })", body)

    def test_it_is_actually_registered_at_startup(self):
        """นิยามไว้เฉย ๆ แต่ไม่มีใครเรียก = ไม่ทำงาน และไม่มีอะไรฟ้อง"""
        self.assertIn("_installDriftRecheckOnReturn();", APP)
        head = APP[APP.index('document.addEventListener("DOMContentLoaded"') :]
        self.assertIn("_installDriftRecheckOnReturn();", head)


class TestItDoesNotBecomePolling(unittest.TestCase):
    def test_it_skips_when_the_tab_is_hidden(self):
        body = _fn_body("_installDriftRecheckOnReturn")
        self.assertIn("if (document.hidden) return;", body)

    def test_it_keeps_a_minimum_gap_between_checks(self):
        body = _fn_body("_installDriftRecheckOnReturn")
        self.assertIn("DRIFT_RECHECK_AFTER_MS", body)
        self.assertIn("_lastDriftCheckAt", body)

    def test_the_gap_is_at_least_fifteen_minutes(self):
        m = re.search(r"DRIFT_RECHECK_AFTER_MS\s*=\s*([^;]+);", APP)
        self.assertIsNotNone(m, "ต้องมีค่าเว้นระยะประกาศไว้ชัด ๆ")
        self.assertEqual(
            eval(m.group(1).replace("*", "*")),  # noqa: S307 - ค่าคงที่ในซอร์สเราเอง
            30 * 60 * 1000,
        )

    def test_the_check_stamps_the_time_it_ran(self):
        """ถ้าไม่ประทับเวลา ตัวเว้นระยะจะไม่มีผล แล้วสลับแท็บทีก็ยิงที"""
        body = _fn_body("checkTargetSunDrift")
        self.assertIn("_lastDriftCheckAt = Date.now();", body)

    def test_no_interval_timer_drives_the_drift_check(self):
        for m in re.finditer(r"setInterval\((.{0,200})", APP, re.S):
            self.assertNotIn(
                "checkTargetSunDrift", m.group(1),
                "ห้ามเปลี่ยนเป็น polling — อ่าน Target Sun ทีละทีม ภาคหนึ่งสิบกว่าทีม",
            )


class TestCacheBusterMovedWithTheScript(unittest.TestCase):
    """แก้ app.js แล้วไม่ขยับ ?v= = ผู้ใช้ได้ไฟล์เก่าจากแคชเบราว์เซอร์ (เคยเกิดมาแล้ว)"""

    def test_app_js_has_a_version_query(self):
        m = re.search(r"app\.js\?v=(\d{10})", HTML)
        self.assertIsNotNone(m, "app.js ต้องมี ?v= กันแคช")
        self.assertGreaterEqual(
            int(m.group(1)), 2026090701,
            "ขยับ ?v= ของ app.js ทุกครั้งที่แก้ไฟล์",
        )


if __name__ == "__main__":
    unittest.main()
