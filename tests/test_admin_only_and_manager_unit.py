"""
สองเรื่องที่เพิ่งแก้ (2026-08-19)

1. **บัญชี "แอดมินอย่างเดียว" ล็อกอินไม่ได้**
   แถวที่ไม่มีรหัสขายเก็บ userpl เป็น sentinel `"none"` ในไฟล์ (ทุกฟิลด์ต้องมีค่า)
   โค้ดฝั่งสิทธิ์อ่านดิบแล้ว `.upper()` จึงได้ **รหัสทีมปลอมชื่อ "NONE"** ไหลเข้า
   `allowed_supervisor_codes` และรายชื่อทีมในหน้าล็อกอิน — แอดมินเลือกทีมนั้นแล้ว
   โหลดไม่ได้ ส่วนคนอื่นเห็น "NONE" โผล่ในลำดับชั้น

2. **ผู้จัดการรายภาคระบุ "หน่วย" (credit/van) ได้**
   เดิมกรองตามหน่วยเฉพาะซุป ผู้จัดการภาคจึงเห็นทุกทีมในภาคเสมอ ทั้งที่จริง ๆ
   ดูแลแค่หน่วยเดียว — ระดับดิวิชันยังระบุไม่ได้ เพราะขอบเขตคือทั้งดิวิชันอยู่แล้ว
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import access_control as ac  # noqa: E402
from backend.services import access_hierarchy as ah  # noqa: E402
from backend.services import user_access_store as uas  # noqa: E402

logging.disable(logging.CRITICAL)

ADMIN_ONLY = "admin.only@sahapat.co.th"
SUP_CREDIT = "sup.credit@sahapat.co.th"
SUP_VAN = "sup.van@sahapat.co.th"
MGR_CREDIT = "mgr.credit@sahapat.co.th"
MGR_ALL = "mgr.all@sahapat.co.th"


def _row(email, upl, **kw):
    base = {
        "email": email,
        "userpl": upl,
        "can_import_targetsun": False,
        "note": "",
        "login_kind": "supervisor_acc",
        "acc_type": "NON",
        "acc_joblevel": "1",
    }
    base.update(kw)
    return base


ROWS = [
    # แอดมินอย่างเดียว — ไม่มีรหัสขาย (sentinel) แต่มี role
    _row(ADMIN_ONLY, "none", login_kind="standard", role="admin",
         acc_region="เหนือ", acc_division="Div.B"),
    _row(SUP_CREDIT, "SL801", acc_region="เหนือ", acc_division="Div.B", acc_unit="credit"),
    _row(SUP_VAN, "SL802", acc_region="เหนือ", acc_division="Div.B", acc_unit="van"),
    _row(MGR_CREDIT, "SL800", login_kind="manager_acc", manager_level="regional",
         acc_region="เหนือ", acc_division="Div.B", acc_unit="credit"),
    _row(MGR_ALL, "SL810", login_kind="manager_acc", manager_level="regional",
         acc_region="เหนือ", acc_division="Div.B"),
]


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._path = os.path.join(self._tmp.name, "user_access.json")
        with open(self._path, "w", encoding="utf-8") as fh:
            json.dump(ROWS, fh, ensure_ascii=False)
        self._old = os.environ.get("USER_ACCESS_JSON_PATH")
        os.environ["USER_ACCESS_JSON_PATH"] = self._path
        ac.invalidate_user_access_cache()

    def tearDown(self):
        if self._old is None:
            os.environ.pop("USER_ACCESS_JSON_PATH", None)
        else:
            os.environ["USER_ACCESS_JSON_PATH"] = self._old
        ac.invalidate_user_access_cache()
        self._tmp.cleanup()


class TestNoneSentinelIsNotATeamCode(_Base):
    def test_helper_maps_the_sentinel_to_empty(self):
        self.assertEqual(uas.real_userpl("none"), "")
        self.assertEqual(uas.real_userpl("NONE"), "")
        self.assertEqual(uas.real_userpl("  sl330 "), "SL330")
        self.assertEqual(uas.real_userpl(None), "")

    def test_acc_rows_never_expose_a_none_code(self):
        codes = {r["userpl"] for r in ac.load_acc_rows()}
        self.assertNotIn("NONE", codes)
        self.assertIn("SL801", codes)

    def test_admin_only_account_gets_no_fake_team(self):
        allowed = ac.compute_allowed_supervisor_codes(ADMIN_ONLY, ac.load_acc_rows())
        self.assertEqual(allowed, set(), "ต้องไม่มีรหัสทีมใด ๆ รวมถึง 'NONE'")

    def test_admin_only_account_can_still_authenticate(self):
        """มี role = ผ่านด่านสิทธิ์ได้ แม้ไม่มีรหัสทีมเลย (จะถูกพาไปหน้าแอดมิน)"""
        ctx = ac.build_user_access_context(ADMIN_ONLY, allow_admin_bypass=False)
        self.assertEqual(ctx["allowed_supervisor_codes"], set())
        self.assertIsNotNone(
            ctx["allowed_supervisor_codes"],
            "ต้องเป็นเซ็ตว่าง ไม่ใช่ None ซึ่งแปลว่า 'ไม่จำกัด'",
        )

    def test_the_fake_code_never_reaches_the_hierarchy(self):
        payload = ah.build_hierarchy_payload()
        self.assertNotIn("NONE", set(payload.get("supervisors") or []))
        self.assertNotIn("NONE", set(payload.get("manager_codes") or []))


class TestRegionalManagerUnitScope(_Base):
    def _visible(self, email):
        rows = ah.enrich_rows_with_visibility(uas.read_rows())
        for r in rows:
            if str(r.get("email")).lower() == email:
                return set(r.get("visible_supervisor_codes") or [])
        self.fail(f"ไม่พบแถวของ {email}")

    def test_manager_with_a_unit_sees_only_that_unit(self):
        vis = self._visible(MGR_CREDIT)
        self.assertIn("SL801", vis, "ซุปเครดิตต้องอยู่ในขอบเขต")
        self.assertNotIn("SL802", vis, "ซุปหน่วยรถต้องไม่อยู่ในขอบเขต")

    def test_manager_keeps_their_own_code(self):
        """กรองหน่วยแล้วต้องไม่กรองรหัสตัวเองทิ้ง ไม่งั้นล็อกอินมาแล้วว่างเปล่า"""
        self.assertIn("SL800", self._visible(MGR_CREDIT))

    def test_manager_without_a_unit_still_sees_the_whole_region(self):
        vis = self._visible(MGR_ALL)
        self.assertIn("SL801", vis)
        self.assertIn("SL802", vis)


class TestUnitFieldIsOnlyForRegionalManagers(unittest.TestCase):
    def _canon(self, **kw):
        base = {"email": "x@sahapat.co.th", "userpl": "SL700", "acc_unit": "credit"}
        base.update(kw)
        return uas.canonicalize_user_access_row(base)

    def test_regional_manager_keeps_the_unit(self):
        row = self._canon(login_kind="manager_acc", manager_level="regional",
                          acc_region="เหนือ", acc_division="Div.B")
        self.assertEqual(row["acc_unit"], "credit")

    def test_division_manager_does_not(self):
        """ขอบเขตคือทั้งดิวิชันอยู่แล้ว — เก็บหน่วยไว้จะเป็นค่าที่ไม่มีผลและทำให้เข้าใจผิด"""
        row = self._canon(login_kind="manager_acc", manager_level="division",
                          acc_division="Div.E")
        self.assertEqual(row["acc_unit"], uas.NONE_SENTINEL)

    def test_supervisor_still_keeps_the_unit(self):
        row = self._canon(login_kind="supervisor_acc", acc_region="เหนือ",
                          acc_division="Div.B")
        self.assertEqual(row["acc_unit"], "credit")


class TestFrontendWiring(unittest.TestCase):
    """ตรรกะฝั่ง browser — ตรวจจากซอร์สเพราะเทส Python รันไม่ได้"""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO, "frontend", "app.js"), encoding="utf-8") as fh:
            cls.app = fh.read()

    def test_admin_only_account_is_detected_from_an_empty_pick_list(self):
        self.assertIn("function _isAdminOnlyAccount", self.app)
        self.assertIn("S.loginPickCount = list.length;", self.app)

    def test_admin_only_account_lands_on_the_admin_page(self):
        self.assertIn("if (_isAdminOnlyAccount()) {", self.app)
        self.assertIn("บัญชีนี้เป็นผู้ดูแลระบบอย่างเดียว", self.app)

    def test_region_admin_without_a_team_gets_the_admin_login_layout(self):
        self.assertIn("(S.isAdmin || _isAdminOnlyAccount())", self.app)

    def test_unit_field_rule_matches_the_backend(self):
        self.assertIn("function _adminUnitFieldAllowed", self.app)
        self.assertIn('_adminUnitFieldAllowed(d.login_kind, d.manager_level)', self.app)

    def test_clearing_the_unit_sends_an_empty_string_not_null(self):
        """null = 'ไม่แตะฟิลด์' ฝั่ง backend — ส่ง null แล้วล้างหน่วยไม่ได้เลย"""
        self.assertIn('acc_unit: draft.acc_unit || "",', self.app)


if __name__ == "__main__":
    unittest.main()
