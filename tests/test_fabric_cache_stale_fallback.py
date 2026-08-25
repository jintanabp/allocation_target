"""
Fabric ล่ม = ต้องใช้ราคาเมื่อวานได้ ไม่ใช่ให้ราคาเป็น 0 ทั้งระบบ

เจอของจริง (2026-08-25): Fabric ตอบ HTTP 400 "your organization's Fabric compute
capacity has exceeded its limits" · เป้ามาจาก Target Sun จึงยังครบทุกแถว แต่ราคา
ต่อหีบมาจาก Fabric พอดึงไม่ได้และแคชก็หมดอายุ (TTL 1 วัน) ทุก SKU เลยได้ราคา 0
ทำให้ "เป้ารวม (บาท)" เป็น 0 ซึ่งหน้าเว็บอ่านเป็นสัญญาณว่า "ไม่มีเป้าในงวดนี้"
ผลคือทุกซุปเปิดงวดกันยาไม่ได้พร้อมกัน ทั้งที่ระบบเป้าไม่ได้ผิดอะไรเลย

ราคาสินค้าแทบไม่ขยับระหว่างงวด แคชเมื่อวานจึงใกล้ความจริงกว่า 0 มาก
"""

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from backend.services import fabric_cache as fc

YEAR, MONTH = 2026, 9


def _stamp(hours_ago: float) -> str:
    t = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return t.replace(microsecond=0).isoformat().replace("+00:00", "Z")


class TestStaleCacheFallback(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="stalecache_")
        self._old = os.environ.get("FABRIC_CACHE_DIR")
        os.environ["FABRIC_CACHE_DIR"] = self._tmpdir

    def tearDown(self):
        if self._old is None:
            os.environ.pop("FABRIC_CACHE_DIR", None)
        else:
            os.environ["FABRIC_CACHE_DIR"] = self._old
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _write(self, name: str, doc: dict):
        with open(os.path.join(self._tmpdir, name), "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)

    def _write_prices(self, hours_ago: float):
        self._write(
            f"price_per_box_{YEAR}_{MONTH:02d}.json",
            {"cached_at": _stamp(hours_ago), "prices": {"734046": 352.0}, "row_count": 1},
        )

    def _write_products(self, hours_ago: float, price_asof: str | None = None):
        self._write(
            f"dim_product_{YEAR}_{MONTH:02d}.json",
            {
                "cached_at": _stamp(hours_ago),
                "price_asof": price_asof or fc.product_price_asof(YEAR, MONTH),
                "rows": [{"sku": "734046", "credit_unit_price": 352.0,
                          "cash_unit_price": 300.0}],
                "row_count": 1,
            },
        )

    # ── ราคาจากประวัติขาย ──────────────────────────────────────────────
    def test_fresh_price_cache_is_used_normally(self):
        self._write_prices(hours_ago=1)
        self.assertEqual(fc.read_price_map(YEAR, MONTH), {"734046": 352.0})

    def test_expired_price_cache_is_ignored_by_default(self):
        """พฤติกรรมเดิมต้องไม่เปลี่ยน — ปกติยังทิ้งแคชหมดอายุแล้วไปดึงใหม่"""
        self._write_prices(hours_ago=48)
        self.assertIsNone(fc.read_price_map(YEAR, MONTH))

    def test_expired_price_cache_is_usable_when_asked(self):
        self._write_prices(hours_ago=48)
        self.assertEqual(
            fc.read_price_map(YEAR, MONTH, allow_stale=True), {"734046": 352.0}
        )

    def test_missing_price_cache_stays_none_even_when_stale_allowed(self):
        self.assertIsNone(fc.read_price_map(YEAR, MONTH, allow_stale=True))

    # ── ข้อมูลสินค้า (ราคาเครดิต) ──────────────────────────────────────
    def test_expired_product_cache_is_usable_when_asked(self):
        self._write_products(hours_ago=48)
        df = fc.read_product_info_df(YEAR, MONTH, allow_stale=True)
        self.assertIsNotNone(df)
        self.assertEqual(len(df), 1)
        self.assertEqual(float(df.iloc[0]["credit_unit_price"]), 352.0)

    def test_expired_product_cache_is_ignored_by_default(self):
        self._write_products(hours_ago=48)
        self.assertIsNone(fc.read_product_info_df(YEAR, MONTH))

    def test_wrong_price_asof_is_still_rejected_when_stale_allowed(self):
        """
        ยอมใช้ของเก่าได้ แต่ห้ามยอมใช้ราคาที่คิดจากวันที่ผิดงวด — คนละเรื่องกัน
        (บั๊กเดิมที่แก้ไปแล้ว: ราคา ณ วันนี้ ไม่ใช่ ณ วันที่ 1 ของงวดเป้า)
        """
        self._write_products(hours_ago=48, price_asof="2026-08-01")
        self.assertIsNone(fc.read_product_info_df(YEAR, MONTH, allow_stale=True))


if __name__ == "__main__":
    unittest.main()
