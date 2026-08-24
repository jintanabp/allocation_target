"""
ผู้จัดการที่แถวไม่มี division/ภาค — ถอยไปใช้ทีมจาก roster ระหว่างรอเติมข้อมูล

ของจริงในไฟล์: ผู้จัดการ 7 คนไม่มีทั้ง division และภาค ระบบจึงคำนวณทีมกลับไม่ได้
เหลือแค่รหัสตัวเอง กดเลือกลูกน้องแล้วโดน 403 ทั้งที่หน้าเลือกทีมยังโชว์ทีมจาก
roster ให้ (access_hierarchy.json ยังเก็บไว้ครบ) — เป็นมาก่อนที่จะเลิกเก็บ
visible_supervisor_codes ลงไฟล์ ไม่เกี่ยวกัน

กติกาที่ต้องคงไว้:
  - ใช้ roster เฉพาะตอนคำนวณจากฟิลด์แล้วไม่ได้อะไรเลย
  - แถวที่ข้อมูลครบ ผลคำนวณต้องชนะ roster เสมอ (ไม่งั้นกลับไปเป็นบั๊กเดิม)
  - พอเติม division/ภาค ครบ fallback ต้องเลิกทำงานเอง
  - ซุปไม่เข้าข่าย — roster เก็บทีมไว้ต่อผู้จัดการเท่านั้น
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import access_control as ac  # noqa: E402

LOST_MGR = "lost.mgr@sahapat.co.th"      # ผจก. ที่ข้อมูลไม่ครบ
FULL_MGR = "full.mgr@sahapat.co.th"      # ผจก. ที่ข้อมูลครบ
LOST_SUP = "lost.sup@sahapat.co.th"      # ซุปที่ข้อมูลไม่ครบ

ROSTER_TEAM = ["SL801", "SL802", "SL803"]

# roster เดิมรู้ทีมของทั้งสองคน — ของ FULL_MGR ตั้งใจให้ "ผิด" เพื่อพิสูจน์ว่า
# แถวที่ข้อมูลครบต้องใช้ผลคำนวณ ไม่ใช่ค่าจาก roster
MDATA = {
    "by_manager": {
        "SL800": ROSTER_TEAM,
        "SL810": ["SL999"],
        "SL820": ROSTER_TEAM,
    },
    "supervisors": ROSTER_TEAM + ["SL811", "SL999"],
    "manager_codes": ["SL800", "SL810", "SL820"],
}

ROWS = [
    {"email": LOST_MGR, "userpl": "SL800", "login_kind": "manager_acc",
     "can_import_targetsun": False, "note": ""},
    {"email": FULL_MGR, "userpl": "SL810", "login_kind": "manager_acc",
     "acc_division": "Div.B", "acc_region": "เหนือ",
     "can_import_targetsun": False, "note": ""},
    {"email": "sup811@sahapat.co.th", "userpl": "SL811", "login_kind": "supervisor_acc",
     "acc_division": "Div.B", "acc_region": "เหนือ",
     "can_import_targetsun": False, "note": ""},
    {"email": LOST_SUP, "userpl": "SL820", "login_kind": "supervisor_acc",
     "can_import_targetsun": False, "note": ""},
]


class TestRosterFallback(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        path = os.path.join(self._tmp.name, "user_access.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(ROWS, fh, ensure_ascii=False)
        self._old = os.environ.get("USER_ACCESS_JSON_PATH")
        os.environ["USER_ACCESS_JSON_PATH"] = path
        ac.invalidate_user_access_cache()

    def tearDown(self):
        if self._old is None:
            os.environ.pop("USER_ACCESS_JSON_PATH", None)
        else:
            os.environ["USER_ACCESS_JSON_PATH"] = self._old
        ac.invalidate_user_access_cache()
        self._tmp.cleanup()

    def _allowed(self, email):
        return sorted(ac.compute_allowed_supervisor_codes(email, ac.load_acc_rows(), MDATA))

    def test_manager_without_division_gets_the_roster_team(self):
        self.assertEqual(self._allowed(LOST_MGR), ["SL800"] + ROSTER_TEAM)

    def test_manager_with_full_data_ignores_the_roster(self):
        # คำนวณได้ SL811 (ซุปใน Div.B/เหนือ) — ห้ามเอา SL999 จาก roster มาปน
        allowed = self._allowed(FULL_MGR)
        self.assertIn("SL811", allowed)
        self.assertNotIn("SL999", allowed, "แถวข้อมูลครบต้องใช้ผลคำนวณ ไม่ใช่ roster")

    def test_supervisor_without_division_stays_alone(self):
        # ซุปไม่มี fallback — roster เก็บทีมไว้ต่อผู้จัดการเท่านั้น
        self.assertEqual(self._allowed(LOST_SUP), ["SL820"])

    def test_fallback_stops_once_the_data_is_filled_in(self):
        self.assertEqual(self._allowed(LOST_MGR), ["SL800"] + ROSTER_TEAM)
        rows = json.load(open(os.environ["USER_ACCESS_JSON_PATH"], encoding="utf-8"))
        for r in rows:
            if r["userpl"] == "SL800":
                r["acc_division"] = "Div.B"
                r["acc_region"] = "เหนือ"
        with open(os.environ["USER_ACCESS_JSON_PATH"], "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False)
        ac.invalidate_user_access_cache()
        allowed = self._allowed(LOST_MGR)
        self.assertIn("SL811", allowed, "เติมข้อมูลแล้วต้องคำนวณทีมได้เอง")
        for code in ROSTER_TEAM:
            self.assertNotIn(code, allowed, f"{code} มาจาก roster — ต้องเลิกใช้แล้ว")

    def test_no_roster_entry_means_no_change(self):
        empty = {"by_manager": {}, "supervisors": [], "manager_codes": []}
        allowed = sorted(
            ac.compute_allowed_supervisor_codes(LOST_MGR, ac.load_acc_rows(), empty)
        )
        self.assertEqual(allowed, ["SL800"])


if __name__ == "__main__":
    unittest.main()
