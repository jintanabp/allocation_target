"""
snapshot บอกที่มาของแถว + เป้าเงินเฉพาะทีม + เป้าเงินตอนกระจาย (ผู้ใช้ขอ 30 ก.ย. 2026)

พบจาก export 10/2026:
  - SL393 และรวมภาคอีสาน: แถวเป็นเป้าเดิมจาก Target Sun (ไม่ใช่ผลตัวกระจาย) แยกไม่ออก
  - SL375: เป้าเงินใน snapshot มีพนักงานทีมอื่น 14 คน และเป้าเงินของ C348 ไม่ใช่ตัวที่ใช้ตอนกระจาย
  - ปุ่มกระจายใหม่ (คงค่าที่แก้) / เฉพาะสินค้าที่เลือก ไม่จด engine_boxes เลย
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

from backend.routers import admin as ra  # noqa: E402
from backend.routers import data as rd  # noqa: E402
from backend.services import allocation_store, emp_assignment_store  # noqa: E402


def _fn(src: str, name: str) -> str:
    i = src.index(f"function {name}(")
    return src[i:src.index("\n}\n", i)]


class TestSnapshotTeamOnlyYellow(unittest.TestCase):
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

    def _put(self, **kw):
        body = rd.AllocationSnapshotBody(
            sup_id="SLA", target_month=10, target_year=2026, status="optimized",
            allocations=[{"emp_id": e, "sku": "X", "allocated_boxes": 1, "row_source": "engine"}
                         for e in ("A1", "A2")],
            **kw,
        )
        rd.put_allocation_snapshot(body, user={"email": "s@x.co"})
        return allocation_store.read_snapshot("SLA", 10, 2026)

    def test_other_teams_money_targets_are_dropped(self):
        snap = self._put(
            yellow={"A1": 100, "A2|R1": 50, "B1": 999, "B1|R9": 5, "NEW9": 7},
            yellow_locked={"A1": True, "B1": True},
            engine_yellow={"A1": 90, "B1": 888},
        )
        self.assertEqual(snap["yellow"], {"A1": 100, "A2|R1": 50, "NEW9": 7},
                         "B1 อยู่ทีม SLB ต้องถูกตัด · NEW9 ไม่มีข้อมูลทีม เก็บไว้")
        self.assertEqual(snap["yellow_locked"], {"A1": True})
        self.assertEqual(snap["engine_yellow"], {"A1": 90})

    def test_row_source_is_kept_on_rows(self):
        snap = self._put(yellow={"A1": 1})
        self.assertEqual({r["row_source"] for r in snap["allocations"]}, {"engine"})

    def test_engine_yellow_saved_with_run_time(self):
        snap = self._put(engine_yellow={"A1": 90, "A2": 80}, engine_run_at="2026-09-30T03:00:00Z")
        self.assertEqual(snap["engine_yellow"], {"A1": 90, "A2": 80})
        self.assertEqual(snap["engine_run_at"], "2026-09-30T03:00:00Z")

    def test_missing_engine_yellow_keeps_previous(self):
        self._put(engine_yellow={"A1": 90}, engine_run_at="t1")
        snap = self._put(yellow={"A1": 100})
        self.assertEqual(snap["engine_yellow"], {"A1": 90})
        self.assertEqual(snap["engine_run_at"], "t1")

    def test_engine_yellow_of_only_other_team_does_not_wipe(self):
        self._put(engine_yellow={"A1": 90}, engine_run_at="t1")
        snap = self._put(engine_yellow={"B1": 1}, engine_run_at="t2")
        self.assertEqual(snap["engine_yellow"], {"A1": 90})
        self.assertEqual(snap["engine_run_at"], "t1")

    def test_export_includes_row_source(self):
        self.assertIn("row_source", ra._ALLOC_ANALYSIS_FIELDS)


class TestFrontendStampsEveryEngineRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO, "frontend", "app.js"), encoding="utf-8") as fh:
            cls.src = fh.read().replace("\r\n", "\n")

    def test_all_optimize_buttons_share_the_stamp(self):
        self.assertIn("_stampEngineRun(allocs);", _fn(self.src, "_doOptimize"))

    def test_stamp_keeps_engine_value_of_locked_cells(self):
        body = _fn(self.src, "_stampEngineRun")
        self.assertIn('a.row_source = "engine"', body)
        self.assertIn("a.is_edited && prev.has(k(a))", body)
        self.assertIn("S.engineYellow = { ...(S.yellow || {}) }", body)

    def test_rows_from_targetsun_are_marked(self):
        for name in ("_allocRowsFromLiveData", "_allocRowsFromLiveTargetsPreview"):
            self.assertIn('row_source: "targetsun"', _fn(self.src, name), name)

    def test_snapshot_body_carries_engine_yellow(self):
        body = _fn(self.src, "saveServerAllocationSnapshot")
        self.assertIn("body.engine_yellow = S.engineYellow", body)


if __name__ == "__main__":
    unittest.main()
