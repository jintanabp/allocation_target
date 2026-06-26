"""Tests for marketing role (admin team tab only)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import access_control as ac  # noqa: E402


class TestMarketingRole(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_cwd = os.getcwd()
        os.chdir(self._tmpdir.name)
        os.makedirs("config", exist_ok=True)
        ua = [
            {
                "email": "mkt@sahapat.co.th",
                "userpl": "MKT",
                "login_kind": "marketing",
                "can_import_targetsun": False,
            }
        ]
        self._ua_path = os.path.abspath("config/user_access.json")
        with open(self._ua_path, "w", encoding="utf-8") as f:
            json.dump(ua, f)
        ac.invalidate_user_access_cache()

    def tearDown(self):
        ac.invalidate_user_access_cache()
        os.chdir(self._orig_cwd)
        self._tmpdir.cleanup()

    def _env(self):
        return mock.patch.dict(
            os.environ,
            {"USER_ACCESS_JSON_PATH": self._ua_path},
            clear=False,
        )

    def test_is_marketing_email(self):
        with self._env():
            self.assertTrue(ac.is_marketing_email("mkt@sahapat.co.th"))
            self.assertFalse(ac.is_marketing_email("other@sahapat.co.th"))

    def test_build_context_marketing(self):
        with self._env():
            with mock.patch.object(ac, "parse_allocation_admin_emails", return_value=set()):
                ctx = ac.build_user_access_context("mkt@sahapat.co.th")
        self.assertTrue(ctx.get("is_marketing"))
        self.assertFalse(ctx.get("is_admin"))
        self.assertFalse(ctx.get("can_import_targetsun"))

    def test_role_label_marketing(self):
        role = ac.role_label_for_meta(
            {"login_kind": "marketing"},
            "MKT",
            set(),
            set(),
        )
        self.assertEqual(role, "marketing")


if __name__ == "__main__":
    unittest.main()
