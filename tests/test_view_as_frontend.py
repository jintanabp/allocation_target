"""
โหมดดูสิทธิ์ (view-as) ฝั่งหน้าเว็บ — QA 21 ต.ค. 2026 L13

- ป้ายด้านบนต้องไม่ขึ้น「สิทธิ์ dev」เมื่อบัญชีที่ดูไม่มีในระบบ (/managers ตอบ 403 → role ของ dev ค้าง)
- ดูแบบบัญชี Marketing ต้องเข้าแท็บทีมพนักงานได้เหมือนเจ้าตัว (เดิมค้างหน้าล็อกอิน)
- แท็บที่บัญชีก่อนหน้าเข้าได้ห้ามติดไปบัญชีถัดไป · ออกจากโหมดแล้วหน้าแอดมินไม่ค้างทับหน้าล็อกอิน
ลองบนเบราว์เซอร์แล้ว (ไม่เปิด auth) — เทสนี้กันไม่ให้ถอยกลับ
"""

from __future__ import annotations

import os
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))


def _fn(src: str, name: str) -> str:
    i = src.index(f"function {name}(")
    return src[i:src.index("\n}\n", i)]


class TestViewAsFrontend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO, "frontend", "app.js"), encoding="utf-8") as fh:
            cls.src = fh.read().replace("\r\n", "\n")

    def test_start_clears_own_role_and_tabs(self):
        body = _fn(self.src, "adminStartViewAs")
        self.assertIn("S.role = null;", body)
        self.assertIn("S.isMarketing = false;", body)
        self.assertIn("_adminTabsFromServer = null;", body)

    def test_unknown_account_gets_its_own_label(self):
        self.assertIn('S.role = "none";', _fn(self.src, "loadManagers"))
        banner = _fn(self.src, "updateViewAsBanner")
        self.assertIn('S.role === "none"', banner)
        self.assertIn('S.role === "marketing"', banner)

    def test_marketing_follows_viewed_account(self):
        body = _fn(self.src, "loadManagers")
        self.assertIn("S.isMarketing = !!data.is_marketing;", body)
        self.assertNotIn("S.isMarketing = !!data.is_marketing && !S.viewAsEmail", body)
        self.assertIn("if (S.isMarketing && !S.isAdmin) {", body)
        self.assertIn("if (S.viewAsEmail && !simAdmin && !S.isMarketing) return;", _fn(self.src, "openAdminView"))

    def test_exit_closes_admin_view_and_tabs(self):
        body = _fn(self.src, "exitViewAsMode")
        self.assertIn("_adminTabsFromServer = null;", body)
        self.assertIn('av.style.display = "none";', body)


if __name__ == "__main__":
    unittest.main()
