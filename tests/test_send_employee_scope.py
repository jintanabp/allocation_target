"""
ส่ง Target Sun ได้เฉพาะพนักงานในทีมที่ผู้ส่งมีสิทธิ์ (ผลตรวจ 28 ก.ย. 2026 §1.3)

เดิมตรวจแค่ sup_id แล้วเติม grain ข้ามทีมให้ทุก emp_id — ผู้มีสิทธิ์ส่งใส่รหัส
พนักงานทีมไหนก็ได้ลงในคำขอของทีมตัวเองแล้วทับเป้าของเขา · USERCODE ก็รับจากหน้าเว็บตรง ๆ

ต้องไม่บล็อกการส่งที่ถูกต้อง: รวมภาค (peer ที่มีสิทธิ์) และคนที่ถูกย้ายเข้ามา (emp_assignments)
ใช้โฟลเดอร์ชั่วคราวทั้งหมด
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from fastapi import HTTPException  # noqa: E402

from backend.routers import lakehouse as rl  # noqa: E402
from backend.schemas import LakehouseUploadRequest  # noqa: E402
from backend.services import emp_assignment_store  # noqa: E402
from backend.services import lakehouse as lh  # noqa: E402


def _req(sup, emps, code=None):
    return LakehouseUploadRequest(
        sup_id=sup, target_month=9, target_year=2026, upload_user_code=code,
        allocations=[{"emp_id": e, "sku": "A", "allocated_boxes": 1} for e in emps],
    )


def _user(allowed, own=()):
    return {
        "email": "s@example.test", "allowed_supervisor_codes": set(allowed),
        "userpls_supervisor_pick": set(own), "userpls_manager_pick": set(),
    }


class TestSendEmployeeScope(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data", exist_ok=True)
        for sup, emps in (("SLA", ["E1", "E2"]), ("SLB", ["E3"]), ("SLC", ["E9"])):
            pd.DataFrame([{"emp_id": e, "sku": "A"} for e in emps]).to_csv(
                f"data/tga_lines_{sup}_2026_09.csv", index=False
            )
        self._p = patch.object(emp_assignment_store, "read_rows", return_value=[])
        self._p.start()

    def tearDown(self):
        self._p.stop()
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def test_team_map_comes_from_grain_file_names(self):
        m = lh.employee_teams_in_period(9, 2026, ["E1", "E9", "NOBODY"])
        self.assertEqual(m, {"E1": {"SLA"}, "E9": {"SLC"}, "NOBODY": set()})

    def test_employee_who_moved_in_is_recognised_from_the_team_roster(self):
        """แถวเป้าเดิมอยู่ทีมเก่า (SLC) แต่รายชื่อทีมใหม่ (SLA) มีเขาแล้ว — ต้องส่งได้"""
        pd.DataFrame([{"emp_id": "E9", "emp_name": "x", "super_code": "SLA"}]).to_csv(
            "data/emp_cache_SLA_2026_09.csv", index=False
        )
        rl._enforce_send_identity(_user({"SLA"}), _req("SLA", ["E1", "E9"]))

    def test_own_team_passes(self):
        rl._enforce_send_identity(_user({"SLA"}), _req("SLA", ["E1", "E2"]))

    def test_other_teams_employee_is_blocked(self):
        """ท่าที่ผลตรวจเตือน: ใส่พนักงานทีม SLC ลงคำขอของ SLA"""
        with self.assertRaises(HTTPException) as ctx:
            rl._enforce_send_identity(_user({"SLA"}), _req("SLA", ["E1", "E9"]))
        d = ctx.exception.detail
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(d["employees"], [{"emp_id": "E9", "teams": ["SLC"]}])

    def test_regional_peer_the_user_may_act_for_passes(self):
        rl._enforce_send_identity(_user({"SLA", "SLB"}), _req("SLA", ["E1", "E3"]))

    def test_employee_moved_into_the_team_passes(self):
        with patch.object(emp_assignment_store, "read_rows",
                          return_value=[{"emp_id": "E9", "to_sup": "SLA"}]):
            rl._enforce_send_identity(_user({"SLA"}), _req("SLA", ["E1", "E9"]))

    def test_employee_without_any_grain_is_not_blocked(self):
        """ไม่มี grain ในทีมไหนเลย = แถวถูกตัดทิ้งตอนสร้างไฟล์อยู่แล้ว ส่งไม่ได้จริง"""
        rl._enforce_send_identity(_user({"SLA"}), _req("SLA", ["E1", "NEW1"]))

    def test_unrestricted_user_skips_the_check(self):
        rl._enforce_send_identity({"allowed_supervisor_codes": None}, _req("SLA", ["E9"]))

    def test_foreign_user_code_is_replaced_with_the_team_code(self):
        req = _req("SLA", ["E1"], code="SL999")
        rl._enforce_send_identity(_user({"SLA"}, own={"SLA"}), req)
        self.assertEqual(req.upload_user_code, "SLA")

    def test_own_manager_code_is_kept(self):
        req = _req("SLA", ["E1"], code="MG01")
        u = _user({"SLA"})
        u["userpls_manager_pick"] = {"MG01"}
        rl._enforce_send_identity(u, req)
        self.assertEqual(req.upload_user_code, "MG01")

    def test_both_send_routes_call_the_check(self):
        import inspect

        self.assertIn("_enforce_send_identity(user, req)", inspect.getsource(rl.prepare_targetsun_from_allocations))
        self.assertIn("_enforce_send_identity(user, req)", inspect.getsource(rl.import_targetsun_from_allocations))


if __name__ == "__main__":
    unittest.main()
