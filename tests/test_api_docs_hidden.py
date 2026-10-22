"""
หน้าเอกสาร API ต้องไม่เปิดให้ทุกคนบน server (OPEN_ITEMS 10.6 · QA 21 ต.ค. 2026 L1)

เดิม /docs /redoc /openapi.json เปิดได้โดยไม่ล็อกอิน — ใครเข้า URL ระบบได้เห็นทั้ง 81 endpoint
พร้อมคำอธิบาย · ตอนนี้เปิดเฉพาะเครื่อง dev ที่ปิดล็อกอิน หรือเมื่อตั้ง ENABLE_API_DOCS=1
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend import app_factory  # noqa: E402


class TestApiDocsHidden(unittest.TestCase):
    def _app(self, env: str | None, auth_on: bool):
        envs = {} if env is None else {"ENABLE_API_DOCS": env}
        with mock.patch.dict(os.environ, envs), \
                mock.patch.object(app_factory.auth_entra, "auth_enabled", return_value=auth_on):
            if env is None:
                os.environ.pop("ENABLE_API_DOCS", None)
            return app_factory.create_app()

    def test_server_with_login_hides_docs_by_default(self):
        app = self._app(None, auth_on=True)
        self.assertIsNone(app.docs_url)
        self.assertIsNone(app.redoc_url)
        self.assertIsNone(app.openapi_url)

    def test_dev_machine_without_login_keeps_docs(self):
        app = self._app(None, auth_on=False)
        self.assertEqual(app.docs_url, "/docs")
        self.assertEqual(app.openapi_url, "/openapi.json")

    def test_env_can_force_either_way(self):
        self.assertEqual(self._app("1", auth_on=True).docs_url, "/docs")
        self.assertIsNone(self._app("0", auth_on=False).docs_url)


if __name__ == "__main__":
    unittest.main()
