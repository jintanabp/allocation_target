"""
ราคา/หีบต้องเลือกตามหน่วยขายของทีม — รถเงินสดกับเครดิตใช้คนละคอลัมน์

`cfm_product_characteristic` เก็บสองราคาต่อสินค้าหนึ่งตัว: CASHUNITPRICE (รถเงินสด)
กับ CREDITUNITPRICE (เครดิต) · บางสินค้าสองราคานี้ไม่เท่ากัน ของเดิมใช้ราคาเครดิต
กับทุกทีม ทีมรถเงินสด 29 รหัสจาก 96 จึงคิดเป้าเงินจากราคาผิดคอลัมน์มาตลอด

แคชสินค้าเป็นก้อนเดียวใช้ร่วมกันทุกทีม จึงเก็บทั้งสองราคาไว้แล้วเลือกตอนใช้
ถ้าฝังราคาเดียวลงแคช ทีมสองแบบจะเขียนทับราคากันเองสลับไปมา
"""

import unittest

import pandas as pd

from backend.services.employees import _build_sku_and_sun_from_tga

SKU = "734046"
CREDIT, CASH = 352.0, 300.0


def _product(credit=CREDIT, cash=CASH, **extra):
    row = {
        "sku": SKU, "credit_unit_price": credit, "cash_unit_price": cash,
        "brand_name_thai": "ปรุงทิพย์", "brand_name_english": "", "section": "",
        "product_name_thai": "", "product_name_english": "",
    }
    row.update(extra)
    return pd.DataFrame([row])


def _tga(qty=10):
    return pd.DataFrame([{"emp_id": "C413", "sku": SKU, "qty": qty}])


def _price_of(df_sku):
    return float(df_sku.loc[df_sku["sku"] == SKU, "price_per_box"].iloc[0])


class TestPriceBySalesUnit(unittest.TestCase):
    def _run(self, sales_type, df_product=None, price_map=None):
        return _build_sku_and_sun_from_tga(
            _tga(), df_product if df_product is not None else _product(),
            ["C413"], [SKU],
            price_latest_by_sku=price_map,
            sales_type=sales_type,
        )

    def test_van_uses_cash_price(self):
        df_sku, df_sun, _ = self._run("C")
        self.assertEqual(_price_of(df_sku), CASH)
        self.assertAlmostEqual(float(df_sun.iloc[0]["target_sun"]), 10 * CASH, places=2)

    def test_credit_uses_credit_price(self):
        df_sku, df_sun, _ = self._run("S")
        self.assertEqual(_price_of(df_sku), CREDIT)
        self.assertAlmostEqual(float(df_sun.iloc[0]["target_sun"]), 10 * CREDIT, places=2)

    def test_unknown_unit_keeps_credit_price(self):
        """หาหน่วยขายไม่เจอ = พฤติกรรมเดิม ไม่ใช่ราคา 0"""
        for unknown in ("", None, "   ", "X"):
            with self.subTest(unit=unknown):
                df_sku, _sun, _ = self._run(unknown)
                self.assertEqual(_price_of(df_sku), CREDIT)

    def test_van_falls_back_to_credit_when_cash_price_missing(self):
        """สินค้าที่ไม่มีราคารถเงินสด ยังต้องคิดเงินได้ ไม่ใช่ตกไปเป็น 0"""
        df_sku, _sun, _ = self._run("C", df_product=_product(cash=0.0))
        self.assertEqual(_price_of(df_sku), CREDIT)

    def test_old_cache_without_cash_column_still_works(self):
        """แคชรุ่นเก่าไม่มีคอลัมน์ราคารถเงินสดเลย — ต้องไม่พังและไม่ได้ 0"""
        old = _product()
        old = old.drop(columns=["cash_unit_price"])
        df_sku, _sun, _ = self._run("C", df_product=old)
        self.assertEqual(_price_of(df_sku), CREDIT)

    def test_sales_history_price_still_the_second_choice(self):
        """ไม่มีราคาทั้งสองคอลัมน์ → ถอยไปราคาจากยอดขายจริงเหมือนเดิม"""
        df_sku, _sun, _ = self._run(
            "C", df_product=_product(credit=0.0, cash=0.0), price_map={SKU: 111.0}
        )
        self.assertEqual(_price_of(df_sku), 111.0)
        self.assertTrue(bool(df_sku.iloc[0]["price_from_sales_history"]))

    def test_no_price_anywhere_is_flagged(self):
        df_sku, _sun, _ = self._run("C", df_product=_product(credit=0.0, cash=0.0))
        self.assertEqual(_price_of(df_sku), 0.0)
        self.assertTrue(bool(df_sku.iloc[0]["price_missing"]))


if __name__ == "__main__":
    unittest.main()
