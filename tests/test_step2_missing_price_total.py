"""
ขั้นที่ 2: ตั้งเป้าเงินเกินเป้ารวมได้ เมื่อเป้ารวมในแอปขาดมูลค่าสินค้าที่ไม่มีราคา (29 ต.ค. 2026)

สินค้าใหม่ยังไม่มีราคาใน Fabric → เป้ารวมในแอป (Σ หีบ×ราคา) ต่ำกว่ายอดบาทจริงใน Target Sun
เดิมแถบขั้นที่ 2 ปิดปุ่มกระจายทันทีที่ผลรวมต่างเกิน 99 บาท ผู้ใช้กระจายด่วนไม่ได้

เครื่องใช้เป้าเงินเป็นสัดส่วนอยู่แล้ว (OR_engine ปรับ scale = มูลค่าหีบ ÷ เป้ารวม) server ไม่ได้ห้าม
จึงเปิดเฉพาะทิศ "เกิน" + มีสินค้าไม่มีราคา · ทิศ "ขาด" ยังห้ามเหมือนเดิม
หน้าเว็บรันในเทสไม่ได้ — ตรวจโครงของโค้ด (ลองพฤติกรรมจริงในเบราว์เซอร์แล้ว)
"""

from __future__ import annotations

import os
import sys
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)


class Step2MissingPriceTest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(REPO, "frontend", "app.js"), encoding="utf-8") as fh:
            self.js = fh.read()
        i = self.js.index("function updateValidation()")
        self.val = self.js[i:self.js.index("\n}\n", i)]

    def test_only_the_over_direction_with_missing_prices_is_allowed(self):
        self.assertIn("} else if (diff < 0 && _noPriceSkus().length) {", self.val)
        branch = self.val.split("} else if (diff < 0 && _noPriceSkus().length) {")[1].split("} else {")[0]
        self.assertIn("btn.disabled = false", branch)
        tail = self.val.split("} else if (diff < 0 && _noPriceSkus().length) {")[1].split("} else {")[1]
        self.assertIn("btn.disabled = true", tail, "กรณีอื่นยังต้องปิดปุ่มเหมือนเดิม")

    def test_missing_price_means_boxes_without_a_positive_price(self):
        i = self.js.index("function _noPriceSkus()")
        fn = self.js[i:self.js.index("\n}\n", i)]
        self.assertIn("supervisor_target_boxes", fn)
        self.assertIn("price_per_box", fn)

    def test_the_run_confirm_explains_and_suggests_even_split(self):
        i = self.js.index("async function _confirmRevenueScaleBeforeRun()")
        fn = self.js[i:self.js.index("\n}\n", i)]
        self.assertIn("_noPriceSkus()", fn)
        self.assertIn("SKU ใหม่แบ่งเท่ากัน", fn)

    def test_the_engine_still_rescales_money_targets(self):
        """ทั้งหมดนี้พึ่งการปรับสัดส่วนในเครื่อง — ถ้าถูกถอดออก ต้องรู้"""
        with open(os.path.join(REPO, "backend", "OR_engine.py"), encoding="utf-8") as fh:
            eng = fh.read()
        self.assertIn("scale = total_possible_rev / total_target_rev", eng)


if __name__ == "__main__":
    unittest.main()
