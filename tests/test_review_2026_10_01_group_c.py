"""
ผลตรวจระบบ 1 ต.ค. 2026 กลุ่ม ค (เรื่องเล็ก — ผู้ใช้สั่งแก้ 1 ต.ค. 2026) · ออฟไลน์ ไม่แตะ Target Sun
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import threading
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.routers import admin as A  # noqa: E402
from backend.services import nightly_check as nc  # noqa: E402

TZ = ZoneInfo("Asia/Bangkok")


def _src(*p):
    with open(os.path.join(REPO, *p), encoding="utf-8") as f:
        return f.read()


APP = _src("frontend", "app.js")


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._t.name)
        os.makedirs("data", exist_ok=True)
        self._p = patch.object(nc, "_notify_dev")
        self.notify = self._p.start()

    def tearDown(self):
        self._p.stop()
        os.chdir(self._cwd)
        self._t.cleanup()


class TestNightlySmallFixes(_Tmp):
    def test_latest_diff_rejects_path_traversal(self):
        outside = os.path.join(self._t.name, "x_2026_10")
        os.makedirs(outside)
        with open(os.path.join(outside, "a.json"), "w") as f:
            f.write('{"secret": 1}')
        self.assertIsNone(nc.latest_diff("../../x", 10, 2026))
        self.assertIsNone(nc.latest_diff("..", 10, 2026))

    def test_settings_writes_do_not_lose_updates(self):
        def a():
            for _ in range(20):
                nc.write_settings(hour=3)

        def b():
            for _ in range(20):
                nc.write_settings(keep_months=9)

        ts = [threading.Thread(target=a), threading.Thread(target=b)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        s = nc.read_settings()
        self.assertEqual((s["hour"], s["keep_months"]), (3, 9))

    def test_scheduled_run_does_not_repeat_same_day(self):
        nc.write_settings(enabled=True)
        now = datetime(2026, 9, 20, 2, 5, tzinfo=TZ)
        nc.run_once(now=now, fetch=lambda *a: {"rows": [], "complete": True}, sleep=lambda s: None)
        self.assertEqual(nc.run_once(now=now, fetch=lambda *a: {"rows": [], "complete": True}),
                         {"skipped": "already_ran"})

    def test_lock_open_failure_is_reported_not_raised(self):
        nc.write_settings(enabled=True)
        with patch.object(nc, "_try_file_lock", side_effect=PermissionError("denied")):
            res = nc.run_once(now=datetime(2026, 9, 20, 2, 5, tzinfo=TZ), fetch=lambda *a: {})
        self.assertIn("lock", res["error"])
        self.notify.assert_called_once()

    def test_is_running_reflects_the_lock(self):
        self.assertFalse(nc.is_running())
        fh = nc._try_file_lock(os.path.join("data", ".nightly_check.lock"))
        try:
            self.assertTrue(nc.is_running())
        finally:
            fh.close()


class TestFrontendAndRouter(unittest.TestCase):
    def test_run_now_reports_busy(self):
        self.assertIn("if nightly_check.is_running():", _src("backend", "routers", "admin.py"))
        self.assertIn("r.started === false", APP)

    def test_nightly_columns_say_rows(self):
        html = _src("frontend", "index.html")
        self.assertIn("ถูกแก้ (แถว)", html)
        self.assertNotIn(">หีบเปลี่ยน<", html)

    def test_detail_panel_names_the_round(self):
        self.assertIn('d.round === "closing" ? " · รอบปิดงวด', APP)

    def test_brand_names_escaped(self):
        self.assertIsNone(re.search(r'value="\$\{b\}"', APP))
        self.assertNotIn('"🏷️ " + b}', APP)
        self.assertIn('"🏷️ " + escH(b)', APP)


class TestUsageLogScope(unittest.TestCase):
    items = [{"sup_id": "SL1", "email": "a@x.test"}, {"sup_id": "SL2", "email": "b@x.test"}]

    def test_role_without_scope_sees_nothing(self):
        self.assertEqual(A._filter_usage_items_for_admin({"role": "marketing"}, self.items), [])

    def test_dev_sees_everything(self):
        self.assertEqual(A._filter_usage_items_for_admin({"role": A.ROLE_DEV}, self.items), self.items)


if __name__ == "__main__":
    unittest.main()
