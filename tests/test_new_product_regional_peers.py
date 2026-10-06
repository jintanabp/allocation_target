"""
รวมภาค: สินค้าที่ทีมอื่นในกลุ่มขายอยู่ ต้องไม่ถูกนับเป็น「สินค้าใหม่」(OPEN_ITEMS 7.7 — ผลตรวจ 5 ต.ค. 2026)

เดิม detect_new_product_skus ดูไฟล์ยอดรายปี (hist_cy_) ของทีมเจ้าของเป้าอย่างเดียว
→ SKU ที่เฉพาะทีม peer ขาย = "ใหม่" → ติ๊กแบ่งเท่าสินค้าใหม่แล้วแบ่งเท่าทั้งภาค
ไฟล์อยู่ใต้ data/ แบบ path สัมพัทธ์ → เทสนี้ chdir ไปโฟลเดอร์ชั่วคราว ไม่แตะ data/ จริง
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.core.allocation_checks import detect_new_product_skus  # noqa: E402
from backend.core.paths import hist_calendar_year_cache_path  # noqa: E402

Y = 2026


def _write(sup, year, rows):
    p = hist_calendar_year_cache_path(sup, year)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    pd.DataFrame(rows, columns=["emp_id", "sku", "hist_boxes"]).to_csv(p, index=False)


class TestNewProductRegionalPeers(unittest.TestCase):
    def setUp(self):
        self.old = os.getcwd()
        self._tmpdir = tempfile.mkdtemp()
        os.chdir(self._tmpdir)
        # ทีมเจ้าของเป้า OWN: ขาย A เท่านั้น · ทีม PEER: ขาย B · C ไม่มีใครขาย · D peer ขายใน 3 เดือนล่าสุด
        _write("OWN", Y, [("E1", "A", 10)])
        _write("OWN", Y - 1, [("E1", "A", 5)])
        _write("PEER", Y, [("P1", "B", 7)])
        _write("PEER", Y - 1, [])

    def tearDown(self):
        os.chdir(self.old)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_single_team_unchanged(self):
        found, mode = detect_new_product_skus("OWN", Y, ["A", "B", "C"])
        self.assertEqual((found, mode), (["B", "C"], "cy_ly"))

    def test_peer_sales_not_new(self):
        found, mode = detect_new_product_skus("OWN", Y, ["A", "B", "C"], peer_sup_ids=["PEER"])
        self.assertEqual((found, mode), (["C"], "cy_ly"))

    def test_cross_team_hist_window_not_new(self):
        hist = pd.DataFrame({"emp_id": ["P1"], "sku": ["D"], "hist_boxes": [3]})
        found, _ = detect_new_product_skus("OWN", Y, ["A", "C", "D"], hist, peer_sup_ids=["PEER"])
        self.assertEqual(found, ["C"])

    def test_all_known_returns_off(self):
        found, mode = detect_new_product_skus("OWN", Y, ["A", "B"], peer_sup_ids=["PEER"])
        self.assertEqual((found, mode), ([], "off"))


if __name__ == "__main__":
    unittest.main()
