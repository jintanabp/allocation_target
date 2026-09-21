"""
กติกาบังคับคลังเดียว — เทสฟังก์ชัน _apply_warehouse_pin_rules ตรง ๆ (ไม่ผ่านทั้งสาย
_build_tga_upload_dataframe — ดู tests/test_warehouse_pin_send_integration.py สำหรับ
เทสเต็มสายที่จำลองเหตุการณ์จริง)

จับคู่กติกาด้วย SKU ตรง ๆ (ไม่ผ่าน section) — จึงไม่ต้องเตรียมไฟล์เป้าราย sup/คอลัมน์
section อะไรเลยในเทสไฟล์นี้ (คนละแบบจากเวอร์ชันแรกที่ยังผูกกับ section)

ดูแผนที่อนุมัติ (plan file) และ backend/services/lakehouse.py::_apply_warehouse_pin_rules
สำหรับบริบทเต็ม — สำคัญ: ต้องกันปัญหาเดียวกับ SL380/SL530/SL525 (คลังใหม่ที่ปลายทางไม่เคยมี
= insert ซ้อนไม่ใช่ update ทับ เพราะ WAREHOUSECODE อยู่ในคีย์ upsert)
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import lakehouse as lh  # noqa: E402
from backend.services import warehouse_pin_rules_store as store  # noqa: E402

logging.disable(logging.CRITICAL)

SUP = "SLWHPIN"


def _row(emp, sku, boxes, wh, area="3", div="S", st="S1", prov="P1"):
    return {
        "emp_id": emp, "sku": sku, "allocated_boxes": boxes,
        "salestype": st, "divisioncode": div, "areacode": area,
        "provincecode": prov, "warehouse_code": wh,
    }


class WarehousePinApplyTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_env = os.environ.get("WAREHOUSE_PIN_RULES_PATH")
        os.environ["WAREHOUSE_PIN_RULES_PATH"] = os.path.join(self._tmp.name, "rules.json")

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("WAREHOUSE_PIN_RULES_PATH", None)
        else:
            os.environ["WAREHOUSE_PIN_RULES_PATH"] = self._old_env
        self._tmp.cleanup()

    def _rule(self, skus, section="702", area="3", div="S", wh="G010"):
        store.write_rules([{"section": section, "skus": skus, "areacode": area, "divisioncode": div, "warehouse_code": wh}])

    def test_no_rules_is_a_no_op(self):
        df = pd.DataFrame([_row("E1", "SKU1", 6, "R082"), _row("E1", "SKU1", 4, "G002")])
        out, stats = lh._apply_warehouse_pin_rules(df, SUP, 10, 2026)
        self.assertEqual(stats["matched_groups"], 0)
        pd.testing.assert_frame_equal(out.reset_index(drop=True), df.reset_index(drop=True))

    def test_split_group_consolidates_onto_pinned_warehouse_and_zeros_the_rest(self):
        self._rule(skus=["SKU1"], area="3", div="S", wh="G010")
        df = pd.DataFrame([_row("E1", "SKU1", 6, "R082"), _row("E1", "SKU1", 4, "G002")])
        out, stats = lh._apply_warehouse_pin_rules(df, SUP, 10, 2026)
        self.assertEqual(stats["matched_groups"], 1)
        self.assertEqual(stats["boxes_moved"], 10)
        self.assertEqual(stats["rows_zeroed"], 2)
        self.assertEqual(stats["new_warehouse_legs"], 1)
        old = out[out["warehouse_code"].isin(["R082", "G002"])]
        self.assertEqual(len(old), 2)
        self.assertTrue((old["allocated_boxes"] == 0).all())
        pinned = out[out["warehouse_code"] == "G010"]
        self.assertEqual(len(pinned), 1)
        self.assertEqual(int(pinned.iloc[0]["allocated_boxes"]), 10)
        self.assertEqual(int(out["allocated_boxes"].sum()), 10)  # ผลรวมไม่เปลี่ยน

    def test_pinned_warehouse_never_seen_before_creates_a_new_leg(self):
        """คลังปักหมุดไม่เคยมีในคู่นี้มาก่อนเลย — ต้องสร้างขาใหม่ ไม่ใช่แก้แถวเดิม"""
        self._rule(skus=["SKU1"], area="3", div="S", wh="G999")
        df = pd.DataFrame([_row("E1", "SKU1", 5, "R082")])
        out, stats = lh._apply_warehouse_pin_rules(df, SUP, 10, 2026)
        self.assertEqual(stats["new_warehouse_legs"], 1)
        self.assertIn("G999", out["warehouse_code"].tolist())
        self.assertEqual(int(out[out["warehouse_code"] == "R082"].iloc[0]["allocated_boxes"]), 0)

    def test_group_already_fully_on_pinned_warehouse_is_untouched(self):
        self._rule(skus=["SKU1"], area="3", div="S", wh="G010")
        df = pd.DataFrame([_row("E1", "SKU1", 5, "G010")])
        out, stats = lh._apply_warehouse_pin_rules(df, SUP, 10, 2026)
        self.assertEqual(stats["matched_groups"], 0)
        pd.testing.assert_frame_equal(out.reset_index(drop=True), df.reset_index(drop=True))

    def test_all_zero_group_creates_no_pointless_empty_row(self):
        """กลุ่มที่หีบรวมเป็น 0 ทุกแถวอยู่แล้ว — ไม่ควรสร้างแถวใหม่เปล่า ๆ ที่คลังปักหมุด"""
        self._rule(skus=["SKU1"], area="3", div="S", wh="G010")
        df = pd.DataFrame([_row("E1", "SKU1", 0, "R082"), _row("E1", "SKU1", 0, "G002")])
        out, stats = lh._apply_warehouse_pin_rules(df, SUP, 10, 2026)
        self.assertEqual(stats["matched_groups"], 0)
        self.assertNotIn("G010", out["warehouse_code"].tolist())
        pd.testing.assert_frame_equal(out.reset_index(drop=True), df.reset_index(drop=True))

    def test_non_matching_sku_is_left_untouched(self):
        """กติกาผูก SKU1 เท่านั้น — SKU2 (แม้อยู่กลุ่ม/ภาค/division เดียวกัน) ต้องไม่ถูกแตะ
        เพราะแอดมินอาจเลือกแค่บางตัวในกลุ่มสินค้า ไม่ใช่ทั้งกลุ่มเสมอไป"""
        self._rule(skus=["SKU1"], area="3", div="S", wh="G010")
        df = pd.DataFrame([_row("E1", "SKU2", 6, "R082"), _row("E1", "SKU2", 4, "G002")])
        out, stats = lh._apply_warehouse_pin_rules(df, SUP, 10, 2026)
        self.assertEqual(stats["matched_groups"], 0)
        pd.testing.assert_frame_equal(out.reset_index(drop=True), df.reset_index(drop=True))

    def test_non_matching_area_is_left_untouched(self):
        self._rule(skus=["SKU1"], area="3", div="S", wh="G010")
        df = pd.DataFrame([_row("E1", "SKU1", 6, "R082", area="4"), _row("E1", "SKU1", 4, "G002", area="4")])
        out, stats = lh._apply_warehouse_pin_rules(df, SUP, 10, 2026)
        self.assertEqual(stats["matched_groups"], 0)
        pd.testing.assert_frame_equal(out.reset_index(drop=True), df.reset_index(drop=True))

    def test_same_emp_sku_two_area_division_legs_only_matched_leg_is_touched(self):
        """คนเดียวกันขายคู่เดียวกันในสองเขตปนกัน — กติกาตรงแค่เขตเดียว อีกเขตต้องไม่โดนแตะ"""
        self._rule(skus=["SKU1"], area="3", div="S", wh="G010")
        df = pd.DataFrame(
            [
                _row("E1", "SKU1", 6, "R082", area="3", div="S"),
                _row("E1", "SKU1", 4, "G002", area="3", div="S"),
                _row("E1", "SKU1", 10, "R303", area="4", div="S"),  # ขาอื่น area 4 ไม่ตรงกติกา
            ]
        )
        out, stats = lh._apply_warehouse_pin_rules(df, SUP, 10, 2026)
        self.assertEqual(stats["matched_groups"], 1)
        other_leg = out[out["areacode"] == "4"]
        self.assertEqual(len(other_leg), 1)
        self.assertEqual(int(other_leg.iloc[0]["allocated_boxes"]), 10)
        self.assertEqual(other_leg.iloc[0]["warehouse_code"], "R303")

    def test_multiple_skus_in_one_rule_all_get_pinned(self):
        self._rule(skus=["SKU1", "SKU2"], area="3", div="S", wh="G010")
        df = pd.DataFrame(
            [
                _row("E1", "SKU1", 6, "R082"), _row("E1", "SKU1", 4, "G002"),
                _row("E1", "SKU2", 3, "R082"), _row("E1", "SKU2", 2, "G002"),
            ]
        )
        out, stats = lh._apply_warehouse_pin_rules(df, SUP, 10, 2026)
        self.assertEqual(stats["matched_groups"], 2)
        pinned = out[out["warehouse_code"] == "G010"]
        self.assertEqual(set(pinned["sku"]), {"SKU1", "SKU2"})
        self.assertEqual(int(pinned[pinned["sku"] == "SKU1"]["allocated_boxes"].sum()), 10)
        self.assertEqual(int(pinned[pinned["sku"] == "SKU2"]["allocated_boxes"].sum()), 5)

    def test_empty_dataframe_is_handled(self):
        self._rule(skus=["SKU1"])
        out, stats = lh._apply_warehouse_pin_rules(pd.DataFrame(), SUP, 10, 2026)
        self.assertEqual(stats["matched_groups"], 0)
        self.assertTrue(out.empty)


if __name__ == "__main__":
    unittest.main()
