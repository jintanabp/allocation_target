"""
ผูกรหัส SL: แอดมินผูกได้ · แอดมินภาคได้เฉพาะภาคตัวเอง (ผู้ใช้ตัดสิน 22 ต.ค. 2026 · QA M1/M2)

เดิมทุกรหัสในกลุ่มต้องอยู่ใน admin_scope["sl_codes"] ซึ่งนับจากแถวซุปในทะเบียนเท่านั้น —
รหัสใหม่ของซุปที่เพิ่งเปลี่ยนรหัสจึงถูกปฏิเสธ「อยู่นอกภาค」ทุกครั้ง หัวหน้าแอดมินผูกเองไม่ได้
และรหัสที่ผูกกับกลุ่มอื่นอยู่แล้วหลุดเป็น 500 แทนที่จะบอกเหตุผล

ใช้ไฟล์ผูกรหัสชั่วคราว — ไม่แตะ config/sl_links.json ของจริง
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest import mock

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from fastapi import HTTPException  # noqa: E402

from backend.routers import admin as admin_router  # noqa: E402
from backend.services import sl_link_store  # noqa: E402

# ทะเบียน: SL100/SL110 ภาคเหนือ (ของแอดมินภาค) · SL200 ภาคใต้ · SL524 ยังไม่มีในทะเบียน (รหัสใหม่)
KNOWN = [{"userpl": "SL100"}, {"userpl": "SL110"}, {"userpl": "SL200"}]
REGION_ADMIN = {"role": "admin", "email": "north@example.invalid",
                "admin_scope": {"breadth": "division_region", "sl_codes": {"SL100", "SL110"}}}
ALL_ADMIN = {"role": "head_admin", "email": "head@example.invalid",
             "admin_scope": {"breadth": "all", "sl_codes": {"SL100", "SL110", "SL200"}}}


class TestSlLinkScope(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for p in (
            mock.patch.dict(os.environ, {"SL_LINKS_JSON_PATH": os.path.join(tmp.name, "sl_links.json")}),
            mock.patch.object(admin_router, "_audit_admin", lambda *a, **k: None),
            mock.patch.object(admin_router, "read_user_access_rows", return_value=KNOWN),
        ):
            p.start()
            self.addCleanup(p.stop)

    def _create(self, admin, old, new):
        return admin_router.create_sl_link(admin_router.SlLinkBody(old_sl=old, new_sls=new), admin=admin)

    def test_all_scope_admin_links_a_brand_new_code(self):
        out = self._create(ALL_ADMIN, "SL200", ["SL524"])
        self.assertEqual(out["row"]["old_sl"], "SL200")

    def test_region_admin_links_new_code_to_own_team(self):
        out = self._create(REGION_ADMIN, "SL100", ["SL524"])
        self.assertIn("SL524", out["row"]["new_sls"])

    def test_region_admin_cannot_link_from_another_region_team(self):
        with self.assertRaises(HTTPException) as cm:
            self._create(REGION_ADMIN, "SL200", ["SL524"])
        self.assertEqual(cm.exception.status_code, 403)

    def test_region_admin_cannot_pull_another_region_team_in(self):
        with self.assertRaises(HTTPException) as cm:
            self._create(REGION_ADMIN, "SL100", ["SL200"])
        self.assertEqual(cm.exception.status_code, 403)
        self.assertEqual(sl_link_store.read_links(), [])

    def test_region_admin_can_link_two_own_teams(self):
        out = self._create(REGION_ADMIN, "SL100", ["SL110"])
        self.assertIn("SL110", out["row"]["new_sls"])

    def test_code_already_in_another_group_is_409_with_reason(self):
        self._create(ALL_ADMIN, "SL100", ["SL524"])
        with self.assertRaises(HTTPException) as cm:
            self._create(ALL_ADMIN, "SL200", ["SL524"])
        self.assertEqual(cm.exception.status_code, 409)
        self.assertIn("SL524", str(cm.exception.detail))


if __name__ == "__main__":
    unittest.main()
