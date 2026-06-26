"""Tests for admin data inventory — no secrets in output."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import admin_inventory  # noqa: E402


class TestAdminInventory(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_cwd = os.getcwd()
        os.chdir(self._tmpdir.name)
        os.makedirs("data", exist_ok=True)
        os.makedirs("config", exist_ok=True)
        with open("config/user_access.json", "w", encoding="utf-8") as f:
            json.dump([{"email": "a@x.com", "userpl": "SL330"}], f)

    def tearDown(self):
        os.chdir(self._orig_cwd)
        self._tmpdir.cleanup()

    def test_build_inventory_no_fabric_check(self):
        ua_path = os.path.abspath("config/user_access.json")
        with mock.patch.dict(
            os.environ,
            {
                "FABRIC_CLIENT_SECRET": "super-secret-value",
                "FABRIC_DATASET_ID": "ds-test",
                "USER_ACCESS_JSON_PATH": ua_path,
            },
            clear=False,
        ):
            inv = admin_inventory.build_data_inventory(check_fabric=False)
        text = admin_inventory.inventory_json_safe(inv)
        self.assertNotIn("super-secret", text)
        self.assertNotIn("access_token", text)
        self.assertEqual(inv["local_config"]["user_access_rows"], 1)
        self.assertIn("fabric", inv)
        self.assertNotIn("connection", inv["fabric"])

    def test_api_map_present(self):
        inv = admin_inventory.build_data_inventory(check_fabric=False)
        self.assertTrue(len(inv["api_map"]) >= 5)
        endpoints = [a["endpoint"] for a in inv["api_map"]]
        self.assertIn("GET /admin/supervisor-team", endpoints)


if __name__ == "__main__":
    unittest.main()
