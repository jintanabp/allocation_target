"""
GET /admin/supervisor-codes ต้องคืนทีมในขอบเขตของแอดมิน ไม่ใช่รายการว่าง (QA 21 ต.ค. 2026)

เดิมตัวกรองอ่านคีย์ "code" แต่ list_supervisor_codes คืนแถวคีย์ "supervisor_code"
แอดมินทุกคน (ยกเว้น dev ที่ไม่ผ่านตัวกรอง) จึงได้ 0 รายการ — dropdown ในแท็บ
「ทีมพนักงาน」และ「ไม่ต้องตั้งเป้า」ขึ้น "— ไม่มีข้อมูล —"
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.routers import admin as admin_router  # noqa: E402

CODES = [
    {"supervisor_code": "SL100", "manager_code": "M1"},
    {"supervisor_code": "SL200", "manager_code": "M1"},
    {"supervisor_code": "SL300", "manager_code": "M2"},
]


class TestSupervisorCodesScope(unittest.TestCase):
    def _call(self, user):
        with patch.object(admin_router, "list_supervisor_codes", return_value=[dict(c) for c in CODES]):
            return admin_router.admin_supervisor_codes(user=user)

    def test_admin_gets_codes_inside_scope(self):
        out = self._call({"role": "admin", "admin_scope": {"sl_codes": {"SL100", "sl300"}}})
        self.assertEqual([c["supervisor_code"] for c in out["supervisors"]], ["SL100", "SL300"])
        self.assertEqual(out["count"], 2)

    def test_head_admin_is_filtered_the_same_way(self):
        out = self._call({"role": "head_admin", "admin_scope": {"sl_codes": {"SL200"}}})
        self.assertEqual([c["supervisor_code"] for c in out["supervisors"]], ["SL200"])

    def test_empty_scope_still_returns_nothing(self):
        out = self._call({"role": "admin", "admin_scope": {"sl_codes": set()}})
        self.assertEqual(out["supervisors"], [])

    def test_non_admin_role_is_not_filtered(self):
        # dev / marketing ไม่ผ่านตัวกรองขอบเขต (พฤติกรรมเดิม)
        out = self._call({"role": "dev"})
        self.assertEqual(out["count"], 3)


if __name__ == "__main__":
    unittest.main()
