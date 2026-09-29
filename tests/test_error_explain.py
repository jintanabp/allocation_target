"""
บันทึกการใช้งานต้องอธิบายข้อผิดพลาดให้แอดมินแก้ได้ทันที (ผู้ใช้ขอ 29 ก.ย. 2026)

เกิดอะไร / น่าจะเกิดจาก / วิธีแก้ / ข้อมูลอ้างอิง — ทุกรหัสที่ backend ตีกลับต้องมีคำอธิบาย
"""

from __future__ import annotations

import glob
import os
import re
import sys
import unittest
from unittest.mock import patch

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from fastapi import HTTPException  # noqa: E402

from backend.services import error_explain as ee  # noqa: E402

# รหัสที่ไม่ใช่ข้อผิดพลาดของผู้ใช้ (ข้อมูลประกอบในคำตอบ ไม่ถูกบันทึกเป็น error)
_NOT_ERRORS = {"aggregate_view", "aggregate_mixed_sales_unit", "sold_only_skus_excluded"}


class TestCatalogCoverage(unittest.TestCase):
    def test_every_error_code_raised_by_the_backend_is_explained(self):
        codes = set()
        for path in glob.glob(os.path.join(REPO, "backend", "**", "*.py"), recursive=True):
            with open(path, encoding="utf-8") as fh:
                codes |= set(re.findall(r'"code":\s*"([a-z_]+)"', fh.read()))
        missing = sorted(c for c in codes - _NOT_ERRORS if c not in ee.CATALOG)
        self.assertEqual(missing, [], "รหัสเหล่านี้ยังไม่มีคำอธิบายใน error_explain.CATALOG")

    def test_every_entry_has_title_cause_and_fix(self):
        for code, (title, cause, fix) in ee.CATALOG.items():
            with self.subTest(code=code):
                self.assertTrue(title and cause and fix)


class TestExplain(unittest.TestCase):
    def test_refs_are_pulled_from_the_detail(self):
        e = HTTPException(409, detail={
            "code": "send_target_mismatch", "sup_id": "SL397", "mismatch_count": 1,
            "message": "ยอดไม่ตรง",
            "mismatches": [{"sku": "734046", "sending_boxes": 25, "expected_boxes": 30, "junk": 1}],
        })
        ex = ee.explain_http(e)
        self.assertEqual(ex["code"], "send_target_mismatch")
        self.assertEqual(ex["refs"]["sup_id"], "SL397")
        self.assertEqual(ex["refs"]["mismatches"], [{"sku": "734046", "sending_boxes": 25, "expected_boxes": 30}])
        self.assertEqual(ex["server_message"], "ยอดไม่ตรง")

    def test_timeout_without_code(self):
        self.assertEqual(ee.explain_http(HTTPException(504, detail={"error_kind": "timeout"}))["code"], "timeout")
        self.assertEqual(ee.explain_http(HTTPException(504, detail="x"))["code"], "timeout")

    def test_unknown_code_still_explains_and_keeps_the_server_text(self):
        ex = ee.explain("brand_new_code", {"message": "อะไรสักอย่างพัง"})
        self.assertIn("dev", ex["fix"])
        self.assertEqual(ex["server_message"], "อะไรสักอย่างพัง")

    def test_long_lists_are_capped_with_a_count(self):
        ex = ee.explain("send_rows_not_importable", {"rows": [{"row": i} for i in range(25)]})
        self.assertEqual(len(ex["refs"]["rows"]), 10)
        self.assertEqual(ex["refs"]["rows_more"], 15)


class TestLogsCarryExplanations(unittest.TestCase):
    def test_optimize_failure_is_now_logged_with_an_explanation(self):
        from backend.routers import optimize as ro
        from backend.schemas import OptimizeRequest

        err = HTTPException(409, detail={"code": "allocation_mismatch", "message": "ไม่ตรงเป้า",
                                         "sku_total_checks": [{"sku": "A", "allocated_sum": 9, "expected_boxes": 10}]})
        with patch.object(ro, "ensure_supervisor_allowed"), patch.object(ro, "ensure_own_supervisor_write"), \
                patch.object(ro, "run_optimization_service", side_effect=err), \
                patch.object(ro, "log_from_user") as spy:
            with self.assertRaises(HTTPException):
                ro.run_optimization(OptimizeRequest(yellowTargets=[]), user={"email": "s@x.co"},
                                    sup_id="SL397", target_month=10, target_year=2026)
        ctx = spy.call_args.kwargs["context"]
        self.assertEqual(ctx["explain"]["code"], "allocation_mismatch")
        self.assertEqual(spy.call_args.kwargs["action"], "optimize_failed")

    def test_partial_send_log_explains_the_skipped_rows(self):
        from backend.routers import lakehouse as rl

        out = rl._send_result_explain(
            "partial", {"result": {"skipped": 2, "errors": [{"rowNum": 8, "message": "Missing required fields: USERCODE"}]}},
            {}, {"checked": True, "ok": False, "unlanded_count": 2, "unlanded_sample": [{"sku": "A"}]},
        )
        self.assertEqual(out["explain"]["code"], "send_partial")
        self.assertEqual(out["explain"]["refs"]["rows"][0]["row"], 8)
        self.assertIn("บางแถวที่ส่งยังไม่ลง", out["explain_more"][0])

    def test_clean_send_has_no_explanation(self):
        from backend.routers import lakehouse as rl

        self.assertEqual(rl._send_result_explain("ok", {}, {"checked": True, "ok": True},
                                                 {"checked": True, "ok": True}), {})

    def test_admin_page_shows_the_explanation_first(self):
        with open(os.path.join(REPO, "frontend", "app.js"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("เกิดอะไร: ${ex.title", src)
        self.assertIn('class="log-explain"', src)


if __name__ == "__main__":
    unittest.main()
