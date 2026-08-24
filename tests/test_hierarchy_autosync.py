"""
ลำดับสิทธิ์อัปเดตเองเมื่อรายชื่อผู้ใช้เปลี่ยน — ไม่มีปุ่มให้คนต้องจำไปกด

ปุ่ม "อัปเดตลำดับสิทธิ์" เปิดให้เฉพาะ dev แต่แอดมินที่แก้ผู้ใช้ได้กลับกดไม่ได้
เจอแต่ 403 ส่วนตัวเรียกอัตโนมัติฝั่งหน้าเว็บก็โดนด่านกันทีมหดตอบ 409 ทุกครั้ง
ผลคือผู้ใช้ใหม่ล็อกอินเข้ามาแล้วไม่มีทีมให้เลือก เพราะ access_hierarchy.json
ยังไม่รู้จักเขา ตอนนี้ย้ายมาทำฝั่ง server ในคำขอเดียวกับการบันทึก

กติกาที่ต้องคงไว้:
  - เพิ่ม/แก้/ลบผู้ใช้ แล้ว access_hierarchy.json ต้องตามทันทีโดยไม่ต้องกดอะไร
  - แอดมินรายภาค (ไม่ใช่ dev) ก็ต้องทำให้เกิดได้
  - ผู้จัดการที่แถวไม่มี division/ภาค ต้องไม่ถูกตัดทีม เพราะข้อมูลชุดนั้นมาจาก
    roster Excel ที่แอปสร้างขึ้นใหม่เองไม่ได้
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

import backend.services.access_hierarchy as ah  # noqa: E402
from backend.routers.admin import (  # noqa: E402
    UserAccessBody,
    UserAccessDeleteBody,
    create_user_access,
    remove_user_access,
)
from backend.services import access_control as ac  # noqa: E402

logging.disable(logging.CRITICAL)

# ผจก. ที่ข้อมูลครบ (คำนวณทีมได้) กับที่ข้อมูลไม่ครบ (คำนวณไม่ได้)
ROWS = [
    {"email": "mgr.ok@x.co.th", "userpl": "SL800", "login_kind": "manager_acc",
     "manager_level": "regional", "acc_division": "Div.E", "acc_region": "กรุงเทพ",
     "can_import_targetsun": False, "note": ""},
    {"email": "sup.a@x.co.th", "userpl": "SL801", "login_kind": "supervisor_acc",
     "acc_division": "Div.E", "acc_region": "กรุงเทพ",
     "can_import_targetsun": False, "note": ""},
    {"email": "mgr.blank@x.co.th", "userpl": "SL900", "login_kind": "manager_acc",
     "can_import_targetsun": False, "note": ""},
]
ROSTER_TEAM = ["SL901", "SL902", "SL903"]

DEV = {"email": "dev@x.co.th", "role": "dev", "auth_disabled": False, "admin_scope": None}
REGION_ADMIN = {
    "email": "north.admin@x.co.th", "role": "admin", "auth_disabled": False,
    "admin_scope": {"breadth": "division_region", "regions": {"กรุงเทพ"}, "divisions": {"Div.E"}},
}


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        d = self._tmp.name
        self.ua = os.path.join(d, "user_access.json")
        self.hier = os.path.join(d, "access_hierarchy.json")
        with open(self.ua, "w", encoding="utf-8") as fh:
            json.dump(ROWS, fh, ensure_ascii=False)
        # roster เดิมรู้ทีมของ SL900 ซึ่งคำนวณกลับไม่ได้
        with open(self.hier, "w", encoding="utf-8") as fh:
            json.dump({"by_manager": {"SL900": ROSTER_TEAM, "SL800": ["SL801"]},
                       "supervisors": ["SL801"] + ROSTER_TEAM,
                       "manager_codes": ["SL800", "SL900"]}, fh, ensure_ascii=False)
        self._old = {k: os.environ.get(k) for k in
                     ("USER_ACCESS_JSON_PATH", "ACCESS_HIERARCHY_JSON_PATH", "USER_ACCESS_CACHE_TTL_SEC")}
        os.environ["USER_ACCESS_JSON_PATH"] = self.ua
        os.environ["ACCESS_HIERARCHY_JSON_PATH"] = self.hier
        os.environ["USER_ACCESS_CACHE_TTL_SEC"] = "0"
        # persist_hierarchy เขียน data/managers_cache.json ด้วย path ของ repo จริง
        self._old_root = ah._repo_root
        ah._repo_root = lambda: d
        ac.invalidate_user_access_cache()

    def tearDown(self):
        ah._repo_root = self._old_root
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        ac.invalidate_user_access_cache()
        self._tmp.cleanup()

    def _hier(self):
        with open(self.hier, encoding="utf-8") as fh:
            return json.load(fh)


class TestUncomputableTeamsSurvive(_Base):
    def test_manager_without_division_keeps_the_roster_team(self):
        payload = ah.build_hierarchy_payload()
        self.assertEqual(sorted(payload["by_manager"]["SL900"]), sorted(ROSTER_TEAM + ["SL900"]))

    def test_manager_with_full_data_uses_the_computation(self):
        payload = ah.build_hierarchy_payload()
        self.assertEqual(sorted(payload["by_manager"]["SL800"]), ["SL800", "SL801"])

    def test_rebuilding_twice_is_stable(self):
        first = ah.build_hierarchy_payload()
        ah.persist_hierarchy(first)
        second = ah.build_hierarchy_payload()
        self.assertEqual(first["by_manager"], second["by_manager"])

    def test_opting_out_lets_the_team_shrink(self):
        payload = ah.build_hierarchy_payload(keep_uncomputable_teams=False)
        self.assertEqual(payload["by_manager"]["SL900"], ["SL900"])


class TestEditingSyncsHierarchy(_Base):
    def _add_new_supervisor(self, admin):
        create_user_access(
            UserAccessBody(
                email="new.sup@x.co.th", userpl="SL850", login_kind="supervisor_acc",
                acc_division="Div.E", acc_region="กรุงเทพ",
            ),
            admin,
        )

    def test_adding_a_user_updates_the_hierarchy_with_no_button(self):
        self.assertNotIn("SL850", self._hier()["supervisors"])
        self._add_new_supervisor(DEV)
        self.assertIn("SL850", self._hier()["supervisors"])

    def test_a_region_admin_can_trigger_it_too(self):
        """เมื่อก่อนแอดมินรายภาคกดปุ่มแล้วโดน 403 — ตอนนี้เกิดเองจากการบันทึก"""
        self._add_new_supervisor(REGION_ADMIN)
        self.assertIn("SL850", self._hier()["supervisors"])

    def test_deleting_a_user_updates_the_hierarchy(self):
        self._add_new_supervisor(DEV)
        remove_user_access(UserAccessDeleteBody(email="new.sup@x.co.th", userpl="SL850"), DEV)
        self.assertNotIn("SL850", self._hier()["supervisors"])

    def test_the_sync_never_cuts_an_uncomputable_manager(self):
        self._add_new_supervisor(DEV)
        self.assertEqual(
            sorted(self._hier()["by_manager"]["SL900"]),
            sorted(ROSTER_TEAM + ["SL900"]),
            "เพิ่มคนอื่นแล้วต้องไม่ไปตัดทีมของผู้จัดการที่ข้อมูลไม่ครบ",
        )

    def test_the_new_user_can_reach_their_own_team(self):
        self._add_new_supervisor(DEV)
        ac.invalidate_user_access_cache()
        ctx = ac.build_user_access_context("new.sup@x.co.th", allow_admin_bypass=False)
        self.assertIn("SL850", ctx["allowed_supervisor_codes"])


if __name__ == "__main__":
    unittest.main()
