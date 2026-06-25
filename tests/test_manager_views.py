"""Tests for manager aggregate view options."""

from __future__ import annotations

import os
import sys
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services.manager_views import (  # noqa: E402
    build_manager_view_options,
    is_division_wide_manager,
    resolve_aggregate_supervisor_codes,
)


class TestManagerViews(unittest.TestCase):
    def test_division_wide_modes(self):
        row = {
            "login_kind": "manager_acc",
            "acc_division": "Div.S",
            "acc_scope": "all",
            "acc_region": "",
        }
        self.assertTrue(is_division_wide_manager(row))
        opts = build_manager_view_options(
            "SL360",
            ["SL360", "SL350", "SL351"],
        )
        self.assertEqual(opts["scope_kind"], "division")
        self.assertIn("all", opts["modes"])
        self.assertIn("individual", opts["modes"])

    def test_regional_modes(self):
        row = {
            "login_kind": "manager_acc",
            "acc_division": "Div.B",
            "acc_scope": "all",
            "acc_region": "กลาง",
        }
        self.assertFalse(is_division_wide_manager(row))
        opts = build_manager_view_options("SL345", ["SL345", "SL401"])
        self.assertEqual(opts["scope_kind"], "region")
        self.assertEqual(opts["modes"], ["individual", "region"])
        self.assertNotIn("all", opts["modes"])

    def test_resolve_all(self):
        team = ["SL360", "SL350", "SL351"]
        codes = resolve_aggregate_supervisor_codes("SL360", team, "all")
        self.assertIn("SL350", codes)
        self.assertNotIn("SL360", codes)


if __name__ == "__main__":
    unittest.main()
