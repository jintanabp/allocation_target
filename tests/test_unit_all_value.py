"""
หน่วยขาย "ทั้งสองหน่วย" (all) ต้องตั้งได้อย่างชัดเจน

เดิมมีแค่ van / credit / เว้นว่าง — และ "เว้นว่าง" ทำหน้าที่สองอย่างพร้อมกัน:
"ยังไม่ได้กรอก" กับ "ตั้งใจให้ดูทั้งสองหน่วย" ซึ่งแยกกันไม่ออก · คนที่ตั้งใจให้ดู
ทั้งสองหน่วยจึงติดธง "ต้องตรวจสอบ" ค้างตลอดไปโดยไม่มีทางเคลียร์

ผลกับการมองเห็นเหมือนเว้นว่าง (เห็นทั้งคู่) ต่างกันแค่ "ตั้งใจแล้ว"
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
from backend.services.user_access_store import canonicalize_user_access_row  # noqa: E402

logging.disable(logging.CRITICAL)


def _read(rel: str) -> str:
    with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
        return fh.read()


class TestAllIsStored(unittest.TestCase):
    def _row(self, unit: str, lk: str = "supervisor_acc") -> dict:
        return canonicalize_user_access_row({
            "email": "x@x.co.th", "userpl": "SL100", "login_kind": lk,
            "acc_division": "Div.B", "acc_region": "กลาง", "acc_unit": unit,
        })

    def test_all_survives_the_save(self):
        self.assertEqual(self._row("all").get("acc_unit"), "all")

    def test_the_two_real_units_still_work(self):
        for unit in ("van", "credit"):
            with self.subTest(unit=unit):
                self.assertEqual(self._row(unit).get("acc_unit"), unit)

    def test_a_junk_value_is_not_stored(self):
        """ค่าที่ไม่รู้จักถูกเก็บเป็น sentinel "ไม่ระบุ" ซึ่งอ่านกลับมาเป็นค่าว่าง"""
        from backend.services.user_access_store import NONE_SENTINEL

        self.assertEqual(self._row("ทั้งหมด").get("acc_unit"), NONE_SENTINEL)

    def test_a_division_manager_still_cannot_set_a_unit(self):
        """ผู้จัดการระดับดิวิชันขอบเขตคือทั้งดิวิชันอยู่แล้ว ไม่ต้องระบุหน่วย"""
        from backend.services.user_access_store import NONE_SENTINEL

        row = canonicalize_user_access_row({
            "email": "m@x.co.th", "userpl": "SL200", "login_kind": "manager_acc",
            "manager_level": "division", "acc_division": "Div.S", "acc_unit": "all",
        })
        self.assertEqual(row.get("acc_unit"), NONE_SENTINEL)


class TestAllBehavesLikeBothUnits(unittest.TestCase):
    """ทีมที่ตั้ง all ต้องติดมาด้วยไม่ว่าเลือกดูหน่วยไหน และไม่นับเป็นหน่วยของตัวเอง"""

    ROWS = [
        {"email": "c@x.co.th", "userpl": "SL396", "login_kind": "supervisor_acc",
         "acc_division": "Div.S", "acc_region": "เหนือ", "acc_unit": "credit",
         "can_import_targetsun": False, "note": ""},
        {"email": "v@x.co.th", "userpl": "SL372", "login_kind": "supervisor_acc",
         "acc_division": "Div.S", "acc_region": "เหนือ", "acc_unit": "van",
         "can_import_targetsun": False, "note": ""},
        {"email": "a@x.co.th", "userpl": "SL500", "login_kind": "supervisor_acc",
         "acc_division": "Div.S", "acc_region": "เหนือ", "acc_unit": "all",
         "can_import_targetsun": False, "note": ""},
    ]

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        path = os.path.join(self._tmp.name, "user_access.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.ROWS, fh, ensure_ascii=False)
        self._old = os.environ.get("USER_ACCESS_JSON_PATH")
        os.environ["USER_ACCESS_JSON_PATH"] = path

    def tearDown(self):
        if self._old is None:
            os.environ.pop("USER_ACCESS_JSON_PATH", None)
        else:
            os.environ["USER_ACCESS_JSON_PATH"] = self._old
        self._tmp.cleanup()

    def test_an_all_team_shows_up_under_both_units(self):
        for unit in ("credit", "van"):
            with self.subTest(unit=unit):
                self.assertIn("SL500", filter_codes_by_unit(["SL396", "SL372", "SL500"], unit))

    def test_all_is_not_counted_as_a_unit_of_its_own(self):
        """
        ถ้านับเป็นหน่วยที่สาม ขอบเขตที่มีแต่ทีม all จะดูเหมือนปนหน่วย
        แล้วโดนด่านกันกระจายข้ามหน่วยบล็อกทั้งที่ไม่ได้ปนอะไรเลย
        """
        self.assertEqual(units_present_in(["SL500"]), [])
        self.assertEqual(units_present_in(["SL396", "SL500"]), ["credit"])


class TestScreenOffersAll(unittest.TestCase):
    def test_the_editor_lists_all(self):
        app = _read("frontend/app.js")
        self.assertIn('"credit", "all"', app.replace("'", '"'))
        self.assertIn("ทั้งสองหน่วย (all)", app)

    def test_the_column_filter_lists_all(self):
        html = _read("frontend/index.html")
        self.assertIn('<option value="all">', html)

    def test_the_blank_value_is_still_flagged_for_review(self):
        """เว้นว่าง = ยังไม่ได้กรอก ต้องยังตามมาให้ระบุ"""
        app = _read("frontend/app.js")
        self.assertIn("ไม่ระบุหน่วยขาย (ดูได้ทั้งสองหน่วยไปก่อน)", app)


if __name__ == "__main__":
    unittest.main()
