"""Unit tests for Excel-based access hierarchy (no Fabric)."""

from __future__ import annotations

import os
import sys
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services.access_hierarchy import (  # noqa: E402
    build_hierarchy_payload,
    compute_visible_supervisors_for_row,
    enrich_rows_with_visibility,
    normalize_div_s_region,
    parse_div_s_scope,
    parse_role_from_position,
)


def _sample_roster() -> list[dict]:
    return [
        {
            "email": "tanat.t@sahapat.co.th",
            "userpl": "SL360",
            "acc_division": "Div.S",
            "login_kind": "manager_acc",
            "acc_scope": "all",
            "acc_region": "",
        },
        {
            "email": "sup1@sahapat.co.th",
            "userpl": "SL314",
            "acc_division": "Div.S",
            "login_kind": "supervisor_acc",
            "acc_scope": "credit",
            "acc_unit": "credit",
            "acc_region": "กลาง",
        },
        {
            "email": "sup2@sahapat.co.th",
            "userpl": "SL358",
            "acc_division": "Div.S",
            "login_kind": "supervisor_acc",
            "acc_scope": "van",
            "acc_unit": "van",
            "acc_region": "กรุงเทพ",
        },
        {
            "email": "pakpoom.k@sahapat.co.th",
            "userpl": "SL345",
            "acc_division": "Div.B",
            "login_kind": "manager_acc",
            "acc_scope": "all",
            "acc_region": "กลาง",
        },
        {
            "email": "supb@sahapat.co.th",
            "userpl": "SL401",
            "acc_division": "Div.B",
            "login_kind": "supervisor_acc",
            "acc_scope": "self",
            "acc_unit": "credit",
            "acc_region": "กลาง",
        },
        {
            "email": "supb2@sahapat.co.th",
            "userpl": "SL402",
            "acc_division": "Div.B",
            "login_kind": "supervisor_acc",
            "acc_scope": "self",
            "acc_unit": "van",
            "acc_region": "เหนือ",
        },
    ]


class TestDivSParse(unittest.TestCase):
    def test_normalize_regions(self):
        self.assertEqual(normalize_div_s_region("BKK"), "กรุงเทพ")
        self.assertEqual(normalize_div_s_region("Central"), "กลาง")
        self.assertEqual(normalize_div_s_region("Div.S"), "")

    def test_parse_scope(self):
        self.assertEqual(parse_div_s_scope("All"), ("manager_acc", "all", ""))
        self.assertEqual(parse_div_s_scope("Credit All"), ("supervisor_acc", "credit", "credit"))
        self.assertEqual(parse_div_s_scope("Van All"), ("supervisor_acc", "van", "van"))
        self.assertIsNone(parse_div_s_scope("2024-01-01"))


class TestBEParse(unittest.TestCase):
    def test_manager_position(self):
        lk, unit, scope = parse_role_from_position("ผช.ผจก.ภาคกลาง")
        self.assertEqual(lk, "manager_acc")
        self.assertEqual(scope, "all")

    def test_supervisor_credit(self):
        lk, unit, scope = parse_role_from_position("ซุปเครดิตภาคกลาง")
        self.assertEqual(lk, "supervisor_acc")
        self.assertEqual(unit, "credit")
        self.assertEqual(scope, "self")


class TestVisibleSupervisors(unittest.TestCase):
    def setUp(self):
        self.roster = _sample_roster()
        self.enriched = enrich_rows_with_visibility(self.roster)

    def test_sl360_sees_all_div_s_supervisors(self):
        row = next(r for r in self.enriched if r["userpl"] == "SL360")
        vis = set(row["visible_supervisor_codes"])
        self.assertIn("SL360", vis)
        self.assertIn("SL314", vis)
        self.assertIn("SL358", vis)
        self.assertNotIn("SL401", vis)

    def test_sl345_sees_only_div_b_central(self):
        row = next(r for r in self.enriched if r["userpl"] == "SL345")
        vis = set(row["visible_supervisor_codes"])
        self.assertEqual(vis, {"SL345", "SL401"})

    def test_supervisor_sees_self_only(self):
        row = next(r for r in self.enriched if r["userpl"] == "SL314")
        self.assertEqual(row["visible_supervisor_codes"], ["SL314"])

    def test_sl330_force_supervisor_bkk(self):
        rows = [
            {
                "userpl": "SL330",
                "acc_division": "Div.S",
                "login_kind": "manager_acc",
                "acc_scope": "all",
                "acc_region": "กรุงเทพ",
            },
            {
                "userpl": "SL384",
                "acc_division": "Div.S",
                "login_kind": "manager_acc",
                "acc_scope": "all",
                "acc_region": "กรุงเทพ",
            },
        ]
        enriched = enrich_rows_with_visibility(rows)
        sl330 = next(r for r in enriched if r["userpl"] == "SL330")
        sl384 = next(r for r in enriched if r["userpl"] == "SL384")
        self.assertEqual(sl330["login_kind"], "supervisor_acc")
        self.assertEqual(sl330["visible_supervisor_codes"], ["SL330"])
        self.assertIn("SL330", sl384["visible_supervisor_codes"])

    def test_div_s_regional_manager(self):
        rows = [
            {
                "userpl": "SL500",
                "acc_division": "Div.S",
                "login_kind": "manager_acc",
                "acc_scope": "all",
                "acc_region": "กลาง",
            },
            {
                "userpl": "SL501",
                "acc_division": "Div.S",
                "login_kind": "supervisor_acc",
                "acc_scope": "credit",
                "acc_region": "กลาง",
            },
            {
                "userpl": "SL502",
                "acc_division": "Div.S",
                "login_kind": "supervisor_acc",
                "acc_scope": "van",
                "acc_region": "กรุงเทพ",
            },
        ]
        vis = compute_visible_supervisors_for_row(rows[0], all_rows=rows)
        self.assertEqual(vis, ["SL500", "SL501"])

    def test_hierarchy_by_manager(self):
        payload = build_hierarchy_payload(self.enriched)
        self.assertEqual(payload["source"], "excel_roster")
        self.assertIn("SL360", payload["manager_codes"])
        bm = payload["by_manager"]
        self.assertIn("SL360", bm)
        team = set(bm["SL360"])
        self.assertIn("SL314", team)
        self.assertIn("SL358", team)


if __name__ == "__main__":
    unittest.main()
