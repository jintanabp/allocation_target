"""
ผลตรวจ 1 ต.ค. 2026 ข12 (ผู้ใช้เลือกข้อ 2): ตำแหน่งที่เห็นข้อมูลเกินภาค (Marketing / Manager ระดับเขต)
ตั้งได้เฉพาะ dev / หัวหน้าแอดมิน — แอดมินตามขอบเขตยังตั้ง Supervisor / Manager ระดับภาคได้ตามเดิม
"""

from __future__ import annotations

import inspect
import os
import sys
import unittest

from fastapi import HTTPException

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.routers import admin as A  # noqa: E402

SCOPED = {"email": "a@x.test", "role": A.ROLE_ADMIN if hasattr(A, "ROLE_ADMIN") else "admin",
          "admin_scope": {"sl_codes": {"SL1"}}}
HEAD = {"email": "h@x.test", "role": A.ROLE_HEAD_ADMIN}
DEV = {"email": "d@x.test", "role": A.ROLE_DEV}


def _row(**kw):
    return {"email": "u@x.test", "userpl": "SL1", **kw}


class TestWideVisibility(unittest.TestCase):
    def test_scoped_admin_cannot_grant_marketing_or_division_manager(self):
        for after in (_row(login_kind="marketing"),
                      _row(login_kind="manager_acc", manager_level="division"),
                      _row(login_kind="district_manager")):
            with self.subTest(after=after):
                with self.assertRaises(HTTPException) as cm:
                    A._ensure_can_grant_wide_visibility(SCOPED, None, after)
                self.assertEqual(cm.exception.status_code, 403)
                with self.assertRaises(HTTPException):
                    A._ensure_can_grant_wide_visibility(SCOPED, _row(), after)

    def test_scoped_admin_can_still_set_narrow_roles(self):
        for after in (_row(), _row(login_kind="supervisor_acc"),
                      _row(login_kind="manager_acc", manager_level="regional")):
            with self.subTest(after=after):
                A._ensure_can_grant_wide_visibility(SCOPED, None, after)

    def test_existing_wide_row_can_be_edited_and_narrowed(self):
        mkt = _row(login_kind="marketing")
        A._ensure_can_grant_wide_visibility(SCOPED, mkt, dict(mkt, note="แก้หมายเหตุ"))
        A._ensure_can_grant_wide_visibility(SCOPED, mkt, _row())

    def test_cannot_move_wide_role_to_another_person(self):
        with self.assertRaises(HTTPException):
            A._ensure_can_grant_wide_visibility(
                SCOPED, _row(login_kind="marketing"), _row(login_kind="marketing", email="other@x.test"))

    def test_head_admin_and_dev_unrestricted(self):
        for who in (HEAD, DEV):
            A._ensure_can_grant_wide_visibility(who, None, _row(login_kind="marketing"))

    def test_both_endpoints_use_the_guard(self):
        for fn in (A.create_user_access, A.update_user_access):
            with self.subTest(fn=fn.__name__):
                self.assertIn("_ensure_can_grant_wide_visibility(admin,", inspect.getsource(fn))


if __name__ == "__main__":
    unittest.main()
