"""
แอดมินยกสิทธิ์ตัวเองไม่ได้ (ผลตรวจ 28 ก.ย. 2026 §1.1, §1.6, §1.11)

ช่องโหว่เดิม: PUT /admin/user-access ทำ updated_row = dict(existing) แล้วเปลี่ยนอีเมล
role/admin_scope/สิทธิ์ส่งติดแถวไปครบ — แอดมินที่ขอบเขตครอบแถวของ head_admin/dev
ย้ายแถวนั้นมาเป็นอีเมลตัวเองแล้วได้ role นั้นไปเลย

ไฟล์ทั้งหมดอยู่ใน temp — ไม่แตะ config/ หรือ data/ ของจริง
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from fastapi import HTTPException  # noqa: E402

from backend.routers import admin as admin_router  # noqa: E402

ALL = {"breadth": "all", "regions": set(), "divisions": set(), "sl_codes": set()}
ADMIN = {"role": "admin", "email": "admin@example.test", "admin_scope": ALL}
HEAD = {"role": "head_admin", "email": "head@example.test", "admin_scope": ALL}
DEV = {"role": "dev", "email": "dev@example.test", "admin_scope": None}

ROWS = [
    {"email": "head@example.test", "userpl": "SL900", "role": "head_admin", "admin_scope": "all",
     "can_import_targetsun": True, "note": ""},
    {"email": "devrow@example.test", "userpl": "", "role": "dev", "login_kind": "standard",
     "can_import_targetsun": True, "note": ""},
    {"email": "admin@example.test", "userpl": "SL100", "role": "admin", "admin_scope": "all",
     "can_import_targetsun": False, "note": ""},
    {"email": "sup@example.test", "userpl": "SL200", "can_import_targetsun": True, "note": ""},
]


class _Store(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "user_access.json")
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(ROWS, f)
        self._patches = [
            patch.dict(os.environ, {"USER_ACCESS_JSON_PATH": self.path, "ALLOCATION_ADMIN_EMAILS": "envdev@example.test"}),
            patch.object(admin_router, "_sync_access_hierarchy", lambda *a, **k: None),
            patch.object(admin_router, "_audit_admin", lambda *a, **k: None),
            patch.object(admin_router, "enrich_user_access_rows", lambda *a, **k: []),
            patch.object(admin_router, "invalidate_user_access_cache", lambda *a, **k: None),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()

    def rows(self):
        with open(self.path, encoding="utf-8") as f:
            return {(r["email"], r["userpl"]): r for r in json.load(f)}

    def assert403(self, fn):
        before = self.rows()
        with self.assertRaises(HTTPException) as ctx:
            fn()
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(self.rows(), before, "โดนปฏิเสธแล้วไฟล์ต้องไม่เปลี่ยน")


class TestMoveRowToSelf(_Store):
    def test_admin_cannot_move_head_admin_row_to_own_email(self):
        """ท่าโจมตีจริงจากผลตรวจ §1.1"""
        self.assert403(lambda: admin_router.update_user_access(
            admin_router.UserAccessUpdateBody(
                email="head@example.test", userpl="SL900", new_email="admin@example.test",
            ),
            admin=ADMIN,
        ))

    def test_admin_cannot_move_a_supervisor_row_to_own_email(self):
        """ย้ายแถวซุปมาเป็นของตัวเอง = ได้สิทธิ์ส่งของทีมนั้น + ตัดเจ้าของจริงออก"""
        self.assert403(lambda: admin_router.update_user_access(
            admin_router.UserAccessUpdateBody(
                email="sup@example.test", userpl="SL200", new_email="admin@example.test",
            ),
            admin=ADMIN,
        ))

    def test_admin_cannot_change_any_rows_email(self):
        self.assert403(lambda: admin_router.update_user_access(
            admin_router.UserAccessUpdateBody(
                email="sup@example.test", userpl="SL200", new_email="someone@example.test",
            ),
            admin=ADMIN,
        ))

    def test_dev_can_still_change_email(self):
        admin_router.update_user_access(
            admin_router.UserAccessUpdateBody(
                email="sup@example.test", userpl="SL200", new_email="sup2@example.test",
            ),
            admin=DEV,
        )
        self.assertIn(("sup2@example.test", "SL200"), self.rows())


class TestPrivilegedRowsAreDevOnly(_Store):
    def test_admin_cannot_edit_delete_or_toggle_role_holders(self):
        for em, upl in (("head@example.test", "SL900"), ("devrow@example.test", "")):
            with self.subTest(email=em):
                self.assert403(lambda: admin_router.update_user_access(
                    admin_router.UserAccessUpdateBody(email=em, userpl=upl or "SLX", note="x"),
                    admin=ADMIN,
                ) if upl else admin_router.set_targetsun_for_email(
                    admin_router.TargetSunEmailBody(email=em, enabled=False), admin=ADMIN,
                ))
        self.assert403(lambda: admin_router.remove_user_access(
            admin_router.UserAccessDeleteBody(email="head@example.test", userpl="SL900"), admin=ADMIN,
        ))

    def test_admin_cannot_add_rows_for_a_role_holder_or_env_dev(self):
        for em in ("head@example.test", "envdev@example.test"):
            with self.subTest(email=em):
                self.assert403(lambda: admin_router.create_user_access(
                    admin_router.UserAccessBody(email=em, userpl="SL777"), admin=ADMIN,
                ))

    def test_admin_can_still_edit_ordinary_users(self):
        admin_router.update_user_access(
            admin_router.UserAccessUpdateBody(email="sup@example.test", userpl="SL200", note="ok"),
            admin=ADMIN,
        )
        self.assertEqual(self.rows()[("sup@example.test", "SL200")]["note"], "ok")


class TestNoSelfEdits(_Store):
    def test_admin_cannot_turn_on_own_send_permission(self):
        """§1.11"""
        self.assert403(lambda: admin_router.set_targetsun_for_email(
            admin_router.TargetSunEmailBody(email="admin@example.test", enabled=True), admin=ADMIN,
        ))

    def test_admin_cannot_add_team_codes_to_self(self):
        self.assert403(lambda: admin_router.create_user_access(
            admin_router.UserAccessBody(email="admin@example.test", userpl="SL555"), admin=ADMIN,
        ))


class TestHeadAdminLimits(_Store):
    def test_head_admin_cannot_demote_another_head_admin_or_dev(self):
        """§1.6 — เดิมตรวจแค่ role ปลายทาง ถอดสิทธิ์ (role ว่าง) จึงผ่าน"""
        for em in ("head@example.test", "devrow@example.test", "envdev@example.test"):
            with self.subTest(email=em):
                other_head = dict(HEAD, email="head2@example.test")
                with self.assertRaises(HTTPException) as ctx:
                    admin_router.set_user_role(
                        admin_router.UserRoleBody(email=em, role=""), admin=other_head,
                    )
                self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(self.rows()[("head@example.test", "SL900")]["role"], "head_admin")

    def test_head_admin_cannot_grant_scope_all(self):
        with self.assertRaises(HTTPException) as ctx:
            admin_router.set_user_role(
                admin_router.UserRoleBody(email="sup@example.test", role="admin", admin_scope="all"),
                admin=HEAD,
            )
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertNotIn("role", self.rows()[("sup@example.test", "SL200")])


if __name__ == "__main__":
    unittest.main()
