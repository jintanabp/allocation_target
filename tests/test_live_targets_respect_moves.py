"""
ผลตรวจ 7 ต.ค. 2026 ข4 — 「ดึงเป้าสด」ในขั้นที่ 3 ต้องใช้รายชื่อหลังย้ายพนักงาน

แคชรายชื่อ (emp_cache_) เก็บรายชื่อดิบก่อนย้ายโดยตั้งใจ · เดิมทางดึงเป้าสดใช้แคชนี้ตรง ๆ
→ ทีมปลายทางไม่มีคนที่ย้ายมา / ทีมต้นทางยังเห็นคนที่ย้ายออกไปแล้ว
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

import pandas as pd
from fastapi import HTTPException

from backend.services import employees as em

MOVES = [{"emp_id": "S516", "to_sup": "SL359", "from_sup": "SL372"}]


class TestLiveTargetsRespectMoves(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data")
        pd.DataFrame({"emp_id": ["S516"]}).to_csv("data/emp_cache_SL372_2026_10.csv", index=False)
        pd.DataFrame({"emp_id": ["S100", "S101"]}).to_csv("data/emp_cache_SL359_2026_10.csv", index=False)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _emp_list_seen(self, sup):
        seen = {}

        def _granular(emp_list, *a, **k):
            seen["emps"] = sorted(emp_list)
            raise HTTPException(503, detail="stop")  # หยุดหลังได้รายชื่อ — ไม่ต้องไปต่อ

        with mock.patch.object(em.targetsun_read, "is_enabled", return_value=True), \
             mock.patch.object(em, "read_cached_employee_payload", return_value=None), \
             mock.patch.object(em.emp_assignment_store, "read_rows", return_value=MOVES), \
             mock.patch.object(em, "FabricDAXConnector", side_effect=RuntimeError("offline")), \
             mock.patch.object(em.targetsun_read, "granular_df_for_team", side_effect=_granular):
            with self.assertRaises(HTTPException):
                em.load_live_targets_payload(sup, 10, 2026, refresh=True)
        return seen.get("emps")

    def test_destination_includes_moved_employee(self):
        self.assertEqual(self._emp_list_seen("SL359"), ["S100", "S101", "S516"])

    def test_source_excludes_moved_employee(self):
        with self.assertRaises(HTTPException):
            # ทีมต้นทางเหลือ 0 คน = 404 ไม่มีรายชื่อ (ไม่ไปดึงเป้าของ S516 มาทับ grain)
            with mock.patch.object(em.targetsun_read, "is_enabled", return_value=True), \
                 mock.patch.object(em, "read_cached_employee_payload", return_value=None), \
                 mock.patch.object(em.emp_assignment_store, "read_rows", return_value=MOVES), \
                 mock.patch.object(em.targetsun_read, "granular_df_for_team") as g:
                try:
                    em.load_live_targets_payload("SL372", 10, 2026, refresh=True)
                finally:
                    g.assert_not_called()


if __name__ == "__main__":
    unittest.main()
