"""
เทสตารางสิทธิ์หน้าแอดมิน (config/admin_permissions.json + require_capability)

จุดสำคัญที่สุดคือ **ค่าตั้งต้นต้องให้ผลเท่าพฤติกรรมเดิมทุกบทบาท** — ตารางสิทธิ์
เข้ามาแทนอาร์เรย์ ADMIN_TABS_* ที่เคยฮาร์ดโค้ดไว้ในหน้าเว็บ ถ้าค่าตั้งต้นเพี้ยน
ผู้ใช้จะเห็นแท็บหาย/เกินตั้งแต่วันแรกโดยไม่มีใครสั่ง
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from backend.services import admin_capabilities as caps
from backend.services import admin_permissions_store as store

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))


def _read_repo(rel: str) -> str:
    with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
        return fh.read()


class TestDefaultsMatchLegacyBehaviour(unittest.TestCase):
    """ค่าตั้งต้นต้องตรงกับ ADMIN_TABS_* เดิมใน frontend/app.js"""

    LEGACY_TABS = {
        "head_admin": {
            "users", "roles", "slLinks", "skuLinks",
            "allocations", "usageLogs", "usageSummary", "team",
        },
        "admin": {
            "users", "slLinks", "skuLinks",
            "allocations", "usageLogs", "usageSummary", "team",
        },
        "marketing": {"team", "skuLinks", "slLinks"},
    }

    def test_default_tabs_match_legacy_arrays(self):
        for role, expected in self.LEGACY_TABS.items():
            got = {caps.tab_for(c) for c in store.DEFAULT_ROLE_CAPABILITIES[role]}
            self.assertEqual(got, expected, f"แท็บตั้งต้นของ {role} ไม่ตรงกับของเดิม")

    def test_dev_gets_everything_including_locked(self):
        dev = set(store.capabilities_for_role("dev"))
        self.assertEqual(dev, set(caps.all_capability_keys()))
        self.assertIn("data_source", dev, "dev ต้องได้ของที่ล็อกไว้ด้วย")
        self.assertIn("emp_moves", dev)

    def test_plain_user_gets_nothing(self):
        self.assertEqual(store.capabilities_for_role(""), [])
        self.assertEqual(store.capabilities_for_role("supervisor"), [])

    def test_emp_moves_is_dev_only_by_default(self):
        """ก่อนที่ dev จะเปิดให้ ย้ายพนักงานต้องยังเป็นของ dev เท่านั้น"""
        for role in caps.CONFIGURABLE_ROLES:
            self.assertNotIn("emp_moves", store.DEFAULT_ROLE_CAPABILITIES[role])


class TestValidationGuards(unittest.TestCase):
    def test_rejects_capability_locked_to_dev(self):
        with self.assertRaises(ValueError):
            store.validate_roles({"admin": ["data_source"]})
        with self.assertRaises(ValueError):
            store.validate_roles({"head_admin": ["data_source"]})

    def test_rejects_roles_capability_for_plain_admin(self):
        """ถ้ามอบ 'roles' ให้แอดมินได้ แอดมินจะยกระดับตัวเองเป็นหัวหน้าแอดมิน"""
        with self.assertRaises(ValueError):
            store.validate_roles({"admin": ["roles"]})
        # หัวหน้าแอดมินยังได้ตามเดิม
        ok = store.validate_roles({"head_admin": ["roles"]})
        self.assertIn("roles", ok["head_admin"])

    def test_rejects_setting_permissions_for_dev(self):
        with self.assertRaises(ValueError):
            store.validate_roles({"dev": ["users"]})

    def test_rejects_unknown_role_and_capability(self):
        with self.assertRaises(ValueError):
            store.validate_roles({"somebody": ["users"]})
        with self.assertRaises(ValueError):
            store.validate_roles({"admin": ["not_a_capability"]})

    def test_missing_roles_are_filled_as_empty(self):
        out = store.validate_roles({"admin": ["users"]})
        for role in caps.CONFIGURABLE_ROLES:
            self.assertIn(role, out)
        self.assertEqual(out["marketing"], [])

    def test_duplicate_capabilities_collapse(self):
        out = store.validate_roles({"admin": ["users", "users", "team"]})
        self.assertEqual(out["admin"], ["users", "team"])


class TestFileHandling(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("ADMIN_PERMISSIONS_JSON_PATH")
        self._tmpdir = tempfile.mkdtemp(prefix="perm_test_")
        self._path = os.path.join(self._tmpdir, "admin_permissions.json")
        os.environ["ADMIN_PERMISSIONS_JSON_PATH"] = self._path

    def tearDown(self):
        if self._old is None:
            os.environ.pop("ADMIN_PERMISSIONS_JSON_PATH", None)
        else:
            os.environ["ADMIN_PERMISSIONS_JSON_PATH"] = self._old
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_missing_file_falls_back_to_defaults(self):
        self.assertFalse(os.path.exists(self._path))
        self.assertEqual(store.read_roles(), store.default_roles())

    def test_broken_json_raises_not_silently_empty(self):
        """ไฟล์เพี้ยนต้อง raise — คืนค่าว่างเงียบ ๆ ในไฟล์สิทธิ์อันตรายกว่าพัง"""
        with open(self._path, "w", encoding="utf-8") as f:
            f.write("{ not json")
        with self.assertRaises(PermissionError):
            store.read_roles()

    def test_file_violating_rules_raises(self):
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "roles": {"admin": ["data_source"]}}, f)
        with self.assertRaises(PermissionError):
            store.read_roles()

    def test_write_then_read_roundtrip(self):
        store.write_roles({"admin": ["users", "team"]}, updated_by="dev@x.com")
        got = store.read_roles()
        self.assertEqual(got["admin"], ["users", "team"])
        with open(self._path, encoding="utf-8") as f:
            raw = json.load(f)
        self.assertEqual(raw["updated_by"], "dev@x.com")
        self.assertEqual(raw["version"], 1)

    def test_granting_emp_moves_to_admin_works(self):
        """เคสที่ผู้ใช้ขอ — เปิดย้ายพนักงานให้แอดมิน"""
        store.write_roles({"admin": ["users", "emp_moves"]})
        self.assertTrue(store.role_has_capability("admin", "emp_moves"))
        self.assertFalse(store.role_has_capability("marketing", "emp_moves"))


class TestRegistryShape(unittest.TestCase):
    def test_every_capability_maps_to_a_tab(self):
        for key in caps.all_capability_keys():
            self.assertTrue(caps.tab_for(key), f"capability {key} ไม่มีแท็บคู่")

    def test_locked_capability_reports_not_grantable(self):
        self.assertFalse(caps.is_grantable("data_source"))
        self.assertTrue(caps.is_grantable("emp_moves"))

    def test_registry_for_api_exposes_grantable_flag(self):
        rows = {r["key"]: r for r in caps.registry_for_api()}
        self.assertFalse(rows["data_source"]["grantable"])
        self.assertEqual(rows["data_source"]["allowed_roles"], [])
        self.assertEqual(rows["roles"]["allowed_roles"], ["head_admin"])


class TestRequireCapabilityGate(unittest.TestCase):
    """
    ด่านจริงฝั่ง server — หน้าเว็บซ่อนแท็บเป็นแค่ความสะดวก
    ถ้าด่านนี้ไม่กัน คนที่รู้ URL ก็ยิงตรงได้
    """

    def setUp(self):
        from backend import deps

        self.deps = deps
        self._orig_base = deps.require_admin_or_marketing_team
        self._old_path = os.environ.get("ADMIN_PERMISSIONS_JSON_PATH")
        self._tmpdir = tempfile.mkdtemp(prefix="perm_dep_")
        os.environ["ADMIN_PERMISSIONS_JSON_PATH"] = os.path.join(
            self._tmpdir, "admin_permissions.json"
        )

    def tearDown(self):
        self.deps.require_admin_or_marketing_team = self._orig_base
        if self._old_path is None:
            os.environ.pop("ADMIN_PERMISSIONS_JSON_PATH", None)
        else:
            os.environ["ADMIN_PERMISSIONS_JSON_PATH"] = self._old_path
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _as(self, role):
        """จำลองว่าด่านฐานคืน context ของบทบาทนี้ (ครอบคลุมโหมดดูสิทธิ์แบบผู้ใช้อื่นด้วย
        เพราะด่านฐานเป็นตัวที่แปลง view-as เป็น role ของบัญชีที่กำลังดู)"""
        self.deps.require_admin_or_marketing_team = (
            lambda authorization=None, x_view_as_email=None: {
                "email": "x@y.co.th", "role": role, "admin_scope": None,
                "is_admin": role == "dev", "is_marketing": role == "marketing",
            }
        )

    def _call(self, cap):
        from fastapi import HTTPException

        dep = self.deps.require_capability(cap)
        try:
            dep(authorization=None, x_view_as_email=None)
            return None
        except HTTPException as e:
            return e.status_code

    def test_dev_passes_everything(self):
        self._as("dev")
        for cap in caps.all_capability_keys():
            self.assertIsNone(self._call(cap), f"dev ต้องผ่าน {cap}")

    def test_admin_blocked_from_emp_moves_by_default(self):
        self._as("admin")
        self.assertEqual(self._call("emp_moves"), 403)

    def test_admin_passes_after_dev_grants_it(self):
        store.write_roles({"admin": ["users", "emp_moves"]})
        self._as("admin")
        self.assertIsNone(self._call("emp_moves"))

    def test_admin_never_passes_locked_capability(self):
        """แม้ dev จะพยายามเปิดให้ ก็บันทึกไม่ได้ตั้งแต่แรก"""
        with self.assertRaises(ValueError):
            store.write_roles({"admin": ["data_source"]})
        self._as("admin")
        self.assertEqual(self._call("data_source"), 403)

    def test_marketing_scoped_to_its_own_capabilities(self):
        self._as("marketing")
        self.assertIsNone(self._call("team"))
        self.assertEqual(self._call("users"), 403)
        self.assertEqual(self._call("emp_moves"), 403)

    def test_view_as_uses_the_viewed_account_role(self):
        """dev ที่กำลังดูสิทธิ์แบบแอดมิน ต้องโดนกันเหมือนแอดมินจริง"""
        self._as("admin")
        self.assertEqual(self._call("emp_moves"), 403)


class TestWiring(unittest.TestCase):
    """อ่านซอร์สยืนยันว่าด่านถูกต่อจริง — กันการเผลอถอดออกภายหลัง"""

    def test_emp_assignment_endpoints_use_the_capability_gate(self):
        src = _read_repo("backend/routers/admin.py")
        self.assertIn('require_capability("emp_moves")', src)
        head = src[src.index('@router.get("/emp-assignments")'):]
        head = head[: head.index("@router.post")]
        self.assertNotIn("require_admin_user", head,
                         "GET /emp-assignments ต้องเลิกใช้ด่าน dev-only แล้ว")

    def test_permission_write_endpoint_is_dev_only(self):
        """PUT /admin/permissions ต้องเป็น dev เท่านั้น ไม่งั้นแก้สิทธิ์ตัวเองได้"""
        src = _read_repo("backend/routers/admin.py")
        blk = src[src.index('@router.put("/permissions")'):]
        blk = blk[: blk.index("@router.get(", 10)]
        self.assertIn("require_admin_user", blk)

    def test_frontend_reads_tabs_from_server(self):
        app_js = _read_repo("frontend/app.js")
        self.assertIn("/admin/permissions/me", app_js)
        self.assertIn("_adminTabsFromServer", app_js)
        # อาร์เรย์เดิมต้องยังอยู่เป็น fallback — ถอยไปใช้ค่าเดิมดีกว่าเปิดทุกแท็บ
        self.assertIn("ADMIN_TABS_ADMIN", app_js)

    def test_emp_move_is_audited(self):
        """แอดมินย้ายคนข้ามทีมได้แล้ว จึงต้องตามรอยได้จากหน้าแอดมิน"""
        src = _read_repo("backend/routers/admin.py")
        self.assertIn('"admin_emp_assignment"', src)


if __name__ == "__main__":
    unittest.main()
