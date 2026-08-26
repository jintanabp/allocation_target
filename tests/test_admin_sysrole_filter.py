"""
กรองรายชื่อผู้ใช้ตาม "สิทธิ์ดูแลระบบ" ได้ แยกจากช่องตำแหน่ง

แอดมินไม่ใช่ตำแหน่งแทนที่ของเดิม แต่เป็นสิทธิ์ที่ซ้อนทับตำแหน่งงาน — Supervisor
ที่เป็นแอดมินภาคด้วยก็ยังเป็น Supervisor เต็มตัว (ดู test_rbac_roles)

ถ้าเอาไปยัดรวมในช่องตำแหน่งช่องเดียว จะกรอง "Supervisor ที่เป็นแอดมิน" ไม่ได้เลย
ต้องเลือกได้อย่างใดอย่างหนึ่ง · สองแกนนี้จึงต้องเป็นคนละช่อง

หน้าเว็บรันในเทสไม่ได้ ตรวจได้แค่ว่าโครงที่จำเป็นครบและต่อกันถูก
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

from backend.services import access_control as ac  # noqa: E402

logging.disable(logging.CRITICAL)


def _read(rel: str) -> str:
    with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
        return fh.read()


APP = _read("frontend/app.js")
HTML = _read("frontend/index.html")

ROWS = [
    {"email": "sup.plain@x.co.th", "userpl": "SL100", "login_kind": "supervisor_acc",
     "acc_division": "Div.B", "acc_region": "กลาง", "can_import_targetsun": False, "note": ""},
    {"email": "sup.admin@x.co.th", "userpl": "SL101", "login_kind": "supervisor_acc",
     "acc_division": "Div.B", "acc_region": "กลาง", "role": "admin",
     "admin_scope": "division_region", "can_import_targetsun": False, "note": ""},
    {"email": "mgr.head@x.co.th", "userpl": "SL200", "login_kind": "manager_acc",
     "manager_level": "regional", "acc_division": "Div.B", "acc_region": "กลาง",
     "role": "head_admin", "can_import_targetsun": False, "note": ""},
]


class TestBackendSendsBothAxes(unittest.TestCase):
    """
    หน้าเว็บกรองสองแกนนี้จากข้อมูลที่ backend ส่งมา — ต้องมาครบทั้งคู่ในแถวเดียวกัน
    """

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

    def _by_email(self):
        return {r["email"]: r for r in ac.enrich_user_access_rows()}

    def test_a_supervisor_who_is_also_an_admin_keeps_both(self):
        row = self._by_email()["sup.admin@x.co.th"]
        self.assertEqual(row["login_kind"], "supervisor_acc", "ตำแหน่งงานต้องไม่หาย")
        self.assertEqual(row["system_role"], "admin", "สิทธิ์ดูแลต้องมาด้วย")

    def test_a_plain_supervisor_has_no_system_role(self):
        row = self._by_email()["sup.plain@x.co.th"]
        self.assertEqual(row["login_kind"], "supervisor_acc")
        self.assertEqual(row["system_role"], "")

    def test_head_admin_is_reported_as_its_own_level(self):
        row = self._by_email()["mgr.head@x.co.th"]
        self.assertEqual(row["system_role"], "head_admin")
        self.assertEqual(row["login_kind"], "manager_acc")


class TestFilterControlExists(unittest.TestCase):
    def test_the_filter_is_a_separate_control_from_position(self):
        self.assertIn('id="adminFSysRole"', HTML)
        self.assertIn('id="adminFRole"', HTML)

    def test_every_admin_level_can_be_picked(self):
        for value in ("admin", "head_admin", "dev"):
            with self.subTest(value=value):
                self.assertIn(f'<option value="{value}">', HTML)

    def test_has_any_and_none_shortcuts(self):
        """"มีสิทธิ์ดูแลทุกระดับ" กับ "ไม่มีสิทธิ์ดูแล" คือสองคำถามที่ถามบ่อยที่สุด"""
        self.assertIn('value="__any__"', HTML)
        self.assertIn('id="adminFSysRole"', HTML)

    def test_the_filter_reads_system_role_not_the_position_field(self):
        self.assertIn('document.getElementById("adminFSysRole")', APP)
        self.assertIn('String(r.system_role || "").trim().toLowerCase()', APP)

    def test_it_is_wired_into_clear_and_visual_state(self):
        """ไม่ลงทะเบียนไว้ = กด "ล้างตัวกรอง" แล้วค่านี้ค้าง ตารางเลยดูเหมือนหายไปเฉย ๆ"""
        self.assertEqual(APP.count('"adminFSysRole",'), 3)
        self.assertIn('["adminFSysRole", (v) => !!v]', APP)


if __name__ == "__main__":
    unittest.main()
