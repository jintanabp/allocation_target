"""
ตรวจเป้าปัจจุบันก่อนส่ง (freshness) และตรวจยอดที่ลงจริงหลังส่ง (readback)

ทั้งสองอย่างอ่านจาก Target Sun อย่างเดียว ไม่เขียนอะไรกลับ และในเทสถูก mock ทั้งหมด
— ไม่มีการต่อเน็ตและไม่มีการส่งใด ๆ

หลักการที่ต้องคงไว้:
  - freshness = "เตือน" ยืนยันข้ามได้ เพราะหลักการเทียบยอดยึด snapshot ที่ดึงมารอบนั้น
  - readback = "รายงาน" ห้าม raise เด็ดขาด ของส่งไปแล้ว ถ้าตรวจไม่ได้ก็แค่บอกว่าตรวจไม่ได้
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

from backend.services import lakehouse as lh  # noqa: E402
from backend.services import targetsun_read as tsr  # noqa: E402

logging.disable(logging.CRITICAL)


def _ts_rows(pairs):
    """(sku, qty) → รูปแบบแถวที่ Target Sun Read API คืนมา"""
    return {"rows": [{"PRODUCTCODE": s, "QUANTITYCASE": q} for s, q in pairs]}


class TestLiveTargetRead(unittest.TestCase):
    """ตัวอ่านเป้าปัจจุบัน — ต้องเงียบและคืน None เมื่อดูไม่ได้ ห้ามพังเส้นทางหลัก"""

    def _patches(self, *, enabled=True, source="targetsun"):
        return (
            patch.object(tsr, "is_enabled", return_value=enabled),
            patch.object(tsr, "get_target_read_source", return_value=source),
        )

    def test_sums_quantity_by_sku(self):
        a, b = self._patches()
        with a, b, patch.object(tsr, "fetch_target_rows", return_value=_ts_rows(
            [("X", 4), ("X", 6), ("Y", 5)]
        )):
            self.assertEqual(
                lh._live_target_boxes_by_sku("SLA", 8, 2026, ["E1", "E2"]),
                {"X": 10, "Y": 5},
            )

    def test_no_emp_codes_means_nothing_to_ask(self):
        self.assertIsNone(lh._live_target_boxes_by_sku("SLA", 8, 2026, []))

    def test_disabled_read_returns_none_without_calling_api(self):
        a, b = self._patches(enabled=False)
        with a, b, patch.object(tsr, "fetch_target_rows") as spy:
            self.assertIsNone(lh._live_target_boxes_by_sku("SLA", 8, 2026, ["E1"]))
            spy.assert_not_called()

    def test_fabric_source_is_not_comparable_so_returns_none(self):
        """เป้ามาจาก Fabric แต่ไปอ่าน Target Sun มาเทียบ = คนละแหล่ง ฟ้องผิดแน่"""
        a, b = self._patches(source="fabric")
        with a, b, patch.object(tsr, "fetch_target_rows") as spy:
            self.assertIsNone(lh._live_target_boxes_by_sku("SLA", 8, 2026, ["E1"]))
            spy.assert_not_called()

    def test_api_error_returns_none_instead_of_raising(self):
        a, b = self._patches()
        with a, b, patch.object(tsr, "fetch_target_rows", side_effect=RuntimeError("boom")):
            self.assertIsNone(lh._live_target_boxes_by_sku("SLA", 8, 2026, ["E1"]))


class TestTargetFreshness(unittest.TestCase):
    SUP = "SLFRESH"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data", exist_ok=True)
        pd.DataFrame([
            {"sku": "X", "supervisor_target_boxes": 10, "price_per_box": 1.0},
            {"sku": "Y", "supervisor_target_boxes": 5, "price_per_box": 1.0},
        ]).to_csv(f"data/target_boxes_{self.SUP}_2026_08.csv", index=False)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _check(self, live, **kw):
        with patch.object(lh, "_live_target_boxes_by_sku", return_value=live):
            lh.assert_target_snapshot_is_fresh(
                self.SUP, 8, 2026, emp_codes=["E1"], **kw
            )

    def test_unchanged_target_passes(self):
        self._check({"X": 10, "Y": 5})

    def test_changed_target_warns_with_details(self):
        with self.assertRaises(HTTPException) as ctx:
            self._check({"X": 12, "Y": 5})
        d = ctx.exception.detail
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(d["code"], "send_target_stale")
        self.assertEqual(d["confirm_field"], "confirm_stale_target")
        self.assertEqual(d["drift_boxes"], 2)
        self.assertEqual(
            d["drifts"][0],
            {"sku": "X", "loaded_boxes": 10, "current_boxes": 12, "diff": 2},
        )

    def test_new_sku_appearing_upstream_counts_as_drift(self):
        with self.assertRaises(HTTPException) as ctx:
            self._check({"X": 10, "Y": 5, "Z": 7})
        self.assertEqual(ctx.exception.detail["drifts"][0]["sku"], "Z")

    def test_user_confirmation_allows(self):
        self._check({"X": 12, "Y": 5}, confirmed=True)

    def test_unreadable_live_target_does_not_block(self):
        """อ่านของจริงไม่ได้ = เตือนไม่ได้ แต่การเทียบกับ snapshot ยังบังคับเต็มจากด่านอื่น"""
        self._check(None)

    def test_missing_snapshot_is_left_to_the_unverifiable_gate(self):
        with patch.object(lh, "_live_target_boxes_by_sku", return_value={"X": 1}):
            lh.assert_target_snapshot_is_fresh("SLNOFILE", 8, 2026, emp_codes=["E1"])


class TestBuilderStaysOffline(unittest.TestCase):
    """
    ตัวสร้างไฟล์ต้องไม่แตะเน็ต — เคยพลาดมาแล้วตอนเอาการเทียบเป้าปัจจุบัน
    ไปใส่ไว้ในตัวสร้าง ทำให้เทสต์ที่สร้างไฟล์ยิง query ขึ้น Target Sun จริง
    """

    def test_builder_does_not_read_live_targets(self):
        import inspect

        src = inspect.getsource(lh._build_tga_upload_dataframe)
        for name in ("_live_target_boxes_by_sku", "assert_target_snapshot_is_fresh"):
            self.assertNotIn(
                name, src,
                "ตัวสร้างไฟล์ต้องออฟไลน์ล้วน — การอ่านสดต้องอยู่ในเส้นทางส่งเท่านั้น",
            )

    def test_send_path_does_the_freshness_check(self):
        import inspect

        from backend.services import targetsun_import as ti

        src = inspect.getsource(ti)
        self.assertEqual(
            src.count("assert_target_snapshot_is_fresh("), 2,
            "ต้องเรียกทั้งเส้นทาง prepare และเส้นทางส่งรวดเดียว",
        )

    def test_live_calls_to_the_real_system_are_blocked(self):
        """
        กันชนระดับชุดเทสต์ — เทสต์ไหนไม่ mock ก็ยิงขึ้นระบบจริงไม่ได้

        ติดตั้งได้เฉพาะเส้นทางที่รองรับ (run_tests.py / pytest) เพราะ
        `unittest discover -s tests` โหลดไฟล์แบบ top-level ไม่ผ่าน tests/__init__.py
        — เส้นทางนั้นไม่มีทั้งกันชนนี้และกันชน config จึงเลิกใช้แล้ว (CI ใช้ run_tests.py)
        """
        import requests
        from requests.sessions import Session

        if not getattr(Session.request, "_alloc_test_guard", False):
            self.skipTest("ไม่มีกันชน — ต้องรันด้วย `python run_tests.py` หรือ pytest")
        with self.assertRaises(RuntimeError):
            requests.post("https://spcws.sahapat.com/spc/targetsun/x", json={})


class TestReadbackAfterSend(unittest.TestCase):
    def _verify(self, live, sent, emp=("E1",)):
        with patch.object(lh, "_live_target_boxes_by_sku", return_value=live):
            return lh.verify_after_send(
                "SLA", 8, 2026, sent_by_sku=sent, emp_codes=list(emp)
            )

    def test_landed_matches_file(self):
        res = self._verify({"X": 10, "Y": 5}, {"X": 10, "Y": 5})
        self.assertTrue(res["checked"])
        self.assertTrue(res["ok"])

    def test_rows_silently_skipped_upstream_are_caught(self):
        """เคสคีย์ upsert ซ้ำ: ปลายทางตอบว่าสำเร็จ แต่กินไม่ครบ"""
        res = self._verify({"X": 6}, {"X": 10})
        self.assertTrue(res["checked"])
        self.assertFalse(res["ok"])
        self.assertEqual(res["diff_boxes"], -4)
        self.assertEqual(
            res["diffs"][0],
            {"sku": "X", "sent_boxes": 10, "landed_boxes": 6, "diff": -4},
        )

    def test_extra_boxes_upstream_also_reported(self):
        res = self._verify({"X": 14}, {"X": 10})
        self.assertFalse(res["ok"])
        self.assertEqual(res["diff_boxes"], 4)

    def test_unreadable_is_reported_not_treated_as_failure(self):
        res = self._verify(None, {"X": 10})
        self.assertFalse(res["checked"])
        self.assertEqual(res["reason"], "read_unavailable")
        self.assertNotIn("ok", res)

    def test_nothing_sent_is_not_checked(self):
        res = self._verify({"X": 1}, {})
        self.assertFalse(res["checked"])
        self.assertEqual(res["reason"], "no_rows")

    def test_never_raises_even_when_the_read_explodes(self):
        with patch.object(lh, "_live_target_boxes_by_sku", side_effect=RuntimeError("boom")):
            res = lh.verify_after_send(
                "SLA", 8, 2026, sent_by_sku={"X": 1}, emp_codes=["E1"]
            )
        self.assertFalse(res["checked"])
        self.assertEqual(res["reason"], "error")


if __name__ == "__main__":
    unittest.main()
