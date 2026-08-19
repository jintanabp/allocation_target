"""
ประวัติการส่งเข้า Target Sun

ผลการส่งเคยอยู่แค่ในข้อความแจ้งเตือนที่หายไปเอง ไม่มีที่เปิดดูย้อนหลังว่าทีมไหน
ส่งเมื่อไหร่ ได้ผลยังไง ทั้งที่ usage log บันทึกไว้ครบอยู่แล้ว — ตัวกรองพวกนี้
คือสิ่งที่ทำให้เปิดอ่านเฉพาะของทีมตัวเองได้
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import usage_log_store as uls  # noqa: E402

logging.disable(logging.CRITICAL)


class TestReadLogsFilters(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("USAGE_LOGS_DIR")
        os.environ["USAGE_LOGS_DIR"] = self._tmp.name
        uls.append_log(level="info", email="a@x.com", role="supervisor",
                       sup_id="SL100", action="send_targetsun", message="ส่งสำเร็จ")
        uls.append_log(level="error", email="b@x.com", role="supervisor",
                       sup_id="SL200", action="send_targetsun", message="ส่งไม่สำเร็จ")
        uls.append_log(level="warn", email="c@x.com", role="admin",
                       sup_id="SL100", action="save_allocation", message="บันทึก")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("USAGE_LOGS_DIR", None)
        else:
            os.environ["USAGE_LOGS_DIR"] = self._old
        self._tmp.cleanup()

    def test_filter_by_action(self):
        rows = uls.read_logs(scan_all=True, action="send_targetsun")
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["action"] == "send_targetsun" for r in rows))

    def test_filter_by_sup_id(self):
        rows = uls.read_logs(scan_all=True, sup_id="SL100")
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["sup_id"] == "SL100" for r in rows))

    def test_filters_combine(self):
        rows = uls.read_logs(scan_all=True, action="send_targetsun", sup_id="SL100")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["message"], "ส่งสำเร็จ")

    def test_sup_id_match_is_case_insensitive(self):
        self.assertEqual(len(uls.read_logs(scan_all=True, sup_id="sl100")), 2)

    def test_no_filter_returns_everything(self):
        self.assertEqual(len(uls.read_logs(scan_all=True)), 3)

    def test_unknown_sup_returns_nothing(self):
        self.assertEqual(uls.read_logs(scan_all=True, sup_id="SL999"), [])


class TestSendHistoryRouteIsScoped(unittest.TestCase):
    def test_route_checks_supervisor_permission(self):
        import inspect

        from backend.routers import data as data_router

        src = inspect.getsource(data_router.get_send_history)
        self.assertIn(
            "ensure_supervisor_allowed", src,
            "ประวัติการส่งเป็นข้อมูลของทีม ต้องตรวจสิทธิ์ก่อนเสมอ",
        )
        self.assertIn('action="send_targetsun"', src)


class TestSendEnvRouteExposesLabelOnly(unittest.TestCase):
    """หน้าจอต้องรู้ว่าจะส่งไปไหน แต่ URL เต็มยังเป็นของ dev"""

    def test_only_labels_are_returned(self):
        import inspect

        from backend.routers import lakehouse as lh_router

        src = inspect.getsource(lh_router.get_send_environment)
        self.assertIn("import_host_label", src)
        self.assertNotIn("import_url", src)
        self.assertNotIn("read_base", src)


if __name__ == "__main__":
    unittest.main()
