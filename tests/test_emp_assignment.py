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
import tempfile
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import emp_assignment_store as store  # noqa: E402

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
        self.assertEqual(moves, {"removed": 0, "added": 0})

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
        self.assertEqual(moves, {"removed": 0, "added": 0})


if __name__ == "__main__":
    unittest.main()
