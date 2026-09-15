"""
ล้างแถวเป้าเก่าที่ "หลุดจากผลกระจายรอบนี้" แต่ยังค้างอยู่ใน Target Sun (ค8)

คนละกรณีกับ _clear_no_target_employees_in_tga (คนทั้งคนไม่มีแถวเลยเพราะอยู่ใน
บัญชีดำ「ไม่ต้องตั้งเป้า」) — ที่นี่คือพนักงานยังอยู่ในรอบส่งนี้จริง แต่ SKU บางตัวที่
เขาเคยมีเป้า (เห็นใน grain ของ TGA) ไม่อยู่ในรอบนี้แล้ว

ข้อห้ามสำคัญที่เทสนี้ต้องกันไว้:
  1. ส่งแบบเต็ม (ไม่มี brand_filter/sku_filter) เท่านั้นถึงจะทำงาน — ส่งบางส่วนต้อง
     no-op เด็ดขาด ไม่งั้นจะเข้าใจผิดว่า "ตั้งใจไม่เลือกส่ง" คือ "หมดเป้าแล้ว"
  2. ต้องเป็น SKU ที่อยู่ใน "จักรวาล SKU ของรอบนี้" เท่านั้น (มีแถวของ SKU นั้นอยู่ใน
     df ไม่ว่าจะเป็นของพนักงานคนไหนก็ตาม) — พนักงานทีมอื่นที่ติดมาในโหมดรวมภาค/หน่วย
     มักมี SKU อื่นในเป้า/ประวัติของทีมตัวเองที่ไม่เกี่ยวกับรอบส่งนี้เลย (บั๊กจริงที่เจอ
     ระหว่างพัฒนา — ดู test_grain_across_teams.py / test_unit_wide_allocation.py)
  3. ไม่แตะพนักงานที่หายไปทั้งคนจาก df รอบนี้ (คนละเรื่องกับ _clear_no_target_employees_in_tga)
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.schemas import LakehouseUploadRequest  # noqa: E402
from backend.services import lakehouse as lh  # noqa: E402

logging.disable(logging.CRITICAL)


def _grain_row(emp, sku, area="10", province="P1", wh="WH1"):
    return {
        "emp_id": emp, "sku": sku, "qty": 5, "salestype": "S1",
        "divisioncode": "D1", "areacode": area, "provincecode": province,
        "warehouse_code": wh,
    }


def _alloc_row(emp, sku, boxes=5):
    return {"emp_id": emp, "sku": sku, "allocated_boxes": boxes}


class TestClearStaleEmployeeSkuRows(unittest.TestCase):
    def test_gap_for_a_present_employee_is_cleared_with_zero(self):
        """E1 ยังอยู่ในรอบนี้ (มีแถว SKU A) แต่ SKU B (อยู่ในจักรวาลรอบนี้ผ่าน E2)
        ที่ E1 เคยมีเป้า ไม่ได้ถูกส่งให้ E1 รอบนี้ — ต้องล้างด้วย 0"""
        df = pd.DataFrame([_alloc_row("E1", "A", 5), _alloc_row("E2", "B", 3)])
        dg = pd.DataFrame([_grain_row("E1", "A"), _grain_row("E1", "B", area="20")])
        out, cleared = lh._clear_stale_employee_sku_rows_in_tga(
            df, "SLX", dg=dg, full_send=True
        )
        e1b = out[(out["emp_id"] == "E1") & (out["sku"] == "B")]
        self.assertEqual(len(e1b), 1)
        self.assertEqual(int(e1b["allocated_boxes"].iloc[0]), 0)
        self.assertEqual(e1b["areacode"].iloc[0], "20")
        self.assertEqual(cleared, 1)

    def test_partial_send_is_a_no_op(self):
        """ส่งแบบเลือกแบรนด์/สินค้าบางส่วน (full_send=False) ต้องไม่แตะอะไรเลย"""
        df = pd.DataFrame([_alloc_row("E1", "A", 5)])
        dg = pd.DataFrame([_grain_row("E1", "A"), _grain_row("E1", "B")])
        out, cleared = lh._clear_stale_employee_sku_rows_in_tga(
            df, "SLX", dg=dg, full_send=False
        )
        self.assertEqual(cleared, 0)
        self.assertEqual(len(out), 1, "ต้องไม่มีแถวเพิ่ม")

    def test_sku_outside_this_rounds_universe_is_never_touched(self):
        """
        SKU ที่ไม่มีใครเลยในรอบนี้ถูกส่ง (ไม่อยู่ใน df เลยแม้แต่แถวเดียว) ต้องไม่ถูกล้าง
        แม้พนักงานที่มี SKU นั้นใน grain จะยังอยู่ในรอบนี้ก็ตาม — ป้องกันกรณีพนักงาน
        ทีมอื่นที่ติดมาในโหมดรวมภาค มี SKU อื่นในเป้าของทีมตัวเองที่ไม่เกี่ยวกับรอบนี้
        """
        df = pd.DataFrame([_alloc_row("E1", "A", 5)])
        dg = pd.DataFrame([_grain_row("E1", "A"), _grain_row("E1", "C")])
        out, cleared = lh._clear_stale_employee_sku_rows_in_tga(
            df, "SLX", dg=dg, full_send=True
        )
        self.assertEqual(cleared, 0)
        self.assertNotIn("C", out["sku"].tolist())

    def test_employee_entirely_absent_from_the_send_is_not_touched(self):
        """คนละงานกับ _clear_no_target_employees_in_tga — ไม่ไปยุ่งกับคนที่หายทั้งคน"""
        df = pd.DataFrame([_alloc_row("E1", "A", 5)])
        dg = pd.DataFrame([_grain_row("E1", "A"), _grain_row("E9", "A")])
        out, cleared = lh._clear_stale_employee_sku_rows_in_tga(
            df, "SLX", dg=dg, full_send=True
        )
        self.assertEqual(cleared, 0)
        self.assertNotIn("E9", out["emp_id"].tolist())

    def test_pair_already_present_is_not_duplicated(self):
        df = pd.DataFrame([_alloc_row("E1", "A", 5), _alloc_row("E1", "B", 2)])
        dg = pd.DataFrame([_grain_row("E1", "A"), _grain_row("E1", "B")])
        out, cleared = lh._clear_stale_employee_sku_rows_in_tga(
            df, "SLX", dg=dg, full_send=True
        )
        self.assertEqual(cleared, 0)
        self.assertEqual(len(out), 2)

    def test_empty_grain_is_a_no_op(self):
        df = pd.DataFrame([_alloc_row("E1", "A", 5)])
        out, cleared = lh._clear_stale_employee_sku_rows_in_tga(
            df, "SLX", dg=pd.DataFrame(), full_send=True
        )
        self.assertEqual(cleared, 0)
        self.assertEqual(len(out), 1)

    def test_empty_df_is_a_no_op(self):
        dg = pd.DataFrame([_grain_row("E1", "A")])
        out, cleared = lh._clear_stale_employee_sku_rows_in_tga(
            pd.DataFrame(), "SLX", dg=dg, full_send=True
        )
        self.assertEqual(cleared, 0)
        self.assertTrue(out.empty)


class TestFullSendGatingIsWiredCorrectly(unittest.TestCase):
    """โค้ดต้องคำนวณ full_send จาก brand_filter/sku_filter จริง ไม่ใช่ hardcode True"""

    def test_source_gates_on_brand_all_and_no_sku_filter(self):
        import inspect

        src = inspect.getsource(lh._build_tga_upload_dataframe)
        self.assertIn("_clear_stale_employee_sku_rows_in_tga(", src)
        self.assertIn('(brand_filter or "ALL").upper() == "ALL" and not sku_filter', src)


def _tga_row(emp, sku, area="10", province="P1", wh="WH1"):
    return {
        "emp_id": emp, "sku": sku, "qty": 5, "salestype": "S1",
        "divisioncode": "D1", "areacode": area, "provincecode": province,
        "warehouse_code": wh,
    }


class TestClearStaleRowsEndToEnd(unittest.TestCase):
    """
    ยืนยันผ่านเส้นทางส่งจริง (_build_tga_upload_dataframe ทั้งฟังก์ชัน) ว่าฟีเจอร์นี้
    ทำงานร่วมกับด่านตรวจที่มีอยู่แล้วได้ถูกต้อง โดยเฉพาะด่านที่ผู้ใช้กังวล: "ไม่ทำให้ส่งเป้าพลาด"

    ตัวตัดสินสุดท้ายคือ _assert_file_preserves_payload_totals — ด่านบังคับ (ไม่มี flag
    ข้าม) ที่เทียบยอดหีบต่อ SKU ของไฟล์สุดท้ายกับยอดตอนรับ payload เข้ามาครั้งแรก (ก่อน
    แตะ grain/ล้างแถวใด ๆ เลย) ถ้าโค้ดใหม่นี้เคยทำให้ยอดเพี้ยน ด่านนี้จะ raise 409 ทันที
    ไม่ปล่อยให้ส่งออกไปเงียบ ๆ — เทสชุดนี้จึงเน้นพิสูจน์ว่า "ไม่มีวันชน 409 นี้" ในเคสปกติ
    """

    SUP = "SLC8E2E"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data", exist_ok=True)
        self._patch = patch.object(
            lh, "_enrich_emp_dimensions", side_effect=lambda df, rows_raw, **kw: df
        )
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _write_grain(self, rows):
        pd.DataFrame(rows).to_csv(f"data/tga_lines_{self.SUP}_2026_09.csv", index=False)

    def _write_targets(self, rows):
        pd.DataFrame(rows).to_csv(f"data/target_boxes_{self.SUP}_2026_09.csv", index=False)

    def _req(self, allocations, **kw):
        return LakehouseUploadRequest(
            sup_id=self.SUP, target_month=9, target_year=2026, upload_user_code="T",
            allocations=allocations, **kw,
        )

    def test_stale_gap_is_cleared_without_ever_tripping_the_totals_guard(self):
        """
        E1 มีเป้าจริงแค่ A รอบนี้ แต่ grain ยังมี E1/B ค้างอยู่ (B ยังอยู่ในจักรวาลรอบนี้
        เพราะ E2 มี B) — ต้องล้าง E1/B ด้วย 0 และห้ามชนด่าน _assert_file_preserves_payload_totals
        """
        self._write_grain([
            _tga_row("E1", "A"),
            _tga_row("E1", "B", area="20"),  # เป้าเก่าของ E1 ที่หลุดไปแล้ว
            _tga_row("E2", "B"),
        ])
        self._write_targets([
            {"sku": "A", "supervisor_target_boxes": 10, "price_per_box": 1.0},
            {"sku": "B", "supervisor_target_boxes": 5, "price_per_box": 1.0},
        ])
        req = self._req([
            {"emp_id": "E1", "sku": "A", "allocated_boxes": 10},
            {"emp_id": "E2", "sku": "B", "allocated_boxes": 5},
        ])
        out, _dropped, _preview, _shortfall = lh._build_tga_upload_dataframe(
            req, drop_incomplete_rows=True
        )
        e1b = out[(out["SALESMANCODE"] == "E1") & (out["PRODUCTCODE"] == "B")]
        self.assertEqual(len(e1b), 1)
        self.assertEqual(int(e1b["QUANTITYCASE"].iloc[0]), 0)
        self.assertEqual(e1b["AREACODE"].iloc[0], "20", "ต้องใช้ dim ของ E1 เองจาก grain")
        self.assertEqual(
            int(out.loc[out["PRODUCTCODE"] == "A", "QUANTITYCASE"].sum()), 10,
            "SKU A ต้องไม่ถูกแตะเลย",
        )
        self.assertEqual(
            int(out.loc[out["PRODUCTCODE"] == "B", "QUANTITYCASE"].sum()), 5,
            "แถวล้าง 0 ต้องไม่ทำให้ยอดรวมของ B เปลี่ยน",
        )
        self.assertEqual(out.attrs.get("stale_rows_cleared_count"), 1)

    def test_sku_fully_excluded_for_other_reasons_also_drops_the_clear_row(self):
        """
        E3/C ส่งไม่ได้เพราะไม่มี grain ให้ C เลย (dims ไม่ครบ + หีบ > 0) → ทั้ง SKU C
        ถูกตัดตามนโยบายเดิม (ตัดทั้ง SKU ดีกว่าส่งครึ่ง ๆ กลาง ๆ) — แถวล้าง 0 ที่เพิ่งสร้าง
        ให้ E1/C ต้องถูกตัดไปด้วย ไม่ใช่หลุดรอดออกไปเป็นข้อยกเว้น
        """
        self._write_grain([
            _tga_row("E1", "A"),
            _tga_row("E1", "C", area="30"),  # เป้าเก่าของ E1 สำหรับ C
            # E3 ไม่มีแถว grain ของ C เลย — allow_new_targetsun_rows default False
            # จึงเดา dim ให้ไม่ได้ แถวจะไม่ครบคีย์
        ])
        self._write_targets([
            {"sku": "A", "supervisor_target_boxes": 10, "price_per_box": 1.0},
            {"sku": "C", "supervisor_target_boxes": 8, "price_per_box": 1.0},
        ])
        req = self._req([
            {"emp_id": "E1", "sku": "A", "allocated_boxes": 10},
            {"emp_id": "E3", "sku": "C", "allocated_boxes": 8},
        ])
        out, _dropped, _preview, _shortfall = lh._build_tga_upload_dataframe(
            req, drop_incomplete_rows=True
        )
        self.assertNotIn("C", out["PRODUCTCODE"].tolist(), "SKU C ต้องถูกตัดทั้งตัวรวมแถวล้าง 0 ด้วย")
        self.assertEqual(int(out.loc[out["PRODUCTCODE"] == "A", "QUANTITYCASE"].sum()), 10)

    def test_sku_filter_partial_send_never_clears_anything(self):
        """ส่งเฉพาะ SKU ที่เลือก (sku_filter) ต้องไม่ล้างช่องว่างใด ๆ เลย แม้จะมีจริง"""
        self._write_grain([
            _tga_row("E1", "A"),
            _tga_row("E1", "D", area="40"),  # ช่องว่างจริงของ E1 แต่ต้องไม่ถูกแตะรอบนี้
            _tga_row("E2", "D"),
        ])
        self._write_targets([
            {"sku": "A", "supervisor_target_boxes": 10, "price_per_box": 1.0},
            {"sku": "D", "supervisor_target_boxes": 5, "price_per_box": 1.0},
        ])
        req = self._req(
            [
                {"emp_id": "E1", "sku": "A", "allocated_boxes": 10},
                {"emp_id": "E2", "sku": "D", "allocated_boxes": 5},
            ],
            sku_filter=["A"],
        )
        out, _dropped, _preview, _shortfall = lh._build_tga_upload_dataframe(
            req, drop_incomplete_rows=True
        )
        self.assertNotIn("D", out["PRODUCTCODE"].tolist())
        self.assertEqual(out.attrs.get("stale_rows_cleared_count"), 0)

    def test_duplicate_warehouse_rows_for_the_same_stale_pair_are_both_cleared(self):
        """
        คู่เดียวกันมีแถว grain สองใบต่างกันแค่คลัง (ของจริงหลังคีย์ upsert รวม
        WAREHOUSECODE) — ต้องล้างทั้งสองแถวแยกกัน ไม่ยุบทิ้งกัน (คนละคีย์ upsert จริง)
        """
        self._write_grain([
            _tga_row("E1", "A"),
            _tga_row("E1", "B", wh="WH1"),
            _tga_row("E1", "B", wh="WH2"),
            _tga_row("E2", "B", wh="WH1"),
        ])
        self._write_targets([
            {"sku": "A", "supervisor_target_boxes": 10, "price_per_box": 1.0},
            {"sku": "B", "supervisor_target_boxes": 5, "price_per_box": 1.0},
        ])
        req = self._req([
            {"emp_id": "E1", "sku": "A", "allocated_boxes": 10},
            {"emp_id": "E2", "sku": "B", "allocated_boxes": 5},
        ])
        out, _dropped, _preview, _shortfall = lh._build_tga_upload_dataframe(
            req, drop_incomplete_rows=True
        )
        e1b = out[(out["SALESMANCODE"] == "E1") & (out["PRODUCTCODE"] == "B")]
        self.assertEqual(len(e1b), 2, "ต้องเหลือ 2 แถวแยกคลัง ไม่ถูกยุบรวมกัน")
        self.assertEqual(sorted(e1b["WAREHOUSECODE"].tolist()), ["WH1", "WH2"])
        self.assertTrue((e1b["QUANTITYCASE"] == 0).all())
        self.assertEqual(
            int(out.loc[out["PRODUCTCODE"] == "B", "QUANTITYCASE"].sum()), 5,
            "แถวล้างซ้ำสองใบต้องไม่ทำให้ยอดของ B เพี้ยนไปจากเป้าจริง",
        )


if __name__ == "__main__":
    unittest.main()
