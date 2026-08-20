"""
โหมด "ดูสิทธิ์แบบผู้ใช้อื่น" ต้องจำลองบัญชีแอดมินได้เหมือนจริง

dev กดดูบัญชีแอดมินภาค → ได้ขอบเขตของบัญชีนั้น (แคบลงจาก dev เสมอ ไม่มีทางกว้างขึ้น)
ดูบัญชีธรรมดา → เข้า route แอดมินไม่ได้ (403 แบบเดียวกับเจ้าตัวจริง)
คนที่ไม่ใช่ dev ใช้ view-as ไม่ได้เลย — กติกาเดิมของ require_authenticated_user
"""

from __future__ import annotations

import inspect
import logging
import os
import sys
import unittest
from unittest.mock import patch

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from fastapi import HTTPException  # noqa: E402

from backend import deps  # noqa: E402

logging.disable(logging.CRITICAL)

DEV = "dev@example.com"
HEAD_ADMIN = "head.admin@example.com"
REGION_ADMIN = "region.admin@example.com"
NORMAL = "somchai@example.com"
SCOPE = {"breadth": "division_region", "regions": {"อีสาน"}, "divisions": {"Div.S"}, "sl_codes": {"SL452"}}


def _roles(email: str) -> str:
    return {DEV: "dev", HEAD_ADMIN: "head_admin", REGION_ADMIN: "admin"}.get(email, "user")


def _admin_role(email: str) -> str:
    """เลียนแบบ admin_role_for_email — dev ไม่นับ เพราะ dev ไม่มีขอบเขต"""
    role = _roles(email)
    return role if role in ("head_admin", "admin") else ""


class _Patched(unittest.TestCase):
    """mock ชั้นระบุตัวตน + ตาราง role — เทสเฉพาะตรรกะของ deps ล้วน ๆ"""

    def setUp(self):
        self._patches = [
            patch.object(deps.auth_entra, "auth_enabled", return_value=True),
            patch.object(deps, "_identity_from_bearer", return_value={"email": self._caller()}),
            patch.object(deps, "is_allocation_admin_email", side_effect=lambda e: e == DEV),
            patch.object(deps, "is_region_admin_email", side_effect=lambda e: e == REGION_ADMIN),
            patch.object(deps, "admin_role_for_email", side_effect=_admin_role),
            patch.object(deps, "is_marketing_email", return_value=False),
            patch.object(deps, "role_for_email", side_effect=_roles),
            patch.object(deps, "admin_scope_for_email", return_value=SCOPE),
            patch.object(deps, "admin_scope_is_usable", return_value=True),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def _caller(self):
        return DEV


class TestDevViewAsAdmin(_Patched):
    def test_scoped_dep_returns_the_viewed_admins_scope(self):
        ctx = deps.require_admin_scoped(authorization="Bearer x", x_view_as_email=REGION_ADMIN)
        self.assertEqual(ctx["role"], "admin")
        self.assertEqual(ctx["email"], REGION_ADMIN)
        self.assertEqual(ctx["admin_scope"], SCOPE)
        self.assertEqual(ctx["view_as_email"], REGION_ADMIN)
        self.assertEqual(ctx["acting_admin_email"], DEV)
        self.assertFalse(ctx["is_admin"], "จำลองแอดมินภาคต้องไม่พกสิทธิ์ dev ติดไป")

    def test_viewing_a_normal_user_gets_403_like_the_real_person(self):
        with self.assertRaises(HTTPException) as c:
            deps.require_admin_scoped(authorization="Bearer x", x_view_as_email=NORMAL)
        self.assertEqual(c.exception.status_code, 403)

    def test_dev_only_routes_are_blocked_during_admin_simulation(self):
        """จอ dev-only ต้องไม่หลุดเข้ามาในการจำลองแอดมินภาค — เหมือนจริง"""
        with self.assertRaises(HTTPException) as c:
            deps.require_admin_user(authorization="Bearer x", x_view_as_email=REGION_ADMIN)
        self.assertEqual(c.exception.status_code, 403)

    def test_dev_only_routes_still_work_when_viewing_a_dev(self):
        ctx = deps.require_admin_user(authorization="Bearer x", x_view_as_email=DEV)
        self.assertEqual(ctx["role"], "dev")

    def test_no_view_as_keeps_full_dev_context(self):
        ctx = deps.require_admin_scoped(authorization="Bearer x")
        self.assertEqual(ctx["role"], "dev")
        self.assertIsNone(ctx["admin_scope"])
        self.assertNotIn("view_as_email", ctx)

    def test_unusable_viewed_scope_is_403_not_full_access(self):
        with patch.object(deps, "admin_scope_is_usable", return_value=False):
            with self.assertRaises(HTTPException) as c:
                deps.require_admin_scoped(authorization="Bearer x", x_view_as_email=REGION_ADMIN)
        self.assertEqual(c.exception.status_code, 403)


class TestNonDevCannotViewAs(_Patched):
    def _caller(self):
        return REGION_ADMIN

    def test_region_admin_cannot_use_view_as_on_admin_routes(self):
        with self.assertRaises(HTTPException) as c:
            deps.require_admin_scoped(authorization="Bearer x", x_view_as_email=NORMAL)
        self.assertEqual(c.exception.status_code, 403)

    def test_region_admin_without_view_as_is_unchanged(self):
        ctx = deps.require_admin_scoped(authorization="Bearer x")
        self.assertEqual(ctx["role"], "admin")
        self.assertEqual(ctx["email"], REGION_ADMIN)


class TestAuthDisabledViewAs(unittest.TestCase):
    """เครื่อง dev (ปิด auth) ต้องจำลอง view-as ได้เหมือนโหมดจริง"""

    def test_view_as_admin_gets_scoped_context(self):
        with patch.object(deps.auth_entra, "auth_enabled", return_value=False), \
             patch.object(deps, "role_for_email", side_effect=_roles), \
             patch.object(deps, "admin_role_for_email", side_effect=_admin_role), \
             patch.object(deps, "admin_scope_for_email", return_value=SCOPE), \
             patch.object(deps, "admin_scope_is_usable", return_value=True):
            ctx = deps.require_admin_scoped(x_view_as_email=REGION_ADMIN)
        self.assertEqual(ctx["role"], "admin")
        self.assertEqual(ctx["admin_scope"], SCOPE)

    def test_no_view_as_keeps_the_dev_stub(self):
        with patch.object(deps.auth_entra, "auth_enabled", return_value=False):
            ctx = deps.require_admin_scoped()
        self.assertEqual(ctx["role"], "dev")
        self.assertTrue(ctx.get("auth_disabled"))


class TestManagersReportsViewedRole(unittest.TestCase):
    def test_managers_uses_the_viewed_accounts_role(self):
        from backend.routers import managers as m

        src = inspect.getsource(m.get_managers)
        self.assertIn('user.get("view_as_email") or user.get("email")', src)
        self.assertNotIn('out["role"] = "user"', src, "ห้ามบังคับ role เป็น user ตอน view-as อีก")


class TestHeadAdminRole(_Patched):
    """หัวหน้าแอดมิน = แอดมิน + ตั้งสิทธิ์คนอื่นได้ แต่ยังไม่ใช่ dev"""

    def _caller(self):
        return HEAD_ADMIN

    def test_head_admin_passes_the_scoped_dep(self):
        ctx = deps.require_admin_scoped(authorization="Bearer x")
        self.assertEqual(ctx["role"], "head_admin")
        self.assertEqual(ctx["admin_scope"], SCOPE)
        self.assertFalse(ctx["is_admin"], "หัวหน้าแอดมินต้องไม่ถูกนับเป็น dev")

    def test_head_admin_can_manage_roles(self):
        ctx = deps.require_role_manager(authorization="Bearer x")
        self.assertEqual(ctx["role"], "head_admin")

    def test_head_admin_is_still_blocked_from_dev_only_routes(self):
        with self.assertRaises(HTTPException) as c:
            deps.require_admin_user(authorization="Bearer x")
        self.assertEqual(c.exception.status_code, 403)


class TestPlainAdminCannotManageRoles(_Patched):
    def _caller(self):
        return REGION_ADMIN

    def test_role_manager_dep_refuses_plain_admin(self):
        with self.assertRaises(HTTPException) as c:
            deps.require_role_manager(authorization="Bearer x")
        self.assertEqual(c.exception.status_code, 403)


class TestAssignRoleGuards(unittest.TestCase):
    """หัวหน้าแอดมินมอบได้เฉพาะ 'admin' และห้ามแตะสิทธิ์ตัวเอง"""

    HEAD = {"role": "head_admin", "email": HEAD_ADMIN}

    def test_head_admin_may_grant_plain_admin(self):
        deps.ensure_can_assign_role(self.HEAD, "admin", "someone@example.com")

    def test_head_admin_may_revoke(self):
        deps.ensure_can_assign_role(self.HEAD, "", "someone@example.com")

    def test_head_admin_cannot_grant_dev_or_head_admin(self):
        for forbidden in ("dev", "head_admin"):
            with self.assertRaises(HTTPException) as c:
                deps.ensure_can_assign_role(self.HEAD, forbidden, "someone@example.com")
            self.assertEqual(c.exception.status_code, 403, forbidden)

    def test_head_admin_cannot_touch_own_role(self):
        with self.assertRaises(HTTPException) as c:
            deps.ensure_can_assign_role(self.HEAD, "", HEAD_ADMIN)
        self.assertEqual(c.exception.status_code, 403)

    def test_dev_is_unrestricted(self):
        actor = {"role": "dev", "email": DEV}
        for role in ("dev", "head_admin", "admin", ""):
            deps.ensure_can_assign_role(actor, role, "anyone@example.com")


class TestRoleTableInAccessControl(unittest.TestCase):
    def test_roles_are_ordered_strongest_first(self):
        from backend.services import access_control as ac

        self.assertEqual(ac.ASSIGNABLE_ROLES, (ac.ROLE_DEV, ac.ROLE_HEAD_ADMIN, ac.ROLE_ADMIN))
        self.assertEqual(ac.ADMIN_ROLES, (ac.ROLE_HEAD_ADMIN, ac.ROLE_ADMIN))
        self.assertEqual(ac.ROLE_REGION_ADMIN, ac.ROLE_ADMIN, "ชื่อเดิมต้องชี้ค่าเดียวกัน")

    def test_every_assignable_role_has_a_label(self):
        from backend.services import access_control as ac

        for role in ac.ASSIGNABLE_ROLES:
            self.assertIn(role, ac.ROLE_LABELS)


if __name__ == "__main__":
    unittest.main()
