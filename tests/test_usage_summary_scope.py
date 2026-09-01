"""
ขอบเขตของผู้ดูแลกับรายงานสรุปการใช้งาน

ข้อสำคัญด้านความปลอดภัย: **ตัวหารต้องหดตามขอบเขตด้วย ไม่ใช่หดแค่ตัวเศษ**
ถ้ากรองแต่ตัวเศษ แอดมินรายภาคจะเห็น "ทีมทั้งหมดในบริษัท 90 ทีม" และ
"พนักงานทั้งบริษัท 4,000 คน" ซึ่งเป็นข้อมูลนอกขอบเขตที่เขาไม่มีสิทธิ์รู้
— และเปอร์เซ็นต์ก็ผิดไปด้วยเพราะเทียบคนละฐาน
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import company_roster as cr  # noqa: E402
from backend.services import fabric_cache as fc  # noqa: E402
from backend.services import usage_summary as us  # noqa: E402

M, Y = 9, 2026

USERS = [
    {"email": "n1@x.com", "userpl": "SL100", "login_kind": "supervisor_acc",
     "acc_region": "เหนือ", "acc_division": "Div.B", "full_name": "ทีมเหนือ 1"},
    {"email": "n2@x.com", "userpl": "SL200", "login_kind": "supervisor_acc",
     "acc_region": "เหนือ", "acc_division": "Div.B", "full_name": "ทีมเหนือ 2"},
    {"email": "s1@x.com", "userpl": "SL800", "login_kind": "supervisor_acc",
     "acc_region": "ใต้", "acc_division": "Div.B", "full_name": "ทีมใต้"},
]

ROSTER = [
    {"emp_id": "E1", "emp_name": "หนึ่ง", "super_code": "SL100"},
    {"emp_id": "E2", "emp_name": "สอง", "super_code": "SL100"},
    {"emp_id": "E3", "emp_name": "สาม", "super_code": "SL200"},
    {"emp_id": "E4", "emp_name": "สี่", "super_code": "SL800"},
    {"emp_id": "E5", "emp_name": "ห้า", "super_code": "SL800"},
]

NORTH = {"SL100", "SL200"}


def _snap(sup, rows):
    return {
        "sup_id": sup, "target_month": M, "target_year": Y, "status": "optimized",
        "allocations": [{"emp_id": e, "sku": "S1", "allocated_boxes": b} for e, b in rows],
        "updated_at": "2026-09-01T03:00:00+00:00", "updated_by": f"{sup.lower()}@x.com",
        "target_sun_sent_at": "2026-09-02T04:00:00+00:00",
    }


class TestScope(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = self._tmp.name
        allocs = os.path.join(root, "allocations")
        cache = os.path.join(root, "cache")
        logs = os.path.join(root, "logs")
        for d in (allocs, cache, logs):
            os.makedirs(d)
        ua = os.path.join(root, "user_access.json")
        sl = os.path.join(root, "sl_links.json")
        with open(ua, "w", encoding="utf-8") as fh:
            json.dump(USERS, fh, ensure_ascii=False)
        with open(sl, "w", encoding="utf-8") as fh:
            json.dump({"links": []}, fh)
        self._env = {
            "ALLOCATIONS_DATA_DIR": allocs, "FABRIC_CACHE_DIR": cache,
            "USAGE_LOGS_DIR": logs, "USER_ACCESS_JSON_PATH": ua, "SL_LINKS_JSON_PATH": sl,
        }
        self._old = {k: os.environ.get(k) for k in self._env}
        os.environ.update(self._env)

        for sup, rows in (("SL100", [("E1", 5)]), ("SL800", [("E4", 9), ("E5", 3)])):
            doc = _snap(sup, rows)
            with open(os.path.join(allocs, f"{sup}_{Y}_{M:02d}.json"), "w", encoding="utf-8") as fh:
                json.dump(doc, fh, ensure_ascii=False)
        fc.write_salesman_roster(cr._normalize(ROSTER))

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def _s(self, codes=None):
        return us.build_usage_summary(month=M, year=Y, sl_codes=codes, force=True)

    def test_dev_sees_everything(self):
        s = self._s()
        self.assertFalse(s["scope"]["scoped"])
        self.assertEqual(s["teams"]["total"], 3)
        self.assertEqual(s["employees"]["total"], 5)
        self.assertEqual(s["employees"]["allocated"], 3)      # E1 + E4 + E5

    def test_denominator_shrinks_too_not_just_the_numerator(self):
        s = self._s(NORTH)
        self.assertTrue(s["scope"]["scoped"])
        self.assertEqual(s["teams"]["total"], 2, "ตัวหารระดับทีมต้องหด")
        self.assertEqual(s["employees"]["total"], 3, "ตัวหารระดับคนต้องหดด้วย")
        self.assertEqual(s["employees"]["allocated"], 1)      # เฉพาะ E1 ของ SL100

    def test_out_of_scope_team_appears_nowhere(self):
        s = self._s(NORTH)
        self.assertNotIn("SL800", {t["sup_id"] for t in s["teams_detail"]})
        self.assertNotIn("ใต้", {r["region"] for r in s["by_region"]})
        self.assertNotIn("SL800", {r["sup_id"] for r in us.team_rows(s)})

    def test_out_of_scope_employees_are_not_silently_added_elsewhere(self):
        """SL800 อยู่นอกขอบเขต — คนของเขาต้องไม่ไปโผล่ในช่อง 'สังกัดรหัสที่กระจายไม่ได้'
        แบบที่ทำให้ยอดคนทั้งหมดยังเท่าเดิม"""
        s = self._s(NORTH)
        self.assertEqual(s["employees"]["not_under_allocating_team"], 2)   # E4, E5
        self.assertEqual(s["employees"]["total"], 3)

    def test_percentages_use_the_scoped_base(self):
        s = self._s(NORTH)
        self.assertEqual(s["teams"]["used_pct"], 50.0)        # SL100 จาก 2 ทีม
        self.assertAlmostEqual(s["employees"]["allocated_pct"], 33.3, places=1)

    def test_scope_note_explains_the_manager_gap(self):
        self.assertEqual(self._s()["scope"]["note"], "")
        self.assertIn("ผู้จัดการ", self._s(NORTH)["scope"]["note"])

    def test_empty_scope_is_empty_not_everything(self):
        """ขอบเขตว่าง = ไม่มีอะไรให้ดู ต้องไม่ตกไปเป็น 'เห็นทั้งระบบ'"""
        s = self._s(set())
        self.assertTrue(s["scope"]["scoped"])
        self.assertEqual(s["teams"]["total"], 0)
        self.assertEqual(s["employees"]["total"], 0)
        self.assertIsNone(s["teams"]["used_pct"], "ตัวหาร 0 = ไม่มีอะไรให้เทียบ ไม่ใช่ 0%")

    def test_cache_is_shared_and_never_keyed_by_admin(self):
        """แคชเก็บข้อมูลดิบแบบไม่กรอง แล้วค่อยกรองในหน่วยความจำ — ขอบเขตจึงไม่รั่ว"""
        us.build_usage_summary(month=M, year=Y, sl_codes=NORTH)
        wide = us.build_usage_summary(month=M, year=Y)
        self.assertTrue(wide["cached"])
        self.assertEqual(wide["teams"]["total"], 3)
        narrow = us.build_usage_summary(month=M, year=Y, sl_codes=NORTH)
        self.assertTrue(narrow["cached"])
        self.assertEqual(narrow["teams"]["total"], 2)


if __name__ == "__main__":
    unittest.main()
