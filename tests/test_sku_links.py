"""Unit tests — SKU link store (expand / collapse / validate)"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

import pandas as pd

from backend.services import sku_link_store as sls


class TestSkuLinks(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp()
        self._path = os.path.join(self._tmpdir, "sku_links.json")
        os.environ["SKU_LINKS_JSON_PATH"] = self._path

    def tearDown(self) -> None:
        os.environ.pop("SKU_LINKS_JSON_PATH", None)

    def _write(self, links: list[dict]) -> None:
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump({"links": links}, f)

    def test_expand_skus_for_dax(self) -> None:
        links = [
            {
                "canonical_sku": "NEW01",
                "alias_skus": ["NEW01", "OLD01", "OLD02"],
                "product_name": "",
                "note": "",
            }
        ]
        self._write(links)
        expanded = sls.expand_skus_for_dax(["NEW01"], links)
        self.assertEqual(sorted(expanded), ["NEW01", "OLD01", "OLD02"])

    def test_collapse_hist_to_canonical(self) -> None:
        links = [
            {
                "canonical_sku": "NEW01",
                "alias_skus": ["NEW01", "OLD01"],
                "product_name": "",
                "note": "",
            }
        ]
        df = pd.DataFrame(
            [
                {"emp_id": "E1", "sku": "NEW01", "hist_boxes": 2.0, "hist_amount": 200.0},
                {"emp_id": "E1", "sku": "OLD01", "hist_boxes": 3.0, "hist_amount": 300.0},
                {"emp_id": "E2", "sku": "OLD01", "hist_boxes": 1.0, "hist_amount": 100.0},
            ]
        )
        out = sls.collapse_hist_to_canonical(df, links)
        self.assertEqual(len(out), 2)
        e1 = out[out["emp_id"] == "E1"].iloc[0]
        self.assertEqual(str(e1["sku"]), "NEW01")
        self.assertAlmostEqual(float(e1["hist_boxes"]), 5.0)
        self.assertAlmostEqual(float(e1["hist_amount"]), 500.0)

    def test_validate_duplicate_alias(self) -> None:
        links = [
            {"canonical_sku": "A", "alias_skus": ["A", "X"]},
            {"canonical_sku": "B", "alias_skus": ["B", "X"]},
        ]
        with self.assertRaises(ValueError):
            sls.validate_links(links)

    def test_upsert_and_delete(self) -> None:
        self._write([])
        saved = sls.upsert_link(
            [],
            canonical_sku="266932",
            alias_skus=["266932", "OLD1"],
            product_name="ทดสอบ",
            updated_by="test@example.com",
        )
        self.assertEqual(len(saved), 1)
        row = sls.find_link("266932", saved)
        self.assertIsNotNone(row)
        self.assertIn("OLD1", row["alias_skus"])
        after = sls.delete_link(saved, "266932")
        self.assertEqual(after, [])

    def test_extra_aliases_for_canonical(self) -> None:
        links = [
            {"canonical_sku": "C1", "alias_skus": ["C1", "A1", "A2"]},
        ]
        extra = sls.extra_aliases_for_canonical("C1", links)
        self.assertEqual(extra, ["A1", "A2"])


if __name__ == "__main__":
    unittest.main()
