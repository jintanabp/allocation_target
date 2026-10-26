"""
เพิ่มรหัสที่ไม่มีในทะเบียนพนักงานเอง — เคส S005 หน่วยส่งออกของ SL532 (26 ต.ค. 2026)

S005 ไม่มีตัวเซลใน Dim_Salesman แต่เป็น "หน่วย" ที่มีเป้าใน Target Sun · รายชื่อทีมดึงจาก
Dim_Salesman ก่อน แล้วค่อยไปหยิบเป้าตามรายชื่อ — รหัสที่ไม่อยู่ในรายชื่อจึงไม่มีทางได้เป้า
แอดมินต้องใส่รหัสเข้าทีมเองได้ (ใช้กลไกเดียวกับการย้าย แค่ไม่มีทีมต้นทาง)

ข้อที่ห้ามพลาด:
  · รหัสต้องโผล่ทีมเดียว (กติกาเดียวกับการย้าย — โผล่สองทีม = เป้านับสองรอบตอนรวมภาค)
  · ไม่มีประวัติขาย/ไม่มีเป้างวดนี้ ก็ต้องเห็นในรายชื่อ ไม่ใช่ถูกซ่อนเงียบ ๆ
  · ส่ง Target Sun ด้วย dim ของแถวเป้ารหัสนั้นเอง ไม่ใช่ของทีมปลายทาง
  · รหัสที่มีทีมจริงอยู่แล้ว ต้องไปทางย้ายปกติ ไม่ใช่เพิ่มเอง
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import pandas as pd
from fastapi import HTTPException

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.core.employee_filter import filter_employees_for_display  # noqa: E402
from backend.routers import admin as admin_router  # noqa: E402
from backend.services import company_roster  # noqa: E402
from backend.services import emp_assignment_store as store  # noqa: E402
from backend.services import lakehouse as lh  # noqa: E402

TEAM = "SL532"
UNIT = "S005"


class _TempDir(unittest.TestCase):
    """cwd + ไฟล์การย้ายชั่วคราว — _employee_directory อ่าน data/ ของ cwd"""

    def setUp(self):
        self._cwd = os.getcwd()
        self._tmpdir = tempfile.mkdtemp(prefix="emp_manual_")
        os.chdir(self._tmpdir)
        os.makedirs("data", exist_ok=True)
        self._old = os.environ.get("EMP_ASSIGNMENTS_JSON_PATH")
        os.environ["EMP_ASSIGNMENTS_JSON_PATH"] = os.path.join(self._tmpdir, "emp_assignments.json")

    def tearDown(self):
        os.chdir(self._cwd)
        if self._old is None:
            os.environ.pop("EMP_ASSIGNMENTS_JSON_PATH", None)
        else:
            os.environ["EMP_ASSIGNMENTS_JSON_PATH"] = self._old
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _team(self, sup, *emp_ids):
        return [{"emp_id": e, "emp_name": f"ชื่อ {e}", "super_code": sup} for e in emp_ids]


class TestTeamList(_TempDir):
    def setUp(self):
        super().setUp()
        store.set_assignment(UNIT, TEAM, emp_name="หน่วยส่งออก")

    def test_the_destination_team_gains_the_unit(self):
        rows, moves = store.apply_to_employee_list(TEAM, self._team(TEAM, "S041", "S044"))
        unit = next(r for r in rows if r["emp_id"] == UNIT)
        self.assertEqual(unit["emp_name"], "หน่วยส่งออก")
        self.assertTrue(unit["manual_added"])
        self.assertEqual(unit["reassigned_from"], "", "ไม่มีทีมต้นทาง — ห้ามขึ้นป้าย 'ย้ายมาจาก'")
        self.assertEqual(moves["added"], 1)

    def test_teammates_are_not_marked(self):
        rows, _ = store.apply_to_employee_list(TEAM, self._team(TEAM, "S041"))
        mate = next(r for r in rows if r["emp_id"] == "S041")
        self.assertNotIn("manual_added", mate)

    def test_it_never_shows_up_in_another_team(self):
        """วันหนึ่ง Dim_Salesman ใส่ S005 ไว้ใต้ทีมอื่น — ต้องยังอยู่ทีมเดียว"""
        other, moves = store.apply_to_employee_list("SL999", self._team("SL999", UNIT, "X1"))
        self.assertEqual([r["emp_id"] for r in other], ["X1"])
        self.assertEqual(moves["removed"], 1)

    def test_if_the_roster_later_lists_it_under_the_same_team_there_is_no_duplicate(self):
        rows, moves = store.apply_to_employee_list(TEAM, self._team(TEAM, UNIT, "S041"))
        self.assertEqual([r["emp_id"] for r in rows].count(UNIT), 1)
        self.assertEqual(moves["added"], 0)

    def test_is_manual(self):
        self.assertTrue(store.is_manual(store.assignment_for_emp(UNIT)))
        store.set_assignment("S516", "SL359", from_sup="SL372")
        self.assertFalse(store.is_manual(store.assignment_for_emp("S516")))
        self.assertFalse(store.is_manual(None))


class TestNotHiddenWithoutHistory(unittest.TestCase):
    """หน่วยส่งออกไม่มีประวัติขาย — ตัวกรองปกติจะซ่อนคนที่ไม่มีเป้าและไม่มียอดปีก่อน"""

    def _df(self, manual_target):
        return pd.DataFrame([
            {"emp_id": "S041", "target_sun": 100.0, "has_tga_rows": True},
            {"emp_id": "S099", "target_sun": 0.0, "has_tga_rows": False},
            {"emp_id": UNIT, "target_sun": manual_target, "has_tga_rows": manual_target > 0,
             "manual_added": True},
        ])

    def test_kept_even_without_a_target(self):
        out, hidden, _ = filter_employees_for_display(self._df(0.0), {})
        self.assertIn(UNIT, set(out["emp_id"]))
        self.assertNotIn("S099", set(out["emp_id"]), "คนปกติที่ไม่มีเป้ายังถูกซ่อนเหมือนเดิม")
        self.assertEqual(hidden, 1)

    def test_with_a_target_it_is_shown_as_usual(self):
        out, hidden, _ = filter_employees_for_display(self._df(500.0), {})
        self.assertIn(UNIT, set(out["emp_id"]))

    def test_nan_flag_on_other_rows_does_not_unhide_them(self):
        df = self._df(0.0)
        self.assertTrue(pd.isna(df.loc[1, "manual_added"]))
        out, _, _ = filter_employees_for_display(df, {})
        self.assertNotIn("S099", set(out["emp_id"]))

    def test_the_flag_survives_the_warehouse_expansion(self):
        from backend.services.wh_split import expand_employee_rows

        rows = [{"emp_id": UNIT, "super_code": TEAM, "manual_added": True}]
        out = expand_employee_rows(rows, None, {})
        self.assertTrue(next(r for r in out if r["emp_id"] == UNIT).get("manual_added"))


class TestSendUsesTheUnitsOwnTargetRows(_TempDir):
    """เป้าของ S005 มาจาก Target Sun ตามรหัส — ส่งกลับต้องลงแถวเดิมของมัน"""

    MONTH, YEAR = 11, 2026

    def setUp(self):
        super().setUp()
        store.set_assignment(UNIT, TEAM, emp_name="หน่วยส่งออก")

        def g(emp, st, area, prov, wh=""):
            return {"emp_id": emp, "sku": "A", "qty": 10, "salestype": st,
                    "divisioncode": "S", "areacode": area, "provincecode": prov,
                    "warehouse_code": wh}

        pd.DataFrame([g("S041", "C", "10", "P1"), g(UNIT, "X", "99", "P9", "W9")]).to_csv(
            f"data/tga_lines_{TEAM}_{self.YEAR}_{self.MONTH:02d}.csv", index=False)
        pd.DataFrame([{"sku": "A", "supervisor_target_boxes": 20, "price_per_box": 1.0}]).to_csv(
            f"data/target_boxes_{TEAM}_{self.YEAR}_{self.MONTH:02d}.csv", index=False)

    def test_the_unit_is_sent_with_its_own_territory(self):
        from backend.schemas import LakehouseUploadRequest

        req = LakehouseUploadRequest(
            sup_id=TEAM, target_month=self.MONTH, target_year=self.YEAR, upload_user_code="T",
            allocations=[{"emp_id": "S041", "sku": "A", "allocated_boxes": 12},
                         {"emp_id": UNIT, "sku": "A", "allocated_boxes": 8}],
            allow_new_targetsun_rows=True,
        )
        out, dropped, _p, shortfall = lh._build_tga_upload_dataframe(req, drop_incomplete_rows=True)
        self.assertEqual(dropped, 0)
        self.assertEqual(shortfall, [])
        row = out[out["SALESMANCODE"] == UNIT]
        self.assertEqual(len(row), 1)
        self.assertEqual(int(row["QUANTITYCASE"].iloc[0]), 8)
        self.assertEqual(row["SALESTYPE"].iloc[0], "X")
        self.assertEqual(str(row["AREACODE"].iloc[0]), "99")
        self.assertEqual(row["PROVINCECODE"].iloc[0], "P9")
        self.assertEqual(int(out["QUANTITYCASE"].sum()), 20)


class TestAdminDirectory(_TempDir):
    def _dir(self, roster=()):
        with mock.patch.object(
            company_roster, "get_company_roster",
            return_value={"available": True, "rows": list(roster), "row_count": len(roster)},
        ):
            return {r["emp_id"]: r for r in admin_router._employee_directory()}

    def test_a_manual_unit_shows_up_marked(self):
        store.set_assignment(UNIT, TEAM, emp_name="หน่วยส่งออก", note="ส่งออก")
        r = self._dir()[UNIT]
        self.assertTrue(r["manual"])
        self.assertEqual(r["home_sup"], "")
        self.assertEqual(r["to_sup"], TEAM)
        self.assertEqual(r["seen_period"], "เพิ่มเอง")
        self.assertEqual(r["emp_name"], "หน่วยส่งออก")

    def test_still_marked_after_the_destination_grain_lists_it(self):
        """หลังทีมปลายทางถูกเปิด แคชแถวเป้ามี S005 — ห้ามกลายเป็น 'ทีมจริง = SL532'"""
        store.set_assignment(UNIT, TEAM, emp_name="หน่วยส่งออก")
        pd.DataFrame([{"emp_id": UNIT, "sku": "A", "qty": 1}]).to_csv(
            f"data/tga_lines_{TEAM}_2026_11.csv", index=False)
        r = self._dir()[UNIT]
        self.assertTrue(r["manual"])
        self.assertEqual(r["home_sup"], "")

    def test_once_the_roster_has_a_real_team_it_is_an_ordinary_move(self):
        store.set_assignment(UNIT, TEAM, emp_name="หน่วยส่งออก")
        r = self._dir([{"emp_id": UNIT, "emp_name": "", "super_code": "SL777"}])[UNIT]
        self.assertFalse(r["manual"])
        self.assertEqual(r["home_sup"], "SL777")
        self.assertEqual(r["to_sup"], TEAM)


class TestAdminEndpoint(_TempDir):
    ADMIN = {"email": "admin@example.com", "role": "admin"}

    def _post(self, roster=(), **body):
        audits = []
        with mock.patch.object(admin_router, "ensure_not_demo_for_global_write"), \
             mock.patch.object(admin_router, "_sup_attrs",
                               return_value={TEAM: {"login_kind": "supervisor_acc"},
                                             "SL359": {"login_kind": "supervisor_acc"}}), \
             mock.patch.object(admin_router, "invalidate_employee_payload_cache", return_value=0), \
             mock.patch.object(admin_router, "_audit_admin",
                               side_effect=lambda *a, **k: audits.append((a, k))), \
             mock.patch.object(company_roster, "get_company_roster",
                               return_value={"available": True, "rows": list(roster)}):
            res = admin_router.admin_set_emp_assignment(
                admin_router.EmpAssignmentBody(**body), admin=self.ADMIN
            )
        return res, audits

    def test_adding_a_unit(self):
        res, audits = self._post(emp_id="s005", emp_name="หน่วยส่งออก", to_sup=TEAM,
                                 note="ส่งออก", manual=True)
        self.assertTrue(res["manual"])
        row = store.assignment_for_emp(UNIT)
        self.assertEqual((row["to_sup"], row["from_sup"], row["emp_name"]),
                         (TEAM, "", "หน่วยส่งออก"))
        self.assertIn("ไม่มีในทะเบียน", audits[0][0][2])
        self.assertEqual(audits[0][1]["sup_id"], TEAM)

    def test_a_code_with_a_real_team_must_use_the_move_table(self):
        with self.assertRaises(HTTPException) as cm:
            self._post([{"emp_id": UNIT, "emp_name": "", "super_code": "SL777"}],
                       emp_id=UNIT, emp_name="x", to_sup=TEAM, manual=True)
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("SL777", cm.exception.detail)
        self.assertIsNone(store.assignment_for_emp(UNIT))

    def test_the_check_uses_the_roster_not_a_full_cache_scan(self):
        """
        ไล่ CSV ทุกทีม ~10 วินาทีบนเซิร์ฟเวอร์ — ผู้ใช้กด「เพิ่ม」แล้วเห็นว่า "ไม่มีอะไรเกิด"
        (26 ต.ค. 2026) · มีทะเบียนแล้วต้องไม่เรียก _employee_directory
        """
        with mock.patch.object(admin_router, "_employee_directory",
                               side_effect=AssertionError("ห้ามไล่แคชไฟล์")):
            res, _ = self._post([{"emp_id": "S041", "emp_name": "", "super_code": TEAM}],
                                emp_id=UNIT, emp_name="หน่วยส่งออก", to_sup=TEAM, manual=True)
        self.assertTrue(res["manual"])

    def test_without_a_roster_it_falls_back_to_the_file_caches(self):
        pd.DataFrame([{"emp_id": UNIT, "sku": "A", "qty": 1}]).to_csv(
            "data/tga_lines_SL777_2026_11.csv", index=False)
        with mock.patch.object(company_roster, "get_company_roster",
                               return_value={"available": False, "rows": []}):
            self.assertIsNone(admin_router._registry_home_team(UNIT))
        audits = []
        with self.assertRaises(HTTPException) as cm, \
             mock.patch.object(admin_router, "ensure_not_demo_for_global_write"), \
             mock.patch.object(admin_router, "_sup_attrs",
                               return_value={TEAM: {"login_kind": "supervisor_acc"}}), \
             mock.patch.object(admin_router, "_audit_admin", side_effect=audits.append), \
             mock.patch.object(company_roster, "get_company_roster",
                               return_value={"available": False, "rows": []}):
            admin_router.admin_set_emp_assignment(
                admin_router.EmpAssignmentBody(emp_id=UNIT, emp_name="x", to_sup=TEAM, manual=True),
                admin=self.ADMIN,
            )
        self.assertIn("SL777", cm.exception.detail)

    def test_an_already_moved_employee_cannot_be_re_added_as_manual(self):
        store.set_assignment("S516", "SL359", from_sup="SL372")
        with self.assertRaises(HTTPException):
            self._post(emp_id="S516", emp_name="x", to_sup=TEAM, manual=True)
        self.assertEqual(store.assignment_for_emp("S516")["from_sup"], "SL372")

    def test_bad_input_is_refused(self):
        for body in (
            {"emp_id": "V001", "emp_name": "x"},          # รหัส V ถูกตัดทุกจอ
            {"emp_id": UNIT, "emp_name": "  "},           # ไม่มีชื่อ
            {"emp_id": "S 005", "emp_name": "x"},         # รหัสผิดรูป
        ):
            with self.subTest(body=body):
                with self.assertRaises(HTTPException) as cm:
                    self._post(to_sup=TEAM, manual=True, **body)
                self.assertEqual(cm.exception.status_code, 400)
        self.assertEqual(store.read_rows(), [])

    def test_unknown_destination_is_refused(self):
        with self.assertRaises(HTTPException):
            self._post(emp_id=UNIT, emp_name="x", to_sup="SLNOPE", manual=True)

    def test_changing_the_team_of_a_manual_unit(self):
        self._post(emp_id=UNIT, emp_name="หน่วยส่งออก", to_sup=TEAM, manual=True)
        self._post(emp_id=UNIT, emp_name="หน่วยส่งออก", to_sup="SL359", manual=True)
        row = store.assignment_for_emp(UNIT)
        self.assertEqual((row["to_sup"], row["from_sup"]), ("SL359", ""))

    def test_removing_a_manual_unit(self):
        self._post(emp_id=UNIT, emp_name="หน่วยส่งออก", to_sup=TEAM, manual=True)
        res, audits = self._post(emp_id=UNIT, to_sup="", manual=True)
        self.assertIsNone(store.assignment_for_emp(UNIT))
        self.assertIn("เอารหัสที่เพิ่มเอง", audits[0][0][2])
        self.assertEqual(audits[0][1]["sup_id"], TEAM, "ต้องผูกบันทึกกับทีมที่เคยอยู่")


class TestTheScreen(unittest.TestCase):
    """หน้าเว็บรันในเทสไม่ได้ — ตรวจว่าปุ่มมีจริง (บทเรียน 'โค้ดมีแต่ไม่มีปุ่ม')"""

    def setUp(self):
        with open(os.path.join(REPO, "frontend", "app.js"), encoding="utf-8") as fh:
            self.js = fh.read()
        with open(os.path.join(REPO, "frontend", "index.html"), encoding="utf-8") as fh:
            self.html = fh.read()

    def test_the_form_and_its_handler_exist(self):
        self.assertIn('onclick="addManualEmp()"', self.html)
        self.assertIn("async function addManualEmp()", self.js)
        for el in ("empManualId", "empManualName", "empManualTo"):
            self.assertIn(f'id="{el}"', self.html)

    def test_it_posts_the_manual_flag(self):
        i = self.js.index("async function addManualEmp()")
        self.assertIn("manual: true", self.js[i:i + 2500])

    def test_the_button_shows_it_is_working(self):
        """ผู้ใช้กดแล้วเห็นว่า "ไม่มีอะไรเกิด" — ต้องเปลี่ยนข้อความ/ปิดปุ่มระหว่างรอ แล้วคืนเสมอ"""
        self.assertIn('id="empManualAddBtn"', self.html)
        i = self.js.index("async function addManualEmp()")
        block = self.js[i:self.js.index("\n}\n", i)]
        self.assertIn("กำลังเพิ่ม", block)
        self.assertIn("btn.disabled = true", block)
        self.assertIn("finally", block)

    def test_clicking_before_the_list_loads_says_so(self):
        i = self.js.index("async function addManualEmp()")
        self.assertIn("if (!_empMoveData)", self.js[i:i + 1200])

    def test_no_second_full_reload_after_adding(self):
        i = self.js.index("async function addManualEmp()")
        block = self.js[i:self.js.index("\n}\n", i)]
        self.assertNotIn("loadEmpMoves()", block)

    def test_the_team_screens_label_it(self):
        i = self.js.index("function _empMovedBadgeHtml")
        self.assertIn("manual_added", self.js[i:i + 1200])


if __name__ == "__main__":
    unittest.main()
