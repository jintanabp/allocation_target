"""
คีย์ upsert ของ Target Sun — ตอนนี้รวม WAREHOUSECODE แล้ว

คีย์คือ PRODUCTCODE+SALESTYPE+DIVISIONCODE+SALESMANCODE+AREACODE+PROVINCECODE
**+WAREHOUSECODE** (เจ้าของระบบเพิ่มคลังเข้าคีย์บน production เมื่อ 7 ก.ย. 2026
เพราะตั้งใจให้เก็บเป้าแยกรายคลังจริง ๆ)

กติกาจึงกลับด้านจากเดิม:
- แถวที่ต่างกันที่ **คลัง** = คนละคีย์ → **ต้องส่งแยกกัน ห้ามยุบรวม**
  (ถ้ายุบ คลังหนึ่งจะได้เป้าทั้งก้อน อีกคลังค้างเลขเดิม = ยอดปลายทางไม่ตรงไฟล์)
- แถวที่ **เหมือนกันทุกคอลัมน์รวมทั้งคลัง** = คีย์ซ้ำจริง → ยังต้องยุบและ **บวก** หีบ
  เพราะตัวนำเข้าข้ามแถวหลังทิ้ง ("Duplicate keys within the same file are skipped")

ที่มาของการเปลี่ยน: ก่อนหน้านี้คีย์ 6 คอลัมน์ทำให้สองแถวที่ต่างกันแค่คลังชนกัน
วัดจากตาราง tga_target_salesman_next เอง — SL460 งวด 09/2026 มี 1,593 แถว
แต่มีแค่ 1,395 คีย์ (198 คีย์มีสองแถว ต่างกันแค่รหัสคลังล้วน ๆ) และในแคชที่มีในเครื่อง
พบแบบนี้ 15 จาก 78 ไฟล์ ใน 13 ทีม

กติกาที่คงไว้ไม่เปลี่ยน: ไม่ว่าจะยุบหรือแตกแถว **ยอดรวมหีบที่ส่งห้ามเปลี่ยน**
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

from backend.schemas import LakehouseUploadRequest  # noqa: E402
from backend.services import lakehouse as lh  # noqa: E402

logging.disable(logging.CRITICAL)


def _grain_row(emp, sku, qty, wh, *, province="P1", area="10", st="S1", div="D1"):
    return {
        "emp_id": emp,
        "sku": sku,
        "qty": qty,
        "salestype": st,
        "divisioncode": div,
        "areacode": area,
        "provincecode": province,
        "warehouse_code": wh,
    }


class TestGrainCollapse(unittest.TestCase):
    """ต้นทาง — แถว grain คนละคลังคือคนละคีย์ ต้องรอดไปเป็นคนละบรรทัด"""

    def test_different_warehouse_is_a_different_key_and_survives(self):
        dg = lh._normalize_grain_dtype(pd.DataFrame([
            _grain_row("E1", "A", 6, "WH1"),
            _grain_row("E1", "A", 4, "WH2"),
        ]))
        sub = lh._grain_by_pair(dg)[("E1", "A")]
        self.assertEqual(len(sub), 2, "คนละคลัง = คนละคีย์ ห้ามยุบรวมกัน")
        self.assertEqual(float(sub["qty"].sum()), 10.0, "ยอดรวมต้องไม่เปลี่ยน")
        self.assertEqual(
            set(sub["warehouse_code"]), {"WH1", "WH2"},
            "ต้องเก็บคลังไว้ครบทั้งสอง เพราะคลังอยู่ในคีย์แล้ว",
        )

    def test_identical_rows_including_warehouse_still_collapse(self):
        """คีย์ซ้ำจริง (เหมือนกันทุกคอลัมน์) ยังต้องยุบและบวก ไม่ใช่ทิ้งแถว"""
        dg = lh._normalize_grain_dtype(pd.DataFrame([
            _grain_row("E1", "A", 6, "WH1"),
            _grain_row("E1", "A", 4, "WH1"),
        ]))
        sub = lh._grain_by_pair(dg)[("E1", "A")]
        self.assertEqual(len(sub), 1, "คลังเดียวกันด้วย = คีย์ซ้ำจริง ต้องเหลือแถวเดียว")
        self.assertEqual(float(sub["qty"].sum()), 10.0, "qty ต้องถูกบวก ไม่ใช่ทิ้งแถว")

    def test_different_province_is_a_different_key_and_survives(self):
        dg = lh._normalize_grain_dtype(pd.DataFrame([
            _grain_row("E1", "A", 6, "WH1", province="P1"),
            _grain_row("E1", "A", 4, "WH2", province="P2"),
        ]))
        sub = lh._grain_by_pair(dg)[("E1", "A")]
        self.assertEqual(len(sub), 2, "คนละจังหวัด = คนละคีย์ ห้ามยุบรวมกัน")

    def test_zero_qty_row_on_another_warehouse_is_kept(self):
        """
        แถว qty=0 ของอีกคลังต้องอยู่ต่อ — มันคือคำสั่งล้างเป้าเดิมของคลังนั้น
        ถ้ายุบทิ้ง เป้าเก่าของ WH2 จะค้างอยู่ตลอดไป
        """
        dg = lh._normalize_grain_dtype(pd.DataFrame([
            _grain_row("E1", "A", 0, "WH2"),
            _grain_row("E1", "A", 9, "WH1"),
        ]))
        sub = lh._grain_by_pair(dg)[("E1", "A")]
        self.assertEqual(len(sub), 2)
        self.assertEqual(float(sub["qty"].sum()), 9.0)

    def test_zero_qty_row_on_the_same_warehouse_is_absorbed(self):
        """
        คลังเดียวกันแต่มีทั้งแถว 0 และแถวจริง = คีย์ซ้ำจริง อันตรายกว่า:
        ถ้าแถว 0 ถูกเขียนก่อน ปลายทางจะเก็บ 0 แล้วข้ามแถวจริงทิ้ง — เป้าเหลือศูนย์
        """
        dg = lh._normalize_grain_dtype(pd.DataFrame([
            _grain_row("E1", "A", 0, "WH1"),
            _grain_row("E1", "A", 9, "WH1"),
        ]))
        sub = lh._grain_by_pair(dg)[("E1", "A")]
        self.assertEqual(len(sub), 1)
        self.assertEqual(float(sub["qty"].sum()), 9.0)

    def test_untouched_when_no_duplicate(self):
        dg = lh._normalize_grain_dtype(pd.DataFrame([
            _grain_row("E1", "A", 6, "WH1"),
            _grain_row("E1", "B", 4, "WH1"),
        ]))
        lookup = lh._grain_by_pair(dg)
        self.assertEqual(len(lookup[("E1", "A")]), 1)
        self.assertEqual(len(lookup[("E1", "B")]), 1)


class TestMergeDuplicateImportKeys(unittest.TestCase):
    """ตาข่ายปลายทาง — กันแถวซ้ำที่มาจากเส้นทางอื่น (เติมแถวศูนย์ / เติม dim จาก Fabric)"""

    def _row(self, emp, sku, boxes, wh, *, area="10", st="S1", div="D1", province="P1"):
        return {
            "emp_id": emp,
            "sku": sku,
            "allocated_boxes": boxes,
            "salestype": st,
            "divisioncode": div,
            "areacode": area,
            "provincecode": province,
            "warehouse_code": wh,
        }

    def test_different_warehouse_rows_are_left_alone(self):
        df = pd.DataFrame([
            self._row("E1", "A", 6, "WH1"),
            self._row("E1", "A", 4, "WH2"),
        ])
        out, removed = lh._merge_duplicate_import_keys(df)
        self.assertEqual(removed, 0, "คนละคลัง = คนละคีย์ ไม่ใช่แถวซ้ำ")
        self.assertEqual(len(out), 2)
        self.assertEqual(int(out["allocated_boxes"].sum()), 10, "ยอดรวมห้ามเปลี่ยน")

    def test_same_warehouse_duplicate_keys_are_summed_not_dropped(self):
        df = pd.DataFrame([
            self._row("E1", "A", 6, "WH1"),
            self._row("E1", "A", 4, "WH1"),
        ])
        out, removed = lh._merge_duplicate_import_keys(df)
        self.assertEqual(removed, 1)
        self.assertEqual(len(out), 1)
        self.assertEqual(int(out["allocated_boxes"].sum()), 10, "ยอดรวมห้ามเปลี่ยน")

    def test_total_boxes_preserved_across_mixed_frame(self):
        df = pd.DataFrame([
            self._row("E1", "A", 6, "WH1"),
            self._row("E1", "A", 4, "WH1"),   # ← คีย์ซ้ำจริง (คลังเดียวกัน)
            self._row("E1", "A", 3, "WH2"),   # ← คนละคลัง = คนละคีย์ ต้องอยู่ต่อ
            self._row("E1", "B", 5, "WH1"),
            self._row("E2", "A", 7, "WH1", province="P2"),
        ])
        before = int(df["allocated_boxes"].sum())
        out, removed = lh._merge_duplicate_import_keys(df)
        self.assertEqual(removed, 1)
        self.assertEqual(int(out["allocated_boxes"].sum()), before)
        self.assertEqual(len(out), 4)

    def test_rows_with_incomplete_dims_are_left_alone(self):
        """dim ยังไม่ครบ = ยังไม่ใช่คีย์จริง ห้ามเอามารวมกัน"""
        df = pd.DataFrame([
            self._row("E1", "A", 6, "WH1", st="", div="", area=""),
            self._row("E1", "A", 4, "WH1", st="", div="", area=""),
        ])
        out, removed = lh._merge_duplicate_import_keys(df)
        self.assertEqual(removed, 0)
        self.assertEqual(len(out), 2)

    def test_noop_returns_same_frame(self):
        df = pd.DataFrame([
            self._row("E1", "A", 6, "WH1"),
            self._row("E1", "B", 4, "WH1"),
        ])
        out, removed = lh._merge_duplicate_import_keys(df)
        self.assertEqual(removed, 0)
        self.assertEqual(int(out["allocated_boxes"].sum()), 10)


class TestBuiltFileHasNoDuplicateKeys(unittest.TestCase):
    """ปลายทางจริง: ไฟล์ที่จะอัปโหลดต้องไม่มีคีย์ซ้ำ (คีย์ 7 คอลัมน์) และยอดต้องครบ"""

    SUP = "SLDUP"
    KEY = [
        "PRODUCTCODE",
        "SALESTYPE",
        "DIVISIONCODE",
        "SALESMANCODE",
        "AREACODE",
        "PROVINCECODE",
        "WAREHOUSECODE",
    ]

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data", exist_ok=True)
        pd.DataFrame([
            _grain_row("E1", "A", 6, "WH1"),
            _grain_row("E1", "A", 4, "WH2"),   # ← คนละคลัง = คนละคีย์ ต้องได้สองบรรทัด
            _grain_row("E2", "A", 5, "WH1", province="P2"),
        ]).to_csv(f"data/tga_lines_{self.SUP}_2026_08.csv", index=False)
        pd.DataFrame([
            {"sku": "A", "supervisor_target_boxes": 25, "price_per_box": 100.0},
        ]).to_csv(f"data/target_boxes_{self.SUP}_2026_08.csv", index=False)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _req(self):
        return LakehouseUploadRequest(
            sup_id=self.SUP,
            target_month=8,
            target_year=2026,
            upload_user_code="TESTER",
            allocations=[
                {"emp_id": "E1", "sku": "A", "allocated_boxes": 10},
                {"emp_id": "E2", "sku": "A", "allocated_boxes": 15},
            ],
        )

    def test_no_duplicate_upsert_keys_in_output(self):
        out, _dropped, _preview, _shortfall = lh._build_tga_upload_dataframe(
            self._req(), drop_incomplete_rows=True
        )
        dupes = out.duplicated(subset=self.KEY).sum()
        self.assertEqual(
            int(dupes), 0,
            "ไฟล์ที่ส่งต้องไม่มีคีย์ซ้ำ ไม่งั้นตัวนำเข้าจะข้ามแถวหลังทิ้งเงียบ ๆ",
        )

    def test_total_boxes_sent_equals_boxes_allocated(self):
        out, _dropped, _preview, _shortfall = lh._build_tga_upload_dataframe(
            self._req(), drop_incomplete_rows=True
        )
        self.assertEqual(
            int(out["QUANTITYCASE"].sum()), 25,
            "ยอดรวมที่ส่งต้องเท่ากับที่กระจายไว้เป๊ะ ๆ",
        )

    def test_split_employee_gets_one_row_per_warehouse(self):
        """
        E1 มีเป้าอยู่สองคลัง (6 กับ 4) และถูกกระจายให้ 10 หีบ
        → ต้องได้สองบรรทัด แยกตามคลัง รวมกันได้ 10 พอดี

        ก่อนคลังเข้าคีย์: ยุบเหลือบรรทัดเดียว 10 หีบลง WH1 — WH2 ค้างเลขเดิม
        ทำให้ยอดที่อ่านกลับมากกว่าไฟล์เสมอ
        """
        out, _dropped, _preview, _shortfall = lh._build_tga_upload_dataframe(
            self._req(), drop_incomplete_rows=True
        )
        e1 = out[out["SALESMANCODE"] == "E1"]
        self.assertEqual(len(e1), 2, "พนักงานสองคลังต้องได้สองบรรทัด")
        self.assertEqual(
            set(e1["WAREHOUSECODE"]), {"WH1", "WH2"},
            "ต้องครบทั้งสองคลัง ไม่ใช่ยุบทิ้งคลังใดคลังหนึ่ง",
        )
        self.assertEqual(
            int(e1["QUANTITYCASE"].sum()), 10,
            "รวมสองบรรทัดต้องเท่ากับที่กระจายให้เป๊ะ",
        )


class TestNoRowMergingAnywhereInThePipeline(unittest.TestCase):
    """
    คุณสมบัติหลักที่ต้องไม่หลุด: **ไฟล์ที่ส่งต้องมีบรรทัดต่อคลังครบเท่ากับที่ปลายทางถืออยู่**

    ตรึงไว้เป็น 1:1 เพราะการรวมแถวเกิดได้หลายจุดในท่อ (ยุบ grain, รวมคีย์ก่อนเขียนไฟล์,
    normalize payload) พลาดจุดเดียวก็ทำให้คลังหนึ่งได้เป้าทั้งก้อนและอีกคลังค้างเลขเดิม
    โดยไม่มีอะไรฟ้อง — ซึ่งเป็นอาการเดิมก่อนคลังเข้าคีย์

    ตรวจกับข้อมูลจริงแล้วด้วย (นอกชุดเทส เพราะ data/ ไม่เข้า git):
    SL460 งวด 09/2026 grain 1,593 แถว → ไฟล์ 1,593 แถว · คีย์ซ้ำ 0 · หีบ 18,100 เท่ากันเป๊ะ
    """

    SUP = "SLWH"
    KEY7 = [
        "PRODUCTCODE",
        "SALESTYPE",
        "DIVISIONCODE",
        "SALESMANCODE",
        "AREACODE",
        "PROVINCECODE",
        "WAREHOUSECODE",
    ]

    # E1×A อยู่สามคลัง · E1×B อยู่สองคลังแต่เป้าเป็น 0 ทั้งคู่ (เคสล้างเป้าเดิม)
    # E2×A คลังเดียว · E3×A คลังว่าง (ว่างก็เป็นค่าคีย์ค่าหนึ่ง)
    GRAIN = [
        _grain_row("E1", "A", 6, "WH1"),
        _grain_row("E1", "A", 3, "WH2"),
        _grain_row("E1", "A", 1, "WH3"),
        _grain_row("E1", "B", 0, "WH1"),
        _grain_row("E1", "B", 0, "WH2"),
        _grain_row("E2", "A", 5, "WH1"),
        _grain_row("E3", "A", 4, ""),
    ]

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data", exist_ok=True)
        pd.DataFrame(self.GRAIN).to_csv(
            f"data/tga_lines_{self.SUP}_2026_08.csv", index=False
        )
        pd.DataFrame([
            {"sku": "A", "supervisor_target_boxes": 29, "price_per_box": 100.0},
            {"sku": "B", "supervisor_target_boxes": 0, "price_per_box": 100.0},
        ]).to_csv(f"data/target_boxes_{self.SUP}_2026_08.csv", index=False)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _build(self):
        req = LakehouseUploadRequest(
            sup_id=self.SUP,
            target_month=8,
            target_year=2026,
            upload_user_code="TESTER",
            allocations=[
                {"emp_id": "E1", "sku": "A", "allocated_boxes": 20},
                {"emp_id": "E1", "sku": "B", "allocated_boxes": 0},
                {"emp_id": "E2", "sku": "A", "allocated_boxes": 5},
                {"emp_id": "E3", "sku": "A", "allocated_boxes": 4},
            ],
        )
        out, _dropped, _preview, _shortfall = lh._build_tga_upload_dataframe(
            req, drop_incomplete_rows=True
        )
        return out

    def test_every_grain_row_becomes_exactly_one_file_row(self):
        out = self._build()
        self.assertEqual(
            len(out), len(self.GRAIN),
            "แถวในไฟล์ต้องเท่ากับแถว grain เป๊ะ — ไม่ยุบและไม่งอก",
        )

    def test_no_duplicate_seven_column_keys(self):
        out = self._build()
        self.assertEqual(int(out.duplicated(subset=self.KEY7).sum()), 0)

    def test_three_warehouse_pair_keeps_all_three_rows(self):
        out = self._build()
        e1a = out[(out["SALESMANCODE"] == "E1") & (out["PRODUCTCODE"] == "A")]
        self.assertEqual(len(e1a), 3, "สามคลัง = สามบรรทัด")
        self.assertEqual(set(e1a["WAREHOUSECODE"]), {"WH1", "WH2", "WH3"})
        self.assertEqual(int(e1a["QUANTITYCASE"].sum()), 20, "รวมแล้วต้องเท่าที่กระจายให้")

    def test_zero_box_pair_still_clears_every_warehouse(self):
        """หีบ 0 ต้องไปถึงทุกคลัง ไม่งั้นเป้าเดิมของคลังที่ไม่ได้ส่งจะค้างตลอดไป"""
        out = self._build()
        e1b = out[(out["SALESMANCODE"] == "E1") & (out["PRODUCTCODE"] == "B")]
        self.assertEqual(len(e1b), 2)
        self.assertEqual(set(e1b["WAREHOUSECODE"]), {"WH1", "WH2"})
        self.assertEqual(int(e1b["QUANTITYCASE"].sum()), 0)

    def test_empty_warehouse_row_is_sent_normally(self):
        """WAREHOUSECODE ว่างเป็นค่าคีย์ค่าหนึ่ง ไม่ใช่แถวที่ต้องคัดทิ้ง"""
        out = self._build()
        e3 = out[out["SALESMANCODE"] == "E3"]
        self.assertEqual(len(e3), 1)
        self.assertEqual(str(e3.iloc[0]["WAREHOUSECODE"]), "")
        self.assertEqual(int(e3.iloc[0]["QUANTITYCASE"]), 4)

    def test_total_boxes_preserved(self):
        out = self._build()
        self.assertEqual(int(out["QUANTITYCASE"].sum()), 29)


if __name__ == "__main__":
    unittest.main()
