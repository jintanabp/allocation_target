"""
สองระดับสิทธิ์: dev (ทั้งระบบ) กับ admin รายภาค

กติกาที่ต้องคงไว้:
  - dev มาจาก ALLOCATION_ADMIN_EMAILS (bootstrap แก้จากในแอปไม่ได้) หรือ role=dev ในไฟล์
  - แอดมินรายภาคต้องได้ "เซ็ตรหัส SL จริง" เสมอ ห้ามได้ None ซึ่งเป็น sentinel ของ
    "ไม่จำกัด" ที่ ensure_supervisor_allowed ใช้
  - ไม่ระบุภาค = ขอบเขตว่าง (fail closed) ไม่ใช่เห็นทั้งระบบ
  - แอดมินรายภาคเลื่อนขั้นตัวเองไม่ได้ — role ไม่อยู่ในฟิลด์ที่แก้ผ่าน PUT /user-access
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import tempfile
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from fastapi import HTTPException  # noqa: E402

from backend import deps  # noqa: E402
from backend.services import access_control as ac  # noqa: E402
from backend.services import access_hierarchy as ah  # noqa: E402
from backend.services import managers as mgrs  # noqa: E402
from backend.services import user_access_store as uas  # noqa: E402

logging.disable(logging.CRITICAL)

DEV_ENV = "dev.env@sahapat.co.th"
DEV_ROW = "dev.row@sahapat.co.th"
NORTH_ADMIN = "north.admin@sahapat.co.th"
NO_REGION_ADMIN = "noregion.admin@sahapat.co.th"
PLAIN = "plain.sup@sahapat.co.th"
DIV_ADMIN = "div.admin@sahapat.co.th"
ALL_ADMIN = "all.admin@sahapat.co.th"
INCOMPLETE = "incomplete@sahapat.co.th"


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
    _row(DEV_ROW, "SL900", role="dev", acc_region="เหนือ", acc_division="Div.B"),
    _row(NORTH_ADMIN, "SL901", role="admin", acc_region="เหนือ", acc_division="Div.B"),
    _row(NO_REGION_ADMIN, "SL902", role="admin"),
    _row(PLAIN, "SL903", acc_region="เหนือ", acc_division="Div.B"),
    _row("n1@sahapat.co.th", "SL910", acc_region="เหนือ", acc_division="Div.B"),
    _row("n2@sahapat.co.th", "SL911", acc_region="เหนือ", acc_division="Div.S"),
    _row("s1@sahapat.co.th", "SL920", acc_region="ใต้", acc_division="Div.B"),
    # ── ขอบเขตแบบอื่นที่ dev ตั้งให้ได้ (ต่อท้ายเสมอ — เทสเดิมอ้าง ROWS ด้วย index) ──
    _row(DIV_ADMIN, "SL904", role="admin", admin_scope="division",
         acc_region="เหนือ", acc_division="Div.B"),
    _row(ALL_ADMIN, "SL905", role="admin", admin_scope="all"),
    # คนที่ข้อมูลยังไม่ครบ — ไม่มีทั้งภาคและดิวิชัน (เคสที่กระดิ่ง "ต้องตรวจสอบ" จับ)
    _row(INCOMPLETE, "SL930"),
]


class _RbacBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._path = os.path.join(self._tmp.name, "user_access.json")
        with open(self._path, "w", encoding="utf-8") as fh:
            json.dump(ROWS, fh, ensure_ascii=False)
        self._old_path = os.environ.get("USER_ACCESS_JSON_PATH")
        self._old_admins = os.environ.get("ALLOCATION_ADMIN_EMAILS")
        self._old_hier = os.environ.get("ACCESS_HIERARCHY_JSON_PATH")
        os.environ["USER_ACCESS_JSON_PATH"] = self._path
        os.environ["ALLOCATION_ADMIN_EMAILS"] = DEV_ENV
        # การตั้ง role ทำให้ hierarchy ถูก sync ใหม่ (admin._sync_access_hierarchy)
        # ซึ่งเขียนทั้ง config/access_hierarchy.json และ data/managers_cache.json
        # ตัวหลังคิด path จาก _repo_root() ไม่มี env คุม จึงต้อง patch เพิ่ม
        os.environ["ACCESS_HIERARCHY_JSON_PATH"] = os.path.join(
            self._tmp.name, "access_hierarchy.json"
        )
        self._old_root = ah._repo_root
        ah._repo_root = lambda: self._tmp.name
        # managers.persist_managers_payload ใช้ path สัมพัทธ์กับ cwd (= repo ตอนรันเทสต์)
        self._old_mgr_cache = mgrs.MANAGERS_CACHE_FILE
        mgrs.MANAGERS_CACHE_FILE = os.path.join(self._tmp.name, "managers_cache.json")
        ac.invalidate_user_access_cache()

    def tearDown(self):
        ah._repo_root = self._old_root
        mgrs.MANAGERS_CACHE_FILE = self._old_mgr_cache
        for key, old in (
            ("USER_ACCESS_JSON_PATH", self._old_path),
            ("ALLOCATION_ADMIN_EMAILS", self._old_admins),
            ("ACCESS_HIERARCHY_JSON_PATH", self._old_hier),
        ):
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        ac.invalidate_user_access_cache()
        self._tmp.cleanup()


class TestRoleResolution(_RbacBase):
    def test_env_bootstrap_is_dev(self):
        self.assertEqual(ac.role_for_email(DEV_ENV), ac.ROLE_DEV)
        self.assertTrue(ac.is_allocation_admin_email(DEV_ENV))

    def test_role_field_in_file_grants_dev(self):
        self.assertEqual(ac.role_for_email(DEV_ROW), ac.ROLE_DEV)
        self.assertTrue(ac.is_allocation_admin_email(DEV_ROW))

    def test_region_admin_is_not_dev(self):
        self.assertEqual(ac.role_for_email(NORTH_ADMIN), ac.ROLE_REGION_ADMIN)
        self.assertFalse(ac.is_allocation_admin_email(NORTH_ADMIN))
        self.assertTrue(ac.is_region_admin_email(NORTH_ADMIN))

    def test_plain_user_has_no_admin_role(self):
        self.assertEqual(ac.role_for_email(PLAIN), "user")
        self.assertFalse(ac.is_allocation_admin_email(PLAIN))
        self.assertFalse(ac.is_region_admin_email(PLAIN))

    def test_env_admin_is_never_downgraded_to_region_admin(self):
        """คนที่อยู่ใน env list ต้องเป็น dev เสมอ แม้ไฟล์จะเขียน role=admin ไว้"""
        os.environ["ALLOCATION_ADMIN_EMAILS"] = NORTH_ADMIN
        ac.invalidate_user_access_cache()
        self.assertEqual(ac.role_for_email(NORTH_ADMIN), ac.ROLE_DEV)
        self.assertFalse(ac.is_region_admin_email(NORTH_ADMIN))


class TestAdminScope(_RbacBase):
    def test_scope_covers_own_region_across_divisions_it_owns(self):
        scope = ac.admin_scope_for_email(NORTH_ADMIN)
        self.assertEqual(scope["regions"], {"เหนือ"})
        self.assertEqual(scope["divisions"], {"Div.B"})
        # Div.B ภาคเหนือเท่านั้น — SL911 เป็น Div.S, SL920 อยู่ภาคใต้
        self.assertIn("SL910", scope["sl_codes"])
        self.assertNotIn("SL911", scope["sl_codes"])
        self.assertNotIn("SL920", scope["sl_codes"])

    def test_scope_is_always_a_real_set_never_the_unlimited_sentinel(self):
        scope = ac.admin_scope_for_email(NORTH_ADMIN)
        self.assertIsInstance(scope["sl_codes"], set)
        self.assertIsNotNone(scope["sl_codes"])

    def test_admin_without_region_gets_empty_scope(self):
        scope = ac.admin_scope_for_email(NO_REGION_ADMIN)
        self.assertEqual(scope["regions"], set())

    def test_row_membership_check(self):
        scope = ac.admin_scope_for_email(NORTH_ADMIN)
        self.assertTrue(ac.row_is_in_admin_scope(ROWS[4], scope))    # เหนือ/Div.B
        self.assertFalse(ac.row_is_in_admin_scope(ROWS[5], scope))   # เหนือ/Div.S
        self.assertFalse(ac.row_is_in_admin_scope(ROWS[6], scope))   # ใต้/Div.B


class TestScopeGuards(_RbacBase):
    def _admin(self):
        return {
            "email": NORTH_ADMIN,
            "role": ac.ROLE_REGION_ADMIN,
            "admin_scope": ac.admin_scope_for_email(NORTH_ADMIN),
        }

    def _dev(self):
        return {"email": DEV_ENV, "role": ac.ROLE_DEV, "admin_scope": None}

    def test_dev_passes_every_row(self):
        deps.ensure_row_in_admin_scope(self._dev(), ROWS[6])
        deps.ensure_sup_in_admin_scope(self._dev(), "SL920")

    def test_region_admin_blocked_outside_region(self):
        with self.assertRaises(HTTPException) as ctx:
            deps.ensure_row_in_admin_scope(self._admin(), ROWS[6])
        self.assertEqual(ctx.exception.status_code, 403)

    def test_region_admin_allowed_inside_region(self):
        deps.ensure_row_in_admin_scope(self._admin(), ROWS[4])
        deps.ensure_sup_in_admin_scope(self._admin(), "SL910")

    def test_region_admin_blocked_for_sup_outside_region(self):
        with self.assertRaises(HTTPException):
            deps.ensure_sup_in_admin_scope(self._admin(), "SL920")

    def test_none_row_is_rejected(self):
        with self.assertRaises(HTTPException):
            deps.ensure_row_in_admin_scope(self._admin(), None)


class TestAdminScopeBreadth(_RbacBase):
    """
    ขอบเขตของแอดมิน — dev เลือกได้ตอนมอบสิทธิ์ว่าให้แก้ผู้ใช้คนไหนได้บ้าง
      all             = ทุกคนในระบบ (รวมคนข้อมูลไม่ครบ)
      division        = ทั้งดิวิชันของตัวเอง ทุกภาค
      division_region = ดิวิชัน + ภาคของตัวเอง (แคบสุด · ค่าเริ่มต้น)
    """

    def _rows_by_email(self):
        return {r["email"]: r for r in ROWS}

    # ── ค่าเริ่มต้น: ของเก่าที่ไม่เคยตั้งต้องไม่ถูกขยายสิทธิ์เอง ──
    def test_missing_field_defaults_to_narrowest(self):
        self.assertEqual(ac.admin_scope_breadth_for_email(NORTH_ADMIN), ac.ADMIN_SCOPE_DIVISION_REGION)
        self.assertEqual(ac.admin_scope_for_email(NORTH_ADMIN)["breadth"], ac.ADMIN_SCOPE_DIVISION_REGION)

    def test_narrowest_still_behaves_exactly_as_before(self):
        scope = ac.admin_scope_for_email(NORTH_ADMIN)
        by = self._rows_by_email()
        self.assertTrue(ac.row_is_in_admin_scope(by["n1@sahapat.co.th"], scope))   # เหนือ/Div.B
        self.assertFalse(ac.row_is_in_admin_scope(by["n2@sahapat.co.th"], scope))  # เหนือ/Div.S
        self.assertFalse(ac.row_is_in_admin_scope(by["s1@sahapat.co.th"], scope))  # ใต้/Div.B

    # ── ระดับ division: ข้ามภาคได้ แต่ข้ามดิวิชันไม่ได้ ──
    def test_division_scope_reaches_other_regions_in_same_division(self):
        scope = ac.admin_scope_for_email(DIV_ADMIN)
        self.assertEqual(scope["breadth"], ac.ADMIN_SCOPE_DIVISION)
        by = self._rows_by_email()
        self.assertTrue(ac.row_is_in_admin_scope(by["s1@sahapat.co.th"], scope), "ใต้/Div.B ต้องถึง")
        self.assertIn("SL920", scope["sl_codes"])

    def test_division_scope_still_blocks_another_division(self):
        scope = ac.admin_scope_for_email(DIV_ADMIN)
        by = self._rows_by_email()
        self.assertFalse(ac.row_is_in_admin_scope(by["n2@sahapat.co.th"], scope), "Div.S ต้องไม่ถึง")
        self.assertNotIn("SL911", scope["sl_codes"])

    def test_division_scope_excludes_rows_with_no_division(self):
        scope = ac.admin_scope_for_email(DIV_ADMIN)
        by = self._rows_by_email()
        self.assertFalse(ac.row_is_in_admin_scope(by[INCOMPLETE], scope))

    # ── ระดับ all: ตัวเดียวที่แตะคนข้อมูลไม่ครบได้ ──
    def test_all_scope_covers_every_row(self):
        scope = ac.admin_scope_for_email(ALL_ADMIN)
        self.assertEqual(scope["breadth"], ac.ADMIN_SCOPE_ALL)
        for r in ROWS:
            self.assertTrue(
                ac.row_is_in_admin_scope(r, scope), f"{r['email']} ต้องอยู่ในขอบเขต all"
            )

    def test_all_scope_reaches_users_with_no_region_or_division(self):
        """เหตุผลหลักที่มีระดับนี้ — คนในรายการ 'ต้องตรวจสอบ' ไม่มีภาค จึงตกนอกขอบเขตอื่นทุกอัน"""
        scope = ac.admin_scope_for_email(ALL_ADMIN)
        by = self._rows_by_email()
        self.assertTrue(ac.row_is_in_admin_scope(by[INCOMPLETE], scope))
        self.assertTrue(ac.row_is_in_admin_scope(by[NO_REGION_ADMIN], scope))

    def test_all_scope_is_still_a_real_set_not_the_unlimited_sentinel(self):
        """กว้างสุดก็ยังต้องเป็นเซ็ตจริง — None คือ sentinel 'ไม่จำกัด' ที่สงวนให้ dev"""
        scope = ac.admin_scope_for_email(ALL_ADMIN)
        self.assertIsInstance(scope["sl_codes"], set)
        self.assertIn("SL920", scope["sl_codes"])
        self.assertIn("SL911", scope["sl_codes"])

    def test_all_scope_works_even_though_its_own_row_has_no_region(self):
        self.assertTrue(ac.admin_scope_is_usable(ac.admin_scope_for_email(ALL_ADMIN)))

    # ── fail closed: ขอบเขตที่ต้องใช้ข้อมูลตัวเองแต่ข้อมูลไม่มี ──
    def test_unusable_when_narrow_scope_lacks_the_data_it_needs(self):
        self.assertFalse(ac.admin_scope_is_usable(ac.admin_scope_for_email(NO_REGION_ADMIN)))

    def test_narrow_scope_without_region_matches_nobody(self):
        scope = ac.admin_scope_for_email(NO_REGION_ADMIN)
        for r in ROWS:
            self.assertFalse(ac.row_is_in_admin_scope(r, scope))

    def test_deps_lets_an_all_scope_admin_in_without_a_region(self):
        """ก่อนมีตัวเลือกนี้ deps ตอบ 403 ทุกคนที่ไม่มีภาค — ต้องไม่บล็อกระดับ all แล้ว"""
        user = {"role": ac.ROLE_REGION_ADMIN, "admin_scope": ac.admin_scope_for_email(ALL_ADMIN)}
        deps.ensure_row_in_admin_scope(user, {"email": INCOMPLETE})   # ต้องไม่โยน

    def test_deps_still_blocks_a_narrow_admin_from_an_incomplete_row(self):
        user = {"role": ac.ROLE_REGION_ADMIN, "admin_scope": ac.admin_scope_for_email(NORTH_ADMIN)}
        with self.assertRaises(HTTPException) as cm:
            deps.ensure_row_in_admin_scope(user, {"email": INCOMPLETE})
        self.assertEqual(cm.exception.status_code, 403)

    # ── ค่าที่เก็บในไฟล์ต้องรอดการเขียนทับ เหมือน role ──
    def test_admin_scope_survives_canonicalize_and_write(self):
        rows = uas.canonicalize_user_access_rows(uas.read_rows())
        got = next(r for r in rows if r["email"] == ALL_ADMIN)
        self.assertEqual(got.get("admin_scope"), "all")

    def test_admin_scope_is_not_patchable_through_the_normal_user_put(self):
        """กันแอดมินขยายขอบเขตตัวเองผ่าน PUT /user-access ปกติ (เหมือนที่กัน role)"""
        from backend.routers import admin as admin_router

        self.assertNotIn("admin_scope", admin_router._META_PATCH_KEYS)
        self.assertNotIn("role", admin_router._META_PATCH_KEYS)


class TestSetRoleEndpointNormalisesScope(_RbacBase):
    """
    ตัว endpoint ที่ dev ใช้มอบสิทธิ์ — เรียกฟังก์ชันตรง ๆ ไม่ผ่าน HTTP
    (ตัด audit log ไปลงโฟลเดอร์ชั่วคราว จะได้ไม่เขียนของจริง)
    """

    def setUp(self):
        super().setUp()
        self._old_logs = os.environ.get("USAGE_LOGS_DIR")
        os.environ["USAGE_LOGS_DIR"] = os.path.join(self._tmp.name, "logs")

    def tearDown(self):
        if self._old_logs is None:
            os.environ.pop("USAGE_LOGS_DIR", None)
        else:
            os.environ["USAGE_LOGS_DIR"] = self._old_logs
        super().tearDown()

    def _call(self, email, role, admin_scope=""):
        from backend.routers import admin as admin_router

        body = admin_router.UserRoleBody(email=email, role=role, admin_scope=admin_scope)
        return admin_router.set_user_role(body, admin={"email": DEV_ENV, "role": ac.ROLE_DEV})

    def _stored(self, email):
        return next(r for r in uas.read_rows() if r["email"] == email)

    def test_admin_without_explicit_scope_gets_the_narrowest(self):
        res = self._call(PLAIN, "admin")
        self.assertEqual(res["admin_scope"], ac.ADMIN_SCOPE_DIVISION_REGION)
        self.assertEqual(self._stored(PLAIN).get("admin_scope"), ac.ADMIN_SCOPE_DIVISION_REGION)

    def test_scope_is_written_and_takes_effect(self):
        self._call(PLAIN, "admin", "all")
        ac.invalidate_user_access_cache()
        self.assertEqual(ac.admin_scope_for_email(PLAIN)["breadth"], ac.ADMIN_SCOPE_ALL)

    def test_bad_scope_is_rejected(self):
        with self.assertRaises(HTTPException) as cm:
            self._call(PLAIN, "admin", "ทั้งประเทศ")
        self.assertEqual(cm.exception.status_code, 400)

    def test_dev_role_never_keeps_a_scope(self):
        """dev ดูแลทั้งระบบอยู่แล้ว — ค่าขอบเขตค้างไว้จะกลายเป็นกับดักตอนถูกลดเป็น admin"""
        self._call(ALL_ADMIN, "dev")
        self.assertNotIn("admin_scope", self._stored(ALL_ADMIN))

    def test_removing_the_role_clears_the_scope_too(self):
        self._call(ALL_ADMIN, "")
        row = self._stored(ALL_ADMIN)
        self.assertNotIn("role", row)
        self.assertNotIn("admin_scope", row)


class TestRoleOverlaysTheJobPosition(_RbacBase):
    """
    "แอดมินภาค" ไม่ใช่ตำแหน่งแทนที่ของเดิม — เป็นสิทธิ์ที่ซ้อนทับตำแหน่งงานเดิม

    คนคนเดียวจึงเป็นได้ทั้ง Supervisor (ทำงานกระจายหีบทีมตัวเองตามปกติ)
    และแอดมินภาค (ดูแลผู้ใช้ในภาค) พร้อมกัน — คนละฟิลด์กันในไฟล์:
      login_kind = ตำแหน่งงาน · role = สิทธิ์ดูแลระบบ
    """

    def test_position_and_admin_role_live_in_separate_fields(self):
        row = next(r for r in ROWS if r["email"] == NORTH_ADMIN)
        self.assertEqual(row["login_kind"], "supervisor_acc", "ตำแหน่งงานยังเป็น Supervisor")
        self.assertEqual(row["role"], "admin", "และมีสิทธิ์แอดมินภาคซ้อนอยู่")

    def test_admin_role_does_not_replace_the_position(self):
        """ยังถูกนับเป็น supervisor_acc ในสายตาโครงสร้างสิทธิ์เดิมทุกประการ"""
        rows = uas.read_rows()
        me = next(r for r in rows if r["email"] == NORTH_ADMIN)
        self.assertEqual(me.get("login_kind"), "supervisor_acc")
        self.assertEqual(me.get("acc_region"), "เหนือ")

    def test_admin_still_gets_normal_data_access_of_their_own_team(self):
        """
        สิทธิ์ดูข้อมูล (ดูทีมไหนได้) ต้องมาจากตำแหน่งงานเหมือนเดิม
        ไม่ใช่ถูก role=admin เข้ามาแทนที่ — และต้องไม่ได้ sentinel None (= ไม่จำกัด)
        """
        ctx = ac.build_user_access_context(NORTH_ADMIN)
        self.assertIsNotNone(
            ctx.get("allowed_supervisor_codes"),
            "แอดมินภาคต้องไม่ได้สิทธิ์ไม่จำกัดแบบ dev",
        )
        self.assertIn("SL901", ctx["allowed_supervisor_codes"], "ยังเห็นทีมตัวเอง")
        self.assertFalse(ctx.get("is_admin"), "is_admin สงวนไว้ให้ dev เท่านั้น")

    def test_dev_role_also_overlays_without_erasing_the_position(self):
        rows = uas.read_rows()
        me = next(r for r in rows if r["email"] == DEV_ROW)
        self.assertEqual(me.get("login_kind"), "supervisor_acc")
        self.assertEqual(ac.role_for_email(DEV_ROW), ac.ROLE_DEV)

    def test_plain_supervisor_is_unaffected_by_the_new_field(self):
        ctx = ac.build_user_access_context(PLAIN)
        self.assertIn("SL903", ctx["allowed_supervisor_codes"])
        self.assertEqual(ac.role_for_email(PLAIN), "user")


class TestAdminOnlyAccounts(_RbacBase):
    """
    บัญชีที่เป็น "แอดมินอย่างเดียว" — ไม่มีตำแหน่งงาน ไม่มีรหัส SL

    ปกติแถวที่ไม่มี userpl จะถูกทิ้งตอนอ่านไฟล์ (กันแถวขยะ) แต่แถวที่มี role
    ยังมีความหมายอยู่ จึงต้องรอด ไม่งั้นสร้างแอดมินเสร็จแล้วหายไปเงียบ ๆ
    """

    def setUp(self):
        super().setUp()
        self._old_logs = os.environ.get("USAGE_LOGS_DIR")
        os.environ["USAGE_LOGS_DIR"] = os.path.join(self._tmp.name, "logs")

    def tearDown(self):
        if self._old_logs is None:
            os.environ.pop("USAGE_LOGS_DIR", None)
        else:
            os.environ["USAGE_LOGS_DIR"] = self._old_logs
        super().tearDown()

    def _call(self, **kw):
        from backend.routers import admin as admin_router

        body = admin_router.UserRoleBody(**kw)
        return admin_router.set_user_role(body, admin={"email": DEV_ENV, "role": ac.ROLE_DEV})

    NEW = "onlyadmin@sahapat.co.th"

    def test_creating_an_admin_only_account_for_a_brand_new_email(self):
        res = self._call(email=self.NEW, role="admin", admin_scope="all")
        self.assertTrue(res["created"])
        row = next(r for r in uas.read_rows() if r["email"] == self.NEW)
        self.assertEqual(row.get("role"), "admin")
        self.assertEqual(str(row.get("userpl") or ""), "", "แอดมินอย่างเดียวไม่ต้องมีรหัส SL")
        self.assertEqual(str(row.get("login_kind") or "standard"), "standard")

    def test_the_new_row_survives_a_read_write_round_trip(self):
        self._call(email=self.NEW, role="admin", admin_scope="all")
        uas.write_rows(uas.read_rows())          # เขียนกลับเหมือนที่หน้าแอดมินทำ
        ac.invalidate_user_access_cache()
        emails = [r["email"] for r in uas.read_rows()]
        self.assertIn(self.NEW, emails, "แถวแอดมินอย่างเดียวต้องไม่หายตอนเขียนไฟล์รอบถัดไป")

    def test_it_actually_gets_admin_rights(self):
        self._call(email=self.NEW, role="admin", admin_scope="all")
        ac.invalidate_user_access_cache()
        self.assertEqual(ac.role_for_email(self.NEW), ac.ROLE_REGION_ADMIN)
        scope = ac.admin_scope_for_email(self.NEW)
        self.assertEqual(scope["breadth"], ac.ADMIN_SCOPE_ALL)
        self.assertTrue(ac.admin_scope_is_usable(scope))

    def test_it_sees_no_team_data_of_its_own(self):
        """มีไว้ดูแลระบบ ไม่ใช่ดูข้อมูลขาย — ต้องไม่ได้สิทธิ์ดูทีมใดติดมาด้วย"""
        self._call(email=self.NEW, role="admin", admin_scope="all")
        ac.invalidate_user_access_cache()
        ctx = ac.build_user_access_context(self.NEW)
        self.assertIsNotNone(ctx.get("allowed_supervisor_codes"), "ต้องไม่ได้ sentinel ไม่จำกัด")
        self.assertEqual(set(ctx["allowed_supervisor_codes"]), set())

    def test_division_and_region_can_be_set_at_creation_time(self):
        self._call(
            email=self.NEW, role="admin", admin_scope="division",
            acc_division="Div.B", acc_region="เหนือ",
        )
        ac.invalidate_user_access_cache()
        scope = ac.admin_scope_for_email(self.NEW)
        self.assertEqual(scope["divisions"], {"Div.B"})
        self.assertIn("SL920", scope["sl_codes"], "Div.B ภาคใต้ต้องอยู่ในขอบเขตระดับ division")

    def test_revoking_removes_the_row_entirely(self):
        """ไม่มีทั้งรหัสและสิทธิ์ = ไม่เหลือเหตุผลให้มีแถวนี้ — ลบให้ชัด ไม่ปล่อยหายเงียบ"""
        self._call(email=self.NEW, role="admin", admin_scope="all")
        res = self._call(email=self.NEW, role="")
        self.assertEqual(res["rows_removed"], 1)
        self.assertNotIn(self.NEW, [r["email"] for r in uas.read_rows()])

    def test_revoking_keeps_a_real_supervisor_row(self):
        """คนที่มีตำแหน่งงานอยู่แล้ว ถอดสิทธิ์แล้วต้องยังเป็นผู้ใช้ปกติต่อไป"""
        self._call(email=PLAIN, role="admin", admin_scope="all")
        res = self._call(email=PLAIN, role="")
        self.assertEqual(res["rows_removed"], 0)
        row = next(r for r in uas.read_rows() if r["email"] == PLAIN)
        self.assertEqual(row["userpl"], "SL903")
        self.assertNotIn("role", row)

    def test_creating_is_refused_when_no_role_is_given(self):
        with self.assertRaises(HTTPException) as cm:
            self._call(email="ghost@sahapat.co.th", role="")
        self.assertEqual(cm.exception.status_code, 404)

    def test_region_of_a_real_supervisor_is_never_overwritten(self):
        """กันหน้าผู้ดูแลเผลอย้ายภาคของ Supervisor ตัวจริง"""
        self._call(email=PLAIN, role="admin", admin_scope="division",
                   acc_division="Div.E", acc_region="ใต้")
        row = next(r for r in uas.read_rows() if r["email"] == PLAIN)
        self.assertEqual(row.get("acc_region"), "เหนือ")
        self.assertEqual(row.get("acc_division"), "Div.B")

    def test_a_row_without_role_or_code_is_still_thrown_away(self):
        """กติกาเดิมยังอยู่ — แถวขยะที่ไม่มีทั้งรหัสและ role ต้องไม่ถูกอ่านเข้ามา"""
        junk = ROWS + [{"email": "junk@sahapat.co.th", "userpl": ""}]
        with open(self._path, "w", encoding="utf-8") as fh:
            json.dump(junk, fh, ensure_ascii=False)
        ac.invalidate_user_access_cache()
        self.assertNotIn("junk@sahapat.co.th", [r["email"] for r in uas.read_rows()])


class TestRolesPageIsWhereRolesAreSet(unittest.TestCase):
    """
    การตั้งสิทธิ์อยู่ที่หน้า "ผู้ดูแลระบบ" หน้าเดียว — ไม่ใช่ dropdown ในทุกแถวของตารางผู้ใช้

    เคยทำเป็น select ในคอลัมน์ตำแหน่งของตารางผู้ใช้ ผลคือ 95 แถว = 95 ช่องเลือก
    ผู้ใช้บอกตรง ๆ ว่า "ดูยากมาก UI มันใช้ยาก" — เทสนี้กันไม่ให้ย้อนกลับไปแบบนั้น
    """

    @staticmethod
    def _read(name):
        with open(os.path.join(REPO, "frontend", name), encoding="utf-8") as fh:
            return fh.read()

    def test_admin_sidebar_has_the_roles_page(self):
        html = self._read("index.html")
        self.assertIn('data-tab="roles"', html)
        self.assertIn('id="adminPanelRoles"', html)
        self.assertIn('id="adminRolesBody"', html)

    def test_user_table_shows_a_read_only_chip_not_a_dropdown(self):
        import inspect
        import re

        src = self._read("app.js")
        m = re.search(
            r"function _adminSystemRoleControlHtml\(r\) \{(.*?)\n\}", src, re.S
        )
        self.assertIsNotNone(m, "ไม่พบฟังก์ชันที่วาดป้ายสิทธิ์ในตารางผู้ใช้")
        body = m.group(1)
        self.assertNotIn("<select", body, "ตารางผู้ใช้ต้องไม่มีช่องเลือกสิทธิ์รายแถวอีก")
        self.assertIn("admin-sysrole-chip", body)
        del inspect

    def test_plain_admin_does_not_see_the_roles_page(self):
        """แอดมินธรรมดาต้องไม่เห็นหน้านี้ — มอบสิทธิ์ต่อให้ตัวเองไม่ได้"""
        src = self._read("app.js")
        m = re.search(r"const ADMIN_TABS_ADMIN = \[(.*?)\];", src, re.S)
        self.assertIsNotNone(m, "ไม่พบรายการแท็บของแอดมินธรรมดา")
        self.assertNotIn("roles", m.group(1))

    def test_head_admin_does_see_the_roles_page(self):
        """หัวหน้าแอดมินเพิ่ม/ถอดสิทธิ์แอดมินคนอื่นได้ จึงต้องมีแท็บนี้"""
        src = self._read("app.js")
        m = re.search(r"const ADMIN_TABS_HEAD_ADMIN = \[(.*?)\];", src, re.S)
        self.assertIsNotNone(m, "ไม่พบรายการแท็บของหัวหน้าแอดมิน")
        self.assertIn("roles", m.group(1))
        self.assertNotIn("data", m.group(1), "แหล่งข้อมูลมีผลทั้งระบบ — ต้องเป็นของ dev เท่านั้น")

    def test_frontend_role_options_match_the_backend(self):
        """ตัวเลือกระดับสิทธิ์ฝั่งหน้าเว็บต้องตรงกับที่ backend ยอมรับ ไม่งั้นกดแล้วได้ 400"""
        src = self._read("app.js")
        m = re.search(r"const ADMIN_SYSROLE_OPTS = \[(.*?)\];", src, re.S)
        self.assertIsNotNone(m)
        found = set(re.findall(r'\["([a-z_]+)",', m.group(1)))
        self.assertEqual(found, set(ac.ASSIGNABLE_ROLES))

    def test_scope_options_match_the_backend(self):
        """รายการตัวเลือกฝั่งหน้าเว็บต้องตรงกับที่ backend ยอมรับ ไม่งั้นกดแล้วได้ 400"""
        src = self._read("app.js")
        m = re.search(r"const ADMIN_SCOPE_OPTS = \[(.*?)\];", src, re.S)
        self.assertIsNotNone(m)
        found = set(re.findall(r'\["([a-z_]+)",', m.group(1)))
        self.assertEqual(found, set(ac.ASSIGNABLE_ADMIN_SCOPES))


class TestRoleCannotBeSelfAssigned(unittest.TestCase):
    def test_role_is_not_in_the_editable_meta_whitelist(self):
        from backend.routers.admin import _META_PATCH_KEYS

        self.assertNotIn(
            "role", _META_PATCH_KEYS,
            "ถ้า role แก้ผ่าน PUT /user-access ได้ แอดมินรายภาคจะเลื่อนขั้นตัวเองเป็น dev",
        )

    def test_role_endpoint_is_limited_to_role_managers(self):
        """
        เปลี่ยนพฤติกรรมโดยตั้งใจ (2026-08-20): เดิม dev เท่านั้น ตอนนี้หัวหน้าแอดมินด้วย

        แต่ต้องผ่านด่านเฉพาะ (require_role_manager) ไม่ใช่ด่านแอดมินทั่วไป —
        ถ้าใช้ require_admin_scoped ตรง ๆ แอดมินธรรมดาจะตั้งสิทธิ์ได้ทันที
        """
        import inspect

        from backend.routers import admin as admin_router

        src = inspect.getsource(admin_router.set_user_role)
        self.assertIn("require_role_manager", src)
        self.assertNotIn("Depends(require_admin_scoped)", src)
        self.assertIn(
            "ensure_can_assign_role", src,
            "หัวหน้าแอดมินต้องถูกจำกัดว่ามอบได้เฉพาะ 'admin' และแตะสิทธิ์ตัวเองไม่ได้",
        )

    def test_role_manager_dep_excludes_plain_admin(self):
        import inspect

        from backend import deps

        src = inspect.getsource(deps.require_role_manager)
        self.assertIn("ROLE_HEAD_ADMIN", src)
        self.assertNotIn("ROLE_ADMIN,", src, "แอดมินธรรมดาต้องไม่หลุดเข้ามาในด่านนี้")


class TestRoleSurvivesFileWrites(_RbacBase):
    def test_canonicalize_keeps_role(self):
        canon = uas.canonicalize_user_access_row(dict(ROWS[1]))
        self.assertEqual(canon["role"], "admin")

    def test_write_read_roundtrip_keeps_role(self):
        uas.write_rows(ROWS)
        ac.invalidate_user_access_cache()
        rows = uas.read_rows()
        by_email = {r["email"]: r for r in rows}
        self.assertEqual(by_email[NORTH_ADMIN].get("role"), "admin")
        self.assertEqual(by_email[DEV_ROW].get("role"), "dev")
        self.assertNotIn("role", by_email[PLAIN], "ผู้ใช้ทั่วไปต้องไม่มี role ติดมา")


if __name__ == "__main__":
    unittest.main()
