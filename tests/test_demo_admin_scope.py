"""
บัญชีสาธิต demoadmin ดูแลได้เฉพาะทีมสาธิต (ผลตรวจ 28 ก.ย. 2026 §1.2)

เดิม: role=admin ขอบเขต all → ดู/แก้/ลบผู้ใช้จริงทั้งบริษัท เปิดปิดสิทธิ์ส่ง
ย้ายพนักงาน export ผลกระจายทุกทีม · ผู้ใช้ตัดสิน 29 ก.ย.: จำกัดขอบเขต ไม่ถอด role

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

ROWS = [
    {"email": "demoadmin@sahapat.co.th", "userpl": "", "role": "admin", "admin_scope": "all",
     "login_kind": "standard"},
    {"email": "demosuper@sahapat.co.th", "userpl": "SLDEMO1", "login_kind": "supervisor_acc",
     "acc_division": DEMO_DIVISION, "acc_region": DEMO_REGION},
    {"email": "real@example.test", "userpl": "SL100", "login_kind": "supervisor_acc",
     "acc_division": DEMO_DIVISION, "acc_region": "ภาคกลาง"},
]


class TestDemoAdminScope(unittest.TestCase):
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

    def test_scope_is_pinned_to_demo_teams_even_though_the_row_says_all(self):
        scope = ac.admin_scope_for_email("demoadmin@sahapat.co.th")
        self.assertEqual(scope["breadth"], ac.ADMIN_SCOPE_DIVISION_REGION)
        self.assertIn("SLDEMO1", scope["sl_codes"])
        self.assertNotIn("SL100", scope["sl_codes"])
        self.assertTrue(ac.admin_scope_is_usable(scope))

    def test_real_users_are_outside_the_demo_scope(self):
        scope = ac.admin_scope_for_email("demoadmin@sahapat.co.th")
        self.assertFalse(ac.row_is_in_admin_scope(ROWS[2], scope))
        self.assertTrue(ac.row_is_in_admin_scope(ROWS[1], scope))

    def test_demo_account_cannot_write_company_wide_things(self):
        with self.assertRaises(HTTPException) as ctx:
            deps.ensure_not_demo_for_global_write({"email": "demoadmin@sahapat.co.th"})
        self.assertEqual(ctx.exception.status_code, 403)
        deps.ensure_not_demo_for_global_write({"email": "real@example.test"})

    def test_every_company_wide_write_has_the_guard(self):
        import inspect

        from backend.routers import admin as ar

        for fn in (
            ar.create_sku_link, ar.update_sku_link, ar.remove_sku_link,
            ar.create_sl_link, ar.update_sl_link, ar.remove_sl_link,
            ar.admin_set_emp_assignment,
        ):
            with self.subTest(fn=fn.__name__):
                self.assertIn("ensure_not_demo_for_global_write(", inspect.getsource(fn))


if __name__ == "__main__":
    unittest.main()
