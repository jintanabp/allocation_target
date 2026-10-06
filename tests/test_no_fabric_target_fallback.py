"""
ไม่ดึงเป้าจาก Fabric อีกแล้ว (ผู้ใช้ตัดสิน 6 ต.ค. 2026) — อ่าน Target Sun ไม่ได้ต้องบอกผู้ใช้ ไม่เดาจาก Fabric

เอกสาร IT เคยให้ตั้ง TARGETSUN_READ_FALLBACK_FABRIC=1 ใน .env — ค่านั้นต้องถูกเมิน (ไม่ต้องไปแก้ .env)
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from fastapi import HTTPException  # noqa: E402

from backend.services import targetsun_read as tsr  # noqa: E402


class TestNoFabricTargetFallback(unittest.TestCase):
    def test_env_flag_is_ignored(self):
        for v in ("1", "true", "", "0"):
            with self.subTest(v=v), patch.dict(os.environ, {"TARGETSUN_READ_FALLBACK_FABRIC": v}):
                self.assertFalse(tsr.fallback_to_fabric())

    def test_target_sun_failure_is_raised_not_swallowed(self):
        with patch.dict(os.environ, {"TARGETSUN_READ_FALLBACK_FABRIC": "1"}), \
             patch.object(tsr, "granular_df_for_team", side_effect=HTTPException(502, detail="down")):
            with self.assertRaises(HTTPException) as cm:
                tsr.try_granular_df_for_team(["E1"], 11, 2026, sup_id="SL1")
        self.assertEqual(cm.exception.status_code, 502)



class TestFabricSourceOptionRemoved(unittest.TestCase):
    """ผู้ใช้สั่งเอาตัวเลือก Fabric ออกจากหน้าแอดมิน (6 ต.ค. 2026)"""

    def test_stored_fabric_value_is_ignored(self):
        import json
        import tempfile

        from backend.services import app_runtime_settings as ars

        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "app_runtime.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"target_read_source": "fabric"}, f)
            with patch.dict(os.environ, {"APP_RUNTIME_SETTINGS_PATH": p}):
                self.assertEqual(ars.get_target_read_source(), "targetsun")
                with self.assertRaises(ValueError):
                    ars.set_target_read_source("fabric")

    def test_admin_page_has_no_fabric_choice(self):
        with open(os.path.join(REPO, "frontend", "index.html"), encoding="utf-8") as f:
            html = f.read()
        self.assertNotIn('name="adminTargetSource" value="fabric"', html)


if __name__ == "__main__":
    unittest.main()
