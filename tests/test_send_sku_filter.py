"""
ส่งเฉพาะบางสินค้า (sku_filter) — ใช้ตอน "ส่งเฉพาะผลกระจายใหม่"

กลไกเดียวกับส่งเฉพาะแบรนด์: SKU นอกรายการไม่ถูกแตะใน Target Sun (ของเดิมคงอยู่)
และประตู S1 เปลี่ยนจาก "ตรวจทุก SKU ที่มีเป้า" เป็น "ตรวจเฉพาะ SKU ใน payload"
แต่ SKU ที่ส่งจริงยังต้องเท่าเป้าเป๊ะ ไม่มีข้อยกเว้น
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

SUP = "SLFILT"


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


class TestSendSkuFilter(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data", exist_ok=True)
        # grain ครบทุกคู่ — เทสชุดนี้ไม่เกี่ยวกับการตัด SKU เพราะ grain ขาด
        pd.DataFrame([
            _grain("E1", "A"),
            _grain("E2", "A"),
            _grain("E1", "B"),
            _grain("E2", "B"),
        ]).to_csv(f"data/tga_lines_{SUP}_2026_08.csv", index=False)
        pd.DataFrame([
            {"sku": "A", "supervisor_target_boxes": 15, "price_per_box": 100.0},
            {"sku": "B", "supervisor_target_boxes": 10, "price_per_box": 50.0},
        ]).to_csv(f"data/target_boxes_{SUP}_2026_08.csv", index=False)

        # กันเส้นทางเติม dim วิ่งไป Fabric ระหว่างเทส (ห้ามมี network ในเทส)
        self._patch = patch.object(
            lh, "_enrich_emp_dimensions", side_effect=lambda df, rows_raw, **kw: df
        )
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _req(self, allocations=None, **kw):
        return LakehouseUploadRequest(
            sup_id=SUP,
            target_month=8,
            target_year=2026,
            upload_user_code="TESTER",
            allocations=allocations
            or [
                {"emp_id": "E1", "sku": "A", "allocated_boxes": 10},
                {"emp_id": "E2", "sku": "A", "allocated_boxes": 5},
                {"emp_id": "E1", "sku": "B", "allocated_boxes": 7},
                {"emp_id": "E2", "sku": "B", "allocated_boxes": 3},
            ],
            **kw,
        )

    def test_default_is_empty_meaning_send_everything(self):
        req = self._req()
        self.assertEqual(req.sku_filter, [])

    def test_file_contains_only_selected_skus(self):
        out, _dropped, _preview, _short = lh._build_tga_upload_dataframe(
            self._req(sku_filter=["A"]), drop_incomplete_rows=True
        )
        self.assertEqual(sorted(out["PRODUCTCODE"].unique().tolist()), ["A"])
        self.assertEqual(int(out["QUANTITYCASE"].sum()), 15)

    def test_included_sku_must_still_match_target_exactly(self):
        """sku_filter ไม่ใช่ทางลัดข้ามประตู — SKU ที่ส่งยังต้องเท่าเป้าเป๊ะ"""
        allocations = [
            {"emp_id": "E1", "sku": "A", "allocated_boxes": 9},   # รวม 14 ≠ เป้า 15
            {"emp_id": "E2", "sku": "A", "allocated_boxes": 5},
        ]
        with self.assertRaises(HTTPException) as ctx:
            lh._build_tga_upload_dataframe(
                self._req(allocations=allocations, sku_filter=["A"]),
                drop_incomplete_rows=True,
                enforce_targets=True,
            )
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail["code"], "send_target_mismatch")

    def test_omitted_skus_are_not_flagged_as_missing(self):
        """เหมือนส่งเฉพาะแบรนด์: SKU นอกรายการไม่โดนฟ้อง missing_from_payload"""
        out, _dropped, _preview, _short = lh._build_tga_upload_dataframe(
            self._req(sku_filter=["A"]),
            drop_incomplete_rows=True,
            enforce_targets=True,
        )
        self.assertEqual(sorted(out["PRODUCTCODE"].unique().tolist()), ["A"])

    def test_without_filter_missing_sku_is_still_caught(self):
        """ไม่ใส่ sku_filter = พฤติกรรมเดิมเป๊ะ — ส่งทุกแบรนด์ต้องครบทุก SKU ที่มีเป้า"""
        allocations = [
            {"emp_id": "E1", "sku": "A", "allocated_boxes": 10},
            {"emp_id": "E2", "sku": "A", "allocated_boxes": 5},
        ]
        with self.assertRaises(HTTPException) as ctx:
            lh._build_tga_upload_dataframe(
                self._req(allocations=allocations),
                drop_incomplete_rows=True,
                enforce_targets=True,
            )
        d = ctx.exception.detail
        self.assertEqual(d["code"], "send_target_mismatch")
        self.assertTrue(any(m.get("missing_from_payload") for m in d["mismatches"]))

    def test_unknown_sku_filter_is_a_clear_404(self):
        with self.assertRaises(HTTPException) as ctx:
            lh._build_tga_upload_dataframe(
                self._req(sku_filter=["ZZZ"]), drop_incomplete_rows=True
            )
        self.assertEqual(ctx.exception.status_code, 404)

    def test_all_zero_filtered_send_is_blocked(self):
        """ส่งหีบ 0 ทั้งชุดจะทับเป้าเดิมใน Target Sun เป็น 0 — ต้องบล็อกก่อนถึงประตูอื่น"""
        allocations = [
            {"emp_id": "E1", "sku": "A", "allocated_boxes": 0},
            {"emp_id": "E2", "sku": "A", "allocated_boxes": 0},
        ]
        with self.assertRaises(HTTPException) as ctx:
            lh._build_tga_upload_dataframe(
                self._req(allocations=allocations, sku_filter=["A"]),
                drop_incomplete_rows=True,
            )
        self.assertEqual(ctx.exception.status_code, 400)


class TestSkuFilterFrontendWiring(unittest.TestCase):
    """ฝั่งเว็บต้องส่ง sku_filter จากตัวเลือก "ส่งเฉพาะผลกระจายใหม่" เท่านั้น"""

    @classmethod
    def setUpClass(cls):
        path = os.path.join(REPO, "frontend", "app.js")
        with open(path, encoding="utf-8") as f:
            cls.src = f.read()

    def test_payload_builder_sends_sku_filter(self):
        self.assertIn("sku_filter: skuFilter", self.src)
        self.assertIn("_lakehouseFreshSkuFilter", self.src)

    def test_fresh_scope_defaults_to_send_all(self):
        # ไม่มีตัวเลือกใน DOM (modal ปิด/ไม่เคยกระจายเฉพาะ) ต้องหมายถึง "ส่งทั้งหมด"
        self.assertIn('return picked && picked.value === "fresh" ? "fresh" : "all"', self.src)

    def test_partial_realloc_passes_only_skus(self):
        self.assertIn("only_skus: onlySkus", self.src)
        self.assertIn("runReAllocationOnlyChanged", self.src)


if __name__ == "__main__":
    unittest.main()
