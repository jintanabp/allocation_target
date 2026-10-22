"""
ตาราง「สิทธิ์หน้าแอดมิน」ต้องกันที่ API ด้วย ไม่ใช่แค่ซ่อนแท็บ (QA 21 ต.ค. 2026 H2)

เดิม endpoint ส่วนใหญ่ใช้ require_admin_scoped / require_role_manager /
require_admin_or_marketing_team ซึ่งไม่อ่านตารางนี้ — dev ถอด users/roles จาก head_admin แล้ว
head_admin ยังแก้ผู้ใช้/มอบ role ผ่าน API ได้ 200 · แอดมินที่ถูกถอดเกือบทุกสิทธิ์ยังอ่าน
รายชื่อ ผลกระจาย usage log และ export ได้

ใช้ไฟล์สิทธิ์ชั่วคราว — ไม่แตะ config/admin_permissions.json ของจริง
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

from backend import deps  # noqa: E402
from backend.routers import admin as admin_router  # noqa: E402

HEAD = "head@example.test"
SCOPE = {"breadth": "all", "regions": set(), "divisions": set(), "sl_codes": {"SL100"}}


class _PermsFile:
    def __init__(self, testcase, roles):
        tmp = tempfile.TemporaryDirectory()
        testcase.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "admin_permissions.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "roles": roles}, f)
        p = patch.dict(os.environ, {"ADMIN_PERMISSIONS_JSON_PATH": path})
        p.start()
        testcase.addCleanup(p.stop)


class TestCapabilityDeps(unittest.TestCase):
    """เรียก dependency ตรง ๆ ในฐานะ head_admin ที่ล็อกอินจริง"""

    def setUp(self):
        for p in (
            patch.object(deps.auth_entra, "auth_enabled", return_value=True),
            patch.object(deps, "_identity_from_bearer", return_value={"email": HEAD}),
            patch.object(deps, "is_allocation_admin_email", return_value=False),
            patch.object(deps, "admin_role_for_email", return_value="head_admin"),
            patch.object(deps, "is_marketing_email", return_value=False),
            patch.object(deps, "admin_scope_for_email", return_value=SCOPE),
            patch.object(deps, "admin_scope_is_usable", return_value=True),
        ):
            p.start()
            self.addCleanup(p.stop)

    def test_removed_capability_is_refused(self):
        _PermsFile(self, {"head_admin": ["allocations"], "admin": [], "marketing": []})
        for dep in (
            deps.require_admin_scoped_cap("users"),
            deps.require_role_manager_cap("roles"),
            deps.require_admin_or_marketing_cap("team"),
        ):
            with self.assertRaises(HTTPException) as cm:
                dep(authorization="Bearer x", x_view_as_email=None)
            self.assertEqual(cm.exception.status_code, 403)

    def test_granted_capability_passes_and_keeps_scope(self):
        _PermsFile(self, {"head_admin": ["users", "roles"], "admin": [], "marketing": []})
        ctx = deps.require_admin_scoped_cap("users")(authorization="Bearer x", x_view_as_email=None)
        self.assertEqual(ctx["role"], "head_admin")
        self.assertEqual(ctx["admin_scope"]["sl_codes"], {"SL100"})
        ctx = deps.require_role_manager_cap("roles")(authorization="Bearer x", x_view_as_email=None)
        self.assertEqual(ctx["role"], "head_admin")

    def test_any_of_several_capabilities_is_enough(self):
        # GET /user-access ใช้ทั้งแท็บผู้ใช้และแท็บผู้ดูแลระบบ
        _PermsFile(self, {"head_admin": ["roles"], "admin": [], "marketing": []})
        ctx = deps.require_admin_scoped_cap("users", "roles")(authorization="Bearer x", x_view_as_email=None)
        self.assertEqual(ctx["role"], "head_admin")

    def test_dev_always_passes(self):
        _PermsFile(self, {"head_admin": [], "admin": [], "marketing": []})
        with patch.object(deps, "is_allocation_admin_email", return_value=True):
            ctx = deps.require_admin_scoped_cap("users")(authorization="Bearer x", x_view_as_email=None)
        self.assertEqual(ctx["role"], "dev")


# endpoint ที่ตั้งใจไม่ผูกกับตารางสิทธิ์: ของ dev อย่างเดียว (ตรวจซ้ำด้วย _require_dev ข้างใน)
# และ /permissions/me ที่ทุกผู้ดูแลต้องเรียกได้เพื่อรู้ว่าตัวเองเห็นแท็บไหน
_EXEMPT = {
    ("GET", "/admin/permissions/me"),
    ("GET", "/admin/nightly-check"),
    ("PUT", "/admin/nightly-check"),
    ("POST", "/admin/nightly-check/run"),
    ("GET", "/admin/nightly-check/team"),
}
_UNGATED = {"require_admin_scoped", "require_role_manager", "require_admin_or_marketing_team"}


class TestEveryAdminEndpointIsGated(unittest.TestCase):
    def test_no_admin_endpoint_uses_an_ungated_dependency(self):
        offenders = []
        for route in admin_router.router.routes:
            methods = getattr(route, "methods", None) or ()
            calls = {d.call.__name__ for d in route.dependant.dependencies}
            if not (calls & _UNGATED):
                continue
            for m in methods:
                key = (m, route.path)
                if key not in _EXEMPT:
                    offenders.append(f"{m} {route.path}")
        self.assertEqual(offenders, [], "endpoint เหล่านี้ไม่ตรวจตารางสิทธิ์หน้าแอดมิน")


if __name__ == "__main__":
    unittest.main()
