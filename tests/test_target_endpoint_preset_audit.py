"""
เปลี่ยนปลายทางส่ง Target Sun ต้องถูกบันทึกลง audit log ว่าใครเปลี่ยนจากอะไรเป็นอะไร
(QA 21 ต.ค. 2026 — เดิมสลับ test/UAT/Production ได้ในคลิกเดียวและไม่มีร่องรอย)

ใช้ไฟล์ตั้งค่าชั่วคราว — ไม่แตะ config/app_runtime.json ของจริง
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.routers import admin as admin_router  # noqa: E402

ADMIN = {"email": "dev@example.test", "role": "dev", "is_admin": True}


class TestTargetEndpointPresetAudit(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(
            os.environ,
            {"APP_RUNTIME_SETTINGS_PATH": os.path.join(self._tmp.name, "app_runtime.json")},
        )
        self._env.start()
        self.calls = []
        self._audit = patch.object(
            admin_router, "_audit_admin", lambda *a, **k: self.calls.append((a, k))
        )
        self._audit.start()
        # ไม่ต้องล้าง cache payload จริงในเทสนี้
        self._cache = patch(
            "backend.services.employee_payload_cache.invalidate_employee_payload_cache",
            return_value=0,
        )
        self._cache.start()

    def tearDown(self):
        self._cache.stop()
        self._audit.stop()
        self._env.stop()
        self._tmp.cleanup()

    def _set(self, preset):
        return admin_router.admin_set_target_endpoints(
            admin_router.TargetEndpointPresetBody(preset=preset), admin=ADMIN
        )

    def test_switch_is_audited_with_before_and_after(self):
        self._set("uat")
        self._set("prod")
        self.assertEqual(len(self.calls), 2)
        args, kwargs = self.calls[-1]
        self.assertIs(args[0], ADMIN)
        self.assertEqual(args[1], "admin_target_endpoint_preset")
        self.assertEqual(kwargs["context"], {"before": "uat", "after": "prod"})
        self.assertEqual(kwargs["level"], "warning")

    def test_non_prod_switch_is_info(self):
        self._set("uat")
        _args, kwargs = self.calls[-1]
        self.assertEqual(kwargs["context"]["after"], "uat")
        self.assertEqual(kwargs["level"], "info")


if __name__ == "__main__":
    unittest.main()
