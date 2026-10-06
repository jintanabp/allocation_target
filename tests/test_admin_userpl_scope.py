"""
ผู้ดูแลให้รหัสทีมนอกขอบเขตตัวเองไม่ได้ (ผลตรวจ 5 ต.ค. 2026 ข้อ 1)

เดิม: ด่านตรวจขอบเขตดูแค่ภาค/ดิวิชันของแถว ซึ่งผู้ดูแลพิมพ์เองได้ — สร้างแถว
「ภาคของตัวเอง + รหัสทีมจริงของภาคอื่น」แล้วเปิดสิทธิ์ส่ง Target Sun ได้ และขอบเขตของ
ผู้ดูแลก็ขยายตามไปครอบทีมนั้น · บัญชีสาธิตก็ทำได้ (ขอบเขตคิดจากแถวที่ใส่ภาคสาธิต)

ใช้ไฟล์ผู้ใช้ชั่วคราว — ไม่แตะ config/ ของจริง
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
from backend.services import access_control as ac  # noqa: E402
from backend.services.demo_data import DEMO_DIVISION, DEMO_REGION  # noqa: E402

DEMO_ADMIN = "demoadmin@sahapat.co.th"
ROWS = [
    {"email": DEMO_ADMIN, "userpl": "", "role": "admin", "admin_scope": "all", "login_kind": "standard"},
    {"email": "demosuper@sahapat.co.th", "userpl": "SLDEMO1", "login_kind": "supervisor_acc",
     "acc_division": DEMO_DIVISION, "acc_region": DEMO_REGION},
    # แถวที่ "ถูกวางยา" ไว้ก่อนมีด่าน — ภาคสาธิต แต่รหัสทีมจริง
    {"email": "demosuper@sahapat.co.th", "userpl": "SL330", "login_kind": "supervisor_acc",
     "acc_division": DEMO_DIVISION, "acc_region": DEMO_REGION, "can_import_targetsun": True},
    {"email": "north@example.test", "userpl": "SL100", "login_kind": "supervisor_acc",
     "acc_division": "Div.A", "acc_region": "ภาคเหนือ"},
    {"email": "south@example.test", "userpl": "SL200", "login_kind": "supervisor_acc",
     "acc_division": "Div.A", "acc_region": "ภาคใต้"},
    {"email": "regadmin@example.test", "userpl": "", "role": "admin", "admin_scope": "division_region",
     "acc_division": "Div.A", "acc_region": "ภาคเหนือ"},
]


def _ctx(email: str) -> dict:
    return {"email": email, "role": "admin", "admin_scope": ac.admin_scope_for_email(email)}


class TestUserplScope(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        path = os.path.join(self._tmp.name, "user_access.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(ROWS, f)
        self._env = patch.dict(os.environ, {"USER_ACCESS_JSON_PATH": path})
        self._env.start()
        ac.invalidate_user_access_cache()

    def tearDown(self):
        self._env.stop()
        ac.invalidate_user_access_cache()
        self._tmp.cleanup()

    def test_demo_scope_is_only_demo_team_codes(self):
        codes = ac.admin_scope_for_email(DEMO_ADMIN)["sl_codes"]
        self.assertNotIn("SL330", codes, "แถวภาคสาธิตที่ใส่รหัสทีมจริงต้องไม่ขยายขอบเขตบัญชีสาธิต")
        self.assertIn("SLDEMO1", codes)

    def test_demo_account_never_reaches_a_real_team(self):
        allowed = ac.compute_allowed_supervisor_codes("demosuper@sahapat.co.th", ac.read_rows())
        self.assertNotIn("SL330", {str(c).upper() for c in allowed})
        self.assertIn("SLDEMO1", {str(c).upper() for c in allowed})

    def test_demo_admin_cannot_assign_a_real_team(self):
        with self.assertRaises(HTTPException) as ctx:
            deps.ensure_userpl_in_admin_scope(_ctx(DEMO_ADMIN), "SL330")
        self.assertEqual(ctx.exception.status_code, 403)
        deps.ensure_userpl_in_admin_scope(_ctx(DEMO_ADMIN), "SLDEMO2")

    def test_regional_admin_cannot_assign_another_regions_team(self):
        regadmin = _ctx("regadmin@example.test")
        self.assertTrue(ac.row_is_in_admin_scope(
            {"acc_region": "ภาคเหนือ", "acc_division": "Div.A"}, regadmin["admin_scope"]
        ), "ภาค/ดิวิชันผ่านด่านเดิม — ด่านรหัสทีมต้องเป็นตัวกัน")
        with self.assertRaises(HTTPException):
            deps.ensure_userpl_in_admin_scope(regadmin, "SL200")
        deps.ensure_userpl_in_admin_scope(regadmin, "sl100")

    def test_regional_admin_can_still_add_a_brand_new_team(self):
        """รหัสทีมที่ยังไม่มีใครใช้ = ทีมใหม่ในภาคตัวเอง ต้องเพิ่มได้เหมือนเดิม"""
        deps.ensure_userpl_in_admin_scope(_ctx("regadmin@example.test"), "SL850")

    def test_demo_admin_cannot_add_even_an_unused_code(self):
        with self.assertRaises(HTTPException):
            deps.ensure_userpl_in_admin_scope(_ctx(DEMO_ADMIN), "SL850")

    def test_dev_is_not_limited(self):
        deps.ensure_userpl_in_admin_scope({"role": deps.ROLE_DEV}, "SL999")

    def test_every_user_access_write_route_checks_the_team_code(self):
        import inspect

        from backend.routers import admin as ar

        for fn in (ar.create_user_access, ar.update_user_access, ar.set_targetsun_for_email):
            with self.subTest(fn=fn.__name__):
                self.assertIn("ensure_userpl_in_admin_scope(", inspect.getsource(fn))



class TestSharedTeamCodeStillEditable(unittest.TestCase):
    """ผลตรวจ 6 ต.ค. 2026 ข1: แถวในขอบเขตที่ใช้รหัสทีมเดียวกับภาคอื่น ต้องแก้หมายเหตุ/ปิดสิทธิ์ส่งได้"""

    ROWS = [
        {"email": "north.sup@example.test", "userpl": "SL532", "login_kind": "supervisor_acc",
         "acc_division": "Div.E", "acc_region": "ภาคเหนือ"},
        {"email": "south.mgr@example.test", "userpl": "SL532", "login_kind": "manager_acc",
         "acc_division": "Div.S", "acc_region": "ภาคใต้", "can_import_targetsun": True},
        {"email": "southadmin@example.test", "userpl": "", "role": "admin", "admin_scope": "division_region",
         "acc_division": "Div.S", "acc_region": "ภาคใต้"},
    ]

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "user_access.json")
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.ROWS, f)
        self._env = patch.dict(os.environ, {"USER_ACCESS_JSON_PATH": self.path})
        self._env.start()
        ac.invalidate_user_access_cache()

    def tearDown(self):
        self._env.stop()
        ac.invalidate_user_access_cache()
        self._tmp.cleanup()

    def _call(self, fn, body):
        from backend.routers import admin as ar

        with patch.object(ar, "_sync_access_hierarchy"), patch.object(ar, "_audit_admin"), \
             patch.object(ar, "enrich_user_access_rows", return_value=[]):
            return fn(body, admin=_ctx("southadmin@example.test"))

    def _rows(self):
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def test_revoking_send_right_works(self):
        from backend.routers import admin as ar

        self._call(ar.set_targetsun_for_email, ar.TargetSunEmailBody(email="south.mgr@example.test", enabled=False))
        row = next(r for r in self._rows() if r["email"] == "south.mgr@example.test")
        self.assertFalse(row["can_import_targetsun"])

    def test_granting_send_right_is_still_blocked(self):
        from backend.routers import admin as ar

        with self.assertRaises(HTTPException):
            self._call(ar.set_targetsun_for_email, ar.TargetSunEmailBody(email="south.mgr@example.test", enabled=True))

    def test_editing_note_without_changing_team_works(self):
        from backend.routers import admin as ar

        self._call(ar.update_user_access, ar.UserAccessUpdateBody(
            email="south.mgr@example.test", userpl="SL532", note="ตรวจแล้ว"))
        row = next(r for r in self._rows() if r["email"] == "south.mgr@example.test")
        self.assertEqual(row["note"], "ตรวจแล้ว")


if __name__ == "__main__":
    unittest.main()
