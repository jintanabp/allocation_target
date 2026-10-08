"""
ผลตรวจ 7 ต.ค. 2026 ข12 — ย้ายพนักงานต้องไปทีมที่มีจริงเท่านั้น

เดิม server รับรหัสปลายทางอะไรก็ได้ (พิมพ์ผิด / ทีมสาธิต / รหัสไม่มีบัญชี) → คนหายจากทีมจริงแต่ไม่ไปโผล่ที่ไหน
เป้าของเขาหลุดจากทุกยอดรวมเงียบ ๆ
"""

from __future__ import annotations

import unittest
from unittest import mock

from fastapi import HTTPException

from backend.routers import admin as admin_router

SUPS = {"SL359": {"login_kind": "supervisor_acc"}, "SL372": {"login_kind": "manager_acc"}, "SLX": {"login_kind": "other"}}


class TestEmpMoveDestinationCheck(unittest.TestCase):
    def _call(self, to_sup):
        body = admin_router.EmpAssignmentBody(emp_id="S516", to_sup=to_sup, from_sup="SL372")
        with mock.patch.object(admin_router, "ensure_not_demo_for_global_write"), \
             mock.patch.object(admin_router, "_sup_attrs", return_value=SUPS), \
             mock.patch.object(admin_router.emp_assignment_store, "set_assignment", return_value=[]) as setter, \
             mock.patch.object(admin_router, "invalidate_employee_payload_cache", return_value=0), \
             mock.patch.object(admin_router, "_audit_admin") as audit:
            out = admin_router.admin_set_emp_assignment(body, admin={"email": "a@x"})
        return out, setter, audit

    def test_unknown_destination_rejected(self):
        for bad in ("SL9999", "SLX"):
            with self.subTest(bad=bad), self.assertRaises(HTTPException) as cm:
                self._call(bad)
            self.assertEqual(cm.exception.status_code, 400)

    def test_demo_destination_rejected(self):
        with mock.patch.object(admin_router, "is_demo_supervisor", side_effect=lambda c: c == "SL359"):
            with self.assertRaises(HTTPException):
                self._call("SL359")

    def test_valid_destination_accepted_and_audited_with_team(self):
        out, setter, audit = self._call("SL359")
        self.assertEqual(out["to_sup"], "SL359")
        setter.assert_called_once()
        self.assertEqual(audit.call_args.kwargs.get("sup_id"), "SL359")

    def test_unassign_allowed(self):
        out, setter, audit = self._call("")
        self.assertEqual(out["to_sup"], "")
        self.assertEqual(audit.call_args.kwargs.get("sup_id"), "SL372")


if __name__ == "__main__":
    unittest.main()
