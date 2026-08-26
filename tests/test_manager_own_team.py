"""
ผู้จัดการที่มีพนักงานขายสังกัดรหัสตัวเองตรง ๆ ต้องเปิดทีมตัวเองได้

เจอของจริง: SL359 (สุขี) มีพนักงาน 4 คน S504/S507/S509/S512 สังกัดรหัส SL359
โดยตรง ไม่ได้ผ่านทีมซุปเลย และงวด 2026-06 มีเป้าครบทุกคน (หมื่นถึงสองหมื่นหีบ)
แต่ระบบตัดรหัสผู้จัดการออกจากทีมของตัวเองเสมอ ("Manager แสดงเฉพาะ Supervisor จริง")
ผลคือ ensure_supervisor_allowed ผ่าน แต่ไม่มีตัวเลือกให้กด — สิทธิ์มีแต่เปิดไม่ได้
ในเครื่องพบแบบนี้ 5 รหัสจาก 25 (SL301 SL356 SL359 SL372 SL532)

กติกาที่ต้องคงไว้:
  - รหัสของผู้จัดการเองต้องอยู่ในรายการ "เลือกเปิดทีละทีม"
  - รหัสผู้จัดการคนอื่น และรหัสพ้องที่ผูก sl_links ไว้ ยังต้องถูกตัดเหมือนเดิม
  - การรวมเป้า (ทั้งภาค / ทั้งหมด) ต้องนับเฉพาะทีมซุป ยอดรวมห้ามขยับ
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

import backend.services.manager_views as mv  # noqa: E402
from backend.services.manager_views import (  # noqa: E402
    build_manager_view_options,
    resolve_aggregate_supervisor_codes,
    team_supervisor_codes,
)

logging.disable(logging.CRITICAL)

# ผจก.รายภาค SL359 คุมซุป 2 ทีม และมีพนักงานสังกัดรหัสตัวเองด้วย
ROWS = [
    {"email": "mgr@x.co.th", "userpl": "SL359", "login_kind": "manager_acc",
     "manager_level": "regional", "acc_division": "Div.S", "acc_region": "เหนือ",
     "can_import_targetsun": False, "note": ""},
    {"email": "sup1@x.co.th", "userpl": "SL396", "login_kind": "supervisor_acc",
     "acc_division": "Div.S", "acc_region": "เหนือ", "acc_unit": "credit",
     "can_import_targetsun": False, "note": ""},
    {"email": "sup2@x.co.th", "userpl": "SL506", "login_kind": "supervisor_acc",
     "acc_division": "Div.S", "acc_region": "เหนือ", "acc_unit": "credit",
     "can_import_targetsun": False, "note": ""},
]
TEAM = ["SL359", "SL396", "SL506"]


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        path = os.path.join(self._tmp.name, "user_access.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(ROWS, fh, ensure_ascii=False)
        self._old = os.environ.get("USER_ACCESS_JSON_PATH")
        os.environ["USER_ACCESS_JSON_PATH"] = path

    def tearDown(self):
        if self._old is None:
            os.environ.pop("USER_ACCESS_JSON_PATH", None)
        else:
            os.environ["USER_ACCESS_JSON_PATH"] = self._old
        self._tmp.cleanup()


class TestOwnCodeIsSelectable(_Base):
    def test_manager_can_pick_their_own_code_as_a_team(self):
        opts = build_manager_view_options("SL359", TEAM)
        self.assertIn("SL359", opts["supervisor_codes"])
        self.assertEqual(sorted(opts["supervisor_codes"]), ["SL359", "SL396", "SL506"])

    def test_it_works_even_when_the_team_list_omits_the_code(self):
        opts = build_manager_view_options("SL359", ["SL396", "SL506"])
        self.assertIn("SL359", opts["supervisor_codes"])

    def test_the_own_code_carries_its_region(self):
        opts = build_manager_view_options("SL359", TEAM)
        self.assertEqual(opts["supervisor_meta"]["SL359"]["region"], "เหนือ")


class TestManagerOwnTeamCountsInAggregate(_Base):
    """
    ผู้จัดการที่มีพนักงานสังกัดตรง ต้องถูกนับรวมในการกระจายรวมภาคด้วย

    เดิมรหัสผู้จัดการถูกตัดออกเสมอ เพื่อให้ยอดรวมไม่ขยับจากของเก่า — ผลคือเป้าของ
    พนักงานที่สังกัดผู้จัดการโดยตรงหายไปจากยอดรวมภาคทั้งก้อน ทีมของเขาจึงไม่เคย
    ถูกเกลี่ยร่วมกับใคร ทั้งที่อยู่ภาคเดียวกันแท้ ๆ (ของจริง 5 คน เช่น SL359
    ที่เป้าทีมตัวเองเป็นก้อนใหญ่เมื่อเทียบกับทีมซุปใต้สังกัด)

    ส่งรายชื่อ own_salesmen_codes เข้าไปตรง ๆ ทุกเทส — ไม่งั้นตัวฟังก์ชันจะไปอ่าน
    โฟลเดอร์ data จริง แล้วผลเทสเปลี่ยนไปตามว่าเครื่องนั้นเคยเปิดทีมไหนมาบ้าง
    """

    WITH_STAFF = {"SL359"}
    NO_STAFF: set[str] = set()

    def test_region_aggregate_includes_the_manager_team(self):
        codes = resolve_aggregate_supervisor_codes(
            "SL359", TEAM, "region", "เหนือ", own_salesmen_codes=self.WITH_STAFF
        )
        self.assertEqual(sorted(codes), ["SL359", "SL396", "SL506"])

    def test_region_entry_includes_the_manager_code(self):
        opts = build_manager_view_options(
            "SL359", TEAM, own_salesmen_codes=self.WITH_STAFF
        )
        self.assertEqual(
            sorted(opts["regions"][0]["supervisor_codes"]),
            ["SL359", "SL396", "SL506"],
        )

    def test_a_manager_without_own_salesmen_is_still_excluded(self):
        """ยอดของผู้จัดการที่ไม่มีพนักงานสังกัดตรง ต้องไม่ขยับจากของเดิม"""
        opts = build_manager_view_options(
            "SL359", TEAM, own_salesmen_codes=self.NO_STAFF
        )
        self.assertEqual(
            sorted(opts["regions"][0]["supervisor_codes"]), ["SL396", "SL506"]
        )
        codes = resolve_aggregate_supervisor_codes(
            "SL359", TEAM, "region", "เหนือ", own_salesmen_codes=self.NO_STAFF
        )
        self.assertEqual(sorted(codes), ["SL396", "SL506"])

    def test_the_manager_code_is_still_selectable_either_way(self):
        """เปิดดูทีมตัวเองทีละทีมได้เสมอ ไม่ว่าจะถูกนับรวมเป้าหรือไม่"""
        for known in (self.WITH_STAFF, self.NO_STAFF):
            opts = build_manager_view_options("SL359", TEAM, own_salesmen_codes=known)
            self.assertIn("SL359", opts["supervisor_codes"])


class TestOtherManagerCodesStillDropped(unittest.TestCase):
    def test_linked_old_code_is_still_excluded(self):
        """SL508 เป็นรหัสเก่าของ SL524 คนเดียวกัน — ต้องไม่โผล่ซ้ำเป็นอีกทีม"""
        from backend.services.sl_link_store import manager_codes_to_exclude_from_team

        excl = manager_codes_to_exclude_from_team(
            "SL524", {"SL508", "SL524"},
            [{"old_sl": "SL508", "canonical_sl": "SL508",
              "new_sls": ["SL524"], "alias_sls": ["SL508", "SL524"]}],
        )
        keep = team_supervisor_codes(["SL508", "SL532"], "SL524", excl, keep_own_code=True)
        self.assertEqual(keep, ["SL532"])

    def test_default_still_drops_the_own_code(self):
        """ตัวเรียกที่ไม่ได้ขอ keep_own_code ต้องได้พฤติกรรมเดิมเป๊ะ"""
        self.assertEqual(
            team_supervisor_codes(["SL359", "SL396"], "SL359"),
            ["SL396"],
        )


class TestDefaultLandingTeam(_Base):
    """หน้าแรกที่เปิด: คนที่มีพนักงานสังกัดตรงเข้าทีมตัวเอง คนอื่นเหมือนเดิม"""

    def setUp(self):
        super().setUp()
        self._data = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self._data.name, "data"), exist_ok=True)

    def tearDown(self):
        self._data.cleanup()
        super().tearDown()

    def _write_cache(self, code, rows=("E001,ชื่อพนักงาน,SL359",)):
        path = os.path.join(self._data.name, "data", f"emp_cache_{code}_2026_09.csv")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("emp_id,emp_name,super_code\n")
            for r in rows:
                fh.write(r + "\n")

    def _known(self):
        return mv.codes_with_own_salesmen(os.path.join(self._data.name, "data"))

    def test_code_with_cached_employees_is_detected(self):
        self._write_cache("SL359")
        self.assertIn("SL359", self._known())

    def test_header_only_file_does_not_count(self):
        path = os.path.join(self._data.name, "data", "emp_cache_SL396_2026_09.csv")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("emp_id,emp_name,super_code\n")
        self.assertNotIn("SL396", self._known())

    def test_missing_folder_is_not_an_error(self):
        self.assertEqual(mv.codes_with_own_salesmen("ไม่มีโฟลเดอร์นี้"), set())

    def test_flag_is_true_only_for_codes_with_staff(self):
        self._write_cache("SL359")
        known = self._known()
        self.assertTrue(build_manager_view_options("SL359", TEAM, None, known)["own_team_has_staff"])
        self.assertFalse(build_manager_view_options("SL396", TEAM, None, known)["own_team_has_staff"])

    def test_flag_is_false_when_nothing_is_cached_yet(self):
        self.assertFalse(
            build_manager_view_options("SL359", TEAM, None, set())["own_team_has_staff"]
        )


if __name__ == "__main__":
    unittest.main()
