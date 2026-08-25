"""
โหมดรวมภาค: ราคาต่อหีบของ SKU เดียวกันต้องเท่ากันทุกทีม ไม่งั้นต้องเตือน

เจอของจริง (งวด 09/2026): SKU ปรุงทิพย์/เกลือทิพย์ 5 ตัวขึ้นราคาวันที่ 1 ก.ย.
ไฟล์เป้าของ SL346 ถูกสร้างไว้ก่อนหน้านั้นจึงยังถือราคาเดือน ส.ค. ขณะที่ SL397/
SL460/SL509/SL540 ถือราคาใหม่ — merge_employees_payloads บวกแต่หีบ ส่วนราคา
ใช้ของทีมแรกที่เจอ ผลรวมของก้อนรวมจึงไม่เท่าผลบวกรายทีม แล้ว revenue_scale
(OR_engine._revenue_scale_factor) ก็ไปสเกลเป้าเงินรายคนทั้งภาคตามส่วนต่างนั้น
— ผลกระจายเลยดิฟจากเป้าเหลืองเป็นหลักล้านโดยไม่มีใครรู้ว่ามาจากไหน
"""

import unittest

from backend.services.employees import merge_employees_payloads

CONFLICT = "aggregate_price_conflict"


def _sku_row(sku: str, price: float, boxes: int) -> dict:
    return {
        "sku": sku,
        "price_per_box": price,
        "price_missing": False,
        "supervisor_target_boxes": boxes,
        "brand_name_thai": "ปรุงทิพย์",
        "brand_name_english": "",
        "section": "",
        "product_name_thai": "",
        "product_name_english": "",
    }


def _payload(sup: str, rows: list[dict]) -> dict:
    return {
        "_source_sup_id": sup,
        "employees": [],
        "skus": rows,
        "sku_warnings": [],
        "new_product_skus": [],
    }


def _merge(payloads: list[dict]) -> dict:
    return merge_employees_payloads(
        payloads,
        aggregate_label="ทดสอบรวมภาค",
        aggregate_sup_ids=[p["_source_sup_id"] for p in payloads],
    )


def _warnings_of(out: dict, kind: str) -> list[dict]:
    return [w for w in out["sku_warnings"] if w.get("type") == kind]


class TestAggregatePriceConflict(unittest.TestCase):
    def test_same_price_across_teams_has_no_warning(self):
        out = _merge([
            _payload("SL397", [_sku_row("734046", 352.0, 100)]),
            _payload("SL460", [_sku_row("734046", 352.0, 50)]),
        ])
        self.assertEqual(_warnings_of(out, CONFLICT), [])

    def test_stale_price_in_one_team_is_reported(self):
        out = _merge([
            _payload("SL397", [_sku_row("734046", 352.0, 100)]),
            _payload("SL346", [_sku_row("734046", 312.0, 50)]),   # ราคาเดือนก่อน
        ])
        w = _warnings_of(out, CONFLICT)
        self.assertEqual(len(w), 1)
        msg = w[0]["message"]
        self.assertIn("734046", msg)
        self.assertIn("SL346", msg)
        self.assertIn("352.00", msg)   # ราคาที่ระบบเลือกใช้
        self.assertIn("312.00", msg)   # ราคาที่ขัดกัน

    def test_boxes_still_sum_across_teams(self):
        """เตือนแล้วต้องไม่เปลี่ยนพฤติกรรมเดิม — หีบยังบวกครบเหมือนก่อน"""
        out = _merge([
            _payload("SL397", [_sku_row("734046", 352.0, 100)]),
            _payload("SL346", [_sku_row("734046", 312.0, 50)]),
        ])
        row = next(s for s in out["skus"] if s["sku"] == "734046")
        self.assertEqual(float(row["supervisor_target_boxes"]), 150.0)
        self.assertEqual(float(row["price_per_box"]), 352.0)

    def test_price_zero_is_not_a_conflict(self):
        """ราคาหาย (0) เป็นคนละเรื่อง — มีคำเตือนช่องเหลืองของตัวเองอยู่แล้ว"""
        out = _merge([
            _payload("SL397", [_sku_row("734046", 352.0, 100)]),
            _payload("SL460", [_sku_row("734046", 0.0, 50)]),
        ])
        self.assertEqual(_warnings_of(out, CONFLICT), [])

    def test_aggregate_view_warning_stays_first(self):
        """แถบสรุปโหมดรวมต้องยังอยู่บนสุด คำเตือนราคาแทรกไว้ถัดไป"""
        out = _merge([
            _payload("SL397", [_sku_row("734046", 352.0, 100)]),
            _payload("SL346", [_sku_row("734046", 312.0, 50)]),
        ])
        self.assertEqual(out["sku_warnings"][0]["type"], "aggregate_view")
        self.assertEqual(out["sku_warnings"][1]["type"], CONFLICT)


if __name__ == "__main__":
    unittest.main()
