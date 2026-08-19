"""
ราคาสินค้าต้องคิด ณ "วันที่ 1 ของงวดเป้า" ไม่ใช่ "วันนี้"

เหตุการณ์จริง (SL346 งวด ก.ย. 2026 ตรวจเมื่อ 2026-08-19):
  เป้าหีบตรงเป๊ะ 12,142 หีบ แต่เป้าบาทต่ำกว่าความจริง 10,477.00 บาท
  สาเหตุ: DAX ดึง CREDITUNITPRICE ด้วย `VAR t = TODAY()` แต่ตารางราคา
  (cfm_product_characteristic) เก็บราคาเป็นช่วงวันที่ — 5 SKU มีราคาใหม่
  ที่ FROMDATE = 2026-09-01 พอดี ระบบจึงหยิบแถวเก่าที่ TODATE = 2026-08-31
  มาคิดเป้าบาทของเดือน ก.ย.

  ตรวจกับฐานข้อมูลจริงแล้ว ทั้ง 5 ตัวเป็นรูปแบบเดียวกัน เช่น
    734046: 312 บาท (2024-05-01 → 2026-08-31) / 352 บาท (2026-09-01 → 9456-12-31)

อาการที่ทำให้ไล่จับยาก: พอถึงวันที่ 1 ของงวด มันหายเอง แล้วกลับมาใหม่งวดถัดไป

เทสชุดนี้ไม่แตะเน็ตเลย — ตรวจที่ข้อความ DAX ที่ประกอบขึ้นและที่ตัวแคช
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

import pandas as pd  # noqa: E402

from backend.fabric_dax_connector import FabricDAXConnector  # noqa: E402
from backend.services import fabric_cache as fc  # noqa: E402


class TestPriceAsOfExpression(unittest.TestCase):
    def test_target_period_becomes_the_first_of_that_month(self):
        self.assertEqual(FabricDAXConnector._price_asof_dax(2026, 9), "DATE(2026, 9, 1)")
        self.assertEqual(FabricDAXConnector._price_asof_dax(2027, 1), "DATE(2027, 1, 1)")

    def test_falls_back_to_today_when_period_unknown(self):
        """ตัวเรียกเก่าที่ยังไม่ส่งงวดมาต้องไม่พัง — ถอยไปพฤติกรรมเดิม"""
        self.assertEqual(FabricDAXConnector._price_asof_dax(None, None), "TODAY()")
        self.assertEqual(FabricDAXConnector._price_asof_dax(0, 0), "TODAY()")

    def test_rejects_out_of_range_month(self):
        self.assertEqual(FabricDAXConnector._price_asof_dax(2026, 13), "TODAY()")
        self.assertEqual(FabricDAXConnector._price_asof_dax(2026, 0), "TODAY()")

    def test_garbage_input_does_not_raise(self):
        self.assertEqual(FabricDAXConnector._price_asof_dax("ก.ย.", "x"), "TODAY()")

    def test_no_dax_injection_from_the_period(self):
        """งวดถูกแปลงเป็น int เสมอ จึงต่อสตริงแปลก ๆ เข้า DAX ไม่ได้"""
        out = FabricDAXConnector._price_asof_dax("2026); EVALUATE(", 9)
        self.assertEqual(out, "TODAY()")


class TestDaxUsesTheTargetPeriod(unittest.TestCase):
    """
    ประกอบ DAX จริงโดยดัก _execute_dax ไว้ — ไม่มีการต่อเน็ต
    """

    def _capture(self, **kw):
        conn = FabricDAXConnector.__new__(FabricDAXConnector)   # ข้าม __init__ (ไม่ต้องมี credential)
        seen: list[str] = []

        def fake_exec(dax_query, debug=False):
            seen.append(dax_query)
            return []

        conn._execute_dax = fake_exec
        conn.get_product_info(sku_list=["734046"], **kw)
        return seen

    def test_query_prices_at_the_first_of_the_target_month(self):
        dax = self._capture(target_year=2026, target_month=9)[0]
        self.assertIn("VAR t = DATE(2026, 9, 1)", dax)
        self.assertNotIn("TODAY()", dax)

    def test_still_filters_on_the_price_date_window(self):
        """กติกาเดิมต้องอยู่ครบ แค่เปลี่ยนวันที่อ้างอิง"""
        dax = self._capture(target_year=2026, target_month=9)[0]
        self.assertIn("[FROMDATE] <= t", dax)
        self.assertIn("[TODATE] >= t", dax)
        self.assertIn("CREDITUNITPRICE", dax)

    def test_without_a_period_it_is_the_old_behaviour(self):
        dax = self._capture()[0]
        self.assertIn("VAR t = TODAY()", dax)

    def test_the_sku_filter_survives_the_change(self):
        dax = self._capture(target_year=2026, target_month=9)[0]
        self.assertIn('"734046"', dax)


class TestStaleProductCacheIsDropped(unittest.TestCase):
    """
    แคชที่เขียนไว้ก่อนแก้บั๊กถือราคา ณ วันที่ดึง — ต้องไม่ถูกใช้ต่อ
    ไม่งั้นแก้โค้ดแล้วตัวเลขยังผิดจนกว่า TTL จะหมด (ค่าเริ่มต้น 1 วัน)
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("APP_CACHE_DIR")
        os.environ["APP_CACHE_DIR"] = self._tmp.name
        self._patched = fc.cache_dir
        fc.cache_dir = lambda: self._tmp.name          # type: ignore[assignment]

    def tearDown(self):
        fc.cache_dir = self._patched                    # type: ignore[assignment]
        if self._old is None:
            os.environ.pop("APP_CACHE_DIR", None)
        else:
            os.environ["APP_CACHE_DIR"] = self._old
        self._tmp.cleanup()

    def _write_legacy(self, year, month, rows):
        """เลียนแบบไฟล์แคชรุ่นเก่า — ไม่มีฟิลด์ price_asof"""
        path = os.path.join(self._tmp.name, f"dim_product_{year}_{month:02d}.json")
        from datetime import datetime, timezone

        doc = {
            "cached_at": datetime.now(timezone.utc).replace(microsecond=0)
            .isoformat().replace("+00:00", "Z"),
            "rows": rows,
            "row_count": len(rows),
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False)
        return path

    def test_legacy_cache_without_the_marker_is_ignored(self):
        self._write_legacy(2026, 9, [{"sku": "734046", "credit_unit_price": 312.0}])
        self.assertIsNone(
            fc.read_product_info_df(2026, 9),
            "แคชรุ่นเก่าถือราคา 312 (ราคา ส.ค.) ต้องถูกทิ้ง ไม่ใช่เอามาใช้ต่อ",
        )

    def test_fresh_cache_round_trips(self):
        df = pd.DataFrame([{"sku": "734046", "credit_unit_price": 352.0}])
        fc.write_product_info_df(2026, 9, df)
        got = fc.read_product_info_df(2026, 9)
        self.assertIsNotNone(got)
        self.assertEqual(float(got.iloc[0]["credit_unit_price"]), 352.0)

    def test_cache_of_one_period_is_not_served_for_another(self):
        """ราคาเดือน ก.ย. ต้องไม่ถูกเสิร์ฟให้งวด ต.ค. (คนละราคาได้)"""
        fc.write_product_info_df(2026, 9, pd.DataFrame([{"sku": "734046"}]))
        path9 = os.path.join(self._tmp.name, "dim_product_2026_09.json")
        path10 = os.path.join(self._tmp.name, "dim_product_2026_10.json")
        with open(path9, encoding="utf-8") as fh:
            doc = json.load(fh)
        with open(path10, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False)   # คัดลอกข้ามงวดแบบผิด ๆ
        self.assertIsNone(fc.read_product_info_df(2026, 10))

    def test_marker_value(self):
        self.assertEqual(fc.product_price_asof(2026, 9), "2026-09-01")


if __name__ == "__main__":
    unittest.main()
