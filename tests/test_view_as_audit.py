"""
dev ที่ "ดูแทน" แล้วบันทึก/ส่ง ต้องมีชื่อ dev อยู่ในบันทึกด้วย (ผลตรวจ 28 ก.ย. 2026 §1.8)

ผู้ใช้ตัดสิน 29 ก.ย.: ให้เขียนได้เหมือนเดิม แต่ log ชื่อ dev คู่กับชื่อคนที่ถูกจำลอง
ใช้โฟลเดอร์ชั่วคราว
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import allocation_store  # noqa: E402
from backend.services import usage_log_store as uls  # noqa: E402

VIEW_AS = {"email": "sup@example.test", "view_as_email": "sup@example.test",
           "acting_admin_email": "dev@example.test"}


class TestViewAsAudit(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmp.cleanup()

    def test_usage_log_keeps_both_names(self):
        with patch.object(uls, "logs_dir", return_value=self._tmp.name):
            row = uls.log_from_user(VIEW_AS, action="save_allocation", sup_id="SLA")
        self.assertEqual(row["email"], "sup@example.test")
        self.assertEqual(row["acting_admin_email"], "dev@example.test")
        with open(os.path.join(self._tmp.name, os.listdir(self._tmp.name)[0]), encoding="utf-8") as fh:
            self.assertEqual(json.loads(fh.readline())["acting_admin_email"], "dev@example.test")

    def test_normal_user_has_no_acting_field(self):
        with patch.object(uls, "logs_dir", return_value=self._tmp.name):
            row = uls.log_from_user({"email": "sup@example.test"}, action="x")
        self.assertNotIn("acting_admin_email", row)

    def test_snapshot_keeps_the_acting_admin(self):
        out = allocation_store._validate_body({
            "sup_id": "SLA", "target_month": 9, "target_year": 2026, "status": "draft",
            "allocations": [], "updated_by": "sup@example.test",
            "updated_by_acting_admin": "dev@example.test",
        })
        self.assertEqual(out["updated_by_acting_admin"], "dev@example.test")

    def test_save_route_records_the_acting_admin(self):
        import inspect

        from backend.routers import data as rd

        self.assertIn('payload["updated_by_acting_admin"]', inspect.getsource(rd.put_allocation_snapshot))


if __name__ == "__main__":
    unittest.main()
