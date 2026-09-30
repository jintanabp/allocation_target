"""
เตือนเมื่อปลายทาง Target Sun กลับไปเป็นค่าตั้งต้น / อ่านกับส่งคนละระบบ (ผลตรวจ §2.2)

app_runtime.json ไม่อยู่ใน git — หาย/เสีย/เครื่องใหม่ = preset "test" (อ่าน Prod ส่ง UAT)
เดิมเงียบ (log แค่ warning) และตัวตรวจหลังส่งอ่านจาก Prod ทั้งที่ส่งไป UAT
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import app_runtime_settings as ars  # noqa: E402
from backend.services import lakehouse as lh  # noqa: E402
from backend.services import targetsun_endpoints as te  # noqa: E402


class TestDefaultSettingsWarning(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "app_runtime.json")
        self._env = patch.dict(os.environ, {"APP_RUNTIME_SETTINGS_PATH": self.path})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def test_missing_file_is_flagged_and_falls_back_to_test_preset(self):
        s = te.targetsun_endpoints_summary()
        self.assertEqual(s["settings_file_status"], "missing")
        self.assertEqual(s["using_default_settings"], "1")
        self.assertEqual(s["cross_env"], "1", "ค่าตั้งต้น test = อ่าน Prod ส่ง UAT")

    def test_corrupt_file_is_flagged(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(ars.settings_file_status(), "corrupt")
        self.assertEqual(te.targetsun_endpoints_summary()["using_default_settings"], "1")

    def test_saved_uat_preset_is_not_flagged(self):
        ars.set_target_endpoint_preset("uat")
        s = te.targetsun_endpoints_summary()
        self.assertEqual(s["using_default_settings"], "0")
        self.assertEqual(s["cross_env"], "0")
        self.assertEqual(s["manual_url_override"], "0")

    def test_manual_url_override_is_flagged(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write('{"target_endpoint_preset": "uat", "target_import_api_base": "https://x.test/spc"}')
        self.assertEqual(te.targetsun_endpoints_summary()["manual_url_override"], "1")

    def test_per_sku_readback_is_unverifiable_across_systems(self):
        with patch.object(te, "targetsun_endpoints_summary", return_value={"cross_env": "1"}):
            rb = lh.verify_after_send("SLA", 9, 2026, sent_by_sku={"A": 1}, emp_codes=["1"])
        self.assertEqual(rb, {"checked": False, "reason": "cross_env"})


class TestWarningsReachTheScreens(unittest.TestCase):
    def test_send_env_route_exposes_the_flag(self):
        import inspect

        from backend.routers import lakehouse as rl

        self.assertIn("using_default_settings", inspect.getsource(rl.get_send_environment))

    def test_frontend_shows_both_warnings(self):
        with open(os.path.join(REPO, "frontend", "app.js"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("j.using_default_settings", src)
        self.assertIn("data?.using_default_settings", src)
        self.assertIn("data?.manual_url_override", src)


if __name__ == "__main__":
    unittest.main()
