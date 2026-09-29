"""
ผลการส่งตัดสินจากคำตอบจริง ไม่ใช่ "success is not False" (ผลตรวจ 28 ก.ย. 2026 §2.3)

เดิม:
  - proxy ตอบ 200 {"message": ...} (ไม่มีช่อง success) → บันทึกว่า "สำเร็จ"
  - คำตอบเป็น list/string → AttributeError ถูก except: pass กลบ → log การส่งหายทั้งแถว
  - success=true แต่มี errors[] / skipped > 0 → "สำเร็จ" ไม่มีหมายเหตุ
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.routers import lakehouse as rl  # noqa: E402
from backend.schemas import LakehouseUploadRequest  # noqa: E402
from backend.services import targetsun_import as ti  # noqa: E402

REQ = LakehouseUploadRequest(sup_id="SLA", target_month=9, target_year=2026)


class TestClassify(unittest.TestCase):
    def test_cases(self):
        c = ti.classify_targetsun_reply
        self.assertEqual(c({"success": True, "result": {"inserted": 1}}), "ok")
        self.assertEqual(c({"success": True, "result": {"skipped": 2}}), "partial")
        self.assertEqual(c({"success": True, "result": {"errors": [{"rowNum": 3}]}}), "partial")
        self.assertEqual(c({"success": False}), "failed")
        self.assertEqual(c({"message": "ok"}), "unknown")
        self.assertEqual(c(["ok"]), "unknown")
        self.assertEqual(c("ok"), "unknown")
        self.assertEqual(c(None), "unknown")


class TestPostWrapsOddReplies(unittest.TestCase):
    def _post(self, body):
        r = MagicMock()
        r.status_code = 200
        r.headers = {"Content-Type": "application/json"}
        r.text = str(body)
        r.json.return_value = body
        with patch.object(ti.requests, "post", return_value=r), \
                patch.object(ti, "targetsun_import_excel_url", return_value="https://import.example.test/x"):
            return ti._post_targetsun_multipart(
                b"x", "f.xlsx", nrow=1, zero_rows=0, dropped_dims=0, not_in_ts=[],
            )

    def test_list_reply_is_wrapped_and_unknown(self):
        out = self._post(["ok"])
        self.assertEqual(out["send_status"], "unknown")
        self.assertIsInstance(out["targetsun"], dict)

    def test_reply_without_success_field_is_unknown(self):
        self.assertEqual(self._post({"message": "done"})["send_status"], "unknown")


class TestSendLog(unittest.TestCase):
    def _log(self, result):
        with patch.object(rl, "log_from_user") as spy:
            rl._log_targetsun_send({"email": "s@x.co"}, REQ, result)
        return spy.call_args.kwargs

    def test_unknown_reply_is_not_logged_as_success(self):
        kw = self._log({"targetsun": {"message": "done"}, "send_status": "unknown"})
        self.assertNotIn("สำเร็จ", kw["message"])
        self.assertFalse(kw["context"]["ok"])
        self.assertEqual(kw["context"]["send_status"], "unknown")

    def test_partial_reply_is_success_with_a_caveat(self):
        kw = self._log({
            "targetsun": {"success": True, "result": {"skipped": 3}}, "send_status": "partial",
        })
        self.assertIn("สำเร็จ — แต่ปลายทางข้าม 3 แถว", kw["message"])
        self.assertTrue(kw["context"]["ok"])

    def test_non_dict_reply_still_produces_a_log_line(self):
        kw = self._log({"targetsun": ["weird"]})
        self.assertEqual(kw["context"]["send_status"], "unknown")

    def test_log_failure_is_not_silent(self):
        calls = []

        def boom(*a, **k):
            calls.append(k)
            if len(calls) == 1:
                raise RuntimeError("disk full")

        with patch.object(rl, "log_from_user", side_effect=boom):
            rl._log_targetsun_send({"email": "s@x.co"}, REQ, {"send_status": "ok", "targetsun": {"success": True}})
        self.assertEqual(len(calls), 2, "บันทึกหลักล้ม ต้องมีบันทึกแบบย่อตามมา")
        self.assertTrue(calls[1]["context"]["log_error"])


class TestFrontend(unittest.TestCase):
    def test_unknown_reply_stops_and_marks_uncertain(self):
        with open(os.path.join(REPO, "frontend", "app.js"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn('j.send_status === "unknown"', src)
        self.assertIn("uncertain: !!handleOpts.uncertain", src)


if __name__ == "__main__":
    unittest.main()
