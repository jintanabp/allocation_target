"""
snapshot ของทีมต้องมีแต่แถวของทีมนั้น (พบจาก export งวด 10/2026, 29 ก.ย. 2026)

11 ทีมมี snapshot ซ้ำกับทีมอื่นเป๊ะ — ทุกตัวเป็น sent_targetsun บันทึกห่างกันไม่กี่วินาที
โดยคนเดียวกัน: การประทับ "ส่งแล้ว" หลังส่งรวมภาคเอา S.allocations ทั้งภาคไปทับทุกทีม
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

from backend.routers import data as rd  # noqa: E402
from backend.services import allocation_store, emp_assignment_store  # noqa: E402


def _fn(src: str, name: str) -> str:
    i = src.index(f"function {name}(")
    return src[i:src.index("\n}\n", i)]


class TestServerRefusesForeignRows(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data/allocations")
        for sup, emps in (("SLA", ["A1", "A2"]), ("SLB", ["B1"])):
            pd.DataFrame([{"emp_id": e, "emp_name": e, "super_code": sup} for e in emps]).to_csv(
                f"data/emp_cache_{sup}_2026_10.csv", index=False
            )
        self._p = [
            patch.object(allocation_store, "allocations_dir", return_value=os.path.abspath("data/allocations")),
            patch.object(emp_assignment_store, "read_rows", return_value=[]),
            patch.object(rd, "ensure_allocation_write_allowed"),
            patch("backend.services.usage_log_store.log_from_user"),
        ]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in reversed(self._p):
            p.stop()
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _put(self, emps, status="sent_targetsun"):
        body = rd.AllocationSnapshotBody(
            sup_id="SLA", target_month=10, target_year=2026, status=status,
            allocations=[{"emp_id": e, "sku": "X", "allocated_boxes": 1} for e in emps],
        )
        return rd.put_allocation_snapshot(body, user={"email": "s@x.co"})

    def test_region_rows_written_into_one_team_are_refused(self):
        with self.assertRaises(HTTPException) as ctx:
            self._put(["A1", "A2", "B1"])
        d = ctx.exception.detail
        self.assertEqual(d["code"], "snapshot_foreign_rows")
        self.assertEqual(d["employees"], [{"emp_id": "B1", "teams": ["SLB"]}])
        self.assertIsNone(allocation_store.read_snapshot("SLA", 10, 2026), "ต้องไม่มีอะไรถูกเขียน")

    def test_own_rows_are_saved(self):
        self._put(["A1", "A2"])
        self.assertEqual(len(allocation_store.read_snapshot("SLA", 10, 2026)["allocations"]), 2)

    def test_employee_without_team_info_or_moved_in_passes(self):
        with patch.object(emp_assignment_store, "read_rows", return_value=[{"emp_id": "B1", "to_sup": "SLA"}]):
            self._put(["A1", "B1", "NEW9"])


class TestFrontendSavesOnlyTheTeamsRows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO, "frontend", "app.js"), encoding="utf-8") as fh:
            cls.src = fh.read().replace("\r\n", "\n")

    def test_mark_sent_passes_the_teams_rows_in_regional_view(self):
        body = _fn(self.src, "_markAllocationSentTargetSun")
        self.assertIn("_teamRowsAndYellow(sid)", body)
        self.assertIn("allocations: team.rows", body)

    def test_regional_save_passes_the_teams_money_targets(self):
        body = _fn(self.src, "saveRegionalAllocationSnapshots")
        self.assertIn("yellow: team.yellow", body)


if __name__ == "__main__":
    unittest.main()
