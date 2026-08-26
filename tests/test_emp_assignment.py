"""
ย้ายพนักงานไปให้ทีมอื่นเกลี่ยเป้า — กรณีขายชายแดน

พนักงานขายชายแดนอยู่ใต้ซุปหน่วยรถตามโครงสร้างจริงใน Dim_Salesman แต่เวลาตั้งเป้า
ต้องไปเกลี่ยร่วมกับทีมหน่วยเครดิตของอีกภาคหนึ่ง · โครงสร้างต้นทางแก้ไม่ได้และไม่ควรแก้
เพราะสังกัดจริงของเขาไม่ได้เปลี่ยน — ย้ายเฉพาะ "ใครเกลี่ยเป้าให้" เท่านั้น

กติกาที่ห้ามพลาด: พนักงานหนึ่งคนต้องโผล่ได้ทีมเดียว ถ้าโผล่สองทีมพร้อมกัน
เป้าของเขาจะถูกนับสองรอบตอนรวมภาค แล้วยอดรวมทั้งภาคเกินจริงแบบเงียบ ๆ
"""

from __future__ import annotations

import json
import logging
import os
import sys
import shutil
import tempfile
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import emp_assignment_store as store  # noqa: E402
from backend.services import lakehouse as lh  # noqa: E402

logging.disable(logging.CRITICAL)

VAN_SUP, CREDIT_SUP, THIRD_SUP = "SL372", "SL341", "SL460"
BORDER_EMP = "S516"


class _TempStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._path = os.path.join(self._tmp.name, "emp_assignments.json")
        self._old = os.environ.get("EMP_ASSIGNMENTS_JSON_PATH")
        os.environ["EMP_ASSIGNMENTS_JSON_PATH"] = self._path

    def tearDown(self):
        if self._old is None:
            os.environ.pop("EMP_ASSIGNMENTS_JSON_PATH", None)
        else:
            os.environ["EMP_ASSIGNMENTS_JSON_PATH"] = self._old
        self._tmp.cleanup()

    def _team(self, *emp_ids: str) -> list[dict]:
        return [{"emp_id": e, "emp_name": f"ชื่อ {e}", "super_code": VAN_SUP} for e in emp_ids]


class TestStoreBasics(_TempStore):
    def test_no_file_means_nobody_moved(self):
        self.assertEqual(store.read_rows(), [])
        rows, moves = store.apply_to_employee_list(VAN_SUP, self._team("S1", "S2"))
        self.assertEqual(len(rows), 2)
        self.assertEqual(moves, {"removed": 0, "added": 0, "flagged": 0})

    def test_set_and_read_back(self):
        store.set_assignment(BORDER_EMP, CREDIT_SUP, from_sup=VAN_SUP,
                             emp_name="สมชาย", note="ขายชายแดน", updated_by="admin")
        row = store.assignment_for_emp(BORDER_EMP)
        self.assertEqual(row["to_sup"], CREDIT_SUP)
        self.assertEqual(row["from_sup"], VAN_SUP)
        self.assertEqual(row["note"], "ขายชายแดน")
        self.assertTrue(row["updated_at"], "ต้องบันทึกเวลาไว้เสมอ")

    def test_setting_an_empty_destination_releases_the_move(self):
        store.set_assignment(BORDER_EMP, CREDIT_SUP, from_sup=VAN_SUP)
        store.set_assignment(BORDER_EMP, "")
        self.assertIsNone(store.assignment_for_emp(BORDER_EMP))

    def test_moving_to_the_same_team_is_not_a_move(self):
        store.set_assignment(BORDER_EMP, VAN_SUP, from_sup=VAN_SUP)
        self.assertIsNone(
            store.assignment_for_emp(BORDER_EMP),
            "ย้ายไปทีมเดิม = ไม่ได้ย้าย ห้ามเก็บแถวที่ทำให้คนอ่านเข้าใจผิด",
        )

    def test_one_employee_can_only_have_one_destination(self):
        store.set_assignment(BORDER_EMP, CREDIT_SUP, from_sup=VAN_SUP)
        store.set_assignment(BORDER_EMP, THIRD_SUP, from_sup=VAN_SUP)
        rows = store.read_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["to_sup"], THIRD_SUP)

    def test_a_hand_edited_file_with_duplicates_still_yields_one_row(self):
        with open(self._path, "w", encoding="utf-8") as fh:
            json.dump({"assignments": [
                {"emp_id": BORDER_EMP, "to_sup": CREDIT_SUP},
                {"emp_id": BORDER_EMP, "to_sup": THIRD_SUP},
            ]}, fh)
        rows = store.read_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["to_sup"], THIRD_SUP, "แถวหลังชนะ")

    def test_a_broken_file_is_treated_as_no_moves(self):
        """ไฟล์พังต้องไม่ทำให้ทุกทีมเปิดงวดไม่ได้"""
        with open(self._path, "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        self.assertEqual(store.read_rows(), [])

    def test_codes_are_normalised(self):
        store.set_assignment(" s516 ", " sl341 ", from_sup=" sl372 ")
        row = store.assignment_for_emp("S516")
        self.assertEqual((row["emp_id"], row["to_sup"], row["from_sup"]),
                         ("S516", "SL341", "SL372"))


class TestApplyToTeamList(_TempStore):
    def setUp(self):
        super().setUp()
        store.set_assignment(BORDER_EMP, CREDIT_SUP, from_sup=VAN_SUP,
                             emp_name="สมชาย ชายแดน")

    def test_source_team_no_longer_sees_them(self):
        rows, moves = store.apply_to_employee_list(
            VAN_SUP, self._team("S1", BORDER_EMP, "S2")
        )
        self.assertEqual([r["emp_id"] for r in rows], ["S1", "S2"])
        self.assertEqual(moves["removed"], 1)

    def test_destination_team_gains_them_with_a_name(self):
        rows, moves = store.apply_to_employee_list(CREDIT_SUP, self._team("S9"))
        self.assertEqual(moves["added"], 1)
        added = next(r for r in rows if r["emp_id"] == BORDER_EMP)
        self.assertEqual(added["emp_name"], "สมชาย ชายแดน")
        self.assertEqual(added["super_code"], CREDIT_SUP)

    def test_never_appears_in_two_teams_at_once(self):
        """เป้าถูกนับสองรอบตอนรวมภาคคือความเสียหายที่ด่านไหนก็ไม่จับ"""
        src, _ = store.apply_to_employee_list(VAN_SUP, self._team(BORDER_EMP, "S1"))
        dst, _ = store.apply_to_employee_list(CREDIT_SUP, self._team("S9"))
        third, _ = store.apply_to_employee_list(THIRD_SUP, self._team(BORDER_EMP, "S8"))
        appearances = sum(
            1 for team in (src, dst, third)
            if any(r["emp_id"] == BORDER_EMP for r in team)
        )
        self.assertEqual(appearances, 1)

    def test_an_unrelated_team_that_still_lists_them_loses_them_too(self):
        """
        ถ้าโครงสร้างต้นทางเปลี่ยนหัวหน้าทีหลัง คนนี้จะโผล่ใต้ทีมใหม่ที่ไม่ใช่ปลายทาง
        ต้องยังหายไปจากทีมนั้น ไม่งั้นกลับมาโดนนับซ้ำอีก
        """
        rows, moves = store.apply_to_employee_list(THIRD_SUP, self._team(BORDER_EMP, "S8"))
        self.assertEqual([r["emp_id"] for r in rows], ["S8"])
        self.assertEqual(moves["removed"], 1)

    def test_destination_already_listing_them_does_not_duplicate(self):
        rows, moves = store.apply_to_employee_list(
            CREDIT_SUP, self._team("S9", BORDER_EMP)
        )
        ids = [r["emp_id"] for r in rows]
        self.assertEqual(ids.count(BORDER_EMP), 1)
        self.assertEqual(moves["added"], 0)

    def test_teams_with_no_moves_are_untouched(self):
        before = self._team("S1", "S2", "S3")
        rows, moves = store.apply_to_employee_list("SL999", before)
        self.assertEqual([r["emp_id"] for r in rows], ["S1", "S2", "S3"])
        self.assertEqual(moves, {"removed": 0, "added": 0, "flagged": 0})


class TestMovedFlagReachesEveryStep(_TempStore):
    """
    พนักงานที่ถูกย้ายมาต้องมีป้ายบอกทุกขั้นของการกระจาย

    เขต ดิวิชัน และหน่วยขายของเขายังเป็นของทีมเดิม ตัวเลขบางอย่างจึงดูแปลกเมื่อเทียบ
    กับเพื่อนร่วมทีม (เช่นประวัติขายคนละเขต) — คนที่เกลี่ยเป้าต้องรู้ตั้งแต่แรกว่าทำไม
    ไม่ใช่มานั่งสงสัยว่าข้อมูลผิดหรือเปล่า
    """

    def setUp(self):
        super().setUp()
        store.set_assignment(BORDER_EMP, CREDIT_SUP, from_sup=VAN_SUP, emp_name="ชายแดน")

    def test_the_added_row_carries_where_it_came_from(self):
        rows, _ = store.apply_to_employee_list(CREDIT_SUP, self._team("C001"))
        moved = next(r for r in rows if r["emp_id"] == BORDER_EMP)
        self.assertEqual(moved["reassigned_from"], VAN_SUP)

    def test_teammates_are_not_marked(self):
        rows, _ = store.apply_to_employee_list(CREDIT_SUP, self._team("C001"))
        mate = next(r for r in rows if r["emp_id"] == "C001")
        self.assertFalse(mate.get("reassigned_from"))

    def test_the_flag_survives_the_warehouse_expansion(self):
        """
        แถวพนักงานผ่านตัวขยายตามคลังก่อนถึงหน้าจอ — ถ้าฟิลด์หายตรงนั้น
        ป้ายจะไม่ขึ้นเลยโดยที่ไม่มีอะไรฟ้อง
        """
        from backend.services.wh_split import expand_employee_rows

        rows, _ = store.apply_to_employee_list(CREDIT_SUP, self._team("C001"))
        df = pd.DataFrame(rows)
        clean = df.where(pd.notna(df), None).to_dict(orient="records")
        out = expand_employee_rows(clean, None, {})
        moved = next(r for r in out if r["emp_id"] == BORDER_EMP)
        self.assertEqual(moved.get("reassigned_from"), VAN_SUP)

    def test_the_screen_shows_it_in_all_three_steps(self):
        """หน้าเว็บรันในเทสไม่ได้ — ตรวจว่าตัวสร้างป้ายถูกเรียกครบทั้งสามตาราง"""
        with open(os.path.join(REPO, "frontend", "app.js"), encoding="utf-8") as fh:
            app = fh.read()
        self.assertIn("function _empMovedBadgeHtml", app)
        self.assertGreaterEqual(
            app.count("_empMovedBadgeHtml("), 5,
            "ต้องถูกเรียกทั้งขั้นที่ 1 (รายชื่อ) ขั้นที่ 2 (เป้าเงิน) และขั้นที่ 3 (ผลกระจาย)",
        )


class TestSendDoesNotDoubleWrite(_TempStore):
    """
    แผนกระจายที่บันทึกไว้ "ก่อน" ย้าย ยังมีคนที่ย้ายไปแล้วอยู่

    ถ้าปล่อยให้ส่ง เป้าของเขาจะถูกเขียนทับด้วยตัวเลขจากแผนเก่า แล้วแต่ว่าใครกดส่ง
    ทีหลัง — ทั้งสองรอบส่งสำเร็จเหมือนกันหมด ไม่มีอะไรฟ้อง เพราะปลายทางรับ upsert
    """

    def setUp(self):
        super().setUp()
        store.set_assignment(BORDER_EMP, CREDIT_SUP, from_sup=VAN_SUP)

    def _alloc(self, *emp_ids: str) -> list[dict]:
        return [{"emp_id": e, "sku": "A", "allocated_boxes": 5} for e in emp_ids]

    def test_source_team_plan_loses_the_moved_employee(self):
        kept, dropped = lh._drop_rows_of_reassigned_employees(
            self._alloc("S1", BORDER_EMP, "S2"), VAN_SUP
        )
        self.assertEqual([r["emp_id"] for r in kept], ["S1", "S2"])
        self.assertEqual(dropped, {BORDER_EMP})

    def test_destination_team_keeps_them(self):
        kept, dropped = lh._drop_rows_of_reassigned_employees(
            self._alloc("S9", BORDER_EMP), CREDIT_SUP
        )
        self.assertEqual([r["emp_id"] for r in kept], ["S9", BORDER_EMP])
        self.assertEqual(dropped, set())

    def test_a_third_team_also_loses_them(self):
        kept, dropped = lh._drop_rows_of_reassigned_employees(
            self._alloc(BORDER_EMP, "S8"), THIRD_SUP
        )
        self.assertEqual([r["emp_id"] for r in kept], ["S8"])
        self.assertEqual(dropped, {BORDER_EMP})

    def test_teams_with_no_moves_are_untouched(self):
        rows = self._alloc("S1", "S2")
        kept, dropped = lh._drop_rows_of_reassigned_employees(rows, "SL999")
        self.assertEqual(len(kept), 2)
        self.assertEqual(dropped, set())

    def test_employee_codes_are_matched_the_same_way_as_the_grain(self):
        """รหัสรูปต่างกันต้องยังจับได้ ไม่งั้นด่านนี้หลุดแบบเงียบ ๆ"""
        kept, dropped = lh._drop_rows_of_reassigned_employees(
            [{"emp_id": BORDER_EMP.lower(), "sku": "A", "allocated_boxes": 5}], VAN_SUP
        )
        self.assertEqual(kept, [])
        self.assertEqual(dropped, {BORDER_EMP})


class TestNewPeriodSendIsClean(unittest.TestCase):
    """
    งวดใหม่หลังย้าย S516 (ทีมหน่วยรถ) ไปให้ทีมหน่วยเครดิตเกลี่ยเป้า

    ข้อที่ต้องจริงพร้อมกันทั้งหมด — ผิดข้อใดข้อหนึ่งคือเป้าใน Target Sun เพี้ยน:
      · ทีมต้นทางไม่มีแถวของเขาเลย (ไม่งั้นเป้าถูกเขียนทับสองรอบ)
      · ทีมปลายทางมีแถวของเขา พร้อมหีบครบ
      · แถวนั้นใช้ "หน่วยขาย เขต จังหวัด ของตัวเขาเอง" ไม่ใช่ของทีมปลายทาง
        (สามค่านี้เป็นส่วนหนึ่งของคีย์ upsert ถ้าเปลี่ยนจะไปสร้างแถวใหม่ผิดเขต
         แทนที่จะทับแถวเดิมของเขา)
    """

    VAN_SUP, CREDIT_SUP = "SL372", "SL341"
    MONTH, YEAR = 10, 2026

    def setUp(self):
        self._cwd = os.getcwd()
        self._tmpdir = tempfile.mkdtemp(prefix="newperiod_send_")
        os.chdir(self._tmpdir)
        os.makedirs("data", exist_ok=True)
        self._old = os.environ.get("EMP_ASSIGNMENTS_JSON_PATH")
        os.environ["EMP_ASSIGNMENTS_JSON_PATH"] = os.path.join(
            self._tmpdir, "emp_assignments.json"
        )
        store.set_assignment(BORDER_EMP, self.CREDIT_SUP, from_sup=self.VAN_SUP)

        def g(emp, st, area, prov):
            return {"emp_id": emp, "sku": "A", "qty": 10, "salestype": st,
                    "divisioncode": "S", "areacode": area, "provincecode": prov,
                    "warehouse_code": ""}

        # งวดใหม่: ทีมต้นทางไม่มี S516 แล้ว · ทีมปลายทางมี พร้อม dim ของตัวเขาเอง
        pd.DataFrame([g("V001", "C", "10", "P1")]).to_csv(
            f"data/tga_lines_{self.VAN_SUP}_{self.YEAR}_{self.MONTH:02d}.csv", index=False)
        pd.DataFrame([
            g("C001", "S", "20", "P2"),
            g(BORDER_EMP, "C", "30", "P3"),
        ]).to_csv(
            f"data/tga_lines_{self.CREDIT_SUP}_{self.YEAR}_{self.MONTH:02d}.csv", index=False)
        for sup, boxes in ((self.VAN_SUP, 10), (self.CREDIT_SUP, 20)):
            pd.DataFrame([
                {"sku": "A", "supervisor_target_boxes": boxes, "price_per_box": 1.0},
            ]).to_csv(
                f"data/target_boxes_{sup}_{self.YEAR}_{self.MONTH:02d}.csv", index=False)

    def tearDown(self):
        os.chdir(self._cwd)
        if self._old is None:
            os.environ.pop("EMP_ASSIGNMENTS_JSON_PATH", None)
        else:
            os.environ["EMP_ASSIGNMENTS_JSON_PATH"] = self._old
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _send(self, sup, allocs):
        from backend.schemas import LakehouseUploadRequest

        req = LakehouseUploadRequest(
            sup_id=sup, target_month=self.MONTH, target_year=self.YEAR,
            upload_user_code="T",
            allocations=[
                {"emp_id": e, "sku": "A", "allocated_boxes": b} for e, b in allocs
            ],
            allow_new_targetsun_rows=True,
        )
        return lh._build_tga_upload_dataframe(req, drop_incomplete_rows=True)

    def test_source_team_file_has_no_trace_of_them(self):
        out, dropped, _p, shortfall = self._send(self.VAN_SUP, [("V001", 10)])
        self.assertNotIn(BORDER_EMP, set(out["SALESMANCODE"]))
        self.assertEqual(int(out["QUANTITYCASE"].sum()), 10)
        self.assertEqual(dropped, 0)
        self.assertEqual(shortfall, [])

    def test_destination_team_sends_them_with_their_own_territory(self):
        out, dropped, _p, shortfall = self._send(
            self.CREDIT_SUP, [("C001", 12), (BORDER_EMP, 8)]
        )
        self.assertEqual(dropped, 0)
        self.assertEqual(shortfall, [])
        row = out[out["SALESMANCODE"] == BORDER_EMP]
        self.assertEqual(len(row), 1)
        self.assertEqual(int(row["QUANTITYCASE"].iloc[0]), 8)
        self.assertEqual(row["SALESTYPE"].iloc[0], "C", "ต้องเป็นหน่วยขายของตัวเขาเอง")
        self.assertEqual(str(row["AREACODE"].iloc[0]), "30", "ต้องเป็นเขตของตัวเขาเอง")
        self.assertEqual(row["PROVINCECODE"].iloc[0], "P3")

    def test_totals_and_upsert_key_stay_sound(self):
        out, _d, _p, _s = self._send(self.CREDIT_SUP, [("C001", 12), (BORDER_EMP, 8)])
        self.assertEqual(int(out["QUANTITYCASE"].sum()), 20)
        key = ["PRODUCTCODE", "SALESTYPE", "DIVISIONCODE", "SALESMANCODE",
               "AREACODE", "PROVINCECODE"]
        self.assertEqual(int(out.duplicated(subset=key).sum()), 0)


class TestTheMovedFlagSurvivesEveryPath(_TempStore):
    """
    ป้าย "ย้ายมา" ต้องขึ้นทุกจอ — รวมตอนผู้จัดการดูก้อนรวมภาค

    ของจริงที่เจอ (S516 → SL359): แคชรายชื่อของทีมปลายทางถูกเขียนทับด้วย
    "รายชื่อหลังย้าย" ไปแล้ว รอบถัดมาตัวย้ายจึงเห็นว่าคนนี้อยู่ในลิสต์อยู่แล้ว
    แล้วข้ามไปเงียบ ๆ · ธงไม่ถูกติด ป้ายไม่ขึ้นสักจอ ทั้งที่เป้าถูกเกลี่ยรวมไปแล้ว
    ไม่มีอะไรฟ้อง เพราะยอดรวมยังถูก — บั๊กตระกูลเดียวกับที่ไล่แก้มาทั้งวัน
    """

    def setUp(self):
        super().setUp()
        store.set_assignment(BORDER_EMP, CREDIT_SUP, from_sup=VAN_SUP,
                             emp_name="สมชาย ชายแดน", updated_by="admin")

    def _dest(self, *emp_ids: str) -> list[dict]:
        return [{"emp_id": e, "emp_name": f"ชื่อ {e}", "super_code": CREDIT_SUP}
                for e in emp_ids]

    def test_a_newly_added_row_carries_the_flag(self):
        rows, _ = store.apply_to_employee_list(CREDIT_SUP, self._dest("S9"))
        moved = next(r for r in rows if r["emp_id"] == BORDER_EMP)
        self.assertEqual(moved["reassigned_from"], VAN_SUP)

    def test_a_row_already_in_the_list_gets_flagged_too(self):
        """เคสของจริง — คนนี้อยู่ในรายชื่อดิบอยู่แล้ว แต่ยังต้องมีป้าย"""
        rows, moves = store.apply_to_employee_list(
            CREDIT_SUP, self._dest("S9", BORDER_EMP)
        )
        moved = next(r for r in rows if r["emp_id"] == BORDER_EMP)
        self.assertEqual(moved["reassigned_from"], VAN_SUP)
        self.assertEqual(moves["added"], 0, "ห้ามซ้ำคน")
        self.assertEqual(moves["flagged"], 1, "ต้องบอกผู้เรียกว่าแถวเปลี่ยนแล้ว")

    def test_a_flag_already_there_is_left_alone(self):
        rows, moves = store.apply_to_employee_list(
            CREDIT_SUP,
            [{"emp_id": BORDER_EMP, "super_code": CREDIT_SUP, "reassigned_from": "SL999"}],
        )
        self.assertEqual(rows[0]["reassigned_from"], "SL999")
        self.assertEqual(moves["flagged"], 0)

    def test_teams_not_involved_see_no_flags(self):
        rows, moves = store.apply_to_employee_list(THIRD_SUP, self._dest("S8"))
        self.assertEqual(moves, {"removed": 0, "added": 0, "flagged": 0})
        self.assertFalse(any(r.get("reassigned_from") for r in rows))


class TestTheRosterCacheKeepsTheRealStructure(unittest.TestCase):
    """
    แคชรายชื่อต้องเก็บ "โครงสร้างจริง" ไม่ใช่รายชื่อหลังย้าย

    เขียนรายชื่อหลังย้ายลงแคชแล้วเสียสามอย่างพร้อมกัน:
      1. ปลดการย้ายแล้วคนนั้นค้างอยู่ทีมปลายทางตลอดไป → เป้าถูกนับสองรอบ
      2. ป้าย "ย้ายมา" หาย เพราะรอบหน้าตัวย้ายเห็นว่าอยู่ในลิสต์อยู่แล้ว
      3. รหัสที่ไม่เคยมีลูกทีม กลายเป็น "ทีม" ในสายตาตัวจัดขอบเขตรวมภาค

    หน้าที่นี้อยู่กลาง load_employees_payload ซึ่งต้องต่อ Fabric จึงรันในเทสไม่ได้
    ตรวจจากโครงของโค้ดแทน — เหมือนที่ทำกับเส้นทางล็อกอินของผู้จัดการ
    """

    SRC = os.path.join(REPO, "backend", "services", "employees.py")

    def setUp(self):
        with open(self.SRC, encoding="utf-8") as fh:
            self.src = fh.read()

    def test_the_raw_roster_is_captured_before_the_move(self):
        i = self.src.index("df_emp_raw = df_emp_fabric.copy()")
        j = self.src.index("emp_assignment_store.apply_to_employee_list")
        self.assertLess(i, j, "ต้องเก็บรายชื่อดิบไว้ก่อนย้าย")

    def test_the_cache_is_written_from_the_raw_roster(self):
        i = self.src.index("emp_cache_path(sup_id, target_month, target_year), index=False")
        before = self.src[i - 400: i]
        self.assertIn("df_emp_raw", before)
        self.assertNotIn("df_emp_fabric.to_csv(", self.src)

    def test_flags_alone_still_replace_the_working_roster(self):
        """ธงอย่างเดียวก็ต้องเอารายชื่อใหม่ไปใช้ ไม่งั้นธงหายตั้งแต่บรรทัดนั้น"""
        self.assertIn(
            'if emp_moves["removed"] or emp_moves["added"] or emp_moves.get("flagged"):',
            self.src,
        )


if __name__ == "__main__":
    unittest.main()
