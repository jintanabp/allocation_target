"""
กันกดส่งซ้ำ/retry ระหว่างที่ POST เดิม (token เดียวกัน) ยังค้างอยู่จริง — ไม่มีด่านนี้
คำขอที่สองจะโหลด prepare bundle เดิมสำเร็จแล้วยิง POST เข้า Target Sun จริงซ้อนกันได้
(ได้ถึง TARGETSUN_IMPORT_TIMEOUT_SEC วินาที ค่าเริ่มต้น 600 — พบจากผลตรวจสอบระบบ 24 ก.ย. 2026
ยืนยันซ้ำจากสองทีมตรวจคนละเรื่องพร้อมกัน)

เทสนี้จำลอง concurrency จริงด้วย threading.Event ควบคุมจังหวะ ไม่ใช้ sleep เดา — mock
requests.post และปิด Target Sun read (is_enabled=False) ทั้งหมด ไม่มีการต่อเน็ตจริงเด็ดขาด
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.schemas import LakehouseUploadRequest  # noqa: E402
from backend.services import targetsun_import as tsi  # noqa: E402
from backend.services import targetsun_read as tsr  # noqa: E402

logging.disable(logging.CRITICAL)


class ClaimReleaseTokenTest(unittest.TestCase):
    def tearDown(self):
        tsi._import_in_flight_tokens.clear()

    def test_second_claim_of_same_token_is_rejected(self):
        tsi._claim_import_token("TOK1")
        with self.assertRaises(Exception) as ctx:
            tsi._claim_import_token("TOK1")
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail["code"], "send_already_in_progress")

    def test_release_allows_reclaiming(self):
        tsi._claim_import_token("TOK1")
        tsi._release_import_token("TOK1")
        tsi._claim_import_token("TOK1")  # ไม่ควร raise — ปล่อยแล้วต้องจับจองใหม่ได้
        tsi._release_import_token("TOK1")

    def test_different_tokens_do_not_block_each_other(self):
        tsi._claim_import_token("TOK1")
        tsi._claim_import_token("TOK2")  # ไม่ควร raise
        tsi._release_import_token("TOK1")
        tsi._release_import_token("TOK2")

    def test_release_of_unclaimed_token_is_a_no_op(self):
        tsi._release_import_token("NEVER_CLAIMED")  # ไม่ควร raise


class ConcurrentImportDoesNotDoubleSendTest(unittest.TestCase):
    """จำลองของจริง: กดส่งซ้ำระหว่างที่ POST เดิมยังค้างอยู่ — ต้องยิง Target Sun แค่ครั้งเดียว"""

    SUP = "SLDUPTEST"
    YEAR, MONTH = 2026, 9

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data", exist_ok=True)
        tsi._import_in_flight_tokens.clear()

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()
        tsi._import_in_flight_tokens.clear()

    def _make_bundle(self, token: str) -> None:
        tsi._save_prepare_bundle(
            token,
            content=b"fake-xlsx-bytes",
            fname="test.xlsx",
            sup_id=self.SUP,
            nrow=1,
            zero_rows=0,
            dropped_dims=0,
            not_in_ts=[],
            upload_user_code="TESTER",
            target_month=self.MONTH,
            target_year=self.YEAR,
            emp_codes=["E1"],
        )

    def test_second_call_while_first_still_posting_is_rejected_not_double_posted(self):
        token = "TOK-CONCURRENT-1"
        self._make_bundle(token)

        post_started = threading.Event()
        release_post = threading.Event()
        post_call_count = {"n": 0}

        def fake_post(*args, **kwargs):
            post_call_count["n"] += 1
            post_started.set()
            # ค้างไว้ตรงนี้เหมือน POST จริงที่ใช้เวลานาน — จำลองหน้าต่างที่คำขอที่สองแทรกได้
            release_post.wait(timeout=5)
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"success": True, "resultMsg": "ok", "result": {}}
            return resp

        req = LakehouseUploadRequest(
            sup_id=self.SUP,
            target_month=self.MONTH,
            target_year=self.YEAR,
            upload_user_code="TESTER",
            allocations=[],
            prepare_token=token,
        )

        results: dict = {}

        def call_first():
            with patch.object(tsr, "is_enabled", return_value=False), \
                 patch("backend.services.targetsun_import.requests.post", side_effect=fake_post):
                try:
                    results["first"] = tsi.import_prepared_targetsun(req)
                except Exception as e:  # ไม่ควรเกิด แต่เก็บไว้ให้เห็นถ้าพัง
                    results["first_error"] = e

        t1 = threading.Thread(target=call_first)
        t1.start()
        self.assertTrue(post_started.wait(timeout=5), "คำขอแรกต้องเริ่ม POST แล้วก่อนคำขอที่สอง")

        # คำขอที่สอง (จำลองกดส่งซ้ำ/retry) ด้วย token เดียวกัน ระหว่างที่คำขอแรกยัง
        # POST ค้างอยู่จริง — ต้องถูกปฏิเสธทันที ไม่ใช่โหลด bundle เดิมแล้วยิงซ้ำ
        with self.assertRaises(Exception) as ctx:
            tsi.import_prepared_targetsun(req)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail["code"], "send_already_in_progress")

        release_post.set()
        t1.join(timeout=5)
        self.assertFalse(t1.is_alive(), "คำขอแรกต้องจบภายในเวลาที่กำหนด")

        self.assertEqual(post_call_count["n"], 1, "Target Sun ต้องถูกยิงแค่ครั้งเดียวเท่านั้น")
        self.assertIn("first", results, f"คำขอแรกควรสำเร็จปกติ: {results.get('first_error')}")
        self.assertNotIn("first_error", results)

    def test_after_first_call_finishes_the_token_lock_is_released(self):
        """ปล่อยล็อกแล้วต้องจับจองใหม่ได้เสมอ ไม่ค้างตลอดไปแม้คำขอแรกจะจบสำเร็จ"""
        token = "TOK-SEQUENTIAL-1"
        self._make_bundle(token)
        req = LakehouseUploadRequest(
            sup_id=self.SUP, target_month=self.MONTH, target_year=self.YEAR,
            upload_user_code="TESTER", allocations=[], prepare_token=token,
        )

        def fake_post(*args, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"success": True, "resultMsg": "ok", "result": {}}
            return resp

        with patch.object(tsr, "is_enabled", return_value=False), \
             patch("backend.services.targetsun_import.requests.post", side_effect=fake_post):
            tsi.import_prepared_targetsun(req)

        self.assertNotIn(token, tsi._import_in_flight_tokens)

    def test_failed_post_still_releases_the_lock(self):
        """POST ล้มเหลว (network error) ก็ต้องปล่อยล็อกด้วย ไม่งั้น token นี้ค้างตลอดไป"""
        token = "TOK-FAILS-1"
        self._make_bundle(token)
        req = LakehouseUploadRequest(
            sup_id=self.SUP, target_month=self.MONTH, target_year=self.YEAR,
            upload_user_code="TESTER", allocations=[], prepare_token=token,
        )

        with patch.object(tsr, "is_enabled", return_value=False), \
             patch(
                 "backend.services.targetsun_import.requests.post",
                 side_effect=RuntimeError("boom"),
             ):
            with self.assertRaises(Exception):
                tsi.import_prepared_targetsun(req)

        self.assertNotIn(token, tsi._import_in_flight_tokens)


if __name__ == "__main__":
    unittest.main()
