"""
นิยาม "ใครกระจายเป้าได้" — ตัวหารของรายงานสรุปการใช้งาน

เลขนี้ผิดเมื่อไหร่ รายงานทั้งหน้าผิดตาม และผิดแบบดูไม่ออกด้วย (เปอร์เซ็นต์ยัง
ดูสมเหตุสมผลอยู่) จึงตรึงทุกกรณีขอบไว้ที่นี่:
  - บัญชี "แอดมินอย่างเดียว" เก็บ userpl เป็น "none" ต้องไม่กลายเป็นทีมชื่อ NONE
  - ทีมสาธิตไม่ใช่ทีมจริง
  - รหัสที่ผูกกันแล้ว = ทีมเดียว ห้ามนับสองครั้ง
  - marketing / standard ไม่มีทีมให้กระจาย
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import allocating_teams as at  # noqa: E402


def _row(email, userpl, kind="supervisor_acc", **kw):
    base = {
        "email": email,
        "userpl": userpl,
        "login_kind": kind,
        "acc_region": "เหนือ",
        "acc_division": "Div.B",
        "acc_unit": "credit",
        "full_name": f"ชื่อ {userpl}",
    }
    base.update(kw)
    return base


ROWS = [
    _row("sup1@x.com", "SL100"),
    _row("sup2@x.com", "SL200"),
    _row("mgr@x.com", "SL300", kind="manager_acc", manager_level="regional"),
    _row("divmgr@x.com", "SL400", kind="manager_acc",
         manager_level="division", acc_region="none"),
    # แอดมินอย่างเดียว — ไม่มีตำแหน่งงาน รหัสเก็บเป็น sentinel
    {"email": "admin@x.com", "userpl": "none", "login_kind": "standard", "role": "admin"},
    _row("mkt@x.com", "SL500", kind="marketing"),
    _row("demo@x.com", "SLDEMO1"),
    # รหัสเก่าที่ผูกไว้กับ SL100 — คนละอีเมล แต่เป็นทีมเดียวกัน
    _row("old@x.com", "SL900"),
]

# รูปแบบเดียวกับ config/sl_links.json จริง: old_sl = canonical, new_sls = รหัสที่ผูกเข้ามา
LINKS = {"links": [{"old_sl": "SL100", "new_sls": ["SL900"], "note": "SL900 ใช้ทีมของ SL100"}]}


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        ua = os.path.join(self._tmp.name, "user_access.json")
        sl = os.path.join(self._tmp.name, "sl_links.json")
        with open(ua, "w", encoding="utf-8") as fh:
            json.dump(ROWS, fh, ensure_ascii=False)
        with open(sl, "w", encoding="utf-8") as fh:
            json.dump(LINKS, fh, ensure_ascii=False)
        self._env = {"USER_ACCESS_JSON_PATH": ua, "SL_LINKS_JSON_PATH": sl}
        self._old = {k: os.environ.get(k) for k in self._env}
        os.environ.update(self._env)

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()


class TestAllocatingTeams(_Base):
    def test_only_supervisors_and_managers(self):
        codes = at.allocating_team_codes()
        self.assertEqual(codes, {"SL100", "SL200", "SL300", "SL400"})

    def test_admin_only_row_never_becomes_team_none(self):
        """userpl 'none' + .upper() ตรง ๆ เคยได้ทีมผีชื่อ NONE"""
        codes = at.allocating_team_codes()
        self.assertNotIn("NONE", codes)
        self.assertNotIn("", codes)

    def test_demo_team_excluded(self):
        self.assertNotIn("SLDEMO1", at.allocating_team_codes())

    def test_marketing_has_no_team(self):
        self.assertNotIn("SL500", at.allocating_team_codes())

    def test_linked_codes_count_once(self):
        """SL900 ผูกกับ SL100 — เป็นทีมเดียว ไม่ใช่สองทีม"""
        codes = at.allocating_team_codes()
        self.assertNotIn("SL900", codes)
        self.assertIn("SL100", codes)
        self.assertEqual(len(at.allocating_teams()), 4)

    def test_manager_level_and_place_carried_through(self):
        by = {t["sup_id"]: t for t in at.allocating_teams()}
        self.assertEqual(by["SL300"]["manager_level"], "regional")
        self.assertEqual(by["SL300"]["login_kind"], "manager_acc")
        self.assertEqual(by["SL100"]["acc_region"], "เหนือ")
        self.assertEqual(by["SL100"]["acc_unit"], "credit")

    def test_none_sentinel_becomes_blank_not_the_word_none(self):
        """ผู้จัดการระดับ division ถูกบังคับให้ acc_region = 'none' ตอน canonicalize"""
        by = {t["sup_id"]: t for t in at.allocating_teams()}
        self.assertEqual(by["SL400"]["acc_region"], "")

    def test_sorted_by_code(self):
        codes = [t["sup_id"] for t in at.allocating_teams()]
        self.assertEqual(codes, sorted(codes))

    def test_emails_counted_separately_from_teams(self):
        """คนกับทีมเป็นคนละเลข — SL900 ถูกยุบเป็นทีมเดียวแต่เจ้าของยังเป็นอีกคน"""
        emails = at.allocating_emails()
        self.assertIn("old@x.com", emails)
        self.assertNotIn("admin@x.com", emails)
        self.assertNotIn("mkt@x.com", emails)
        self.assertEqual(len(emails), 5)


class TestPredicate(unittest.TestCase):
    def test_can_allocate_row(self):
        self.assertTrue(at.can_allocate_row(_row("a@x.com", "SL100")))
        self.assertTrue(at.can_allocate_row(_row("a@x.com", "SL100", kind="manager_acc")))
        self.assertFalse(at.can_allocate_row(_row("a@x.com", "SL100", kind="marketing")))
        self.assertFalse(at.can_allocate_row(_row("a@x.com", "none", kind="standard")))
        self.assertFalse(at.can_allocate_row(_row("a@x.com", "SLDEMO2")))
        self.assertFalse(at.can_allocate_row({}))
        self.assertFalse(at.can_allocate_row(None))


if __name__ == "__main__":
    unittest.main()
