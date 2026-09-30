"""
FabricDAXConnector ใช้ MSAL app ร่วมกันทั้งโปรเซส (ผลตรวจ §5.1-7)

เดิมสร้าง MSAL app ใหม่ทุก connector (สร้างทุกคำขอ) token ที่ MSAL เก็บไว้ในตัว app จึงไม่เคย
ถูกใช้ซ้ำ และ atexit.register สะสมเพิ่มเรื่อย ๆ · ปลอม msal ทั้งหมด ไม่ต่อเน็ต
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend import fabric_dax_connector as f  # noqa: E402


class _FakeApp:
    def __init__(self, *a, **kw):
        self.kw = kw


class TestSharedMsal(unittest.TestCase):
    def setUp(self):
        f._MSAL_APPS.clear()
        f._USER_TOKEN_CACHES.clear()

    def tearDown(self):
        f._MSAL_APPS.clear()
        f._USER_TOKEN_CACHES.clear()

    def test_service_principal_reused(self):
        with patch.dict(os.environ, {"FABRIC_CLIENT_SECRET": "s1"}), \
                patch.object(f.msal, "ConfidentialClientApplication", side_effect=_FakeApp) as cca:
            a, b = f.FabricDAXConnector(), f.FabricDAXConnector()
        self.assertIs(a._cca, b._cca)
        self.assertEqual(cca.call_count, 1)

    def test_new_secret_new_app(self):
        with patch.object(f.msal, "ConfidentialClientApplication", side_effect=_FakeApp):
            with patch.dict(os.environ, {"FABRIC_CLIENT_SECRET": "s1"}):
                a = f.FabricDAXConnector()
            with patch.dict(os.environ, {"FABRIC_CLIENT_SECRET": "s2"}):
                b = f.FabricDAXConnector()
        self.assertIsNot(a._cca, b._cca)

    def test_user_login_registers_atexit_once(self):
        env = {k: v for k, v in os.environ.items() if k != "FABRIC_CLIENT_SECRET"}
        with patch.dict(os.environ, env, clear=True), \
                patch.object(f.msal, "PublicClientApplication", side_effect=_FakeApp), \
                patch.object(f.atexit, "register") as reg, \
                patch.object(f.os.path, "exists", return_value=False):
            a, b, c = f.FabricDAXConnector(), f.FabricDAXConnector(), f.FabricDAXConnector()
        self.assertIs(a._pca, c._pca)
        self.assertIs(a.cache, b.cache)
        self.assertEqual(reg.call_count, 1)


if __name__ == "__main__":
    unittest.main()
