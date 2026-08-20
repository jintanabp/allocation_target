"""
I8 — พนักงานที่ส่งเข้ามาต้องมีแถวกลับออกไปเสมอ (หีบ 0 ได้)

เหตุที่ต้องมีด่านนี้: I1 (`validate_allocation_vs_targets`) รวมยอด **ต่อ SKU อย่างเดียว**
ไม่มีแกนพนักงาน ถ้าพนักงานคนหนึ่งหลุดออกจากผลลัพธ์ หีบของเขาจะถูกเกลี่ยไปคนอื่น
แล้วยอดต่อ SKU ยังตรงเป้าเป๊ะ → ทุกด่านผ่าน ทั้งที่หีบไปตกผิดคน

เคสจริงที่เจอ: SL509 พนักงาน C442 มีคลัง R408 (เป้าเงินเต็ม) และ R493 (เป้าเงิน 0)
ขั้นที่ 3 C442 หายทั้งคน หีบถูกเกลี่ยไปเพื่อนร่วมทีมโดยไม่มีอะไรเตือน
"""

from __future__ import annotations

import inspect
import os
import sys
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.core.allocation_checks import (  # noqa: E402
    missing_employee_alloc_keys,
    validate_allocation_vs_targets,
    zero_fill_missing_employees,
)

DF_SKU = pd.DataFrame([
    {"sku": "A", "supervisor_target_boxes": 300},
    {"sku": "B", "supervisor_target_boxes": 60},
])


def _alloc(rows):
    return pd.DataFrame(rows)


def _full():
    """ชุดที่ถูกต้อง — C442 อยู่ครบ"""
    out = []
    for sku, tot in (("A", (100, 98, 102)), ("B", (20, 20, 20))):
        for (emp, wh), boxes in zip((("C442", "R408"), ("C415", ""), ("C412", "")), tot):
            out.append({
                "emp_id": emp, "warehouse_code": wh, "sku": sku, "allocated_boxes": boxes,
                "price_per_box": 12.5, "brand_name_thai": "แบรนด์ก",
            })
    return _alloc(out)


def _c442_vanished():
    """ชุดที่ C442 หาย และหีบถูกเกลี่ยไปสองคนที่เหลือ — ยอดต่อ SKU ยังเท่าเดิม"""
    out = []
    for sku, tot in (("A", (148, 152)), ("B", (30, 30))):
        for (emp, wh), boxes in zip((("C415", ""), ("C412", "")), tot):
            out.append({
                "emp_id": emp, "warehouse_code": wh, "sku": sku, "allocated_boxes": boxes,
                "price_per_box": 12.5, "brand_name_thai": "แบรนด์ก",
            })
    return _alloc(out)


REQUESTED = [
    {"emp_id": "C442", "warehouse_code": "R408", "yellow_target": 937458.27},
    {"emp_id": "C415", "warehouse_code": "", "yellow_target": 500000.0},
    {"emp_id": "C412", "warehouse_code": "", "yellow_target": 500000.0},
]


class TestTheGapThatExistedBefore(unittest.TestCase):
    """บันทึกช่องโหว่เดิมไว้เป็นเทส — ถ้าวันหลังมีคนไปแก้ I1 ให้ครอบแกนพนักงาน เทสนี้จะฟ้อง"""

    def test_sku_validator_cannot_see_a_vanished_employee(self):
        self.assertEqual(validate_allocation_vs_targets(_full(), DF_SKU), [])
        self.assertEqual(
            validate_allocation_vs_targets(_c442_vanished(), DF_SKU), [],
            "I1 ตรวจต่อ SKU อย่างเดียว จึงมองไม่เห็นว่าพนักงานหายไปทั้งคน",
        )

    def test_employee_validator_catches_what_the_sku_validator_misses(self):
        self.assertEqual(missing_employee_alloc_keys(_full(), REQUESTED), [])
        missing = missing_employee_alloc_keys(_c442_vanished(), REQUESTED)
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["emp_id"], "C442")
        self.assertEqual(missing[0]["warehouse_code"], "R408")
        self.assertGreater(missing[0]["yellow_target"], 0, "ต้องพกเป้าเงินมาด้วยเพื่อจัดระดับความรุนแรง")


class TestZeroFillPreservesI1(unittest.TestCase):
    def setUp(self):
        self.gone = _c442_vanished()
        self.missing = missing_employee_alloc_keys(self.gone, REQUESTED)

    def test_i1_still_holds_after_zero_fill(self):
        """เทสสำคัญสุด — เติมแถว 0 ต้องไม่ขยับยอดต่อ SKU แม้แต่หีบเดียว"""
        before = validate_allocation_vs_targets(self.gone, DF_SKU)
        fixed = zero_fill_missing_employees(self.gone, self.missing)
        after = validate_allocation_vs_targets(fixed, DF_SKU)
        self.assertEqual(before, [])
        self.assertEqual(after, [], "การเติมแถว 0 ต้องไม่ทำให้ I1 พัง")
        for sku in ("A", "B"):
            self.assertEqual(
                int(fixed[fixed.sku == sku]["allocated_boxes"].sum()),
                int(self.gone[self.gone.sku == sku]["allocated_boxes"].sum()),
            )

    def test_adds_exactly_one_row_per_sku(self):
        fixed = zero_fill_missing_employees(self.gone, self.missing)
        added = fixed[fixed.emp_id == "C442"]
        self.assertEqual(len(added), 2, "2 SKU → 2 แถว")
        self.assertEqual(sorted(added["sku"].tolist()), ["A", "B"])
        self.assertTrue((added["allocated_boxes"] == 0).all())
        self.assertTrue((added["warehouse_code"] == "R408").all())
        self.assertNotIn("C442|R408", added["emp_id"].tolist(), "emp_id ต้องไม่พ่วงคลังมาด้วย")

    def test_metadata_columns_are_carried_not_blanked(self):
        fixed = zero_fill_missing_employees(self.gone, self.missing)
        added = fixed[fixed.emp_id == "C442"]
        self.assertTrue((added["price_per_box"] == 12.5).all())
        self.assertTrue((added["brand_name_thai"] == "แบรนด์ก").all(),
                        "ถ้าปล่อยว่าง แท็บแบรนด์ในหน้าเว็บจะมีช่องว่างโผล่")

    def test_is_a_noop_when_nothing_is_missing(self):
        full = _full()
        out = zero_fill_missing_employees(full, missing_employee_alloc_keys(full, REQUESTED))
        self.assertEqual(len(out), len(full))

    def test_is_idempotent(self):
        once = zero_fill_missing_employees(self.gone, self.missing)
        twice = zero_fill_missing_employees(once, missing_employee_alloc_keys(once, REQUESTED))
        self.assertEqual(len(twice), len(once), "เรียกซ้ำต้องไม่เพิ่มแถว")

    def test_single_warehouse_path_untouched(self):
        """เส้นทางปกติ (ไม่มีคลัง) ต้องไม่มีอะไรเปลี่ยน"""
        rows = [
            {"emp_id": "E1", "warehouse_code": "", "sku": "A", "allocated_boxes": 300,
             "price_per_box": 12.5, "brand_name_thai": "แบรนด์ก"},
            {"emp_id": "E1", "warehouse_code": "", "sku": "B", "allocated_boxes": 60,
             "price_per_box": 12.5, "brand_name_thai": "แบรนด์ก"},
        ]
        df = _alloc(rows)
        req = [{"emp_id": "E1", "warehouse_code": None, "yellow_target": 1.0},
               {"emp_id": "E9", "warehouse_code": None, "yellow_target": 0.0}]
        missing = missing_employee_alloc_keys(df, req)
        self.assertEqual([m["emp_id"] for m in missing], ["E9"])
        fixed = zero_fill_missing_employees(df, missing)
        kept = fixed[fixed.emp_id == "E1"].reset_index(drop=True)
        pd.testing.assert_frame_equal(kept, df.reset_index(drop=True))
        self.assertTrue((fixed[fixed.emp_id == "E9"]["warehouse_code"] == "").all())

    def test_zero_money_absence_is_reported_but_not_an_error(self):
        """คลังที่เป้าเงิน 0 หายไปเป็นเรื่องถูกต้อง — ด่านต้องรายงานพร้อมเป้าเงิน 0 ให้ผู้เรียกแยกเอง"""
        req = REQUESTED + [{"emp_id": "C442", "warehouse_code": "R493", "yellow_target": 0.0}]
        missing = missing_employee_alloc_keys(_full(), req)
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["warehouse_code"], "R493")
        self.assertEqual(missing[0]["yellow_target"], 0.0)


class TestServiceWiring(unittest.TestCase):
    """ลำดับการเรียกคือเหตุผลทั้งหมดของความถูกต้อง — ตรึงไว้"""

    def setUp(self):
        from backend.services import optimize as opt

        self.src = inspect.getsource(opt.run_optimization_service)

    def test_zero_fill_runs_after_restore_and_before_the_i1_gate(self):
        i_restore = self.src.index("restore_allocation_emp_ids")
        i_fill = self.src.index("zero_fill_missing_employees")
        i_gate = self.src.index("validate_allocation_vs_targets")
        self.assertLess(i_restore, i_fill, "ต้องเติมหลังแปลง or_emp_id กลับเป็น emp+คลังแล้ว")
        self.assertLess(i_fill, i_gate, "ต้องเติมก่อนด่าน I1 เพื่อให้ตรวจกับ frame ที่เขียนไฟล์จริง")

    def test_requested_keys_come_from_the_unfiltered_frame(self):
        i_req = self.src.index("_requested_alloc_keys")
        i_filter = self.src.index('df_all_targets["yellow_target"] > 0')
        self.assertLess(
            i_req, i_filter,
            "ต้องเก็บรายชื่อที่ขอมาก่อนกรองเป้าเงิน > 0 ไม่งั้นด่านจะกลายเป็นของหลอก",
        )


if __name__ == "__main__":
    unittest.main()
