"""Unit tests — SL link store (alias → canonical for access)"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from backend.services import sl_link_store as sls


class TestSlLinks(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp()
        self._path = os.path.join(self._tmpdir, "sl_links.json")
        os.environ["SL_LINKS_JSON_PATH"] = self._path

    def tearDown(self) -> None:
        os.environ.pop("SL_LINKS_JSON_PATH", None)

    def _write(self, links: list[dict]) -> None:
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump({"links": links}, f)

    def test_resolve_to_canonical(self) -> None:
        links = [
            {
                "canonical_sl": "SL508",
                "alias_sls": ["SL508", "SL524"],
                "note": "",
            }
        ]
        self._write(links)
        self.assertEqual(sls.resolve_to_canonical("SL524"), "SL508")
        self.assertEqual(sls.resolve_to_canonical("SL508"), "SL508")
        self.assertEqual(sls.resolve_to_canonical("SL999"), "SL999")

    def test_expand_sl_codes(self) -> None:
        self._write(
            [{"canonical_sl": "SL508", "alias_sls": ["SL508", "SL524"], "note": ""}]
        )
        expanded = sls.expand_sl_codes({"SL524", "SL532"})
        self.assertIn("SL508", expanded)
        self.assertIn("SL524", expanded)
        self.assertIn("SL532", expanded)

    def test_validate_duplicate_alias(self) -> None:
        links = [
            {"canonical_sl": "SL508", "alias_sls": ["SL508", "SL524"]},
            {"canonical_sl": "SL510", "alias_sls": ["SL510", "SL524"]},
        ]
        with self.assertRaises(ValueError):
            sls.validate_links(links)

    def test_upsert_and_delete(self) -> None:
        self._write([])
        saved = sls.upsert_link(
            [],
            canonical_sl="SL508",
            alias_sls=["SL508", "SL524"],
            note="ทดสอบ",
            updated_by="test@example.com",
        )
        self.assertEqual(len(saved), 1)
        self.assertEqual(sls.find_link("SL508", saved)["canonical_sl"], "SL508")
        after = sls.delete_link(saved, "SL508")
        self.assertEqual(after, [])


class TestSlLinkAccess(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp()
        self._sl_path = os.path.join(self._tmpdir, "sl_links.json")
        os.environ["SL_LINKS_JSON_PATH"] = self._sl_path
        with open(self._sl_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "links": [
                        {
                            "canonical_sl": "SL508",
                            "alias_sls": ["SL508", "SL524"],
                            "note": "",
                        }
                    ]
                },
                f,
            )

    def tearDown(self) -> None:
        os.environ.pop("SL_LINKS_JSON_PATH", None)

    def test_compute_allowed_merges_canonical_row(self) -> None:
        from backend.services.access_control import compute_allowed_supervisor_codes

        acc_rows = [
            {"email": "thanit.l@sahapat.co.th", "userpl": "SL524"},
            {"email": "thanit.l@sahapat.co.th", "userpl": "SL508"},
        ]
        # Patch read_rows via monkeypatching is heavy; pass meta through full_rows in function
        # compute_allowed uses read_rows internally — set USER_ACCESS path or mock
        from unittest.mock import patch

        full_rows = [
            {
                "email": "thanit.l@sahapat.co.th",
                "userpl": "SL508",
                "login_kind": "manager_acc",
                "acc_division": "Div.B",
                "acc_region": "กรุงเทพ",
                "acc_scope": "all",
                "visible_supervisor_codes": ["SL508", "SL532"],
            },
            {
                "email": "thanit.l@sahapat.co.th",
                "userpl": "SL524",
                "login_kind": "manager_acc",
                "visible_supervisor_codes": ["SL524"],
            },
        ]
        with patch("backend.services.access_control.read_rows", return_value=full_rows):
            allowed = compute_allowed_supervisor_codes(
                "thanit.l@sahapat.co.th", acc_rows, None
            )
        self.assertIn("SL532", allowed)
        self.assertIn("SL524", allowed)
        self.assertIn("SL508", allowed)


if __name__ == "__main__":
    unittest.main()
