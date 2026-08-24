"""
แก้สิทธิ์ผู้ใช้แล้ว "ดูได้" ต้องเปลี่ยนตาม

บั๊กเดิม: user_access.json เก็บผลลัพธ์ visible_supervisor_codes ลงไฟล์ แล้วโค้ดอ่าน
สิทธิ์ให้ค่าที่เก็บไว้ชนะการคำนวณใหม่เสมอ ผลคือแอดมินแก้หน่วย/ภาค/ดิวิชัน แล้วทั้ง
คอลัมน์ "ดูได้" และสิทธิ์ตอนล็อกอินจริงไม่ขยับเลย (ช่อง preview ในฟอร์มคำนวณสด
อยู่แล้วเลยโชว์ค่าใหม่สวนกับค่าที่บันทึกไป)

กติกาที่ต้องคงไว้:
  - "ดูได้" คำนวณสดจากฟิลด์ปัจจุบันเสมอ ค่าที่ค้างในไฟล์ต้องทับไม่ได้
  - เขียนไฟล์แล้วต้องไม่มีฟิลด์นี้หลงเหลือ
  - สิทธิ์ตอนล็อกอินจริงต้องเปลี่ยนตามด้วย ไม่ใช่แค่หน้าจอ
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.routers.admin import UserAccessUpdateBody, _patch_row_meta  # noqa: E402
from backend.services import access_control as ac  # noqa: E402
from backend.services import user_access_store as uas  # noqa: E402

VAN1 = "van1@sahapat.co.th"
VAN2 = "van2@sahapat.co.th"
CREDIT1 = "credit1@sahapat.co.th"


def _sup(email, upl, unit, stale):
    """ซุปในภาคเดียวกัน พร้อม 'ดูได้' ที่ค้างไว้ในไฟล์แบบข้อมูลจริง"""
    return {
        "email": email,
        "userpl": upl,
        "can_import_targetsun": False,
        "note": "",
        "login_kind": "supervisor_acc",
        "acc_division": "Div.S",
        "acc_region": "อีสาน",
        "acc_unit": unit,
        "acc_scope": unit,
        "visible_supervisor_codes": stale,
    }


VAN_TEAM = ["SL100", "SL101"]
ROWS = [
    _sup(VAN1, "SL100", "van", VAN_TEAM),
    _sup(VAN2, "SL101", "van", VAN_TEAM),
    _sup(CREDIT1, "SL200", "credit", ["SL200"]),
]


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._path = os.path.join(self._tmp.name, "user_access.json")
        with open(self._path, "w", encoding="utf-8") as fh:
            json.dump(ROWS, fh, ensure_ascii=False)
        self._old = os.environ.get("USER_ACCESS_JSON_PATH")
        os.environ["USER_ACCESS_JSON_PATH"] = self._path
        ac.invalidate_user_access_cache()

    def tearDown(self):
        if self._old is None:
            os.environ.pop("USER_ACCESS_JSON_PATH", None)
        else:
            os.environ["USER_ACCESS_JSON_PATH"] = self._old
        ac.invalidate_user_access_cache()
        self._tmp.cleanup()

    def _visible(self, upl):
        row = next(r for r in ac.enrich_user_access_rows() if r["userpl"] == upl)
        return sorted(row["visible_supervisors"])

    def _save_unit(self, email, upl, unit):
        """เลียนแบบ PUT /admin/user-access ที่แก้เฉพาะหน่วย"""
        body = UserAccessUpdateBody(
            email=email, userpl=upl, acc_unit=unit,
            login_kind="supervisor_acc", manager_level="",
            acc_division="Div.S", acc_region="อีสาน", note="",
        )
        rows = uas.read_rows()
        updated = dict(next(r for r in rows if r["userpl"] == upl))
        _patch_row_meta(updated, body)
        uas.write_rows([updated if r["userpl"] == upl else r for r in rows])
        ac.invalidate_user_access_cache()


class TestStoredValueNeverWins(_Base):
    def test_file_value_does_not_override_the_computation(self):
        # SL200 เป็นซุปเครดิตคนเดียวในภาค — ค่าที่ค้างไว้ตรงกับที่คำนวณได้อยู่แล้ว
        self.assertEqual(self._visible("SL200"), ["SL200"])
        # SL100/SL101 เป็นซุปหน่วยรถ เห็นกันเอง
        self.assertEqual(self._visible("SL100"), VAN_TEAM)

    def test_write_strips_the_stored_field(self):
        uas.write_rows(uas.read_rows())
        with open(self._path, encoding="utf-8") as fh:
            saved = json.load(fh)
        for row in saved:
            self.assertNotIn(
                "visible_supervisor_codes", row,
                f"{row['userpl']}: ไม่ควรเก็บผลลัพธ์ 'ดูได้' ลงไฟล์อีกแล้ว",
            )

    def test_read_drops_the_stored_field(self):
        for row in uas.read_rows():
            self.assertNotIn("visible_supervisor_codes", row)


class TestEditingUpdatesVisibility(_Base):
    def test_changing_unit_moves_the_user_to_the_other_team(self):
        self.assertEqual(self._visible("SL100"), VAN_TEAM)
        self._save_unit(VAN1, "SL100", "credit")
        # ย้ายไปหน่วยเครดิตแล้วต้องเห็นเพื่อนหน่วยเครดิต ไม่ใช่ทีมรถเดิม
        self.assertEqual(self._visible("SL100"), ["SL100", "SL200"])
        self.assertEqual(self._visible("SL200"), ["SL100", "SL200"])

    def test_the_team_left_behind_shrinks_too(self):
        self._save_unit(VAN1, "SL100", "credit")
        self.assertEqual(self._visible("SL101"), ["SL101"])

    def test_login_permission_changes_not_just_the_screen(self):
        before = ac.compute_allowed_supervisor_codes(VAN1, ac.load_acc_rows())
        self.assertEqual(sorted(before), VAN_TEAM)
        self._save_unit(VAN1, "SL100", "credit")
        after = ac.compute_allowed_supervisor_codes(VAN1, ac.load_acc_rows())
        self.assertEqual(sorted(after), ["SL100", "SL200"])

    def test_admin_preview_matches_what_gets_saved(self):
        """ฟอร์มโชว์อะไรตอนพิมพ์ บันทึกแล้วต้องได้อย่างนั้น — เดิมสวนกัน"""
        preview = sorted(ac.visible_supervisors_for_row_dict({
            "userpl": "SL100",
            "login_kind": "supervisor_acc",
            "acc_division": "Div.S",
            "acc_region": "อีสาน",
            "acc_unit": "credit",
            "acc_scope": "credit",
        }))
        self._save_unit(VAN1, "SL100", "credit")
        self.assertEqual(preview, self._visible("SL100"))


if __name__ == "__main__":
    unittest.main()
