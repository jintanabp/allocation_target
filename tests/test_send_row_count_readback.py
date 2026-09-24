"""
ตรวจจำนวนแถวจริงใน Target Sun ก่อน/หลังส่ง — จับแถวซ้ำคนละคลัง (11.3 / ปริศนา SL453)
ที่ verify_after_send (ยอดหีบรวมต่อ SKU) มองไม่เห็น เพราะสองแถวคนละคลังบวกยอดกันแล้ว
ยังเท่าไฟล์ที่ส่งไปพอดี

อ่านจาก Target Sun อย่างเดียว ไม่เขียนอะไรกลับ และในเทสถูก mock ทั้งหมด —
ไม่มีการต่อเน็ตและไม่มีการส่งใด ๆ ห้ามส่งเข้า Target Sun จริงเด็ดขาดระหว่างเทส
"""

from __future__ import annotations

import logging
import os
import sys
import unittest
from unittest.mock import patch

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import lakehouse as lh  # noqa: E402
from backend.services import targetsun_read as tsr  # noqa: E402

logging.disable(logging.CRITICAL)


def _row(sku, emp, wh="", area="1", qty=1, salestype="0", division="B", province="10"):
    return {
        "PRODUCTCODE": sku,
        "SALESMANCODE": emp,
        "SALESTYPE": salestype,
        "DIVISIONCODE": division,
        "AREACODE": area,
        "PROVINCECODE": province,
        "WAREHOUSECODE": wh,
        "QUANTITYCASE": qty,
    }


class TestLiveTargetSnapshot(unittest.TestCase):
    """_live_target_snapshot อ่านสด 1 ครั้ง คืนทั้งยอดหีบ/จำนวนแถว/ชุดคีย์"""

    def _patches(self, *, enabled=True, source="targetsun"):
        return (
            patch.object(tsr, "is_enabled", return_value=enabled),
            patch.object(tsr, "get_target_read_source", return_value=source),
        )

    def test_by_sku_and_row_count_and_keys(self):
        rows = [_row("X", "E1", wh="R001"), _row("X", "E1", wh="R002"), _row("Y", "E2")]
        a, b = self._patches()
        with a, b, patch.object(tsr, "fetch_target_rows", return_value={"rows": rows}):
            snap = lh._live_target_snapshot("SLA", 8, 2026, ["E1", "E2"])
        self.assertEqual(snap["row_count"], 3)
        self.assertEqual(snap["by_sku"], {"X": 2, "Y": 1})
        # สองแถวแรกต่างกันแค่คลัง (R001/R002) ต้องเป็นคนละคีย์ ไม่ยุบรวม
        self.assertEqual(len(snap["keys"]), 3)

    def test_areacode_zero_is_not_dropped_as_blank(self):
        """AREACODE=0 (int) ต้องเป็นคีย์ '0' ไม่ใช่ '' — ไม่งั้นชนกับแถวคลัง/พื้นที่ว่างจริง"""
        rows = [_row("X", "E1", area=0)]
        a, b = self._patches()
        with a, b, patch.object(tsr, "fetch_target_rows", return_value={"rows": rows}):
            snap = lh._live_target_snapshot("SLA", 8, 2026, ["E1"])
        key = next(iter(snap["keys"]))
        self.assertEqual(key.split("|")[4], "0")

    def test_disabled_returns_none_without_calling_api(self):
        a, b = self._patches(enabled=False)
        with a, b, patch.object(tsr, "fetch_target_rows") as spy:
            self.assertIsNone(lh._live_target_snapshot("SLA", 8, 2026, ["E1"]))
            spy.assert_not_called()

    def test_api_error_returns_none_instead_of_raising(self):
        a, b = self._patches()
        with a, b, patch.object(tsr, "fetch_target_rows", side_effect=RuntimeError("boom")):
            self.assertIsNone(lh._live_target_snapshot("SLA", 8, 2026, ["E1"]))

    def test_boxes_by_sku_still_delegates_after_refactor(self):
        """_live_target_boxes_by_sku (ของเดิม) ต้องให้ผลเหมือนก่อน refactor เป๊ะ"""
        rows = [_row("X", "E1", qty=4), _row("X", "E1", wh="R2", qty=6), _row("Y", "E1", qty=5)]
        a, b = self._patches()
        with a, b, patch.object(tsr, "fetch_target_rows", return_value={"rows": rows}):
            self.assertEqual(
                lh._live_target_boxes_by_sku("SLA", 8, 2026, ["E1"]),
                {"X": 10, "Y": 5},
            )


class TestRowCountAfterSend(unittest.TestCase):
    """verify_row_count_after_send — ตัวจับแถวซ้ำหลักของฟีเจอร์นี้"""

    def _verify(self, *, before, after, file_keys, emp=("E1",)):
        with patch.object(lh, "_live_target_snapshot", return_value=after):
            return lh.verify_row_count_after_send(
                "SLA", 8, 2026,
                emp_codes=list(emp),
                before_snapshot=before,
                file_keys=file_keys,
            )

    def test_normal_send_reconciles(self):
        before = {"row_count": 5, "keys": {"k1", "k2", "k3", "k4", "k5"}}
        after = {"row_count": 7, "keys": set()}
        file_keys = {"k1", "k2", "k3", "k4", "k5", "new1", "new2"}  # 2 คีย์ใหม่จริง
        res = self._verify(before=before, after=after, file_keys=file_keys)
        self.assertTrue(res["checked"])
        self.assertTrue(res["ok"])
        self.assertEqual(res["before_count"], 5)
        self.assertEqual(res["after_count"], 7)
        self.assertEqual(res["expected_new_rows"], 2)
        self.assertEqual(res["actual_new_rows"], 2)
        self.assertEqual(res["unexpected_extra_rows"], 0)

    def test_duplicate_row_bug_detected(self):
        """ไฟล์มีคีย์ใหม่แค่ 1 แต่หลังส่งแถวเพิ่ม 2 แถว — ต้องฟ้องว่ามีส่วนเกิน"""
        before = {"row_count": 5, "keys": {"k1", "k2", "k3", "k4", "k5"}}
        after = {"row_count": 7, "keys": set()}
        file_keys = {"k1", "k2", "k3", "k4", "k5", "new1"}
        res = self._verify(before=before, after=after, file_keys=file_keys)
        self.assertTrue(res["checked"])
        self.assertFalse(res["ok"])
        self.assertEqual(res["expected_new_rows"], 1)
        self.assertEqual(res["actual_new_rows"], 2)
        self.assertEqual(res["unexpected_extra_rows"], 1)

    def test_missing_insert_also_flagged(self):
        """หลังส่งแถวไม่เพิ่มเลยทั้งที่ไฟล์มีคีย์ใหม่ — insert หายเงียบ ๆ ก็ต้องฟ้อง"""
        before = {"row_count": 5, "keys": {"k1", "k2", "k3", "k4", "k5"}}
        after = {"row_count": 5, "keys": set()}
        file_keys = {"k1", "k2", "k3", "k4", "k5", "new1", "new2"}
        res = self._verify(before=before, after=after, file_keys=file_keys)
        self.assertFalse(res["ok"])
        self.assertEqual(res["unexpected_extra_rows"], -2)

    def test_before_unavailable_reports_not_checked(self):
        res = self._verify(before=None, after={"row_count": 5, "keys": set()}, file_keys=set())
        self.assertFalse(res["checked"])
        self.assertEqual(res["reason"], "before_unavailable")
        self.assertNotIn("ok", res)

    def test_after_unavailable_reports_not_checked(self):
        res = self._verify(before={"row_count": 5, "keys": set()}, after=None, file_keys=set())
        self.assertFalse(res["checked"])
        self.assertEqual(res["reason"], "after_unavailable")

    def test_never_raises_even_when_after_read_explodes(self):
        with patch.object(lh, "_live_target_snapshot", side_effect=RuntimeError("boom")):
            res = lh.verify_row_count_after_send(
                "SLA", 8, 2026,
                emp_codes=["E1"],
                before_snapshot={"row_count": 1, "keys": set()},
                file_keys=set(),
            )
        self.assertFalse(res["checked"])
        self.assertEqual(res["reason"], "error")


class TestBuilderStaysOfflineExtended(unittest.TestCase):
    """ตัวสร้างไฟล์ต้องออฟไลน์ล้วน — เพิ่ม _live_target_snapshot เข้าไปในรายการต้องห้าม"""

    def test_builder_does_not_read_live_targets(self):
        import inspect

        src = inspect.getsource(lh._build_tga_upload_dataframe)
        for name in (
            "_live_target_snapshot",
            "_live_target_boxes_by_sku",
            "assert_target_snapshot_is_fresh",
        ):
            self.assertNotIn(
                name, src,
                "ตัวสร้างไฟล์ต้องออฟไลน์ล้วน — การอ่านสดต้องอยู่ในเส้นทางส่งเท่านั้น",
            )


if __name__ == "__main__":
    unittest.main()
