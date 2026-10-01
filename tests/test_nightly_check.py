"""
เฟส F3: ตรวจรายคืนว่ามีใครแก้เป้าใน Target Sun หลังส่ง — ออฟไลน์ล้วน (Target Sun ปลอม)
ห้ามต่อ Target Sun จริง: ทุกเทสต์ส่ง fetch ปลอมเข้า run_once
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import nightly_check as nc  # noqa: E402
from backend.services import sent_ledger as sl  # noqa: E402

TZ = ZoneInfo("Asia/Bangkok")


def _row(sku, emp, wh, qty):
    return {"PRODUCTCODE": sku, "SALESTYPE": "S", "DIVISIONCODE": "B", "SALESMANCODE": emp,
            "AREACODE": "10", "PROVINCECODE": "P1", "WAREHOUSECODE": wh, "QUANTITYCASE": qty}


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data", exist_ok=True)
        self._p = patch.object(nc, "_notify_dev")
        self.notify = self._p.start()

    def tearDown(self):
        self._p.stop()
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _send(self):
        sl.record_send("SLA", 10, 2026, [_row("A", "E1", "", 5), _row("B", "E2", "W1", 3)], send_status="ok")

    def _fetch(self, rows, complete=True):
        calls = []

        def f(year, month, codes):
            calls.append((year, month, tuple(codes)))
            return {"rows": rows, "complete": complete}
        f.calls = calls
        return f


class TestCompare(_Base):
    def test_changed_missing_extra(self):
        self._send()
        led = sl.read_ledger("SLA", 10, 2026)["rows"]
        from backend.services.lakehouse import _live_target_row_key as k
        live = {k(_row("A", "E1", "", 9)): 9, k(_row("A", "E1", "W7", 2)): 2, k(_row("Z", "E1", "", 4)): 4}
        d = nc.compare(led, live)
        self.assertEqual([(x["sent"], x["now"]) for x in d["changed"]], [(5, 9)])
        self.assertEqual([x["sent"] for x in d["missing"]], [3])
        self.assertEqual([x["now"] for x in d["extra"]], [2], "สินค้า Z ไม่เคยส่ง ไม่นับ")


class TestRunOnce(_Base):
    NOW = datetime(2026, 9, 20, 2, 5, tzinfo=TZ)

    def test_disabled_by_default_does_nothing(self):
        self._send()
        f = self._fetch([])
        self.assertEqual(nc.run_once(now=self.NOW, fetch=f), {"skipped": "disabled"})
        self.assertEqual(f.calls, [], "ค่าตั้งต้นปิด = ห้ามอ่าน Target Sun")
        self.assertFalse(nc.read_settings()["enabled"])

    def test_detects_edit_after_send(self):
        self._send()
        nc.write_settings(enabled=True)
        f = self._fetch([_row("A", "E1", "", 9), _row("B", "E2", "W1", 3)])
        res = nc.run_once(now=self.NOW, fetch=f, sleep=lambda s: None)
        self.assertEqual(res["errors"], 0)
        st = nc.read_state()["teams"]["SLA|2026-10"]
        self.assertEqual((st["status"], st["changed"], st["boxes_changed"]), ("ok", 1, 4))
        self.assertEqual(f.calls, [(2026, 10, ("E1", "E2"))])
        self.assertEqual(nc.latest_diff("SLA", 10, 2026)["changed"][0]["now"], 9)

    def test_days_1_to_14_skipped(self):
        self._send()
        nc.write_settings(enabled=True)
        f = self._fetch([])
        nc.run_once(now=datetime(2026, 9, 5, 2, 0, tzinfo=TZ), fetch=f)
        self.assertEqual(f.calls, [])
        self.assertEqual(nc.read_state()["teams"]["SLA|2026-10"]["reason"], "table_not_ready")

    def test_past_period_not_read(self):
        self._send()
        nc.write_settings(enabled=True)
        f = self._fetch([])
        nc.run_once(now=datetime(2026, 10, 20, 2, 0, tzinfo=TZ), fetch=f)
        self.assertEqual(f.calls, [], "งวด 10 เริ่มแล้ว ตาราง 'งวดถัดไป' ไม่มีแถวงวดนี้")

    def test_incomplete_read_is_error_and_notifies_dev(self):
        self._send()
        nc.write_settings(enabled=True)
        res = nc.run_once(now=self.NOW, fetch=self._fetch([_row("A", "E1", "", 5)], complete=False))
        self.assertEqual(res["errors"], 1)
        self.notify.assert_called_once()

    def test_empty_table_not_flagged(self):
        self._send()
        nc.write_settings(enabled=True)
        nc.run_once(now=self.NOW, fetch=self._fetch([]))
        self.assertEqual(nc.read_state()["teams"]["SLA|2026-10"]["reason"], "empty_table")
        self.notify.assert_not_called()

    def test_cross_env_skips_without_reading(self):
        self._send()
        nc.write_settings(enabled=True)
        with patch("backend.services.targetsun_endpoints.targetsun_endpoints_summary",
                   return_value={"cross_env": "1"}):
            self.assertEqual(nc.run_once(now=self.NOW), {"skipped": "cross_env"})

    def test_busy_lock_skips_without_reading(self):
        """ล็อกไฟล์ถูกถือโดยอีกโปรเซส (กลไกล็อกทดสอบข้ามโปรเซสแล้วใน test_runtime_checks)"""
        self._send()
        nc.write_settings(enabled=True)
        f = self._fetch([])
        with patch.object(nc, "_try_file_lock", return_value=None):
            self.assertEqual(nc.run_once(now=self.NOW, fetch=f), {"skipped": "busy"})
        self.assertEqual(f.calls, [])

    def test_settings_validated(self):
        with self.assertRaises(ValueError):
            nc.write_settings(hour=25)

    def test_closing_hour_saved_and_validated(self):
        self.assertEqual(nc.read_settings()["closing_hour"], 23)
        self.assertEqual(nc.write_settings(closing_hour=21)["closing_hour"], 21)
        self.assertEqual(nc.read_settings()["closing_hour"], 21)
        with self.assertRaises(ValueError):
            nc.write_settings(closing_hour=24)

    def test_admin_page_has_the_closing_hour_field(self):
        root = os.path.join(os.path.dirname(__file__), "..")
        html = open(os.path.join(root, "frontend", "index.html"), encoding="utf-8").read()
        js = open(os.path.join(root, "frontend", "app.js"), encoding="utf-8").read()
        self.assertIn('id="adminNightlyClosingHour"', html)
        self.assertIn("closing_hour: closingHour", js)
        api = open(os.path.join(root, "backend", "routers", "admin.py"), encoding="utf-8").read()
        self.assertIn("closing_hour=body.closing_hour", api)


class TestScheduler(_Base):
    def test_due_only_when_enabled_at_hour_once_a_day(self):
        s = {**nc.DEFAULTS, "enabled": True, "hour": 2}
        now = datetime(2026, 9, 20, 2, 30, tzinfo=TZ)
        self.assertTrue(nc._due(s, {}, now))
        self.assertFalse(nc._due(s, {"last_run_date": "2026-09-20"}, now))
        self.assertFalse(nc._due({**s, "enabled": False}, {}, now))
        self.assertFalse(nc._due(s, {}, now.replace(hour=3)))

    def test_closing_due_only_on_last_day_at_closing_hour_once(self):
        s = {**nc.DEFAULTS, "enabled": True}
        last = datetime(2026, 9, 30, 23, 10, tzinfo=TZ)
        self.assertTrue(nc._closing_due(s, {}, last))
        self.assertTrue(nc._closing_due(s, {"last_run_date": "2026-09-30"}, last), "รอบตี 2 ไม่กันรอบปิดงวด")
        self.assertFalse(nc._closing_due(s, {"last_closing_date": "2026-09-30"}, last))
        self.assertFalse(nc._closing_due(s, {}, last.replace(day=29)))
        self.assertFalse(nc._closing_due(s, {}, last.replace(hour=22)))
        self.assertFalse(nc._closing_due({**s, "enabled": False}, {}, last))
        self.assertTrue(nc._closing_due(s, {}, datetime(2028, 2, 29, 23, 0, tzinfo=TZ)), "ก.พ. ปีอธิกสุรทิน")
        self.assertFalse(nc._closing_due(s, {}, datetime(2028, 2, 28, 23, 0, tzinfo=TZ)))


class TestClosingRound(_Base):
    def test_closing_round_saved_separately_and_becomes_latest(self):
        self._send()
        nc.write_settings(enabled=True)
        nc.run_once(now=datetime(2026, 9, 30, 2, 5, tzinfo=TZ),
                    fetch=self._fetch([_row("A", "E1", "", 5), _row("B", "E2", "W1", 3)]), sleep=lambda s: None)
        res = nc.run_once(now=datetime(2026, 9, 30, 23, 5, tzinfo=TZ),
                          fetch=self._fetch([_row("A", "E1", "", 7), _row("B", "E2", "W1", 3)]),
                          sleep=lambda s: None, closing=True)
        self.assertEqual(res.get("round"), "closing")
        d = os.path.join("data", "ts_nightly", "SLA_2026_10")
        self.assertEqual(sorted(os.listdir(d)), ["2026-09-30.json", "2026-09-30_close.json"])
        latest = nc.latest_diff("SLA", 10, 2026)
        self.assertEqual((latest["round"], latest["changed"][0]["now"]), ("closing", 7))
        st = nc.read_state()
        self.assertEqual((st["last_run_date"], st["last_closing_date"]), ("2026-09-30", "2026-09-30"))


if __name__ == "__main__":
    unittest.main()
