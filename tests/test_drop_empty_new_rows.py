"""
ตัดแถวเป้า 0 ที่ "สร้างแถวใหม่เปล่า ๆ" ตอนส่งเข้า Target Sun (ค9)

คู่พนักงาน×สินค้าที่ Target Sun ไม่เคยมีมาก่อน (ไม่พบใน grain แต่เดา dim ได้จากแถวอื่น
ของพนักงานคนเดียวกัน — dims_inferred == True) และหีบ = 0 ไม่มีอะไรให้ทับ/ล้าง
ส่งไปก็แค่สร้างแถวเปล่าในปลายทางโดยไม่มีประโยชน์ — ตัดทิ้งได้อย่างปลอดภัย

ต้องแยกจากแถวหีบ 0 ที่ปลายทาง "มีอยู่แล้ว" (dims_inferred เป็น NaN เพราะเจอคู่นี้ใน grain)
ซึ่งต้องส่ง 0 ไปทับเพื่อล้างเป้างวดก่อน — ห้ามตัด · และห้ามกระทบยอดรวมต่อ SKU (I1)
เพราะแถวที่ตัดมีค่า 0 อยู่แล้วไม่ว่าตัดหรือไม่ตัด

ดู docs/next-plan-2026-09.md หัวข้อ 11.2 / "เริ่มงาน ค9 ตรงไหน"
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

SUP = "SLC9TEST"


def _grain(emp, sku, qty=5, wh="WH1"):
    return {
        "emp_id": emp,
        "sku": sku,
        "qty": qty,
        "salestype": "S1",
        "divisioncode": "D1",
        "areacode": "10",
        "provincecode": "P1",
        "warehouse_code": wh,
    }


class TestDropEmptyNewRows(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data", exist_ok=True)
        # E1 มีเป้าที่ปลายทางของ SKU OLD อยู่แล้ว — ให้ emp_dims_from_own_grain เดา dim
        # ของ E1 ได้ · NEWZERO / NEWBOXES ไม่มีแถวใน grain เลยทั้งคู่ (ต้องเดาจาก OLD)
        pd.DataFrame([_grain("E1", "OLD")]).to_csv(
            f"data/tga_lines_{SUP}_2026_09.csv", index=False
        )
        pd.DataFrame(
            [
                {"sku": "OLD", "supervisor_target_boxes": 5, "price_per_box": 100.0},
                {"sku": "NEWBOXES", "supervisor_target_boxes": 3, "price_per_box": 50.0},
                {"sku": "NEWZERO", "supervisor_target_boxes": 0, "price_per_box": 20.0},
            ]
        ).to_csv(f"data/target_boxes_{SUP}_2026_09.csv", index=False)
        # กันไม่ให้เส้นทางเติม dim วิ่งไป Fabric ระหว่างเทส (ห้ามมี network ในเทส)
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
            target_month=9,
            target_year=2026,
            upload_user_code="TESTER",
            allow_new_targetsun_rows=True,
            allocations=allocations
            or [
                {"emp_id": "E1", "sku": "OLD", "allocated_boxes": 5},
                {"emp_id": "E1", "sku": "NEWZERO", "allocated_boxes": 0},
                {"emp_id": "E1", "sku": "NEWBOXES", "allocated_boxes": 3},
            ],
            **kw,
        )

    def test_the_empty_new_row_is_cut_on_the_send_path(self):
        out, _dropped, _preview, _short = lh._build_tga_upload_dataframe(
            self._req(), drop_incomplete_rows=True
        )
        self.assertNotIn("NEWZERO", out["PRODUCTCODE"].tolist())

    def test_a_new_row_that_actually_has_boxes_is_kept(self):
        out, _dropped, _preview, _short = lh._build_tga_upload_dataframe(
            self._req(), drop_incomplete_rows=True
        )
        self.assertIn("NEWBOXES", out["PRODUCTCODE"].tolist())
        self.assertEqual(
            int(out[out["PRODUCTCODE"] == "NEWBOXES"]["QUANTITYCASE"].sum()), 3
        )

    def test_the_existing_pair_is_untouched(self):
        out, _dropped, _preview, _short = lh._build_tga_upload_dataframe(
            self._req(), drop_incomplete_rows=True
        )
        self.assertIn("OLD", out["PRODUCTCODE"].tolist())

    def test_per_sku_totals_are_unaffected_by_the_cut(self):
        """ตัดแถว 0 แล้วผลรวมทั้งไฟล์ต้องเหมือนเดิม (I1) — 0 ไม่กระทบผลรวมอยู่แล้ว"""
        out, _d, _p, _s = lh._build_tga_upload_dataframe(
            self._req(), drop_incomplete_rows=True
        )
        self.assertEqual(int(out["QUANTITYCASE"].sum()), 8)  # 5 (OLD) + 3 (NEWBOXES) + 0

    def test_excel_download_keeps_the_empty_new_row_for_review(self):
        """ห้ามตัดฝั่งดาวน์โหลด Excel — ผู้ใช้ต้องเห็นครบเพื่อตรวจแม้เป็นแถวเปล่า"""
        out, _dropped, _preview, _short = lh._build_tga_upload_dataframe(
            self._req(), drop_incomplete_rows=False
        )
        self.assertIn("NEWZERO", out["PRODUCTCODE"].tolist())

    def test_a_zero_row_for_a_pair_the_destination_already_has_is_never_cut(self):
        """
        คนละกรณีกับ NEWZERO — ปลายทางมี OLD อยู่แล้ว (เจอคู่นี้ใน grain ตรง ๆ)
        ถ้าส่ง 0 มาต้องส่งไปทับจริงเพื่อล้างเป้างวดก่อน ไม่ใช่แถวสร้างใหม่เปล่า ๆ
        """
        out, _d, _p, _s = lh._build_tga_upload_dataframe(
            self._req(allocations=[{"emp_id": "E1", "sku": "OLD", "allocated_boxes": 0}]),
            drop_incomplete_rows=True,
        )
        self.assertIn("OLD", out["PRODUCTCODE"].tolist())
        self.assertEqual(int(out["QUANTITYCASE"].sum()), 0)


if __name__ == "__main__":
    unittest.main()
