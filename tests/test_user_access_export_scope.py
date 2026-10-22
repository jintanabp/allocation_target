"""
ดาวน์โหลดสำรองรายชื่อผู้ใช้: แอดมินได้เฉพาะแถวในขอบเขต (QA 21 ต.ค. 2026 Q12)

เดิม dev เท่านั้น — ปุ่มขึ้นให้หัวหน้าแอดมิน/แอดมิน และคู่มือสั่งให้ดาวน์โหลดทุกครั้งที่แก้ แต่กดแล้ว 403
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from unittest import mock

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.routers import admin as admin_router  # noqa: E402

ROWS = [
    {"email": "a@x.test", "userpl": "SL100", "acc_region": "เหนือ", "acc_division": "Div.S"},
    {"email": "b@x.test", "userpl": "SL200", "acc_region": "ใต้", "acc_division": "Div.S"},
]


class TestExportScope(unittest.TestCase):
    def _export(self, admin, in_scope=lambda r, s: True):
        with mock.patch.object(admin_router, "read_user_access_rows", return_value=[dict(r) for r in ROWS]), \
                mock.patch.object(admin_router, "row_is_in_admin_scope", side_effect=in_scope):
            res = admin_router.admin_export_user_access(admin=admin)
        return json.loads(res.body), res.headers["content-disposition"]

    def test_dev_gets_the_whole_file(self):
        rows, disp = self._export({"role": "dev"})
        self.assertEqual(len(rows), 2)
        self.assertIn('filename="user_access.json"', disp)

    def test_regional_admin_gets_only_own_rows_with_partial_name(self):
        rows, disp = self._export(
            {"role": "admin", "admin_scope": {"regions": {"เหนือ"}}},
            in_scope=lambda r, s: r.get("acc_region") == "เหนือ",
        )
        self.assertEqual([r["userpl"] for r in rows], ["SL100"])
        self.assertIn("user_access_partial.json", disp)

    def test_endpoint_needs_the_users_capability(self):
        import inspect

        src = inspect.getsource(admin_router.admin_export_user_access)
        self.assertIn('require_admin_scoped_cap("users")', src)


if __name__ == "__main__":
    unittest.main()
