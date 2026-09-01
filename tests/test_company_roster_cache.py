"""
แคชรายชื่อพนักงานทั้งบริษัท — ต้องทนตอน Fabric ล่ม และต้องไม่โดนล้างโดยไม่ตั้งใจ

สองอย่างที่กัดจริงถ้าพลาด:
  1. กด "รีเฟรช" ตอนปลายทางล่มแล้วของเดิมหาย → ตัวหารเป็น 0 → รายงานบอกว่า
     ไม่มีพนักงานในระบบเลย ซึ่งแย่กว่าการโชว์ข้อมูลเมื่อวานมาก
  2. ตั้งชื่อไฟล์ชนกับ prefix ที่ invalidate_period_cache จับ → กด "ล้างแคชงวด"
     ทีเดียวรายชื่อทั้งบริษัทหายไปด้วย

ไม่แตะเน็ต — ตัว connector ถูกแทนด้วยของปลอมทุกเทสต์
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

RAW = [
    {"emp_id": "s402", "emp_name": "สมชาย", "super_code": "sl397"},
    {"emp_id": "S420", "emp_name": "อารีย์", "super_code": "SL397"},
    {"emp_id": "V901", "emp_name": "รถเงินสด", "super_code": "SL397"},   # ต้องถูกตัด
    {"emp_id": "S402", "emp_name": "ซ้ำ", "super_code": "SL397"},        # ต้องถูกยุบ
    {"emp_id": "C410", "emp_name": "บุญมี", "super_code": "SL460"},
]


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = {k: os.environ.get(k) for k in ("FABRIC_CACHE_DIR", "FABRIC_STATIC_CACHE_TTL_SEC")}
        os.environ["FABRIC_CACHE_DIR"] = self._tmp.name

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def _fake_fetch(self, rows=RAW, boom=None):
        def _f():
            if boom:
                raise boom
            return cr._normalize(rows)
        return _f


class TestNormalize(unittest.TestCase):
    def test_upper_dedupe_and_drop_van(self):
        rows = cr._normalize(RAW)
        # เรียงตาม (ทีม, รหัส) — SL397 มาก่อน SL460
        self.assertEqual([r["emp_id"] for r in rows], ["S402", "S420", "C410"])
        self.assertEqual(rows[0]["super_code"], "SL397")

    def test_roster_by_team_keys_on_team(self):
        by = cr.roster_by_team(cr._normalize(RAW))
        self.assertEqual(by["SL397"], {"S402", "S420"})
        self.assertEqual(by["SL460"], {"C410"})


class TestCacheRoundTrip(_Base):
    def test_read_only_never_calls_fabric(self):
        """ไม่มีแคช = ตอบว่าไม่มี ไม่ใช่แอบไปดึง"""
        called = []
        orig = cr.fetch_company_roster
        cr.fetch_company_roster = lambda: called.append(1) or []
        try:
            got = cr.get_company_roster()
        finally:
            cr.fetch_company_roster = orig
        self.assertFalse(called)
        self.assertFalse(got["available"])
        self.assertEqual(got["row_count"], 0)

    def test_refresh_writes_and_reads_back(self):
        orig = cr.fetch_company_roster
        cr.fetch_company_roster = self._fake_fetch()
        try:
            got = cr.get_company_roster(refresh=True)
        finally:
            cr.fetch_company_roster = orig
        self.assertTrue(got["available"])
        self.assertEqual(got["row_count"], 3)
        self.assertIsNone(got["error"])
        self.assertFalse(got["stale"])
        # อ่านซ้ำแบบไม่ refresh ต้องได้ของเดิม
        again = cr.get_company_roster()
        self.assertEqual(again["row_count"], 3)

    def test_failed_refresh_keeps_previous_bytes(self):
        orig = cr.fetch_company_roster
        cr.fetch_company_roster = self._fake_fetch()
        try:
            cr.get_company_roster(refresh=True)
        finally:
            cr.fetch_company_roster = orig
        path = fc._roster_path()
        with open(path, "rb") as fh:
            before = fh.read()

        cr.fetch_company_roster = self._fake_fetch(boom=RuntimeError("capacity เต็ม"))
        try:
            got = cr.get_company_roster(refresh=True)
        finally:
            cr.fetch_company_roster = orig
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), before, "ไฟล์เดิมต้องไม่ถูกแตะเลย")
        self.assertTrue(got["available"])
        self.assertEqual(got["row_count"], 3)
        self.assertIn("capacity", got["error"])

    def test_failed_refresh_with_no_cache_reports_empty(self):
        orig = cr.fetch_company_roster
        cr.fetch_company_roster = self._fake_fetch(boom=RuntimeError("ล่ม"))
        try:
            got = cr.get_company_roster(refresh=True)
        finally:
            cr.fetch_company_roster = orig
        self.assertFalse(got["available"])
        self.assertEqual(got["rows"], [])
        self.assertIn("ล่ม", got["error"])


class TestStaleness(_Base):
    def test_expired_cache_is_usable_but_flagged(self):
        fc.write_salesman_roster(cr._normalize(RAW))
        os.environ["FABRIC_STATIC_CACHE_TTL_SEC"] = "0"
        self.assertIsNone(fc.read_salesman_roster(), "TTL 0 = ปิดแคช")
        os.environ["FABRIC_STATIC_CACHE_TTL_SEC"] = "-1"
        self.assertIsNone(fc.read_salesman_roster())
        # อายุติดลบไม่ได้ จำลองด้วยการย้อน cached_at แทน
        os.environ["FABRIC_STATIC_CACHE_TTL_SEC"] = "3600"
        path = fc._roster_path()
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["cached_at"] = "2020-01-01T00:00:00Z"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        got = fc.read_salesman_roster(allow_stale=True)
        self.assertIsNotNone(got)
        self.assertTrue(got["stale"])
        self.assertIsNone(fc.read_salesman_roster(allow_stale=False))


class TestNotSweptByPeriodInvalidation(_Base):
    def setUp(self):
        super().setUp()
        fc.write_salesman_roster(cr._normalize(RAW))
        fc.write_price_map(2026, 9, {"111294": 1050.0})

    def _roster_exists(self):
        return os.path.isfile(fc._roster_path())

    def test_period_invalidation_leaves_roster_alone(self):
        fc.invalidate_period_cache(2026, 9)
        self.assertTrue(self._roster_exists())

    def test_invalidate_everything_still_leaves_roster_alone(self):
        """ไม่ระบุงวด = ลบทุกไฟล์ที่ตรง prefix — รายชื่อต้องไม่อยู่ในนั้น"""
        fc.invalidate_period_cache()
        self.assertTrue(self._roster_exists())

    def test_roster_removed_only_when_named(self):
        removed = fc.invalidate_period_cache(layers={"roster"})
        self.assertEqual(removed, 1)
        self.assertFalse(self._roster_exists())

    def test_shows_up_in_cache_status_as_not_period_scoped(self):
        layers = {x["layer"]: x for x in fc.cache_status(2026, 9)}
        self.assertIn("roster", layers)
        self.assertFalse(layers["roster"]["period_scoped"])
        self.assertTrue(layers["roster"]["exists"])
        self.assertEqual(layers["roster"]["row_count"], 3)


if __name__ == "__main__":
    unittest.main()
