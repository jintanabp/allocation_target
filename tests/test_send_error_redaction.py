"""
ข้อความ error ตอนส่งต้องไม่โชว์ URL เต็ม/คำตอบดิบของ Target Sun ให้คนที่ไม่ใช่ dev
(OPEN_ITEMS 7.11 — ผลตรวจ 5 ต.ค. 2026) · dev ยังเห็นครบ · log บน server ไม่เปลี่ยน
"""

from __future__ import annotations

import inspect
import os
import sys
import unittest

from fastapi import HTTPException

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.routers import lakehouse as lh  # noqa: E402

URL = "https://ts.internal.example:8443/api/importTargetSalesmanNextFromExcel"
SUP = {"email": "s@x", "role": "supervisor"}
DEV = {"email": "d@x", "role": "dev"}


class TestSendErrorRedaction(unittest.TestCase):
    def test_gateway_detail_redacted_for_supervisor(self):
        d = {"message": "ตัวกลางตัด (HTTP 504)", "error_kind": "gateway_unknown",
             "upstream_status": 504, "body_preview": "<html>IIS 10.0 …</html>", "import_url": URL}
        out = lh._redact_send_detail(SUP, d)
        self.assertNotIn("import_url", out)
        self.assertNotIn("body_preview", out)
        self.assertEqual(out["upstream_status"], 504)
        self.assertEqual(out["message"], d["message"])
        self.assertNotIn(URL, str(out))

    def test_network_message_with_url_replaced(self):
        e = HTTPException(502, detail={"message": f"HTTPSConnectionPool(host='ts.internal.example'): {URL}",
                                       "error_kind": "connection", "hint_th": "x"})
        red = lh._redacted_http(SUP, e)
        self.assertEqual(red.status_code, 502)
        self.assertNotIn("ts.internal.example", str(red.detail))
        self.assertIn("เชื่อมต่อ Target Sun ไม่ได้", red.detail["message"])

    def test_dev_sees_everything(self):
        d = {"message": URL, "error_kind": "connection", "import_url": URL, "body_preview": "b"}
        self.assertIs(lh._redact_send_detail(DEV, d), d)
        e = HTTPException(502, detail=d)
        self.assertIs(lh._redacted_http(DEV, e), e)

    def test_success_result_keeps_rest(self):
        res = {"send_status": "ok", "import_url": URL, "targetsun": {"success": True}}
        out = lh._redact_send_detail(SUP, res)
        self.assertEqual(out["targetsun"], {"success": True})
        self.assertNotIn("import_url", out)
        self.assertIn("import_url", res)  # ของเดิมไม่ถูกแก้ (log ใช้ตัวนี้)

    def test_routes_use_redaction(self):
        imp = inspect.getsource(lh.import_targetsun_from_allocations)
        self.assertIn("_redacted_http(user, e)", imp)
        self.assertIn("return _redact_send_detail(user, result)", imp)
        res = inspect.getsource(lh.resend_unlanded)
        self.assertIn("_redacted_http(user, e)", res)


if __name__ == "__main__":
    unittest.main()
