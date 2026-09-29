"""
ประตูตรวจ "ยอดหีบต่อ SKU ต้องตรงเป้าทีม" ก่อนส่งเข้า Target Sun

กติกาที่ต้องคงไว้:
  - เส้นทาง **ส่งจริง** เท่านั้นที่บล็อก (409)
  - เส้นทาง **ดาวน์โหลด Excel มาตรวจ** ต้องทำได้เสมอ แม้ตัวเลขยังไม่ตรง
    (ถ้าบล็อกตรงนั้นด้วย ยิ่งมีปัญหายิ่งตรวจไม่ได้ = กลับหัวกลับหาง)
  - **ไม่มีทางยืนยันข้าม** (29 ก.ย. 2026): ยอดหีบห้ามขาดหรือเกินแม้แต่หีบเดียว
    เดิมมี confirm_target_mismatch / confirm_unverifiable_target / ALLOC_ALLOW_MISMATCH
  - ส่งรวมภาค (defer_to_batch) ไม่บล็อกรายทีม แต่ยกให้ด่านยอดรวมทั้งภาคตัดสิน
"""

from __future__ import annotations

import inspect
import logging
import os
import sys
import tempfile
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from fastapi import HTTPException  # noqa: E402

from backend.services import lakehouse as lh  # noqa: E402
from backend.services import targetsun_import as ti  # noqa: E402

logging.disable(logging.CRITICAL)


class TestSendTargetGate(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data", exist_ok=True)
        pd.DataFrame([
            {"sku": "A", "supervisor_target_boxes": 10, "price_per_box": 100.0},
            {"sku": "B", "supervisor_target_boxes": 5, "price_per_box": 50.0},
        ]).to_csv("data/target_boxes_SLTEST_2026_08.csv", index=False)
        pd.DataFrame([{"emp_id": "E1", "target_sun": 1000.0}]).to_csv(
            "data/target_sun_SLTEST_2026_08.csv", index=False
        )

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()
        os.environ.pop("ALLOC_ALLOW_MISMATCH", None)

    def _df(self, a_boxes: int, b_boxes: int = 5):
        return pd.DataFrame([
            {"emp_id": "E1", "sku": "A", "allocated_boxes": a_boxes},
            {"emp_id": "E1", "sku": "B", "allocated_boxes": b_boxes},
        ])

    def test_matching_totals_pass(self):
        lh._assert_send_matches_sup_targets(self._df(10), "SLTEST", 8, 2026)

    def test_mismatch_blocks_with_details(self):
        with self.assertRaises(HTTPException) as ctx:
            lh._assert_send_matches_sup_targets(self._df(7), "SLTEST", 8, 2026)
        self.assertEqual(ctx.exception.status_code, 409)
        d = ctx.exception.detail
        self.assertEqual(d["code"], "send_target_mismatch")
        self.assertEqual(d["mismatch_count"], 1)
        self.assertEqual(
            d["mismatches"][0],
            {"sku": "A", "sending_boxes": 7, "expected_boxes": 10,
             "missing_from_payload": False, "no_target": False},
            "ต้องบอกได้ว่า SKU ไหน ส่งเท่าไร เป้าเท่าไร และมีอยู่ใน payload ไหม",
        )
        self.assertNotIn("confirm_field", d, "ต้องไม่มีปุ่มยืนยันข้ามแล้ว")

    def test_sku_with_target_but_no_rows_is_caught_when_sending_all_brands(self):
        """
        รูที่เจอตอน audit: เป้า TGA เพิ่ม SKU ใหม่หลังหน้าเว็บโหลดขั้นที่ 1
        SKU นั้นจะไม่มีแถวใน payload เลย การวนจาก payload อย่างเดียวจึงไม่มีทางเห็น
        """
        df = self._df(10, 5)          # ส่งแค่ A กับ B
        pd.DataFrame([
            {"sku": "A", "supervisor_target_boxes": 10, "price_per_box": 100.0},
            {"sku": "B", "supervisor_target_boxes": 5, "price_per_box": 50.0},
            {"sku": "C", "supervisor_target_boxes": 80, "price_per_box": 70.0},   # ← ใหม่
        ]).to_csv("data/target_boxes_SLTEST_2026_08.csv", index=False)

        # ส่งทุกแบรนด์ → payload ต้องครบ → ต้องจับได้
        with self.assertRaises(HTTPException) as ctx:
            lh._assert_send_matches_sup_targets(
                df, "SLTEST", 8, 2026, check_missing_skus=True
            )
        d = ctx.exception.detail
        self.assertEqual(d["missing_sku_count"], 1)
        self.assertEqual(
            d["mismatches"][0],
            {"sku": "C", "sending_boxes": 0, "expected_boxes": 80,
             "missing_from_payload": True, "no_target": False},
        )

    def test_missing_sku_check_is_off_when_sending_one_brand(self):
        """
        ส่งแยกแบรนด์ payload มีแค่ SKU ของแบรนด์นั้นอยู่แล้ว
        ถ้าเปิดตรวจ SKU ที่หายไว้ จะฟ้องผิดทุก SKU ของแบรนด์อื่น
        """
        df = self._df(10, 5)
        pd.DataFrame([
            {"sku": "A", "supervisor_target_boxes": 10, "price_per_box": 100.0},
            {"sku": "B", "supervisor_target_boxes": 5, "price_per_box": 50.0},
            {"sku": "C", "supervisor_target_boxes": 80, "price_per_box": 70.0},
        ]).to_csv("data/target_boxes_SLTEST_2026_08.csv", index=False)
        lh._assert_send_matches_sup_targets(df, "SLTEST", 8, 2026)   # default = ปิด


    def test_send_path_enables_missing_sku_check_only_for_all_brands(self):
        src = inspect.getsource(lh._build_tga_upload_dataframe)
        self.assertIn("check_missing_skus=", src)
        self.assertIn('brand_filter or "ALL").upper() == "ALL"', src)

    def test_no_confirm_parameters_remain(self):
        sig = inspect.signature(lh._assert_send_matches_sup_targets)
        self.assertNotIn("confirmed", sig.parameters)
        self.assertNotIn("unverifiable_confirmed", sig.parameters)

    def test_env_escape_hatch_is_gone(self):
        """ALLOC_ALLOW_MISMATCH ถูกถอดออก — ตั้งไว้บน server ก็ไม่มีผล"""
        os.environ["ALLOC_ALLOW_MISMATCH"] = "1"
        with self.assertRaises(HTTPException):
            lh._assert_send_matches_sup_targets(self._df(7), "SLTEST", 8, 2026)

    def test_batch_send_defers_and_returns_differences(self):
        """ส่งรวมภาค: รายทีมต่างได้ (I7) — คืนรายการไว้ให้ bundle จด ไม่บล็อก"""
        out = lh._assert_send_matches_sup_targets(
            self._df(7), "SLTEST", 8, 2026, defer_to_batch=True
        )
        self.assertEqual([p["sku"] for p in out], ["A"])

    def test_batch_send_still_blocks_when_target_unreadable(self):
        with self.assertRaises(HTTPException) as ctx:
            lh._assert_send_matches_sup_targets(
                self._df(7), "SLNOFILE", 8, 2026, defer_to_batch=True
            )
        self.assertEqual(ctx.exception.detail["code"], "send_target_unverifiable")

    def test_fractional_boxes_block(self):
        df = pd.DataFrame([
            {"emp_id": "E1", "sku": "A", "allocated_boxes": 9.5},
            {"emp_id": "E2", "sku": "A", "allocated_boxes": 0.5},
            {"emp_id": "E1", "sku": "B", "allocated_boxes": 5.25},
        ])
        with self.assertRaises(HTTPException) as ctx:
            lh._assert_send_matches_sup_targets(df, "SLTEST", 8, 2026)
        self.assertEqual(ctx.exception.detail["code"], "send_boxes_not_integer")

    def test_missing_target_file_blocks_without_confirm(self):
        """
        เปลี่ยนพฤติกรรมโดยตั้งใจ (เดิม: ไม่มีไฟล์เป้า = ปล่อยผ่านเงียบ ๆ)

        ของเดิมทำให้ประตูที่แข็งแรงที่สุดปิดตัวเองในสถานการณ์ที่ควรทำงานที่สุด
        คือไฟล์เป้าถูกล้างตามอายุ cache แล้วผู้ใช้เปิด snapshot เก่ามาส่ง
        """
        with self.assertRaises(HTTPException) as ctx:
            lh._assert_send_matches_sup_targets(self._df(7), "SLNOFILE", 8, 2026)
        self.assertEqual(ctx.exception.status_code, 409)
        d = ctx.exception.detail
        self.assertEqual(d["code"], "send_target_unverifiable")
        self.assertNotIn("confirm_field", d)

    def test_global_legacy_target_file_is_not_used_as_a_substitute(self):
        """
        ไฟล์เป้า global เดิมไม่มี sup_id ในชื่อ ทีมที่โหลดทีหลังเขียนทับของทีมก่อน
        ถ้าประตูตกไปอ่านไฟล์นั้น = เทียบ payload ทีมนี้กับเป้าของอีกทีม
        """
        pd.DataFrame([
            {"sku": "A", "supervisor_target_boxes": 7, "price_per_box": 100.0},
        ]).to_csv("data/target_boxes.csv", index=False)
        with self.assertRaises(HTTPException) as ctx:
            lh._assert_send_matches_sup_targets(self._df(7), "SLNOFILE", 8, 2026)
        self.assertEqual(
            ctx.exception.detail["code"], "send_target_unverifiable",
            "ต้องไม่เอาไฟล์ global มาตรวจแทน แม้ตัวเลขจะบังเอิญตรงก็ตาม",
        )

    def test_sku_without_target_but_with_boxes_blocks(self):
        """ไม่มีเป้า = เป้า 0 — มีหีบเมื่อไรคือหีบงอก (เดิมข้ามไป)"""
        df = pd.DataFrame([{"emp_id": "E1", "sku": "ZZZ", "allocated_boxes": 3}])
        with self.assertRaises(HTTPException) as ctx:
            lh._assert_send_matches_sup_targets(df, "SLTEST", 8, 2026)
        p = ctx.exception.detail["mismatches"][0]
        self.assertEqual((p["sku"], p["expected_boxes"], p["no_target"]), ("ZZZ", 0, True))

    def test_sku_without_target_with_zero_boxes_passes(self):
        df = pd.DataFrame([{"emp_id": "E1", "sku": "ZZZ", "allocated_boxes": 0}])
        lh._assert_send_matches_sup_targets(df, "SLTEST", 8, 2026)


class TestShortfallFromDroppedRows(unittest.TestCase):
    """
    ประตูที่สอง: แถวที่ถูกตัด (ไม่มี SALESTYPE/DIVISION/AREACODE) มีหีบ > 0 → เป้าจะขาดจริง

    ต้องดูจาก "แถวที่ถูกตัด" ไม่ใช่ "แถวที่เหลือ" ไม่งั้นจับเคส SKU ที่ถูกตัดทั้งตัวไม่ได้
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data", exist_ok=True)
        pd.DataFrame([
            {"sku": "A", "supervisor_target_boxes": 3, "price_per_box": 100.0},
            {"sku": "B", "supervisor_target_boxes": 50, "price_per_box": 50.0},
        ]).to_csv("data/target_boxes_SLTEST_2026_08.csv", index=False)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _row(self, emp, sku, boxes, *, has_dims=True):
        dims = ("01", "D1", "A1") if has_dims else ("", "", "")
        return {
            "emp_id": emp, "sku": sku, "allocated_boxes": boxes,
            "salestype": dims[0], "divisioncode": dims[1], "areacode": dims[2],
            "provincecode": "P1", "warehouse_code": "WH1",
        }

    def test_dropped_zero_box_rows_are_not_a_shortfall(self):
        """เคสในหน้าจอจริง: ตัด 4 คู่ที่หีบ 0 — ไม่มีอะไรหาย ต้องไม่บล็อก"""
        df = pd.DataFrame([
            self._row("E1", "A", 3),
            self._row("S403", "A", 0, has_dims=False),
            self._row("S494", "A", 0, has_dims=False),
        ])
        self.assertEqual(lh._shortfall_from_dropped_rows(df, "SLTEST", 8, 2026), [])

    def test_dropped_row_with_boxes_is_reported(self):
        """เป้ารวม 3 · ตัด 1 หีบของคนหนึ่ง → Target Sun จะได้ 2"""
        df = pd.DataFrame([
            self._row("E1", "A", 1),
            self._row("E2", "A", 1),
            self._row("E3", "A", 1, has_dims=False),
        ])
        out = lh._shortfall_from_dropped_rows(df, "SLTEST", 8, 2026)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["sku"], "A")
        self.assertEqual(out[0]["missing_boxes"], 1)
        self.assertEqual(out[0]["sending_boxes"], 2)
        self.assertEqual(out[0]["expected_boxes"], 3)
        self.assertEqual(out[0]["pairs"], [{"emp_id": "E3", "allocated_boxes": 1}])

    def test_sku_dropped_entirely_is_caught(self):
        """
        เคสที่การ 'ย้าย _assert_send_matches_sup_targets มาหลัง drop' จะพลาด —
        SKU B หายจาก df ทั้งตัว groupby จึงไม่มี key ให้เทียบกับเป้า
        """
        df = pd.DataFrame([
            self._row("E1", "A", 3),
            self._row("E1", "B", 30, has_dims=False),
            self._row("E2", "B", 20, has_dims=False),
        ])
        out = lh._shortfall_from_dropped_rows(df, "SLTEST", 8, 2026)
        skus = {o["sku"]: o for o in out}
        self.assertIn("B", skus, "SKU ที่ถูกตัดทั้งตัวต้องถูกจับได้")
        self.assertEqual(skus["B"]["missing_boxes"], 50)
        self.assertEqual(skus["B"]["sending_boxes"], 0)
        self.assertEqual(skus["B"]["expected_boxes"], 50)
        self.assertEqual(skus["B"]["pair_count"], 2)

    def test_pairs_listed_for_manual_topup(self):
        """ผู้ใช้ต้องเอารายการนี้ไปกรอกเองใน Target Sun — ต้องครบทุกคู่"""
        df = pd.DataFrame([self._row(f"E{i}", "B", i, has_dims=False) for i in range(1, 6)])
        out = lh._shortfall_from_dropped_rows(df, "SLTEST", 8, 2026)
        self.assertEqual(out[0]["pair_count"], 5)
        self.assertEqual(len(out[0]["pairs"]), 5)
        self.assertEqual(out[0]["pairs"][0], {"emp_id": "E5", "allocated_boxes": 5},
                         "เรียงหีบมากไปน้อย ให้เห็นตัวใหญ่ก่อน")

    def test_missing_target_file_still_reports(self):
        """จำนวนหีบที่หายไม่ได้ขึ้นกับไฟล์เป้า — อ่านเป้าไม่ได้ก็ยังต้องเตือน"""
        df = pd.DataFrame([self._row("E1", "A", 4, has_dims=False)])
        out = lh._shortfall_from_dropped_rows(df, "SLNOFILE", 8, 2026)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["missing_boxes"], 4)
        self.assertIsNone(out[0]["expected_boxes"])

    def test_nothing_dropped_is_empty(self):
        df = pd.DataFrame([self._row("E1", "A", 3)])
        self.assertEqual(lh._shortfall_from_dropped_rows(df, "SLTEST", 8, 2026), [])


class TestManualTopupConfirmIsSeparate(unittest.TestCase):
    """
    confirm_manual_topup ต้องแยกจาก confirm_target_mismatch

    เหตุผล: ในโหมดรวมภาคยอดไม่ตรงเป้าทีมเป็นเรื่องปกติตาม I7 คนจึงกดยืนยันจนชิน
    ถ้าใช้ flag เดียวกัน ปัญหา master data จะถูกกดข้ามไปโดยไม่ได้อ่าน
    """

    def test_schema_keeps_topup_flag_but_drops_the_bypass_flags(self):
        from backend.schemas import LakehouseUploadRequest

        f = LakehouseUploadRequest.model_fields
        self.assertIn("confirm_manual_topup", f)
        self.assertIs(f["confirm_manual_topup"].default, False)
        self.assertNotIn("confirm_target_mismatch", f)
        self.assertNotIn("confirm_unverifiable_target", f)
        self.assertIn("send_batch_id", f)

    def test_old_clients_sending_the_removed_flags_are_ignored(self):
        """หน้าเว็บรุ่นเก่าที่ยังส่ง flag เดิมมา ต้องไม่ 422 และต้องไม่มีผลอะไร"""
        from backend.schemas import LakehouseUploadRequest

        req = LakehouseUploadRequest(
            sup_id="SLTEST", target_month=8, target_year=2026,
            confirm_target_mismatch=True, confirm_unverifiable_target=True,
        )
        self.assertFalse(hasattr(req, "confirm_target_mismatch"))

    def test_unverifiable_gate_has_no_bypass(self):
        src = inspect.getsource(lh._assert_send_matches_sup_targets)
        gate = src.split("send_target_unverifiable")[0]
        gate = gate[gate.index("if targets is None"):]
        code = "\n".join(ln.split("#")[0] for ln in gate.splitlines())
        self.assertNotIn("confirm", code)
        self.assertNotIn("return", code)

    def test_mismatch_confirm_does_not_bypass_shortfall_gate(self):
        src = inspect.getsource(lh._build_tga_upload_dataframe)
        gate = src.split("ประตูที่สอง")[1]
        # ตัดคอมเมนต์ทิ้งก่อน — ชื่อ flag ของประตูแรกถูกอ้างในคำอธิบายว่า "ไม่ใช่ตัวนี้"
        code = "\n".join(ln.split("#")[0] for ln in gate.splitlines())
        self.assertIn("confirm_manual_topup", code)
        self.assertNotIn(
            "confirm_target_mismatch", code,
            "ประตูหีบขาดต้องไม่ยอมให้ flag ของประตูแรกปลดล็อกได้",
        )


class TestGateWiring(unittest.TestCase):
    """ประตูต้องต่ออยู่กับเส้นทางส่งเท่านั้น — ตรวจที่การต่อสาย ไม่ใช่ที่ผลลัพธ์"""

    def test_builder_defaults_to_not_enforcing(self):
        sig = inspect.signature(lh._build_tga_upload_dataframe)
        self.assertIn("enforce_targets", sig.parameters)
        self.assertIs(
            sig.parameters["enforce_targets"].default, False,
            "ค่าเริ่มต้นต้องไม่บล็อก — ไม่งั้นทุกเส้นทางที่ใช้ตัวสร้างร่วมกันจะโดนหมด",
        )

    def test_xlsx_prepare_defaults_to_not_enforcing(self):
        sig = inspect.signature(lh.prepare_lakehouse_xlsx)
        self.assertIs(sig.parameters["enforce_targets"].default, False)

    def test_excel_download_path_does_not_enforce(self):
        src = inspect.getsource(lh.export_allocations_excel)
        self.assertNotIn(
            "enforce_targets", src,
            "ดาวน์โหลด Excel ต้องทำได้เสมอ แม้ยอดยังไม่ตรงเป้า",
        )

    def test_send_paths_enforce(self):
        src = inspect.getsource(ti)
        self.assertEqual(
            src.count("enforce_targets=True"), 2,
            "เส้นทางส่ง (prepare_targetsun_import + import_allocations_to_targetsun) ต้องเปิดทั้งคู่",
        )


if __name__ == "__main__":
    unittest.main()
