"""
แคชสินค้า/ราคากลางของงวดต้องไม่ถูกตัดเหลือทีมเดียว (OPEN_ITEMS 7.4 — ผลตรวจ 5 ต.ค. 2026)

- แคชหมดอายุแล้วทีม B ดึงของใหม่ → ต้องรวมกับ SKU ทีม A ที่อยู่ในไฟล์ ไม่ใช่เขียนทับ
- โหลดรวมภาคหลายเธรดพร้อมกัน → SKU ของทุกเธรดต้องอยู่ครบ
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import fabric_cache as fc  # noqa: E402

Y, M = 2026, 11


def _prod(skus):
    return pd.DataFrame({"sku": skus, "unit_price": [10.0] * len(skus), "cash_unit_price": [9.0] * len(skus)})


class TestSharedProductCacheMerge(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._env = {k: os.environ.get(k) for k in ("FABRIC_CACHE_DIR", "FABRIC_STATIC_CACHE_TTL_SEC")}
        os.environ["FABRIC_CACHE_DIR"] = self._tmpdir
        os.environ["FABRIC_STATIC_CACHE_TTL_SEC"] = "86400"

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _expire(self, path):
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        doc["cached_at"] = "2020-01-01T00:00:00Z"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f)

    def test_expired_product_cache_keeps_other_team_skus(self):
        fc.write_product_info_df(Y, M, _prod(["A1", "A2"]))
        self._expire(fc._product_path(Y, M))
        self.assertIsNone(fc.read_product_info_df(Y, M))  # หมดอายุจริง
        merged = fc.merge_product_info_df(Y, M, _prod(["B1"]))
        self.assertEqual(set(merged["sku"]), {"A1", "A2", "B1"})
        self.assertEqual(set(fc.read_product_info_df(Y, M)["sku"]), {"A1", "A2", "B1"})

    def test_old_cache_without_cash_price_not_merged(self):
        fc.write_product_info_df(Y, M, pd.DataFrame({"sku": ["OLD"], "unit_price": [5.0]}))
        merged = fc.merge_product_info_df(Y, M, _prod(["B1"]))
        self.assertEqual(list(merged["sku"]), ["B1"])

    def test_expired_price_cache_keeps_other_team_prices(self):
        fc.write_price_map(Y, M, {"A1": 100.0})
        self._expire(fc._price_path(Y, M))
        fc.merge_price_map(Y, M, {"B1": 50.0})
        self.assertEqual(fc.read_price_map(Y, M), {"A1": 100.0, "B1": 50.0})

    def test_parallel_merges_keep_everything(self):
        errs = []

        def run(i):
            try:
                fc.merge_product_info_df(Y, M, _prod([f"T{i}"]))
                fc.merge_price_map(Y, M, {f"T{i}": float(i)})
            except Exception as e:  # pragma: no cover
                errs.append(e)

        ts = [threading.Thread(target=run, args=(i,)) for i in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(errs, [])
        want = {f"T{i}" for i in range(8)}
        self.assertEqual(set(fc.read_product_info_df(Y, M)["sku"]), want)
        self.assertEqual(set(fc.read_price_map(Y, M)), want)


if __name__ == "__main__":
    unittest.main()
