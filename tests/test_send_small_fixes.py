"""
เรื่องเล็กของการส่ง (ผลตรวจ 28 ก.ย. 2026 §1.10, §2.9)

- §1.10 บันทึกจากหน้าเว็บปลอมประวัติการส่งของทีมอื่นไม่ได้
- §2.9 log ใช้งวดที่ส่งจริง · sup_id ว่างไม่ข้ามการตรวจเจ้าของไฟล์ · เตือนเมื่อปิด SSL
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from fastapi import HTTPException  # noqa: E402

from backend.routers import admin as ar  # noqa: E402
from backend.routers import lakehouse as rl  # noqa: E402
from backend.schemas import LakehouseUploadRequest  # noqa: E402
from backend.services import targetsun_import as ti  # noqa: E402


class TestClientLogCannotSpoofSends(unittest.TestCase):
    def _post(self, user, **body):
        with patch.object(ar, "append_log", side_effect=lambda **k: k) as spy:
            ar.admin_post_usage_log(ar.UsageLogBody(**body), user=user)
        return spy.call_args.kwargs

    def test_action_is_always_prefixed(self):
        kw = self._post({"email": "s@x.co", "allowed_supervisor_codes": None},
                        action="send_targetsun", sup_id="SLA", message="ส่งสำเร็จ")
        self.assertEqual(kw["action"], "client_send_targetsun",
                         "ต้องไม่ปนกับ send_targetsun จริงใน /data/send-history")

    def test_other_teams_sup_id_is_refused(self):
        with self.assertRaises(HTTPException) as ctx:
            self._post({"email": "s@x.co", "allowed_supervisor_codes": {"SLA"}},
                       action="x", sup_id="SLZ")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_bad_level_and_long_text_are_normalised(self):
        kw = self._post({"email": "s@x.co", "allowed_supervisor_codes": None},
                        action="client_timing", level="critical", message="m" * 900)
        self.assertEqual(kw["action"], "client_timing")
        self.assertEqual(kw["level"], "error")
        self.assertEqual(len(kw["message"]), 500)


class TestBundleOwnerAlwaysChecked(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        ti._save_prepare_bundle(
            "t1", content=b"x", fname="f.xlsx", sup_id="SLA", nrow=1, zero_rows=0,
            dropped_dims=0, not_in_ts=[], upload_user_code=None,
        )

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def test_empty_sup_id_is_refused(self):
        with self.assertRaises(HTTPException) as ctx:
            ti._load_prepare_bundle("t1", "")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_other_team_is_refused(self):
        with self.assertRaises(HTTPException) as ctx:
            ti._load_prepare_bundle("t1", "SLB")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_batch_reader_still_works_without_sup(self):
        self.assertEqual(ti.load_prepare_batch(["t1"])[0]["sup_id"], "SLA")


class TestLogUsesTheSentPeriod(unittest.TestCase):
    def test_bundle_period_wins_over_request_period(self):
        req = LakehouseUploadRequest(sup_id="SLA", target_month=8, target_year=2026, prepare_token="t")
        user = {"email": "s@x.co", "allowed_supervisor_codes": None}
        result = {"target_month": 9, "target_year": 2026, "send_status": "ok",
                  "targetsun": {"success": True}}
        with patch.object(rl, "ensure_supervisor_allowed"), \
                patch.object(rl, "ensure_own_supervisor_write"), \
                patch.object(rl, "ensure_targetsun_import_allowed"), \
                patch.object(rl, "ensure_demo_team_not_sent"), \
                patch.object(rl, "import_prepared_targetsun", return_value=result), \
                patch.object(rl, "_alert_row_count"), \
                patch.object(rl, "log_from_user") as spy:
            rl.import_targetsun_from_allocations(req, user=user)
        self.assertEqual(spy.call_args.kwargs["target_month"], 9)


class TestSslWarning(unittest.TestCase):
    def test_disabled_verification_is_logged_loudly(self):
        from backend import app_factory

        # เทสต์โมดูลอื่นเรียก logging.disable(CRITICAL) ทั้งโปรเซส — เปิดชั่วคราวแล้วคืนค่า
        prev = logging.root.manager.disable
        logging.disable(logging.NOTSET)
        try:
            with patch.dict(os.environ, {"TARGETSUN_IMPORT_VERIFY_SSL": "0"}), \
                    self.assertLogs("target_allocation", level=logging.ERROR) as cm:
                app_factory._warn_if_ssl_verification_off()
        finally:
            logging.disable(prev)
        self.assertIn("TARGETSUN_IMPORT_VERIFY_SSL", cm.output[0])


if __name__ == "__main__":
    unittest.main()
