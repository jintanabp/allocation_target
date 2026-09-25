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


class ImportRechecksFreshnessTest(unittest.TestCase):
    """
    ข้อ 5 จากผลตรวจสอบระบบ (24 ก.ย. 2026): assert_target_snapshot_is_fresh เดิมเรียก
    แค่ตอน prepare — ระหว่างเตรียมครบทุกทีม/ถามยืนยัน/ตรวจยอดรวมทั้งชุด แล้วค่อยวน
    import ทีละทีม (แต่ละ POST ค้างได้นานถึง TARGETSUN_IMPORT_TIMEOUT_SEC วินาที) เป้า
    อาจขยับอีกรอบได้โดยไม่มีอะไรจับ — ตอนนี้ import_prepared_targetsun ตรวจซ้ำด้วย
    เอง (ใช้ live snapshot เดียวกับที่อ่านไปแล้วสำหรับตรวจจำนวนแถว ไม่ยิงซ้ำ)
    """

    SUP = "SLDRIFTTEST"
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

    def _write_step1_snapshot(self, rows):
        import pandas as pd

        pd.DataFrame(rows).to_csv(
            f"data/target_boxes_{self.SUP}_{self.YEAR}_{self.MONTH:02d}.csv", index=False
        )

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

    def _req(self, token, **kw):
        return LakehouseUploadRequest(
            sup_id=self.SUP, target_month=self.MONTH, target_year=self.YEAR,
            upload_user_code="TESTER", allocations=[], prepare_token=token, **kw,
        )

    def test_target_drifted_after_prepare_blocks_the_import(self):
        """เป้าตอนโหลดขั้นที่ 1 คือ X=10 แต่ตอนจะ import จริง Target Sun ขยับเป็น X=12 แล้ว"""
        self._write_step1_snapshot(
            [{"sku": "X", "supervisor_target_boxes": 10, "price_per_box": 1.0}]
        )
        token = "TOK-DRIFT-1"
        self._make_bundle(token)

        with patch.object(tsr, "is_enabled", return_value=True), \
             patch.object(tsr, "get_target_read_source", return_value="targetsun"), \
             patch.object(
                 tsr, "fetch_target_rows",
                 return_value={"rows": [{"PRODUCTCODE": "X", "QUANTITYCASE": 12}]},
             ), \
             patch("backend.services.targetsun_import.requests.post") as post_spy:
            with self.assertRaises(Exception) as ctx:
                tsi.import_prepared_targetsun(self._req(token))
            self.assertEqual(ctx.exception.status_code, 409)
            self.assertEqual(ctx.exception.detail["code"], "send_target_stale")
            post_spy.assert_not_called()  # บล็อกก่อนยิง POST เลย

        # ปล่อยล็อกด้วย ไม่งั้น token ค้างแม้จะ block ไปแล้ว
        self.assertNotIn(token, tsi._import_in_flight_tokens)

    def test_confirm_stale_target_from_prepare_still_carries_through(self):
        """ผู้ใช้ยืนยันความต่างไปแล้วตอน prepare (confirm_stale_target=True) — import ต้องไม่ถามซ้ำ"""
        self._write_step1_snapshot(
            [{"sku": "X", "supervisor_target_boxes": 10, "price_per_box": 1.0}]
        )
        token = "TOK-DRIFT-CONFIRMED"
        self._make_bundle(token)

        def fake_post(*args, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"success": True, "resultMsg": "ok", "result": {}}
            return resp

        with patch.object(tsr, "is_enabled", return_value=True), \
             patch.object(tsr, "get_target_read_source", return_value="targetsun"), \
             patch.object(
                 tsr, "fetch_target_rows",
                 return_value={"rows": [{"PRODUCTCODE": "X", "QUANTITYCASE": 12}]},
             ), \
             patch("backend.services.targetsun_import.requests.post", side_effect=fake_post):
            out = tsi.import_prepared_targetsun(self._req(token, confirm_stale_target=True))
        self.assertEqual(out["targetsun"]["success"], True)

    def test_unchanged_target_does_not_block(self):
        self._write_step1_snapshot(
            [{"sku": "X", "supervisor_target_boxes": 10, "price_per_box": 1.0}]
        )
        token = "TOK-NO-DRIFT"
        self._make_bundle(token)

        def fake_post(*args, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"success": True, "resultMsg": "ok", "result": {}}
            return resp

        with patch.object(tsr, "is_enabled", return_value=True), \
             patch.object(tsr, "get_target_read_source", return_value="targetsun"), \
             patch.object(
                 tsr, "fetch_target_rows",
                 return_value={"rows": [{"PRODUCTCODE": "X", "QUANTITYCASE": 10}]},
             ), \
             patch("backend.services.targetsun_import.requests.post", side_effect=fake_post):
            out = tsi.import_prepared_targetsun(self._req(token))
        self.assertEqual(out["targetsun"]["success"], True)

    def test_reuses_the_same_live_read_does_not_call_fetch_twice(self):
        """
        ตรวจซ้ำต้องใช้ live snapshot เดียวกับที่อ่านไปแล้วสำหรับตรวจจำนวนแถวหลังส่ง
        ไม่ใช่ยิง Target Sun ซ้ำอีกรอบสำหรับด่านนี้โดยเฉพาะ
        """
        self._write_step1_snapshot(
            [{"sku": "X", "supervisor_target_boxes": 10, "price_per_box": 1.0}]
        )
        token = "TOK-SINGLE-READ"
        self._make_bundle(token)

        def fake_post(*args, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"success": True, "resultMsg": "ok", "result": {}}
            return resp

        fetch_calls = {"n": 0}

        def fake_fetch(*args, **kwargs):
            fetch_calls["n"] += 1
            return {"rows": [{"PRODUCTCODE": "X", "QUANTITYCASE": 10}]}

        with patch.object(tsr, "is_enabled", return_value=True), \
             patch.object(tsr, "get_target_read_source", return_value="targetsun"), \
             patch.object(tsr, "fetch_target_rows", side_effect=fake_fetch), \
             patch("backend.services.targetsun_import.requests.post", side_effect=fake_post):
            tsi.import_prepared_targetsun(self._req(token))

        # ก่อนส่ง (แถว/freshness ใช้อ่านเดียวกัน) + หลังส่ง (verify_row_count_after_send) = 2
        # ครั้งพอดี ไม่ใช่ 3 (ถ้าด่าน freshness ยิงซ้ำเองอีกรอบ)
        self.assertEqual(fetch_calls["n"], 2)

    def _write_grain(self, emp_ids):
        import pandas as pd

        pd.DataFrame(
            [{"emp_id": e, "sku": "X", "areacode": "A1", "divisioncode": "D1"} for e in emp_ids]
        ).to_csv(f"data/tga_lines_{self.SUP}_{self.YEAR}_{self.MONTH:02d}.csv", index=False)

    @staticmethod
    def _ok_post(*args, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"success": True, "resultMsg": "ok", "result": {}}
        return resp

    def test_file_covers_only_part_of_team_does_not_false_alarm(self):
        """
        ทีมมี E1+E2 (เป้า X=10 = E1 6 + E2 4) แต่ไฟล์มีแค่ E1 (เช่น E2 ไม่ต้องตั้งเป้า)
        ด่านนี้ต้องเทียบด้วยคนทั้งทีมเหมือนตอน prepare — ถ้าเทียบแค่ E1 จะเห็น 6 ≠ 10
        แล้วฟ้อง "เป้าเปลี่ยน" ผิด บล็อกการส่งทุกครั้ง ทั้งที่เป้าไม่ได้ขยับเลย
        """
        self._write_step1_snapshot(
            [{"sku": "X", "supervisor_target_boxes": 10, "price_per_box": 1.0}]
        )
        self._write_grain(["E1", "E2"])
        token = "TOK-SUBSET"
        self._make_bundle(token)  # emp_codes=["E1"]

        def fake_fetch(year, month, codes):
            rows = {"E1": 6, "E2": 4}
            return {"rows": [
                {"SALESMANCODE": c, "PRODUCTCODE": "X", "QUANTITYCASE": rows[c]}
                for c in codes if c in rows
            ]}

        with patch.object(tsr, "is_enabled", return_value=True), \
             patch.object(tsr, "get_target_read_source", return_value="targetsun"), \
             patch.object(tsr, "fetch_target_rows", side_effect=fake_fetch), \
             patch("backend.services.targetsun_import.requests.post", side_effect=self._ok_post):
            out = tsi.import_prepared_targetsun(self._req(token))
        self.assertEqual(out["targetsun"]["success"], True)

    def test_file_covers_part_of_team_real_drift_still_blocks(self):
        """คนในไฟล์ไม่ครบทีมก็ยังต้องจับการขยับจริงได้ (E2 ขยับ 4 → 7)"""
        self._write_step1_snapshot(
            [{"sku": "X", "supervisor_target_boxes": 10, "price_per_box": 1.0}]
        )
        self._write_grain(["E1", "E2"])
        token = "TOK-SUBSET-DRIFT"
        self._make_bundle(token)

        def fake_fetch(year, month, codes):
            rows = {"E1": 6, "E2": 7}
            return {"rows": [
                {"SALESMANCODE": c, "PRODUCTCODE": "X", "QUANTITYCASE": rows[c]}
                for c in codes if c in rows
            ]}

        with patch.object(tsr, "is_enabled", return_value=True), \
             patch.object(tsr, "get_target_read_source", return_value="targetsun"), \
             patch.object(tsr, "fetch_target_rows", side_effect=fake_fetch), \
             patch("backend.services.targetsun_import.requests.post") as post_spy:
            with self.assertRaises(Exception) as ctx:
                tsi.import_prepared_targetsun(self._req(token))
            post_spy.assert_not_called()
        self.assertEqual(ctx.exception.detail["code"], "send_target_stale")

    def test_confirmation_given_at_prepare_is_remembered_in_the_bundle(self):
        """
        คำขอ import ที่มี token (frontend) ไม่ได้พก confirm_stale_target มา — ผู้ใช้ที่
        กดยืนยันไปแล้วตอน prepare ต้องไม่โดนบล็อกซ้ำที่ด่านนี้ (ไม่งั้นส่งไม่ได้เลย)
        """
        self._write_step1_snapshot(
            [{"sku": "X", "supervisor_target_boxes": 10, "price_per_box": 1.0}]
        )
        token = "TOK-CONFIRMED-IN-BUNDLE"
        tsi._save_prepare_bundle(
            token, content=b"x", fname="t.xlsx", sup_id=self.SUP, nrow=1, zero_rows=0,
            dropped_dims=0, not_in_ts=[], upload_user_code="TESTER",
            target_month=self.MONTH, target_year=self.YEAR, emp_codes=["E1"],
            confirmed_stale_target=True,
        )
        with patch.object(tsr, "is_enabled", return_value=True), \
             patch.object(tsr, "get_target_read_source", return_value="targetsun"), \
             patch.object(
                 tsr, "fetch_target_rows",
                 return_value={"rows": [{"PRODUCTCODE": "X", "QUANTITYCASE": 12}]},
             ), \
             patch("backend.services.targetsun_import.requests.post", side_effect=self._ok_post):
            out = tsi.import_prepared_targetsun(self._req(token))  # ไม่มี confirm ในคำขอ
        self.assertEqual(out["targetsun"]["success"], True)

    def test_prepare_records_the_confirmation_into_the_bundle(self):
        """prepare_targetsun_import ต้องส่ง confirm_stale_target ของคำขอลง bundle"""
        import inspect

        src = inspect.getsource(tsi.prepare_targetsun_import)
        self.assertIn("confirmed_stale_target=", src)


if __name__ == "__main__":
    unittest.main()
