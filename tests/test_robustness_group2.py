"""
งานค้างจากออดิตความทนทาน 26 ส.ค. 2026 (กลุ่ม 2 — ผู้ใช้สั่งแก้ 1 ต.ค. 2026): ล่ม / ไฟล์เสีย

1. employees.py เขียนไฟล์ประวัติ / grain แบบไม่ atomic → ไฟล์ครึ่ง ๆ
2. optimize._read_hist_cache ไฟล์เสีย = 500 ดิบ · และห้ามถือเป็นตารางว่าง (กติกาไม่เคยขายจะตัดทั้งทีมเป็น 0)
3. OR_engine ไม่ coerce price_per_box → KeyError / TypeError
4. DAX ราคาไม่มีชั้นถอยเครดิตอย่างเดียว — CASHUNITPRICE หาย = ราคา 0 ทุกทีม
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest

import pandas as pd
from fastapi import HTTPException

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend import OR_engine as eng  # noqa: E402
from backend.fabric_dax_connector import FabricDAXConnector  # noqa: E402
from backend.services.optimize import _read_hist_cache  # noqa: E402


def _src(*parts: str) -> str:
    with open(os.path.join(REPO, *parts), encoding="utf-8") as f:
        return f.read()


class TestEmployeesWritesAtomically(unittest.TestCase):
    def test_no_plain_to_csv_left(self):
        self.assertIsNone(re.search(r"\.to_csv\(", _src("backend", "services", "employees.py")))


class TestHistCacheUnreadable(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmp.cleanup()

    def _file(self, text: str) -> str:
        p = os.path.join(self._tmp.name, "hist_SLX_2026_10_3m.csv")
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    def test_missing_file_is_still_empty(self):
        df = _read_hist_cache(os.path.join(self._tmp.name, "nope.csv"), ["E1"])
        self.assertTrue(df.empty)

    def test_good_file_reads(self):
        df = _read_hist_cache(self._file("emp_id,sku,hist_boxes\nE1,S1,4\nE2,S1,1\n"), ["E1"])
        self.assertEqual(df["emp_id"].tolist(), ["E1"])

    def test_zero_byte_file_stops_with_clear_message(self):
        with self.assertRaises(HTTPException) as cm:
            _read_hist_cache(self._file(""), ["E1"])
        self.assertEqual(cm.exception.status_code, 409)
        self.assertEqual(cm.exception.detail["code"], "hist_cache_unreadable")
        self.assertIn("hist_SLX_2026_10_3m.csv", cm.exception.detail["message"])

    def test_file_without_emp_id_stops(self):
        with self.assertRaises(HTTPException):
            _read_hist_cache(self._file("x,y\n1,2\n"), ["E1"])

    def test_twelve_month_read_does_not_swallow_it(self):
        """ถ้ากลืนเป็นตารางว่าง กติกาไม่เคยขายจะเห็นว่าทีมมีไฟล์แต่ไม่มีใครขาย → ตัดทุกคนเป็น 0"""
        src = _src("backend", "services", "optimize.py")
        i = src.index("n_months=12),\n                hist_sup_ids,")
        block = src[i:i + 400]
        self.assertLess(block.index("except HTTPException:"), block.index("except Exception"))


class TestEnginePriceCoerced(unittest.TestCase):
    def _norm(self, df_sku):
        df_emp = pd.DataFrame([{"emp_id": "E1", "yellow_target": 100.0}])
        _, sku, _, _ = eng._normalize_engine_inputs(df_emp, df_sku, pd.DataFrame(), [])
        return sku

    def test_missing_price_column_becomes_zero(self):
        sku = self._norm(pd.DataFrame([{"sku": "S1", "supervisor_target_boxes": 3}]))
        self.assertEqual(sku["price_per_box"].tolist(), [0.0])

    def test_text_prices_become_numbers(self):
        sku = self._norm(pd.DataFrame([
            {"sku": "S1", "supervisor_target_boxes": 3, "price_per_box": "12.5"},
            {"sku": "S2", "supervisor_target_boxes": 3, "price_per_box": "abc"},
            {"sku": "S3", "supervisor_target_boxes": 3, "price_per_box": None},
        ]))
        self.assertEqual(sku["price_per_box"].tolist(), [12.5, 0.0, 0.0])
        self.assertEqual(sku["price_per_box"].dtype.kind, "f")

    def test_allocate_runs_without_price_column(self):
        df_emp = pd.DataFrame([{"emp_id": "E1", "yellow_target": 100.0},
                               {"emp_id": "E2", "yellow_target": 100.0}])
        df_sku = pd.DataFrame([{"sku": "S1", "supervisor_target_boxes": 4}])
        out = eng.allocate_boxes(df_emp, df_sku, pd.DataFrame(columns=["emp_id", "sku", "hist_boxes"]),
                                 strategy="EVEN", tiered_allocation=False)
        self.assertEqual(int(out["allocated_boxes"].sum()), 4)


class TestCreditOnlyPriceFallback(unittest.TestCase):
    def _run(self, fail_when):
        conn = FabricDAXConnector.__new__(FabricDAXConnector)   # ข้าม __init__ — ไม่มีการต่อเน็ต
        seen: list[str] = []

        def fake_exec(dax_query, debug=False):
            seen.append(dax_query)
            if fail_when(dax_query):
                raise RuntimeError("column not found")
            return [{"[ProductCode]": "734046", "[CreditUnitPrice]": 352.0, "[CashUnitPrice]": 340.0}]

        conn._execute_dax = fake_exec
        return conn.get_product_info(sku_list=["734046"], target_year=2026, target_month=10), seen

    def test_cash_column_missing_falls_back_to_credit_only(self):
        df, seen = self._run(lambda q: "CASHUNITPRICE" in q)
        self.assertEqual(len(seen), 3)
        self.assertNotIn("CASHUNITPRICE", seen[2])
        self.assertIn("CREDITUNITPRICE", seen[2])
        self.assertTrue(seen[2].rstrip().endswith(")"))
        self.assertEqual(df["credit_unit_price"].tolist(), [352.0])
        # ไม่มีคอลัมน์ราคารถเงินสด = แคชรอบนี้ถูกทิ้งแล้วดึงใหม่รอบหน้า ไม่ค้างราคาเครดิตไว้ทั้ง TTL
        self.assertNotIn("cash_unit_price", df.columns)

    def test_normal_path_unchanged(self):
        df, seen = self._run(lambda q: False)
        self.assertEqual(len(seen), 1)
        self.assertEqual(df["cash_unit_price"].tolist(), [340.0])

    def test_section_failure_keeps_cash_price(self):
        df, seen = self._run(lambda q: "'Dim_Product'[Section]" in q)
        self.assertEqual(len(seen), 2)
        self.assertEqual(df["cash_unit_price"].tolist(), [340.0])

    def test_everything_failing_still_raises(self):
        with self.assertRaises(RuntimeError):
            self._run(lambda q: True)


if __name__ == "__main__":
    unittest.main()
