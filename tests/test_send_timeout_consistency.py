"""
หมดเวลารอ ต้องไม่ทำให้หน้าจอ / log / Target Sun บอกคนละอย่าง (ผลตรวจ 28 ก.ย. 2026 §2.4)

- หน้าเว็บต้องรอนานกว่างานฝั่ง server ที่แย่ที่สุด (อ่าน 120 + POST 600 + อ่านหลัง 2×120)
- server หมดเวลาตอน POST (504) = "ไม่รู้ผล" ไม่ใช่ "ไม่สำเร็จ" — ปลายทางอาจบันทึกไปแล้ว
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from fastapi import HTTPException  # noqa: E402

from backend.routers import lakehouse as rl  # noqa: E402
from backend.schemas import LakehouseUploadRequest  # noqa: E402
from backend.services import targetsun_import as ti  # noqa: E402


def _app_js() -> str:
    with open(os.path.join(REPO, "frontend", "app.js"), encoding="utf-8") as fh:
        return fh.read()


class TestFrontendWaitsLongEnough(unittest.TestCase):
    def test_import_timeout_exceeds_server_worst_case(self):
        src = _app_js()
        body = src[src.index("async function _fetchTargetSunImport"):]
        body = body[:body.index("\n}\n")]
        ms = int(re.findall(r"\n\s*(\d{6,})\n\s*\);", body)[0])
        self.assertGreater(ms, (120 + 600 + 2 * 120) * 1000)

    def test_504_is_shown_as_uncertain(self):
        src = _app_js()
        self.assertIn("res.status === 504", src)


class TestServerTimeoutIsUnknown(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def test_log_says_unknown_on_504(self):
        req = LakehouseUploadRequest(sup_id="SLA", target_month=9, target_year=2026, prepare_token="t1")
        user = {"email": "s@x.co", "allowed_supervisor_codes": None, "can_import_targetsun": True}
        with patch.object(rl, "ensure_supervisor_allowed"), \
                patch.object(rl, "ensure_own_supervisor_write"), \
                patch.object(rl, "ensure_targetsun_import_allowed"), \
                patch.object(rl, "ensure_demo_team_not_sent"), \
                patch.object(rl, "import_prepared_targetsun",
                             side_effect=HTTPException(504, detail={"error_kind": "timeout"})), \
                patch.object(rl, "log_from_user") as spy:
            with self.assertRaises(HTTPException):
                rl.import_targetsun_from_allocations(req, user=user)
        kw = spy.call_args.kwargs
        self.assertIn("ไม่รู้ผล", kw["message"])
        self.assertNotIn("ไม่สำเร็จ", kw["message"])
        self.assertEqual(kw["context"]["send_status"], "unknown")

    def test_bundle_is_kept_after_504_but_removed_after_other_errors(self):
        for status, kept in ((504, True), (502, False)):
            with self.subTest(status=status):
                tok = f"tok{status}"
                ti._save_prepare_bundle(
                    tok, content=b"x", fname="f.xlsx", sup_id="SLA", nrow=1, zero_rows=0,
                    dropped_dims=0, not_in_ts=[], upload_user_code=None,
                    target_month=9, target_year=2026, emp_codes=["E1"],
                )
                req = LakehouseUploadRequest(sup_id="SLA", target_month=9, target_year=2026, prepare_token=tok)
                with patch.object(ti, "_live_target_snapshot", return_value=None), \
                        patch.object(ti, "team_emp_codes_from_grain", return_value=["E1"]), \
                        patch.object(ti, "assert_target_snapshot_is_fresh"), \
                        patch.object(ti, "_post_targetsun_multipart", side_effect=HTTPException(status)):
                    with self.assertRaises(HTTPException):
                        ti.import_prepared_targetsun(req)
                self.assertEqual(os.path.isfile(f"data/ts_prepare/{tok}.json"), kept)


if __name__ == "__main__":
    unittest.main()
