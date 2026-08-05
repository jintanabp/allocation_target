"""Tests for Target Sun read/import endpoint split."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import targetsun_endpoints as tse  # noqa: E402
from backend.services import app_runtime_settings as ars  # noqa: E402


class TestTargetSunEndpointSplit(unittest.TestCase):
    def test_code_defaults_read_uat_send_uat(self):
        read_b, import_b = tse._code_default_bases()
        self.assertIn("spcuatws", read_b)
        self.assertIn("spcuatws", import_b)
        self.assertEqual(read_b, import_b)

    def test_import_url_uses_import_base_not_read(self):
        with patch.object(tse, "resolve_endpoint_bases", return_value=(
            "https://spcws.sahapat.com/spc/targetsun",
            "https://spcuatws.sahapat.com/spc/targetsun",
        )):
            url = tse.targetsun_import_excel_url()
        self.assertTrue(url.startswith("https://spcuatws"))
        self.assertIn("importTargetSalesmanNextFromExcel", url)

    def test_cross_env_summary(self):
        with patch.object(tse, "resolve_endpoint_bases", return_value=(
            "https://spcws.sahapat.com/spc/targetsun",
            "https://spcuatws.sahapat.com/spc/targetsun",
        )):
            s = tse.targetsun_endpoints_summary()
        self.assertEqual(s["cross_env"], "1")
        self.assertEqual(s["read_host_label"], "Prod")
        self.assertEqual(s["import_host_label"], "UAT")

    def test_prod_preset_same_host(self):
        with patch("backend.services.app_runtime_settings.get_target_endpoint_config", return_value={
            "preset": "prod",
            "read_base": None,
            "import_base": None,
            "preset_stored": "prod",
        }):
            read_b, import_b = tse.resolve_endpoint_bases()
        self.assertEqual(read_b, import_b)
        self.assertIn("spcws", read_b)


class TestRuntimeEndpointPreset(unittest.TestCase):
    def test_set_preset_persists(self):
        """
        เทสต์นี้ "เขียนไฟล์จริง" ได้ถ้าเปลี่ยนเส้นทางไม่สำเร็จ — และค่าที่เขียนคือ
        preset ปลายทาง Target Sun ซึ่งตัดสินว่าข้อมูลจะถูกส่งไปที่ไหน

        จึงกันสองชั้น:
          1. ตั้ง APP_RUNTIME_SETTINGS_PATH (settings_json_path() เช็คตัวนี้เป็นอันดับแรก)
             — ได้ผลแม้โค้ดภายในจะเรียก settings_json_path() จากที่อื่น
          2. patch.object ทับอีกชั้นเผื่อ env ถูกอ่านไปแล้ว
        แล้ว assert ตอนท้ายว่าไฟล์จริงไม่ถูกแตะ
        """
        real_path = os.path.join(REPO, "config", "app_runtime.json")
        before = None
        if os.path.isfile(real_path):
            with open(real_path, encoding="utf-8") as f:
                before = f.read()

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "app_runtime.json")
            with patch.dict(os.environ, {"APP_RUNTIME_SETTINGS_PATH": path}):
                with patch.object(ars, "settings_json_path", return_value=path):
                    data = ars.set_target_endpoint_preset("uat")
                    self.assertEqual(data["target_endpoint_preset"], "uat")
                    cfg = ars.get_target_endpoint_config()
                    self.assertEqual(cfg["preset"], "uat")
            self.assertTrue(os.path.isfile(path), "ต้องเขียนลงไฟล์ชั่วคราว")

        if before is not None:
            with open(real_path, encoding="utf-8") as f:
                self.assertEqual(
                    f.read(), before,
                    "config/app_runtime.json ของจริงถูกแก้ระหว่างเทสต์ — "
                    "ห้ามเด็ดขาด เพราะเป็นค่าที่ชี้ปลายทางส่ง Target Sun",
                )


if __name__ == "__main__":
    unittest.main()
