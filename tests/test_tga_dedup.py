"""
คีย์ upsert ของ Target Sun ซ้ำ = หีบหายเงียบ ๆ

คีย์คือ PRODUCTCODE+SALESTYPE+DIVISIONCODE+SALESMANCODE+AREACODE+PROVINCECODE
(ไม่มี WAREHOUSECODE) และตัวนำเข้า "ข้าม" แถวที่คีย์ซ้ำภายในไฟล์เดียวกัน
พนักงานที่ขายจากสองคลังจึงถูกแตกเป็นสองแถวที่คีย์เหมือนกัน แถวหลังถูกทิ้ง
โดยระบบยังรายงานว่าส่งสำเร็จ

ยืนยันด้วยข้อมูลจริงในแคชตอนตรวจ: 13 จาก 78 ไฟล์มีคีย์ซ้ำ, 1,229 คู่พนักงาน×สินค้า
ต่างกันแค่คลัง, ทีมที่หนักสุดหายราว 35% ของยอดทีมนั้น

กติกาที่ต้องคงไว้: ยุบแถวได้ แต่ต้อง **บวก** จำนวนหีบเสมอ ยอดรวมที่ส่งห้ามเปลี่ยน
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
    """ยุบที่ต้นทาง — แถว grain ที่ dim เหมือนกันต่างแค่คลัง คือแถวเดียวกันของ Oracle"""

    def test_same_dims_different_warehouse_collapses_and_sums_qty(self):
        dg = lh._normalize_grain_dtype(pd.DataFrame([
            _grain_row("E1", "A", 6, "WH1"),
            _grain_row("E1", "A", 4, "WH2"),
        ]))
        sub = lh._grain_by_pair(dg)[("E1", "A")]
        self.assertEqual(len(sub), 1, "คีย์เดียวกันต้องเหลือแถวเดียว")
        self.assertEqual(float(sub["qty"].sum()), 10.0, "qty ต้องถูกรวม ไม่ใช่ทิ้งแถว")
        self.assertEqual(
            sub.iloc[0]["warehouse_code"], "WH1",
            "เก็บคลังของแถวที่ qty มากสุด (WAREHOUSECODE ใช้ตอน insert เท่านั้น)",
        )

    def test_different_province_is_a_different_key_and_survives(self):
        dg = lh._normalize_grain_dtype(pd.DataFrame([
            _grain_row("E1", "A", 6, "WH1", province="P1"),
            _grain_row("E1", "A", 4, "WH2", province="P2"),
        ]))
        sub = lh._grain_by_pair(dg)[("E1", "A")]
        self.assertEqual(len(sub), 2, "คนละจังหวัด = คนละคีย์ ห้ามยุบรวมกัน")

    def test_zero_qty_row_sharing_a_key_is_absorbed(self):
        """
        แถว qty=0 ที่คีย์ชนกับแถวจริงอันตรายกว่า: ถ้ามันถูกเขียนก่อน
        Oracle จะเก็บ 0 แล้วข้ามแถวจริงทิ้ง — เป้าเหลือศูนย์
        """
        dg = lh._normalize_grain_dtype(pd.DataFrame([
            _grain_row("E1", "A", 0, "WH2"),
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

    def test_duplicate_keys_are_summed_not_dropped(self):
        df = pd.DataFrame([
            self._row("E1", "A", 6, "WH1"),
            self._row("E1", "A", 4, "WH2"),
        ])
        out, removed = lh._merge_duplicate_import_keys(df)
        self.assertEqual(removed, 1)
        self.assertEqual(len(out), 1)
        self.assertEqual(int(out["allocated_boxes"].sum()), 10, "ยอดรวมห้ามเปลี่ยน")

    def test_total_boxes_preserved_across_mixed_frame(self):
        df = pd.DataFrame([
            self._row("E1", "A", 6, "WH1"),
            self._row("E1", "A", 4, "WH2"),
            self._row("E1", "B", 5, "WH1"),
            self._row("E2", "A", 7, "WH1", province="P2"),
        ])
        before = int(df["allocated_boxes"].sum())
        out, removed = lh._merge_duplicate_import_keys(df)
        self.assertEqual(removed, 1)
        self.assertEqual(int(out["allocated_boxes"].sum()), before)
        self.assertEqual(len(out), 3)

    def test_rows_with_incomplete_dims_are_left_alone(self):
        """dim ยังไม่ครบ = ยังไม่ใช่คีย์จริง ห้ามเอามารวมกัน"""
        df = pd.DataFrame([
            self._row("E1", "A", 6, "WH1", st="", div="", area=""),
            self._row("E1", "A", 4, "WH2", st="", div="", area=""),
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
    """ปลายทางจริง: ไฟล์ที่จะอัปโหลดต้องไม่มีคีย์ซ้ำ และยอดต้องครบ"""

    SUP = "SLDUP"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data", exist_ok=True)
        pd.DataFrame([
            _grain_row("E1", "A", 6, "WH1"),
            _grain_row("E1", "A", 4, "WH2"),   # ← ต่างกันแค่คลัง = คีย์ซ้ำของ Oracle
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
        key = ["PRODUCTCODE", "SALESTYPE", "DIVISIONCODE", "SALESMANCODE", "AREACODE", "PROVINCECODE"]
        dupes = out.duplicated(subset=key).sum()
        self.assertEqual(
            int(dupes), 0,
            "ไฟล์ที่ส่งต้องไม่มีคีย์ซ้ำ ไม่งั้น Oracle จะข้ามแถวหลังทิ้งเงียบ ๆ",
        )

    def test_total_boxes_sent_equals_boxes_allocated(self):
        out, _dropped, _preview, _shortfall = lh._build_tga_upload_dataframe(
            self._req(), drop_incomplete_rows=True
        )
        self.assertEqual(
            int(out["QUANTITYCASE"].sum()), 25,
            "ยอดรวมที่ส่งต้องเท่ากับที่กระจายไว้เป๊ะ ๆ",
        )

    def test_split_employee_keeps_all_of_their_boxes_on_one_row(self):
        out, _dropped, _preview, _shortfall = lh._build_tga_upload_dataframe(
            self._req(), drop_incomplete_rows=True
        )
        e1 = out[out["SALESMANCODE"] == "E1"]
        self.assertEqual(len(e1), 1, "พนักงานสองคลังต้องเหลือแถวเดียวต่อคีย์")
        self.assertEqual(
            int(e1["QUANTITYCASE"].sum()), 10,
            "ก่อนแก้: 10 หีบถูกแบ่ง 6/4 แล้ว Oracle เก็บแค่ 6 — หาย 4 หีบเงียบ ๆ",
        )


if __name__ == "__main__":
    unittest.main()
