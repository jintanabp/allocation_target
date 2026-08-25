"""
ผลที่เก็บไว้ตอน "ราคาดึงไม่ได้" ต้องไม่ถูกเสิร์ฟซ้ำ

เจอของจริง 2026-08-25: Fabric capacity เต็ม เซิร์ฟเวอร์โหลดทีมหนึ่งแล้วได้ผลที่
มีเป้าหีบครบแต่ราคาเป็น 0 ทุกตัว แล้ว **เก็บผลนั้นลง payload cache อายุ 1 ชั่วโมง**
พอราคากลับมาใช้ได้แล้ว ระบบก็ยังหยิบของที่เก็บไว้มาเสิร์ฟ ผู้ใช้จึงยังเปิดงวด
ไม่ได้อยู่ดี และคนที่เข้าเซิร์ฟเวอร์ไม่ได้ก็ล้างแคชเองไม่ได้ด้วย

ทางแก้: ถือว่าผลแบบนี้เป็น "แคชใช้ไม่ได้" แล้วสร้างใหม่ทันที ไม่ต้องรอหมดอายุ
"""

import unittest

from backend.services.employees import _payload_has_boxes_but_no_money


def _payload(rows):
    return {"skus": [
        {"sku": s, "supervisor_target_boxes": b, "price_per_box": p} for s, b, p in rows
    ]}


class TestPoisonedPayloadDetection(unittest.TestCase):
    def test_boxes_with_no_price_is_poisoned(self):
        self.assertTrue(_payload_has_boxes_but_no_money(
            _payload([("111294", 21, 0.0), ("111302", 36, 0.0)])
        ))

    def test_normal_payload_is_kept(self):
        self.assertFalse(_payload_has_boxes_but_no_money(
            _payload([("111294", 21, 1050.0), ("111302", 36, 1050.0)])
        ))

    def test_partial_prices_is_kept(self):
        """ราคาหายบางตัวเป็นเรื่องปกติ มีคำเตือนช่องเหลืองอยู่แล้ว ไม่ต้องทิ้งแคช"""
        self.assertFalse(_payload_has_boxes_but_no_money(
            _payload([("111294", 21, 1050.0), ("111302", 36, 0.0)])
        ))

    def test_no_boxes_at_all_is_not_poisoned(self):
        """งวดที่ยังไม่มีเป้าจริง ๆ = คนละเรื่อง ต้องไม่ไปวนดึงใหม่ทุกครั้ง"""
        self.assertFalse(_payload_has_boxes_but_no_money(
            _payload([("111294", 0, 0.0)])
        ))

    def test_empty_or_missing_skus_is_not_poisoned(self):
        self.assertFalse(_payload_has_boxes_but_no_money({"skus": []}))
        self.assertFalse(_payload_has_boxes_but_no_money({}))
        self.assertFalse(_payload_has_boxes_but_no_money(None))

    def test_broken_rows_do_not_raise(self):
        self.assertFalse(_payload_has_boxes_but_no_money(
            {"skus": [{"sku": "x"}, None, {"supervisor_target_boxes": "ไม่ใช่ตัวเลข"}]}
        ))


if __name__ == "__main__":
    unittest.main()
