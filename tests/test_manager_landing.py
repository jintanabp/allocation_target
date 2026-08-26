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

import shutil  # noqa: E402

from backend.services.manager_views import (  # noqa: E402
    build_manager_view_options,
    drop_manager_code_without_team,
    has_team_data_in_period,
)
from backend.services.sl_link_store import supervisor_team_for_manager  # noqa: E402

logging.disable(logging.CRITICAL)

NL = chr(10)          # ขึ้นบรรทัดจริง — codes_with_own_salesmen ต้องอ่านเจอแถวที่สอง


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


class TestManagerWithoutOwnTargetsIsNotATeam(unittest.TestCase):
    """
    ผู้จัดการที่งวดนี้ไม่มีเป้าของตัวเอง ต้องไม่ถูกนับเป็นทีมหนึ่งในขอบเขตรวมภาค

    ของจริง (SL372 งวด 09/2026): มีพนักงาน 3 คน แต่ทั้งสามไม่มีแถวเป้าสักแถว
    ทีมนั้นจึงเปิดไม่ได้ พอติดอยู่ในขอบเขตก็กลายเป็นทีมที่ถูกข้ามพร้อมคำเตือนทุกครั้ง

    ตัดสินจาก "แถวเป้า" ไม่ใช่ "มีพนักงานไหม" — ทีมที่มีคนแต่ไม่มีเป้าเลย
    เอาไปรวมก็ไม่มีอะไรให้รวม และกระจายไม่ได้
    """

    MGR, MONTH, YEAR = "SL372", 9, 2026
    TEAM = ["SL351", "SL372", "SL396"]

    def setUp(self):
        self._cwd = os.getcwd()
        self._tmpdir = tempfile.mkdtemp(prefix="mgr_noteam_")
        os.chdir(self._tmpdir)
        os.makedirs("data", exist_ok=True)

    def tearDown(self):
        os.chdir(self._cwd)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _grain(self, sup: str, rows: int) -> None:
        with open(f"data/tga_lines_{sup}_{self.YEAR}_{self.MONTH:02d}.csv",
                  "w", encoding="utf-8") as fh:
            fh.write("emp_id,sku,qty,salestype,divisioncode,areacode,provincecode,warehouse_code\n")
            for i in range(rows):
                fh.write(f"E{i},A,5,S,B,10,P1,\n")

    def _employees_only(self, sup: str) -> None:
        """มีพนักงาน แต่ไม่มีแถวเป้าเลย — เคสของ SL372"""
        with open(f"data/emp_cache_{sup}_{self.YEAR}_{self.MONTH:02d}.csv",
                  "w", encoding="utf-8") as fh:
            fh.write("emp_id,emp_name,super_code\n")
            fh.write(f"E1,ชื่อ,{sup}\n")
        self._grain(sup, 0)

    def test_a_team_with_targets_counts(self):
        self._grain("SL396", 5)
        self.assertTrue(has_team_data_in_period("SL396", self.MONTH, self.YEAR))

    def test_employees_without_targets_do_not_count(self):
        self._employees_only(self.MGR)
        self.assertFalse(
            has_team_data_in_period(self.MGR, self.MONTH, self.YEAR),
            "มีคนแต่ไม่มีเป้า = ไม่มีอะไรให้รวม",
        )

    def test_no_file_at_all_does_not_count(self):
        self.assertFalse(has_team_data_in_period("SL999", self.MONTH, self.YEAR))

    def test_the_manager_code_is_dropped_from_the_scope(self):
        self._employees_only(self.MGR)
        self._grain("SL351", 3)
        self._grain("SL396", 3)
        self.assertEqual(
            drop_manager_code_without_team(self.TEAM, self.MGR, self.MONTH, self.YEAR),
            ["SL351", "SL396"],
        )

    def test_a_manager_with_their_own_targets_stays(self):
        """ผู้จัดการที่มีพนักงานสังกัดตรงจริง ยังนับเป็นทีมหนึ่งเหมือนซุปคนหนึ่ง"""
        self._grain(self.MGR, 4)
        self._grain("SL351", 3)
        self._grain("SL396", 3)
        self.assertEqual(
            sorted(drop_manager_code_without_team(self.TEAM, self.MGR, self.MONTH, self.YEAR)),
            ["SL351", "SL372", "SL396"],
        )

    def test_real_supervisors_without_data_are_never_dropped(self):
        """
        ทีมซุปจริงที่ยังไม่มีข้อมูลต้องยังโผล่ แล้วถูกรายงานว่าโหลดไม่ได้
        ไม่งั้นเป้าของทีมนั้นหายจากยอดรวมโดยไม่มีใครรู้
        """
        self._grain(self.MGR, 4)
        self.assertIn(
            "SL351",
            drop_manager_code_without_team(self.TEAM, self.MGR, self.MONTH, self.YEAR),
        )


class TestAManagerWithOwnStaffIsATeamToOtherManagers(unittest.TestCase):
    """
    ผู้จัดการที่มีพนักงานขายสังกัดรหัสตัวเอง = ทีมจริง ในสายตาผู้จัดการคนอื่นด้วย

    ของจริง: S516 ถูกย้ายจาก SL372 ไปให้ SL359 (หน่วยเครดิต ภาคเหนือ ซึ่ง SL372
    ดูแลอยู่) เกลี่ยเป้าให้ · แต่ SL372 เปิดรวมภาคเหนือแล้วไม่เห็นเขาเลย เพราะ
    ตัวคัดทีมตัดรหัสผู้จัดการ "ทุกตัว" ออกจากทีม — ทีมของ SL359 ทั้งทีมหายไปด้วย
    ไม่ใช่แค่คนที่ย้ายมา และยอดรวมภาคก็ขาดไปทั้งทีมโดยไม่มีอะไรฟ้อง

    กติกานี้เคยแก้ไปครึ่งเดียว: team_supervisor_codes มี keep_own_code ให้ผู้จัดการ
    นับทีมของตัวเองได้ (docstring อ้าง SL359 ตรง ๆ) แต่ฝั่งที่ประกอบ by_manager
    ยังใช้กติกาเดิม — สองฝั่งตัดสินคนละแบบบนข้อมูลชุดเดียวกัน
    """

    TEAM = ["SL351", "SL359", "SL372", "SL396"]
    PICKS = {"SL359", "SL372"}          # ทั้งคู่เป็นรหัสผู้จัดการ

    def test_a_manager_code_with_staff_stays_in_the_team(self):
        out = supervisor_team_for_manager(
            self.TEAM, "SL372", self.PICKS, keep_codes={"SL359"}
        )
        self.assertIn("SL359", out)

    def test_a_manager_code_without_staff_is_still_dropped(self):
        out = supervisor_team_for_manager(self.TEAM, "SL372", self.PICKS, keep_codes=set())
        self.assertNotIn("SL359", out)

    def test_the_owner_code_is_always_dropped_from_their_own_team(self):
        """รหัสตัวเองยังตัดเหมือนเดิม — ตรงนั้นมี keep_own_code คุมอยู่อีกชั้น"""
        out = supervisor_team_for_manager(
            self.TEAM, "SL372", self.PICKS, keep_codes={"SL359", "SL372"}
        )
        self.assertNotIn("SL372", out)

    def test_real_supervisors_are_untouched(self):
        out = supervisor_team_for_manager(
            self.TEAM, "SL372", self.PICKS, keep_codes={"SL359"}
        )
        self.assertEqual(sorted(out), ["SL351", "SL359", "SL396"])

    def test_the_screen_uses_the_same_rule(self):
        """ไม่งั้นทีมนั้นอยู่ในยอดรวมแต่ไม่มีในรายการให้เลือกเปิด — คนละความจริงสองจอ"""
        self.assertIn("own_team_has_staff", APP)
        i = APP.index("function _supervisorOnlyTeam(")
        block = APP[i: i + 900]
        self.assertIn("hasOwnStaff", block)


class TestManagersInTheSameRegionSeeEachOther(unittest.TestCase):
    """
    ผู้จัดการภาคเดียวกัน หน่วยเดียวกันหรือ "all" ต้องเห็นกันและกัน
    (เมื่อมีพนักงานขายสังกัดรหัสตัวเอง)

    เดิมเทียบหน่วยตรง ๆ ทีมที่กำกับ "all" จึงไม่เข้าเงื่อนไขของใครเลย
    การมองเห็นกลายเป็นทางเดียว: SL372 (all) เห็น SL359 (เครดิต) แต่ SL359
    ไม่เห็น SL372 กลับ ทั้งที่อยู่ภาคเดียวกันและมีพนักงานสังกัดตรงทั้งคู่
    """

    ALL_MGR, CREDIT_MGR, VAN_MGR = "SL372", "SL359", "SL351"
    CREDIT_SUP, BLANK_SUP = "SL396", "SL533"

    def setUp(self):
        self._cwd = os.getcwd()
        self._tmpdir = tempfile.mkdtemp(prefix="unit_vis_")
        os.chdir(self._tmpdir)
        os.makedirs("data", exist_ok=True)
        # ผู้จัดการนับเป็นทีมได้ต่อเมื่อมีพนักงานสังกัดรหัสตัวเอง
        for code in (self.ALL_MGR, self.CREDIT_MGR, self.VAN_MGR):
            with open(f"data/emp_cache_{code}_2026_09.csv", "w", encoding="utf-8") as fh:
                fh.write("emp_id,emp_name,super_code\n")
                fh.write(f"E1,ชื่อ,{code}\n")

    def tearDown(self):
        os.chdir(self._cwd)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _rows(self):
        def mgr(code, unit):
            return {"email": f"{code.lower()}@x.co.th", "userpl": code,
                    "login_kind": "manager_acc", "manager_level": "regional",
                    "acc_division": "Div.S", "acc_region": "เหนือ", "acc_unit": unit}

        def sup(code, unit):
            return {"email": f"{code.lower()}@x.co.th", "userpl": code,
                    "login_kind": "supervisor_acc", "acc_division": "Div.S",
                    "acc_region": "เหนือ", "acc_unit": unit, "acc_scope": "credit"}

        return [
            mgr(self.ALL_MGR, "all"),
            mgr(self.CREDIT_MGR, "credit"),
            mgr(self.VAN_MGR, "van"),
            sup(self.CREDIT_SUP, "credit"),
            sup(self.BLANK_SUP, ""),
        ]

    def _visible(self):
        from backend.services.access_hierarchy import enrich_rows_with_visibility

        return {
            str(r["userpl"]): set(r.get("visible_supervisor_codes") or [])
            for r in enrich_rows_with_visibility(self._rows())
        }

    def test_a_credit_manager_sees_the_all_manager(self):
        """ตัวที่ผู้ใช้รายงาน — ทีม all คร่อมทั้งสองหน่วย ต้องเห็นจากฝั่งเครดิต"""
        self.assertIn(self.ALL_MGR, self._visible()[self.CREDIT_MGR])

    def test_a_van_manager_sees_the_all_manager_too(self):
        self.assertIn(self.ALL_MGR, self._visible()[self.VAN_MGR])

    def test_the_all_manager_still_sees_both_units(self):
        vis = self._visible()[self.ALL_MGR]
        self.assertIn(self.CREDIT_MGR, vis)
        self.assertIn(self.VAN_MGR, vis)

    def test_credit_and_van_still_do_not_see_each_other(self):
        vis = self._visible()
        self.assertNotIn(self.VAN_MGR, vis[self.CREDIT_MGR])
        self.assertNotIn(self.CREDIT_MGR, vis[self.VAN_MGR])

    def test_a_team_with_no_unit_set_counts_as_both(self):
        """
        ครึ่งหนึ่งของตารางสิทธิ์ยังไม่ได้กรอกหน่วย · ถ้าถือว่า "ไม่รู้ = ไม่เห็น"
        ทีมกลุ่มนั้นหายจากยอดรวมภาคทั้งที่มีอยู่จริง — และตัวกรองขอบเขต
        (filter_codes_by_unit) ก็นับพวกเขาอยู่แล้ว สองฝั่งต้องใช้กติกาชุดเดียวกัน
        """
        vis = self._visible()
        self.assertIn(self.BLANK_SUP, vis[self.CREDIT_MGR])
        self.assertIn(self.BLANK_SUP, vis[self.VAN_MGR])

    def test_a_supervisor_sees_the_all_team_among_their_peers(self):
        """ซุปใช้กติกาเดียวกัน ไม่งั้นสองฝั่งเห็นคนละยอดบนหน้าจอเดียวกัน"""
        self.assertIn(self.ALL_MGR, self._visible()[self.CREDIT_SUP])


class TestEmptyManagerCodesLeaveTheScope(unittest.TestCase):
    """
    พอเปิดให้เห็นกันและกันแล้ว รหัสของอีกฝ่ายเข้ามาอยู่ในขอบเขตด้วย — ถ้างวดนั้น
    เขาไม่มีเป้า ทีมนั้นจะโหลดไม่ได้ กลายเป็น "ทีมที่ถูกข้าม" พร้อมคำเตือนทุกครั้ง
    """

    MONTH, YEAR = 9, 2026

    def setUp(self):
        self._cwd = os.getcwd()
        self._tmpdir = tempfile.mkdtemp(prefix="empty_mgr_")
        os.chdir(self._tmpdir)
        os.makedirs("data", exist_ok=True)
        self._acc = os.path.join(self._tmpdir, "user_access.json")
        with open(self._acc, "w", encoding="utf-8") as fh:
            json.dump([
                {"email": "a@x.co.th", "userpl": "SL372", "login_kind": "manager_acc"},
                {"email": "b@x.co.th", "userpl": "SL359", "login_kind": "manager_acc"},
                {"email": "c@x.co.th", "userpl": "SL396", "login_kind": "supervisor_acc"},
            ], fh, ensure_ascii=False)
        self._old = os.environ.get("USER_ACCESS_JSON_PATH")
        os.environ["USER_ACCESS_JSON_PATH"] = self._acc

    def tearDown(self):
        if self._old is None:
            os.environ.pop("USER_ACCESS_JSON_PATH", None)
        else:
            os.environ["USER_ACCESS_JSON_PATH"] = self._old
        os.chdir(self._cwd)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _grain(self, sup: str, rows: int) -> None:
        with open(f"data/tga_lines_{sup}_{self.YEAR}_{self.MONTH:02d}.csv",
                  "w", encoding="utf-8") as fh:
            fh.write("emp_id,sku,qty,salestype,divisioncode,areacode,provincecode,warehouse_code\n")
            for i in range(rows):
                fh.write(f"E{i},A,5,S,B,10,P1,\n")

    def test_another_manager_without_targets_is_dropped(self):
        self._grain("SL359", 3)
        self._grain("SL396", 3)          # SL372 ไม่มีเป้าเลย
        self.assertEqual(
            drop_manager_code_without_team(
                ["SL359", "SL372", "SL396"], "SL359", self.MONTH, self.YEAR
            ),
            ["SL359", "SL396"],
        )

    def test_another_manager_with_targets_stays(self):
        for c in ("SL359", "SL372", "SL396"):
            self._grain(c, 3)
        self.assertEqual(
            drop_manager_code_without_team(
                ["SL359", "SL372", "SL396"], "SL359", self.MONTH, self.YEAR
            ),
            ["SL359", "SL372", "SL396"],
        )

    def test_a_supervisor_without_targets_is_never_dropped(self):
        self._grain("SL359", 3)
        self.assertIn(
            "SL396",
            drop_manager_code_without_team(
                ["SL359", "SL396"], "SL359", self.MONTH, self.YEAR
            ),
        )

    def test_dropping_everything_falls_back_to_the_original(self):
        """ตัดจนไม่เหลือ = ตัดสินผิดแน่ ๆ ปล่อยให้ด่านถัดไปว่ากันต่อ"""
        self.assertEqual(
            drop_manager_code_without_team(
                ["SL359", "SL372"], "SL359", self.MONTH, self.YEAR
            ),
            ["SL359", "SL372"],
        )


class TestDivisionManagersSeeManagerOwnedTeams(unittest.TestCase):
    """
    ผู้จัดการระดับ division เห็นทุกทีมใน division ของตัวเอง — รวมทีมของผู้จัดการภาค
    ที่มีพนักงานขายสังกัดรหัสตัวเอง

    กติกา "ผู้จัดการที่มีลูกน้องตรง = ทีมจริงทีมหนึ่ง" ใส่ให้เส้นทางผู้จัดการภาค
    ไปแล้ว แต่เส้นทางระดับ division ยังนับเฉพาะ supervisor_acc — SL301 จึงไม่เห็น
    ทั้ง SL372 และ SL359 ทั้งที่เป็นทีมในภาคเหนือของ Div.S แท้ ๆ
    และผู้จัดการภาคเห็นกันเองอยู่แล้ว

    ไม่ต้องกรอกหน่วยให้ผู้จัดการระดับ division — หน่วยว่างแปลว่าไม่กรอง
    แล้วค่อยกดเลือกดูแยกภาค/แยกหน่วยบนหน้าจอ
    """

    def setUp(self):
        self._cwd = os.getcwd()
        self._tmpdir = tempfile.mkdtemp(prefix="div_mgr_")
        os.chdir(self._tmpdir)
        os.makedirs("data", exist_ok=True)
        with open("data/emp_cache_SL372_2026_09.csv", "w", encoding="utf-8") as fh:
            fh.write("emp_id,emp_name,super_code%s" % NL)
            fh.write("E1,x,SL372%s" % NL)

    def tearDown(self):
        os.chdir(self._cwd)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    ROWS = [
        {"email": "d@x.co.th", "userpl": "SL301", "login_kind": "manager_acc",
         "manager_level": "division", "acc_division": "Div.S"},
        {"email": "m@x.co.th", "userpl": "SL372", "login_kind": "manager_acc",
         "manager_level": "regional", "acc_division": "Div.S", "acc_region": "เหนือ"},
        {"email": "s@x.co.th", "userpl": "SL396", "login_kind": "supervisor_acc",
         "acc_division": "Div.S", "acc_region": "เหนือ", "acc_unit": "credit"},
        {"email": "n@x.co.th", "userpl": "SL900", "login_kind": "manager_acc",
         "manager_level": "regional", "acc_division": "Div.S", "acc_region": "ใต้"},
    ]

    def _visible(self):
        from backend.services.access_hierarchy import enrich_rows_with_visibility

        return {
            str(r["userpl"]): set(r.get("visible_supervisor_codes") or [])
            for r in enrich_rows_with_visibility(self.ROWS)
        }

    def test_the_division_manager_sees_a_regional_manager_with_staff(self):
        self.assertIn("SL372", self._visible()["SL301"])

    def test_they_still_see_the_ordinary_supervisors(self):
        self.assertIn("SL396", self._visible()["SL301"])

    def test_a_manager_code_without_staff_is_not_a_team(self):
        """ทีมว่าง ไม่มีอะไรให้เกลี่ย — ไม่ต้องโผล่"""
        self.assertNotIn("SL900", self._visible()["SL301"])

    def test_no_unit_on_the_division_manager_means_no_filter(self):
        """ผู้จัดการระดับ division ไม่ต้องกรอกหน่วย — เห็นทุกหน่วยในทุกภาค"""
        from backend.services.access_hierarchy import _unit_matches

        self.assertTrue(_unit_matches("", "credit"))
        self.assertTrue(_unit_matches("", "van"))


if __name__ == "__main__":
    unittest.main()
