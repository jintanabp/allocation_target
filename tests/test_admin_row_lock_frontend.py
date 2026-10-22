"""
หน้าแอดมินไม่โชว์ปุ่มที่กดแล้วได้ 403 แน่นอน — QA 21 ต.ค. 2026 L14

- แถวผู้ใช้ของตัวเอง / ของบัญชีผู้ดูแล: ผู้ดูแลที่ไม่ใช่ dev แก้/ลบ/เปิดปิดสิทธิ์ส่งไม่ได้
  (กติกา backend: deps.ensure_can_edit_user_rows) → ปุ่มปิดพร้อมเหตุผล
- ตารางสิทธิ์: ติ๊กผูกรหัส SKU ให้แอดมิน/Marketing หรือผูกรหัส SL ให้ Marketing = เข้าดูได้อย่างเดียว
  ต้องบอกในตาราง และต้องตรงกับด่านฝั่ง backend
ลองบนเบราว์เซอร์แล้ว — เทสนี้กันไม่ให้ถอยกลับ / กันสองฝั่งเดินคนละทาง
"""

from __future__ import annotations

import inspect
import os
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))


def _fn(src: str, name: str) -> str:
    i = src.index(f"function {name}(")
    return src[i:src.index("\n}\n", i)]


class TestAdminRowLockFrontend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO, "frontend", "app.js"), encoding="utf-8") as fh:
            cls.src = fh.read().replace("\r\n", "\n")

    def test_lock_matches_backend_rule(self):
        body = _fn(self.src, "_adminRowLockReason")
        self.assertIn('if (S.isAdmin || S.role === "dev") return "";', body)
        self.assertIn("S.viewAsEmail || S.userEmail", body)
        self.assertIn("x.system_role", body)

    def test_buttons_use_lock(self):
        body = _fn(self.src, "_adminRenderTableRowView")
        self.assertIn('class="admin-action admin-action--edit"${lockAttr}', body)
        self.assertIn('admin-btn-del"${lockAttr}', body)
        self.assertIn('aria-label="Target Sun"${lockAttr}', body)

    def test_view_only_table_matches_backend_guards(self):
        from backend.routers import admin

        self.assertIn('("dev", "head_admin")', inspect.getsource(admin._require_head_admin_for_company_wide))
        self.assertIn('sku_links: ["admin", "marketing"]', self.src)
        self.assertIn('sl_links: ["marketing"]', self.src)
        # ผูกรหัส SL เขียนได้เฉพาะผู้ดูแล (Marketing เข้าแค่ GET)
        self.assertIn('require_admin_scoped_cap("sl_links")', inspect.getsource(admin.create_sl_link))
        self.assertIn("perms-viewonly", _fn(self.src, "adminPermsRender"))


if __name__ == "__main__":
    unittest.main()
