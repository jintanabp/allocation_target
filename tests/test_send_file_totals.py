"""
ด่านสุดท้าย: ยอดในไฟล์ที่จะอัปโหลดต้องเท่ากับผลกระจายหีบเป๊ะ ๆ

ทำไมต้องมีอีกด่านทั้งที่มีประตูตรวจเป้าอยู่แล้ว: ประตูแรกตรวจตั้งแต่ก่อนแตกแถวตาม
TGA grain, เติมแถวศูนย์, ยุบคีย์ซ้ำ และตัดแถวที่ dim ไม่ครบ — ตัวเลขใน .xlsx
ที่ส่งขึ้นไปจริงจึงไม่เคยถูกตรวจซ้ำเลย เคสคีย์ upsert ซ้ำที่ทำหีบหายเงียบ ๆ
รอดประตูแรกมาได้ทุกครั้งเพราะตอนตรวจยอดยังถูกอยู่

กติกา: ห้ามน้อยกว่าและห้ามมากกว่า และห้ามมี flag ให้กดข้าม เพราะส่วนต่างตรงนี้
ไม่ใช่การตัดสินใจของผู้ใช้ แต่แปลว่าท่อแปลงข้อมูลพัง
"""

from __future__ import annotations

import inspect
import logging
import os
import sys
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from fastapi import HTTPException  # noqa: E402

from backend.services import lakehouse as lh  # noqa: E402

logging.disable(logging.CRITICAL)


def _file_df(rows):
    return pd.DataFrame([{"sku": s, "allocated_boxes": b} for s, b in rows])


class TestBoxesBySku(unittest.TestCase):
    def test_sums_and_strips(self):
        df = _file_df([(" A ", 3), ("A", 4), ("B", 5)])
        self.assertEqual(lh._boxes_by_sku(df), {"A": 7, "B": 5})

    def test_empty(self):
        self.assertEqual(lh._boxes_by_sku(pd.DataFrame()), {})


class TestFilePreservesPayloadTotals(unittest.TestCase):
    def _assert(self, file_rows, payload, exempt=None):
        lh._assert_file_preserves_payload_totals(
            _file_df(file_rows), payload, sup_id="SLTEST", exempt_skus=exempt or set()
        )

    def test_equal_totals_pass(self):
        self._assert([("A", 10), ("B", 5)], {"A": 10, "B": 5})

    def test_fewer_boxes_blocks(self):
        with self.assertRaises(HTTPException) as ctx:
            self._assert([("A", 6), ("B", 5)], {"A": 10, "B": 5})
        d = ctx.exception.detail
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(d["code"], "send_file_total_changed")
        self.assertEqual(d["diff_boxes"], -4)
        self.assertEqual(
            d["diffs"][0],
            {"sku": "A", "payload_boxes": 10, "file_boxes": 6, "diff": -4},
            "ต้องบอกได้ว่า SKU ไหนหายกี่หีบ",
        )

    def test_more_boxes_also_blocks(self):
        """ห้ามมากกว่าด้วย — แถวงอกจากการเติม dim ซ้ำก็ทำเป้าเกินได้"""
        with self.assertRaises(HTTPException) as ctx:
            self._assert([("A", 12)], {"A": 10})
        self.assertEqual(ctx.exception.detail["diff_boxes"], 2)
        self.assertEqual(ctx.exception.detail["diffs"][0]["diff"], 2)

    def test_sku_vanishing_entirely_blocks(self):
        with self.assertRaises(HTTPException) as ctx:
            self._assert([("A", 10)], {"A": 10, "B": 5})
        self.assertEqual(ctx.exception.detail["diffs"][0]["sku"], "B")

    def test_exempt_sku_is_skipped(self):
        """SKU ที่ตัดทั้งตัวเพราะไม่มีใน Target Sun — ประตูที่สองรายงานแยกอยู่แล้ว"""
        self._assert([("A", 10)], {"A": 10, "B": 5}, exempt={"B"})

    def test_exemption_does_not_hide_other_skus(self):
        with self.assertRaises(HTTPException) as ctx:
            self._assert([("A", 7)], {"A": 10, "B": 5}, exempt={"B"})
        skus = [d["sku"] for d in ctx.exception.detail["diffs"]]
        self.assertEqual(skus, ["A"])


class TestGateCannotBeBypassed(unittest.TestCase):
    """ด่านนี้ต้องไม่มีทางกดข้าม — ตรวจที่ตัวโค้ด ไม่ใช่ที่ผลลัพธ์"""

    def test_no_confirm_flag_in_the_check(self):
        src = inspect.getsource(lh._assert_file_preserves_payload_totals)
        code = "\n".join(ln.split("#")[0] for ln in src.splitlines())
        self.assertNotIn(
            "confirm", code,
            "ยอดไฟล์ไม่ตรงผลกระจาย = ระบบพัง ห้ามให้ผู้ใช้กดยืนยันข้าม",
        )

    def test_no_env_escape_hatch(self):
        src = inspect.getsource(lh._assert_file_preserves_payload_totals)
        self.assertNotIn("environ", src)

    def test_builder_runs_the_check_before_writing_rows(self):
        src = inspect.getsource(lh._build_tga_upload_dataframe)
        self.assertIn("_assert_file_preserves_payload_totals(", src)
        self.assertLess(
            src.index("_assert_file_preserves_payload_totals("),
            src.index('"PRODUCTCODE"'),
            "ต้องตรวจก่อนประกอบแถวที่จะเขียนลงไฟล์",
        )

    def test_check_runs_after_dedup(self):
        src = inspect.getsource(lh._build_tga_upload_dataframe)
        self.assertLess(
            src.index("_merge_duplicate_import_keys("),
            src.index("_assert_file_preserves_payload_totals("),
            "ต้องตรวจ 'ของจริงหลังยุบคีย์ซ้ำ' ไม่ใช่ก่อนหน้านั้น",
        )


if __name__ == "__main__":
    unittest.main()
