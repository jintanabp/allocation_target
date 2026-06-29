"""Tests for peer read-only write guard."""

from __future__ import annotations

import os
import sys
import unittest

from fastapi import HTTPException

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.deps import ensure_own_supervisor_write  # noqa: E402


class TestPeerWriteGuard(unittest.TestCase):
    def test_allows_home_supervisor(self):
        user = {
            "auth_disabled": False,
            "allowed_supervisor_codes": {"SL397", "SL402"},
            "home_supervisor_codes": {"SL397"},
        }
        ensure_own_supervisor_write(user, "SL397")

    def test_blocks_peer_supervisor(self):
        user = {
            "auth_disabled": False,
            "allowed_supervisor_codes": {"SL397", "SL402"},
            "home_supervisor_codes": {"SL397"},
        }
        with self.assertRaises(HTTPException) as ctx:
            ensure_own_supervisor_write(user, "SL402")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_allows_manager_empty_home(self):
        user = {
            "auth_disabled": False,
            "allowed_supervisor_codes": {"SL397", "SL402"},
            "home_supervisor_codes": set(),
        }
        ensure_own_supervisor_write(user, "SL402")
        ensure_own_supervisor_write(user, "SL397")


class TestLoginPickPeers(unittest.TestCase):
    """region_peers — หน้า login ต้องมีแค่รหัสตัวเอง ไม่รวม peer"""

    def test_supervisor_login_pick_excludes_peers(self):
        from backend.services.access_control import filter_managers_payload_for_user

        full = {
            "rows": [
                {"supervisor_code": "SL341", "depend_on": "NONE"},
                {"supervisor_code": "SL382", "depend_on": "NONE"},
            ],
            "supervisors": ["SL341", "SL382", "SL375"],
            "by_manager": {},
        }
        user = {
            "userpls_supervisor_pick": ["SL341"],
            "userpls_manager_pick": [],
            "allowed_supervisor_codes": {"SL341", "SL382", "SL375"},
            "home_supervisor_codes": {"SL341"},
        }
        out = filter_managers_payload_for_user(full, user)
        self.assertEqual(out["managers"], ["SL341 (Supervisor)"])
        self.assertIn("SL382", out["peer_supervisor_codes"])
        self.assertNotIn("SL382 (Supervisor)", out["managers"])


if __name__ == "__main__":
    unittest.main()
