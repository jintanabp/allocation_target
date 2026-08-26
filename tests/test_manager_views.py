"""Tests for manager aggregate view options."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import access_control as ac  # noqa: E402
from backend.services.manager_views import (  # noqa: E402
    build_manager_view_options,
    is_division_wide_manager,
    resolve_aggregate_supervisor_codes,
)

# roster ของเทสเอง — เดิมอ่าน config/user_access.json ตัวจริง เทสจึงผูกกับข้อมูล
# ของบริษัทที่แอดมินแก้ได้ทุกวัน (และหายไปเลยบน CI หลังไฟล์ถูกถอดออกจาก git)
ROSTER = [
    {"email": "mgr.div@x.co.th", "userpl": "SL360", "login_kind": "manager_acc",
     "manager_level": "division", "acc_division": "Div.S", "acc_scope": "all",
     "can_import_targetsun": False, "note": ""},
    {"email": "mgr.region@x.co.th", "userpl": "SL345", "login_kind": "manager_acc",
     "manager_level": "regional", "acc_division": "Div.B", "acc_region": "กลาง",
     "acc_scope": "all", "can_import_targetsun": False, "note": ""},
    {"email": "sup.central@x.co.th", "userpl": "SL350", "login_kind": "supervisor_acc",
     "acc_division": "Div.S", "acc_region": "กลาง", "acc_scope": "van",
     "can_import_targetsun": False, "note": ""},
    {"email": "sup.north@x.co.th", "userpl": "SL351", "login_kind": "supervisor_acc",
     "acc_division": "Div.S", "acc_region": "เหนือ", "acc_scope": "van",
     "can_import_targetsun": False, "note": ""},
    {"email": "sup.b@x.co.th", "userpl": "SL401", "login_kind": "supervisor_acc",
     "acc_division": "Div.B", "acc_region": "กลาง", "acc_scope": "credit",
     "can_import_targetsun": False, "note": ""},
]


class _RosterBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        path = os.path.join(cls._tmp.name, "user_access.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(ROSTER, fh, ensure_ascii=False)
        cls._old = os.environ.get("USER_ACCESS_JSON_PATH")
        os.environ["USER_ACCESS_JSON_PATH"] = path
        ac.invalidate_user_access_cache()

    @classmethod
    def tearDownClass(cls):
        if cls._old is None:
            os.environ.pop("USER_ACCESS_JSON_PATH", None)
        else:
            os.environ["USER_ACCESS_JSON_PATH"] = cls._old
        ac.invalidate_user_access_cache()
        cls._tmp.cleanup()


class TestManagerViews(_RosterBase):
    def test_division_wide_modes(self):
        row = {
            "login_kind": "manager_acc",
            "acc_division": "Div.S",
            "acc_scope": "all",
            "acc_region": "",
        }
        self.assertTrue(is_division_wide_manager(row))
        opts = build_manager_view_options(
            "SL360",
            ["SL360", "SL350", "SL351"],
        )
        self.assertEqual(opts["scope_kind"], "division")
        self.assertIn("all", opts["modes"])
        self.assertIn("individual", opts["modes"])

    def test_regional_modes(self):
        row = {
            "login_kind": "manager_acc",
            "acc_division": "Div.B",
            "acc_scope": "all",
            "acc_region": "กลาง",
        }
        self.assertFalse(is_division_wide_manager(row))
        opts = build_manager_view_options("SL345", ["SL345", "SL401"])
        self.assertEqual(opts["scope_kind"], "region")
        self.assertEqual(opts["modes"], ["individual", "region"])
        self.assertNotIn("all", opts["modes"])

    def test_resolve_all(self):
        team = ["SL360", "SL350", "SL351"]
        codes = resolve_aggregate_supervisor_codes("SL360", team, "all")
        self.assertIn("SL350", codes)
        self.assertNotIn("SL360", codes)

    def test_manager_code_excluded_from_supervisor_team(self):
        from backend.services.manager_views import team_supervisor_codes
        from backend.services.sl_link_store import manager_codes_to_exclude_from_team

        team = ["SL508", "SL532"]
        excl = manager_codes_to_exclude_from_team(
            "SL524",
            {"SL508", "SL524"},
            [{"old_sl": "SL508", "canonical_sl": "SL508", "new_sls": ["SL524"], "alias_sls": ["SL508", "SL524"]}],
        )
        supers = team_supervisor_codes(team, "SL524", excl)
        self.assertEqual(supers, ["SL532"])
        self.assertNotIn("SL508", supers)


if __name__ == "__main__":
    unittest.main()
