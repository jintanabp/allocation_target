"""Tests for peer write guard (same-group peers may write)."""

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

    def test_allows_peer_supervisor_in_allowed(self):
        """peer ในกลุ่มเดียวกัน (อยู่ใน allowed) เขียนได้"""
        user = {
            "auth_disabled": False,
            "allowed_supervisor_codes": {"SL397", "SL402"},
            "home_supervisor_codes": {"SL397"},
        }
        ensure_own_supervisor_write(user, "SL402")

    def test_blocks_supervisor_outside_allowed(self):
        user = {
            "auth_disabled": False,
            "allowed_supervisor_codes": {"SL397", "SL402"},
            "home_supervisor_codes": {"SL397"},
        }
        with self.assertRaises(HTTPException) as ctx:
            ensure_own_supervisor_write(user, "SL999")
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

    def test_manager_acc_not_dual_supervisor_pick(self):
        from backend.services.access_control import filter_managers_payload_for_user
        from backend.services.sl_link_store import manager_pick_label, read_links

        full = {
            "rows": [],
            "supervisors": ["SL508", "SL532"],
            "manager_codes": ["SL508"],
            "by_manager": {"SL508": ["SL508", "SL532"]},
        }
        user = {
            "userpls_supervisor_pick": set(),
            "userpls_manager_pick": {"SL508"},
            "allowed_supervisor_codes": {"SL508", "SL532"},
            "home_supervisor_codes": set(),
        }
        out = filter_managers_payload_for_user(full, user)
        labels = out.get("managers") or []
        sl_links = read_links()
        expected_mgr = manager_pick_label("SL508", sl_links)
        self.assertIn(expected_mgr, labels)
        self.assertNotIn("SL508 (Supervisor)", labels)
        self.assertEqual(out.get("by_manager", {}).get("SL508"), ["SL532"])


if __name__ == "__main__":
    unittest.main()
