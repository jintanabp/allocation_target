"""
ผู้จัดการต้องเข้า Dashboard ได้ แม้ทีมของรหัสตัวเองไม่มีเป้า

เจอจริงกับ SL372: เป็นผู้จัดการที่ไม่มีพนักงานสังกัดรหัสตัวเอง ทีมนั้นจึงไม่มีเป้า
พอระบบพาไปเปิด "ทีมตัวเอง" เสมอ ก็เจอ "ไม่มีข้อมูลเป้างวดนี้" แล้วล็อกอินหยุด
ตรงนั้น เข้าหน้า Dashboard ไม่ได้เลย ทั้งที่ทีมซุปใต้สังกัดมีเป้าครบ

แก้ผิดจุดมาสองรอบ เพราะมีสามที่ที่ต้องตรงกันหมด:
  1. build_manager_view_options ต้องเสนอโหมดรวมให้
  2. ตอนล็อกอินต้องเลือกโหมดรวมเป็นค่าเริ่มต้น (และไม่มีใครตั้ง individual ทับทีหลัง)
  3. ตอนโหลดข้อมูลครั้งแรกต้องโหลด "ก้อนรวม" ไม่ใช่ทีมเดียว   ← ตัวจริงที่ทำให้ยังพัง

หน้าเว็บรันในเทสไม่ได้ ข้อ 2-3 จึงตรวจจากโครงของโค้ด
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

from backend.services.manager_views import build_manager_view_options  # noqa: E402

logging.disable(logging.CRITICAL)


def _read(rel: str) -> str:
    with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
        return fh.read()


APP = _read("frontend/app.js")

ROWS = [
    {"email": "mgr@x.co.th", "userpl": "SL372", "login_kind": "manager_acc",
     "manager_level": "regional", "acc_division": "Div.S", "acc_region": "เหนือ",
     "can_import_targetsun": False, "note": ""},
    {"email": "s1@x.co.th", "userpl": "SL396", "login_kind": "supervisor_acc",
     "acc_division": "Div.S", "acc_region": "เหนือ", "acc_unit": "credit",
     "can_import_targetsun": False, "note": ""},
    {"email": "s2@x.co.th", "userpl": "SL506", "login_kind": "supervisor_acc",
     "acc_division": "Div.S", "acc_region": "เหนือ", "acc_unit": "credit",
     "can_import_targetsun": False, "note": ""},
]


class TestOptionsOfferAnAggregate(unittest.TestCase):
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

    def test_a_manager_without_own_staff_still_gets_a_region_mode(self):
        opts = build_manager_view_options(
            "SL372", ["SL372", "SL396", "SL506"], own_salesmen_codes=set()
        )
        self.assertIn("region", opts["modes"])
        self.assertEqual(opts["scope_kind"], "region")
        self.assertEqual(opts["manager_region"], "เหนือ")

    def test_the_region_has_the_teams_under_them(self):
        opts = build_manager_view_options(
            "SL372", ["SL372", "SL396", "SL506"], own_salesmen_codes=set()
        )
        self.assertEqual(
            sorted(opts["regions"][0]["supervisor_codes"]), ["SL396", "SL506"]
        )


class TestLoginPicksTheAggregate(unittest.TestCase):
    """ข้อ 2 — เลือกโหมดรวมเป็นค่าเริ่มต้น และห้ามมีใครตั้ง individual ทับทีหลัง"""

    def test_there_is_one_place_that_decides_the_default_mode(self):
        self.assertIn("function _applyDefaultManagerViewMode", APP)

    def test_the_manager_login_path_no_longer_hardcodes_individual(self):
        """
        ของเดิมเขียน S.managerViewMode = "individual" ไว้ในเส้นทางล็อกอินของผู้จัดการ
        ทับค่าที่เพิ่งเลือกไว้ — ตอนนี้ต้องเหลือเฉพาะเส้นทางของซุปเท่านั้น
        """
        m = re.search(r'S\.loginRole = "manager";(.*?)S\.loginRole = "supervisor";', APP, re.S)
        self.assertIsNotNone(m, "ไม่พบบล็อกล็อกอินของผู้จัดการ")
        self.assertNotIn('S.managerViewMode = "individual"', m.group(1))

    def test_the_decision_runs_after_the_options_are_known(self):
        m = re.search(r'S\.loginRole = "manager";(.*?)S\.loginRole = "supervisor";', APP, re.S)
        block = m.group(1)
        self.assertLess(
            block.index("_syncManagerViewOptionsFromLogin"),
            block.rindex("_applyDefaultManagerViewMode()"),
            "ต้องเลือกโหมดหลังจากรู้ตัวเลือกแล้ว",
        )


class TestFirstLoadUsesTheAggregate(unittest.TestCase):
    """
    ข้อ 3 — ตัวจริงที่ทำให้ยังพัง

    ตั้งโหมดไว้เฉย ๆ ไม่พอ ถ้าตอนโหลดครั้งแรกยังเรียกตัวโหลด "ทีมเดียว" อยู่
    ทีมนั้นคือรหัสของผู้จัดการเอง ซึ่งไม่มีเป้า → คืน false → ล็อกอินหยุด
    """

    def test_login_loads_the_aggregate_when_the_mode_is_not_individual(self):
        self.assertIn(
            'if (S.loginRole === "manager" && S.managerViewMode !== "individual")', APP
        )
        self.assertIn(
            "ok = await loadAggregateData(S.managerViewMode, S.managerViewRegion)", APP
        )

    def test_it_falls_back_to_one_team_so_the_reason_is_visible(self):
        """ก้อนรวมโหลดไม่ได้ ต้องไม่ค้างอยู่หน้าล็อกอินโดยไม่บอกสาเหตุ"""
        i = APP.index("ok = await loadAggregateData(S.managerViewMode, S.managerViewRegion)")
        after = APP[i: i + 700]
        self.assertIn('S.managerViewMode = "individual"', after)
        self.assertIn("ok = await loadData(S.supId", after)


class TestSkippedTeamsAreVisible(unittest.TestCase):
    """
    ทีมที่โหลดไม่สำเร็จเคยหายเงียบจากยอดรวม — เป็นช่องเดียวกับบั๊ก "ยอดรวมไม่ฟ้อง"
    ที่ไล่แก้มาทั้งวัน · ผู้จัดการที่ทีมตัวเองไม่มีเป้าจะเจอเคสนี้ทุกครั้ง
    """

    def test_the_payload_field_is_read(self):
        self.assertIn("data.skipped_supervisors", APP)

    def test_it_becomes_a_warning_the_banner_renders(self):
        self.assertIn('type: "aggregate_team_skipped"', APP)
        self.assertIn('w.type === "aggregate_team_skipped"', APP)


if __name__ == "__main__":
    unittest.main()
