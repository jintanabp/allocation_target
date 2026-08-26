"""
ผู้จัดการภาคที่ไม่มีหน่วยกำกับ ต้องเลือกกระจายทีละหน่วยได้

คนกลุ่มนี้เห็นทั้งเครดิตและรถเงินสดอยู่แล้ว (ตัวกรองหน่วยคืนทุกรหัสเมื่อไม่ได้ระบุ
หน่วย) แต่ "กระจายรวมทั้งภาค" ทำไม่ได้เลย เพราะด่านกันการกระจายข้ามหน่วยขายจะบล็อก
— สองหน่วยใช้ราคาคนละชุด เอาเป้าหีบมาบวกรวมแล้วกระจายด้วยกันไม่ได้

ตัวเลือกหน่วยจึงไม่ใช่ความสะดวก แต่เป็นทางเดียวที่คนกลุ่มนี้จะกระจายรวมภาคได้
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
    filter_codes_by_unit,
    units_present_in,
)

logging.disable(logging.CRITICAL)

ROWS = [
    {"email": "mgr@x.co.th", "userpl": "SL900", "login_kind": "manager_acc",
     "manager_level": "regional", "acc_division": "Div.S", "acc_region": "เหนือ",
     "can_import_targetsun": False, "note": ""},
    {"email": "c1@x.co.th", "userpl": "SL396", "login_kind": "supervisor_acc",
     "acc_division": "Div.S", "acc_region": "เหนือ", "acc_unit": "credit",
     "can_import_targetsun": False, "note": ""},
    {"email": "c2@x.co.th", "userpl": "SL506", "login_kind": "supervisor_acc",
     "acc_division": "Div.S", "acc_region": "เหนือ", "acc_unit": "credit",
     "can_import_targetsun": False, "note": ""},
    {"email": "v1@x.co.th", "userpl": "SL372", "login_kind": "supervisor_acc",
     "acc_division": "Div.S", "acc_region": "เหนือ", "acc_unit": "van",
     "can_import_targetsun": False, "note": ""},
    {"email": "u1@x.co.th", "userpl": "SL351", "login_kind": "supervisor_acc",
     "acc_division": "Div.S", "acc_region": "เหนือ",
     "can_import_targetsun": False, "note": ""},          # ยังไม่ได้ระบุหน่วย
]
ALL_CODES = ["SL396", "SL506", "SL372", "SL351"]


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


class TestUnitFilter(_Base):
    def test_credit_keeps_only_credit_teams(self):
        self.assertEqual(
            sorted(filter_codes_by_unit(ALL_CODES, "credit")),
            ["SL351", "SL396", "SL506"],
        )

    def test_van_keeps_only_van_teams(self):
        self.assertEqual(
            sorted(filter_codes_by_unit(ALL_CODES, "van")), ["SL351", "SL372"]
        )

    def test_teams_without_a_unit_are_never_dropped(self):
        """
        ข้อมูลไม่ครบต้องไม่ทำให้ทีมหายจากมุมมองเงียบ ๆ (ของจริง acc_unit ว่างเกือบครึ่ง)
        ด่านกันกระจายข้ามหน่วยก็ไม่นับทีมที่ไม่รู้หน่วยเหมือนกัน สองที่จึงสอดคล้องกัน
        """
        for unit in ("credit", "van"):
            with self.subTest(unit=unit):
                self.assertIn("SL351", filter_codes_by_unit(ALL_CODES, unit))

    def test_no_unit_asked_means_no_filtering(self):
        for unit in ("", None, "ทั้งหมด", "ALL"):
            with self.subTest(unit=unit):
                self.assertEqual(filter_codes_by_unit(ALL_CODES, unit), ALL_CODES)

    def test_the_original_list_is_not_mutated(self):
        before = list(ALL_CODES)
        filter_codes_by_unit(ALL_CODES, "credit")
        self.assertEqual(ALL_CODES, before)


class TestUnitsPresent(_Base):
    def test_reports_both_units_when_the_scope_mixes_them(self):
        self.assertEqual(units_present_in(ALL_CODES), ["credit", "van"])

    def test_a_single_unit_scope_reports_one(self):
        self.assertEqual(units_present_in(["SL396", "SL506"]), ["credit"])

    def test_unknown_units_are_not_reported_as_a_unit(self):
        self.assertEqual(units_present_in(["SL351"]), [])

    def test_empty_scope_is_not_an_error(self):
        self.assertEqual(units_present_in([]), [])


class TestPickingAUnitUnblocksAllocation(_Base):
    """
    ก่อนมีตัวเลือกนี้ ผู้จัดการที่ไม่มีหน่วยกำกับกระจายรวมภาคไม่ได้เลย
    เพราะขอบเขตปนสองหน่วยแล้วโดนด่านบล็อก
    """

    def test_full_scope_still_mixes_two_units(self):
        self.assertEqual(len(units_present_in(ALL_CODES)), 2)

    def test_after_picking_a_unit_only_one_remains(self):
        for unit in ("credit", "van"):
            with self.subTest(unit=unit):
                picked = filter_codes_by_unit(ALL_CODES, unit)
                self.assertEqual(units_present_in(picked), [unit])


class TestUnitVocabularyMatchesBothSides(_Base):
    """
    ระบบใช้คำเรียกหน่วยขายสองชุด แล้วเคยส่งผิดชุดข้ามฝั่งจนช่องเลือกไม่โผล่เลย

      รหัสภายใน (จาก TargetSun): S = เครดิต · C = รถเงินสด
      คำที่ผู้ใช้/หน้าเว็บใช้     : credit / van

    ตัวกรองหน่วย ค่าใน dropdown และพารามิเตอร์ที่ยิงกลับ ใช้ credit/van หมด
    ถ้า backend ส่ง S/C ไป หน้าเว็บจับคู่ไม่ติด — ช่องเลือกหน่วยไม่มีวันโผล่
    และไม่มีอะไรฟ้องเลยสักอย่าง
    """

    def _merged(self):
        from backend.services.employees import merge_employees_payloads

        def pay(sid, unit):
            return {
                "_source_sup_id": sid,
                "sales_unit": unit,
                "employees": [{"emp_id": "E" + sid, "target_sun": 100}],
                "skus": [{"sku": "A", "price_per_box": 10, "supervisor_target_boxes": 5}],
                "sku_warnings": [],
                "new_product_skus": [],
            }

        return merge_employees_payloads(
            [pay("SL396", "S"), pay("SL372", "C")],
            aggregate_label="รวม",
            aggregate_sup_ids=["SL396", "SL372"],
        )

    def test_the_payload_speaks_the_screen_s_words(self):
        out = self._merged()
        self.assertEqual(
            out.get("sales_unit_by_sup"), {"SL396": "credit", "SL372": "van"}
        )

    def test_internal_codes_never_leak_to_the_screen(self):
        out = self._merged()
        self.assertNotIn(
            "S", set(out.get("sales_unit_by_sup", {}).values()),
            "S/C เป็นรหัสภายใน ห้ามหลุดไปหน้าเว็บ",
        )

    def test_the_screen_tolerates_both_spellings(self):
        """กันพลาดซ้ำ — หน้าเว็บต้องรับได้ทั้งสองแบบถึงจะไม่เงียบอีก"""
        with open(os.path.join(REPO, "frontend", "app.js"), encoding="utf-8") as fh:
            app = fh.read()
        self.assertIn('if (v === "credit" || v === "s") return "credit";', app)
        self.assertIn('if (v === "van" || v === "c") return "van";', app)


class TestUnitPickerIsWiredEndToEnd(unittest.TestCase):
    """
    ตัวเลือกหน่วยต่อกันสี่ทอด ถ้าขาดทอดใดทอดหนึ่งก็ไม่มีช่องให้กด โดยไม่มีอะไรฟ้อง:
      backend ส่งหน่วยรายทีม → หน้าจอเก็บไว้ → ตัดสินว่าโชว์ช่องไหม → ส่งค่ากลับไป
    """

    def _read(self, rel: str) -> str:
        with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
            return fh.read()

    def test_backend_sends_the_unit_of_each_team(self):
        emp = self._read("backend/services/employees.py")
        self.assertIn('"sales_unit_by_sup"', emp)

    def test_the_endpoints_accept_a_unit(self):
        data = self._read("backend/routers/data.py")
        self.assertEqual(
            data.count('unit: str = Query("", description='), 2,
            "ต้องรับได้ทั้งรวมของผู้จัดการและรวมภาคของซุป",
        )
        self.assertIn("filter_codes_by_unit(sup_ids, unit)", data)

    def test_the_screen_keeps_what_backend_sent(self):
        app = self._read("frontend/app.js")
        self.assertIn("data.sales_unit_by_sup", app)
        self.assertIn("S.salesUnitBySup", app)

    def test_the_picker_shows_when_the_scope_mixes_units(self):
        app = self._read("frontend/app.js")
        self.assertIn("function _unitsInCurrentScope", app)
        self.assertIn("units.length > 1", app)

    def test_the_picker_stays_after_a_unit_is_picked(self):
        """
        กับดัก: พอเลือกเครดิต ขอบเขตเหลือแต่ทีมเครดิต จำนวนหน่วยกลายเป็น 1
        ถ้าเงื่อนไขโชว์เป็น "มากกว่า 1 หน่วย" อย่างเดียว ช่องจะหายไปทันที
        แล้วผู้ใช้กลับไปดูทุกหน่วยไม่ได้อีกเลย ต้องออกไปเปลี่ยนภาคหรือล็อกอินใหม่
        """
        app = self._read("frontend/app.js")
        self.assertIn(
            "S.aggregateMode && (units.length > 1 || !!S.managerViewUnit)", app
        )

    def test_going_back_to_all_units_is_an_option(self):
        html = self._read("frontend/index.html")
        i = html.index('id="managerViewUnitSelect"')
        block = html[i: i + 500]
        self.assertIn('<option value="">', block, "ต้องมีตัวเลือกกลับไปดูทุกหน่วย")

    def test_picking_a_unit_reloads_with_it(self):
        app = self._read("frontend/app.js")
        self.assertIn("function onManagerViewUnitChange", app)
        self.assertIn(
            '(S.managerViewUnit ? `&unit=${encodeURIComponent(S.managerViewUnit)}` : "")',
            app,
        )

    def test_the_control_exists_on_the_page(self):
        html = self._read("frontend/index.html")
        self.assertIn('id="managerViewUnitSelect"', html)
        for value in ('value=""', 'value="credit"', 'value="van"'):
            with self.subTest(value=value):
                self.assertIn(value, html)


class TestServerAllocationPanelFollowsTheUnit(unittest.TestCase):
    """
    "ผลกระจายหีบล่าสุด (server)" ต้องตามหน่วยขายที่เลือกดูอยู่

    ของเดิมส่งรายชื่อทีมทั้งหมดไปเสมอ — เลือกดูเฉพาะเครดิต แต่ตารางสรุปยังขึ้น
    ทีมรถเงินสดครบ · สองมุมมองที่ไม่ตรงกันบนหน้าจอเดียว แล้วไม่มีอะไรฟ้อง
    เพราะตัวเลขแต่ละแถวถูกของมันเอง
    """

    def setUp(self):
        with open(os.path.join(REPO, "frontend", "app.js"), encoding="utf-8") as fh:
            self.app = fh.read()

    def test_there_is_one_place_that_narrows_the_team_list(self):
        self.assertIn("function _scopedSupervisorChoices()", self.app)

    def test_every_path_to_the_summary_uses_it(self):
        """
        สามทางดึงสรุปเหมือนกันและใช้แคชก้อนเดียวกัน — แก้ทางเดียวไม่พอ
        ตัว prefetch เบื้องหลังจะเติมแคชด้วยรายชื่อทั้งหมดก่อน แล้วตารางอ่านก้อนนั้น
        """
        hits, at = [], 0
        while True:
            i = self.app.find("/data/allocations/summary", at)
            if i < 0:
                break
            hits.append(self.app[i - 600: i])
            at = i + 1
        self.assertEqual(len(hits), 3, "จำนวนทางเรียกเปลี่ยน — ต้องไล่กรองให้ครบใหม่")
        for k, before in enumerate(hits):
            self.assertIn("_scopedSupervisorChoices()", before, f"ทางที่ {k + 1} ยังไม่กรอง")

    def test_the_cache_key_separates_the_units(self):
        """ไม่งั้นสลับหน่วยแล้วได้ตารางเดิมจากแคช"""
        i = self.app.index("function _allocSummaryCacheKey()")
        block = self.app[i: i + 600]
        self.assertIn("_scopedSupervisorChoices()", block)
        self.assertIn("S.managerViewUnit", block)

    def test_the_scope_from_the_server_wins(self):
        """
        กรองเองจาก salesUnitBySup ไม่ได้ — แมพนั้นมีเฉพาะทีมในขอบเขตตอนนี้
        ทีมนอกขอบเขตจึงอ่านได้ว่า "ไม่รู้หน่วย" แล้วติดมาด้วยตามกติกา
        ตารางเลยขึ้นครบทุกหน่วยเหมือนเดิมทั้งที่เลือกหน่วยเดียวไว้
        """
        i = self.app.index("function _scopedSupervisorChoices()")
        block = self.app[i: i + 1600]
        self.assertIn("if (S.aggregateMode && scope.length) return scope;", block)

    def test_teams_without_a_unit_are_never_hidden(self):
        """ทางถอย (ไม่ได้อยู่ในมุมมองรวม) ยังใช้กติกาเดียวกับฝั่ง server"""
        i = self.app.index("function _scopedSupervisorChoices()")
        block = self.app[i: i + 1600]
        self.assertIn("return !u || u === want;", block)


if __name__ == "__main__":
    unittest.main()
