"""Tests for admin supervisor team cache + Fabric refresh."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest import mock

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.core.paths import emp_cache_path  # noqa: E402
from backend.services import admin_team  # noqa: E402


class TestAdminTeam(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_cwd = os.getcwd()
        os.chdir(self._tmpdir.name)
        os.makedirs("data", exist_ok=True)

    def tearDown(self):
        os.chdir(self._orig_cwd)
        self._tmpdir.cleanup()

    def test_cache_hit_skips_fabric(self):
        path = emp_cache_path("SL330", 7, 2026)
        pd.DataFrame(
            [{"emp_id": "E001", "emp_name": "ทดสอบ", "super_code": "SL330"}]
        ).to_csv(path, index=False)

        with mock.patch.object(admin_team, "admin_team_cache_ttl_sec", return_value=900):
            with mock.patch.object(admin_team, "_fetch_from_fabric") as fetch:
                out = admin_team.load_supervisor_team(
                    "SL330", target_year=2026, target_month=7, force_refresh=False
                )
                fetch.assert_not_called()

        self.assertTrue(out["from_cache"])
        self.assertEqual(out["employee_count"], 1)
        self.assertEqual(out["employees"][0]["emp_id"], "E001")

    def test_force_refresh_calls_fabric(self):
        path = emp_cache_path("SL330", 7, 2026)
        pd.DataFrame([{"emp_id": "OLD", "emp_name": "เก่า", "super_code": "SL330"}]).to_csv(
            path, index=False
        )
        fresh = pd.DataFrame(
            [{"emp_id": "NEW", "emp_name": "ใหม่", "super_code": "SL330"}]
        )

        with mock.patch.object(admin_team, "admin_team_cache_ttl_sec", return_value=900):
            with mock.patch.object(
                admin_team, "_fetch_from_fabric", return_value=(fresh, "ชื่อซุป")
            ) as fetch:
                out = admin_team.load_supervisor_team(
                    "SL330", target_year=2026, target_month=7, force_refresh=True
                )
                fetch.assert_called_once_with("SL330")

        self.assertFalse(out["from_cache"])
        self.assertEqual(out["employees"][0]["emp_id"], "NEW")
        self.assertEqual(out["super_name"], "ชื่อซุป")

    def test_expired_cache_refreshes_from_fabric(self):
        path = emp_cache_path("SL330", 7, 2026)
        pd.DataFrame([{"emp_id": "E1", "emp_name": "A", "super_code": "SL330"}]).to_csv(
            path, index=False
        )
        import time

        old = time.time() - 5000
        os.utime(path, (old, old))
        fresh = pd.DataFrame(
            [{"emp_id": "E2", "emp_name": "B", "super_code": "SL330"}]
        )

        with mock.patch.object(admin_team, "admin_team_cache_ttl_sec", return_value=900):
            with mock.patch.object(
                admin_team, "_fetch_from_fabric", return_value=(fresh, "")
            ):
                out = admin_team.load_supervisor_team(
                    "SL330", target_year=2026, target_month=7, force_refresh=False
                )

        self.assertFalse(out["from_cache"])
        self.assertEqual(out["employees"][0]["emp_id"], "E2")


if __name__ == "__main__":
    unittest.main()
