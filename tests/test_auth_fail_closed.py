"""
ล็อกอินต้องไม่ถูกปิดเองเมื่อ config ไม่ครบ (ผลตรวจ 28 ก.ย. 2026 §1.4)

เดิม: ขาด AZURE_AUTH_CLIENT_ID/tenant → auth_enabled() = False เงียบ ๆ →
ทุกคนที่เข้า URL ได้เป็น dev รวมสิทธิ์ส่ง Target Sun
ผู้ใช้ตัดสิน 29 ก.ย.: ปิดล็อกอินได้เฉพาะเครื่อง dev (localhost) ไม่ต้องแก้ .env บน server
"""

from __future__ import annotations

import inspect
import os
import sys
import unittest
from unittest.mock import patch

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend import auth_entra  # noqa: E402

_FULL = {"AZURE_AUTH_CLIENT_ID": "cid", "AZURE_AUTH_TENANT_ID": "tid", "AZURE_AUTH_DISABLED": ""}
_UVICORN = ["python", "-m", "uvicorn", "backend.main:app"]


class TestStartupCheck(unittest.TestCase):
    def _env(self, **kv):
        env = {k: "" for k in ("AZURE_AUTH_CLIENT_ID", "AZURE_AUTH_TENANT_ID", "FABRIC_TENANT_ID", "AZURE_AUTH_DISABLED")}
        env.update(kv)
        return patch.dict(os.environ, env)

    def test_full_config_starts(self):
        with self._env(**_FULL):
            auth_entra.check_auth_config_at_startup(_UVICORN + ["--host", "0.0.0.0"])

    def test_missing_client_id_refuses_to_start(self):
        with self._env(AZURE_AUTH_TENANT_ID="tid"):
            with self.assertRaises(RuntimeError) as ctx:
                auth_entra.check_auth_config_at_startup(_UVICORN + ["--host", "0.0.0.0"])
        self.assertIn("AZURE_AUTH_CLIENT_ID", str(ctx.exception))

    def test_missing_tenant_refuses_to_start(self):
        with self._env(AZURE_AUTH_CLIENT_ID="cid"):
            with self.assertRaises(RuntimeError):
                auth_entra.check_auth_config_at_startup(_UVICORN)

    def test_explicit_disable_on_localhost_is_allowed(self):
        with self._env(AZURE_AUTH_DISABLED="1"):
            auth_entra.check_auth_config_at_startup(_UVICORN + ["--host", "127.0.0.1"])
            auth_entra.check_auth_config_at_startup(_UVICORN)  # uvicorn ค่าเริ่มต้น = 127.0.0.1
            auth_entra.check_auth_config_at_startup(_UVICORN + ["--host=localhost"])

    def test_explicit_disable_on_a_public_host_refuses_to_start(self):
        with self._env(AZURE_AUTH_DISABLED="1"):
            with self.assertRaises(RuntimeError):
                auth_entra.check_auth_config_at_startup(_UVICORN + ["--host", "0.0.0.0"])

    def test_runs_first_in_app_startup(self):
        from backend import app_factory

        src = inspect.getsource(app_factory.create_app)
        life = src[src.index("async def lifespan"):]
        self.assertLess(
            life.index("check_auth_config_at_startup()"), life.index("os.makedirs"),
            "ต้องตรวจก่อนสตาร์ทงานอื่นที่เขียนไฟล์",
        )


if __name__ == "__main__":
    unittest.main()
