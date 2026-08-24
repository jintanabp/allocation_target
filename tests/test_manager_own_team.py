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


class TestAggregateTotalsDoNotMove(_Base):
    def test_region_aggregate_still_counts_only_supervisor_teams(self):
        codes = resolve_aggregate_supervisor_codes("SL359", TEAM, "region", "เหนือ")
        self.assertEqual(sorted(codes), ["SL396", "SL506"])
        self.assertNotIn("SL359", codes)

    def test_region_entry_excludes_the_manager_code(self):
        opts = build_manager_view_options("SL359", TEAM)
        self.assertEqual(sorted(opts["regions"][0]["supervisor_codes"]), ["SL396", "SL506"])


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


if __name__ == "__main__":
    unittest.main()
