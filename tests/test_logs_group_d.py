"""
ผลตรวจ 7 ต.ค. 2026 กลุ่ม ง — หน้า log / บันทึกผล อ่านแล้วเข้าใจ แก้ได้ทันที

- error 500 ที่ไม่คาดคิด ลงบันทึกการใช้งาน (หน้าแอดมินเห็น) พร้อมรหัสอ้างอิง · ผู้ใช้เห็นข้อความไทย ไม่มี traceback
- หน้า log กรองตามเรื่อง/ข้อความ/เฉพาะปัญหาได้
- แอดมินดูสิ่งที่ระบบส่งจริง (sent ledger) ได้
- กระจายสำเร็จก็ลงบันทึก (ตกทางสำรอง = warn)
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import admin as admin_router
from backend.services import sent_ledger


class TestUnhandledErrorLogged(unittest.TestCase):
    def test_500_goes_to_usage_log_with_ref(self):
        from backend import app_factory

        app = app_factory.create_app()

        @app.get("/_boom_test")
        def _boom(sup_id: str = "SLX1", target_month: int = 11, target_year: int = 2026):
            raise KeyError("secret-internal-detail")

        with mock.patch("backend.services.usage_log_store.append_log") as log, \
             mock.patch.object(app_factory.auth_entra, "auth_enabled", return_value=True):
            client = TestClient(app, raise_server_exceptions=False)
            r = client.get("/_boom_test?sup_id=SLX1&target_month=11&target_year=2026")
        self.assertEqual(r.status_code, 500)
        body = r.json()["detail"]
        self.assertIn("รหัสอ้างอิง", body)
        self.assertNotIn("secret-internal-detail", body)
        kw = log.call_args.kwargs
        self.assertEqual(kw["action"], "server_error")
        self.assertEqual(kw["sup_id"], "SLX1")
        self.assertEqual((kw["target_month"], kw["target_year"]), (11, 2026))
        self.assertIn(kw["context"]["ref"], body)


class TestUsageLogFilters(unittest.TestCase):
    ROWS = [
        {"level": "info", "action": "send_targetsun", "message": "ส่งสำเร็จ", "sup_id": "SL1"},
        {"level": "error", "action": "send_targetsun", "message": "ส่งไม่สำเร็จ", "sup_id": "SL2"},
        {"level": "warn", "action": "optimize", "message": "ตกทางสำรอง", "sup_id": "SL1"},
        {"level": "info", "action": "admin_emp_assignment", "message": "ย้าย S516", "detail": "จากทีม SL372"},
    ]

    def f(self, **kw):
        return admin_router._apply_usage_log_filters(
            list(self.ROWS), kw.get("action_prefix"), kw.get("q"), kw.get("problems_only", False)
        )

    def test_action_prefix(self):
        self.assertEqual(len(self.f(action_prefix="send_targetsun")), 2)
        self.assertEqual(len(self.f(action_prefix="admin_")), 1)

    def test_problems_only(self):
        self.assertEqual({r["level"] for r in self.f(problems_only=True)}, {"warn", "error"})

    def test_text_search_includes_detail(self):
        self.assertEqual([r["action"] for r in self.f(q="sl372")], ["admin_emp_assignment"])


class TestSentLedgerForAdmins(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data")

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def test_summary_and_export(self):
        row = dict(PRODUCTCODE="A", SALESTYPE="S", DIVISIONCODE="D", SALESMANCODE="E1", AREACODE="10",
                   PROVINCECODE="", WAREHOUSECODE="", QUANTITYCASE=7)
        sent_ledger.record_send("SLZZL", 11, 2026, [row], token="tok123456", user="T", send_status="ok",
                                import_url="https://ts.example/import")
        with mock.patch.object(admin_router, "ensure_sup_in_admin_scope"):
            out = admin_router.admin_get_sent_ledger(admin={}, sup_id="slzzl", target_month=11, target_year=2026)
            resp = admin_router.admin_export_sent_ledger(admin={}, sup_id="SLZZL", target_month=11, target_year=2026)
        self.assertEqual(out["row_count"], 1)
        self.assertEqual(out["boxes_total"], 7)
        self.assertEqual(out["import_url_host"], "ts.example")
        self.assertEqual(out["sends"][0]["token"], "tok12345")
        self.assertIn(b"PK", resp.body[:4])


class TestOptimizeSuccessLogged(unittest.TestCase):
    def test_fallback_logged_as_warn(self):
        from backend.routers import optimize as opt_router

        req = mock.Mock(target_sup_ids=[], strategy="L3M", only_skus=[])
        with mock.patch.object(opt_router, "log_from_user") as log:
            opt_router._log_optimize_ok({}, req, "SL1", 11, 2026,
                                        {"optimization_fallback": True, "allocations": [1, 2]}, 3.2)
        kw = log.call_args.kwargs
        self.assertEqual((kw["action"], kw["level"]), ("optimize", "warn"))
        self.assertIn("สัดส่วน", kw["message"])


if __name__ == "__main__":
    unittest.main()
