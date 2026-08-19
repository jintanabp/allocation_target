"""
SKU ที่ส่งได้ไม่ครบ ต้องไม่ส่งทั้งตัว

เหตุที่ส่งไม่ครบคือ Target Sun ไม่เคยมีแถวของคู่พนักงาน×สินค้านั้น จึงเขียนทับไม่ได้
ถ้าส่งเฉพาะส่วนที่ส่งได้ เป้าของ SKU นั้นจะกลายเป็นครึ่ง ๆ กลาง ๆ — บางคนถูกทับด้วย
เลขใหม่ บางคนค้างเลขเก่า รวมแล้วไม่ตรงเป้าและไล่ต้นตอยาก

นโยบายที่ผู้ใช้เลือก: ตัด SKU นั้นทั้งตัว (ของเดิมใน Target Sun ไม่ถูกแตะ ยอดจึงไม่หาย)
แล้วแจ้งให้ชัดว่า SKU ไหน ใคร กี่หีบ เพื่อไปเกลี่ยเองใน Target Sun
ผลพลอยได้: ทุก SKU ที่อยู่ในไฟล์ต้องตรงเป้าเป๊ะ ไม่มีข้อยกเว้น
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

from fastapi import HTTPException  # noqa: E402

from backend.schemas import LakehouseUploadRequest  # noqa: E402
from backend.services import lakehouse as lh  # noqa: E402

logging.disable(logging.CRITICAL)

SUP = "SLPART"


def _grain(emp, sku, qty=5):
    return {
        "emp_id": emp,
        "sku": sku,
        "qty": qty,
        "salestype": "S1",
        "divisioncode": "D1",
        "areacode": "10",
        "provincecode": "P1",
        "warehouse_code": "WH1",
    }


class TestWholeSkuExclusion(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data", exist_ok=True)
        # มีเป้า TGA ให้ A ทั้งสองคน แต่ B มีแค่ E1 — E2/B จึงส่งไม่ได้
        pd.DataFrame([
            _grain("E1", "A"),
            _grain("E2", "A"),
            _grain("E1", "B"),
        ]).to_csv(f"data/tga_lines_{SUP}_2026_08.csv", index=False)
        pd.DataFrame([
            {"sku": "A", "supervisor_target_boxes": 15, "price_per_box": 100.0},
            {"sku": "B", "supervisor_target_boxes": 10, "price_per_box": 50.0},
        ]).to_csv(f"data/target_boxes_{SUP}_2026_08.csv", index=False)

        # กันไม่ให้เส้นทางเติม dim วิ่งไป Fabric ระหว่างเทส (ห้ามมี network ในเทส)
        self._patch = patch.object(
            lh, "_enrich_emp_dimensions", side_effect=lambda df, rows_raw, **kw: df
        )
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _req(self, **kw):
        return LakehouseUploadRequest(
            sup_id=SUP,
            target_month=8,
            target_year=2026,
            upload_user_code="TESTER",
            allocations=[
                {"emp_id": "E1", "sku": "A", "allocated_boxes": 10},
                {"emp_id": "E2", "sku": "A", "allocated_boxes": 5},
                {"emp_id": "E1", "sku": "B", "allocated_boxes": 7},
                {"emp_id": "E2", "sku": "B", "allocated_boxes": 3},   # ← ไม่มีเป้าใน TGA
            ],
            **kw,
        )

    def test_partially_sendable_sku_is_dropped_entirely(self):
        out, _dropped, _preview, _short = lh._build_tga_upload_dataframe(
            self._req(), drop_incomplete_rows=True
        )
        self.assertEqual(
            sorted(out["PRODUCTCODE"].unique().tolist()), ["A"],
            "B ส่งได้แค่บางคน จึงต้องไม่ถูกส่งเลย",
        )
        self.assertNotIn(
            7, out[out["PRODUCTCODE"] == "B"]["QUANTITYCASE"].tolist(),
            "ห้ามหลุดส่งเฉพาะคนที่มีเป้า",
        )

    def test_sku_that_is_fully_sendable_matches_its_target_exactly(self):
        out, _dropped, _preview, _short = lh._build_tga_upload_dataframe(
            self._req(), drop_incomplete_rows=True
        )
        self.assertEqual(
            int(out[out["PRODUCTCODE"] == "A"]["QUANTITYCASE"].sum()), 15,
            "SKU ที่ส่งต้องเท่าเป้าเป๊ะ",
        )
        self.assertEqual(int(out["QUANTITYCASE"].sum()), 15)

    def test_report_says_which_sku_and_how_many_boxes_were_left_out(self):
        _out, _dropped, _preview, shortfall = lh._build_tga_upload_dataframe(
            self._req(), drop_incomplete_rows=True
        )
        item = next(s for s in shortfall if s["sku"] == "B")
        self.assertTrue(item["excluded_whole_sku"])
        self.assertEqual(item["excluded_boxes"], 10, "ทั้ง SKU 10 หีบไม่ถูกส่ง")
        self.assertEqual(item["missing_boxes"], 3, "ส่วนที่ไม่มีเป้าใน TGA คือ 3 หีบ")
        self.assertEqual(item["sending_boxes"], 0, "ต้องไม่บอกว่ากำลังส่ง 7 หีบ")
        self.assertIn("E2", [p["emp_id"] for p in item["pairs"]])

    def test_send_path_asks_before_skipping_and_names_the_skus(self):
        with self.assertRaises(HTTPException) as ctx:
            lh._build_tga_upload_dataframe(
                self._req(), drop_incomplete_rows=True, enforce_targets=True
            )
        d = ctx.exception.detail
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(d["code"], "send_target_shortfall")
        self.assertEqual(d["confirm_field"], "confirm_manual_topup")
        self.assertTrue(d["whole_sku_excluded"])
        self.assertEqual(d["excluded_skus"], ["B"])
        self.assertEqual(d["excluded_boxes"], 10)

    def test_confirming_sends_only_the_complete_skus(self):
        out, _dropped, _preview, _short = lh._build_tga_upload_dataframe(
            self._req(confirm_manual_topup=True),
            drop_incomplete_rows=True,
            enforce_targets=True,
        )
        self.assertEqual(sorted(out["PRODUCTCODE"].unique().tolist()), ["A"])
        self.assertEqual(int(out["QUANTITYCASE"].sum()), 15)

    def test_excel_download_still_includes_everything(self):
        """ดาวน์โหลดมาตรวจต้องเห็นครบเสมอ — การตัด SKU เป็นเรื่องของเส้นทางส่งเท่านั้น"""
        out, _dropped, _preview, _short = lh._build_tga_upload_dataframe(
            self._req(), drop_incomplete_rows=False
        )
        self.assertEqual(sorted(out["PRODUCTCODE"].unique().tolist()), ["A", "B"])
        self.assertEqual(int(out["QUANTITYCASE"].sum()), 25)


class TestBatchRequestedExclusion(unittest.TestCase):
    """
    ทีมนี้ส่ง SKU ได้ครบ แต่ทีมอื่นในภาคส่งไม่ได้ → ต้องตัดชุดเดียวกันทุกทีม

    เพราะหีบถูกเกลี่ยข้ามทีมมาแล้ว การส่งเฉพาะทีมที่ส่งได้จะทำให้เป้าของ SKU นั้น
    ทั้งภาคครึ่ง ๆ กลาง ๆ — ด่าน verify-send-batch เป็นคนสั่งผ่าน exclude_skus
    """

    SUP = "SLBATCH"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data", exist_ok=True)
        pd.DataFrame([
            _grain("E1", "A"),
            _grain("E2", "A"),
            _grain("E1", "B"),
            _grain("E2", "B"),
        ]).to_csv(f"data/tga_lines_{self.SUP}_2026_08.csv", index=False)
        pd.DataFrame([
            {"sku": "A", "supervisor_target_boxes": 15, "price_per_box": 100.0},
            {"sku": "B", "supervisor_target_boxes": 10, "price_per_box": 50.0},
        ]).to_csv(f"data/target_boxes_{self.SUP}_2026_08.csv", index=False)
        self._patch = patch.object(
            lh, "_enrich_emp_dimensions", side_effect=lambda df, rows_raw, **kw: df
        )
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _req(self, **kw):
        return LakehouseUploadRequest(
            sup_id=self.SUP,
            target_month=8,
            target_year=2026,
            upload_user_code="TESTER",
            allocations=[
                {"emp_id": "E1", "sku": "A", "allocated_boxes": 10},
                {"emp_id": "E2", "sku": "A", "allocated_boxes": 5},
                {"emp_id": "E1", "sku": "B", "allocated_boxes": 7},
                {"emp_id": "E2", "sku": "B", "allocated_boxes": 3},
            ],
            **kw,
        )

    def test_without_exclusion_both_skus_are_sent(self):
        out, _d, _p, _s = lh._build_tga_upload_dataframe(
            self._req(), drop_incomplete_rows=True
        )
        self.assertEqual(sorted(out["PRODUCTCODE"].unique().tolist()), ["A", "B"])
        self.assertEqual(int(out["QUANTITYCASE"].sum()), 25)

    def test_batch_exclusion_drops_the_sku_here_too(self):
        out, _d, _p, shortfall = lh._build_tga_upload_dataframe(
            self._req(exclude_skus=["B"], confirm_manual_topup=True),
            drop_incomplete_rows=True,
            enforce_targets=True,
        )
        self.assertEqual(sorted(out["PRODUCTCODE"].unique().tolist()), ["A"])
        self.assertEqual(int(out["QUANTITYCASE"].sum()), 15)
        item = next(s for s in shortfall if s["sku"] == "B")
        self.assertTrue(item["excluded_whole_sku"])
        self.assertTrue(item["excluded_by_batch"])
        self.assertEqual(item["excluded_boxes"], 10, "ต้องบอกว่าไม่ได้ส่ง 10 หีบ")

    def test_unknown_sku_in_exclude_list_is_ignored(self):
        out, _d, _p, shortfall = lh._build_tga_upload_dataframe(
            self._req(exclude_skus=["NOPE"]), drop_incomplete_rows=True
        )
        self.assertEqual(sorted(out["PRODUCTCODE"].unique().tolist()), ["A", "B"])
        self.assertEqual(shortfall, [])

    def test_excel_download_ignores_batch_exclusion(self):
        """ดาวน์โหลดมาตรวจต้องเห็นครบเสมอ"""
        out, _d, _p, _s = lh._build_tga_upload_dataframe(
            self._req(exclude_skus=["B"]), drop_incomplete_rows=False
        )
        self.assertEqual(sorted(out["PRODUCTCODE"].unique().tolist()), ["A", "B"])


if __name__ == "__main__":
    unittest.main()
