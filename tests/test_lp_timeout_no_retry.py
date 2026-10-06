"""
LP หมดเวลาแล้วต้องไม่ลองซ้ำ (OPEN_ITEMS 7.6 — ผลตรวจ 5 ต.ค. 2026)

เดิม tiered LP ลองรอบสอง (ขยายกรอบ) ทุกสถานะที่ไม่ใช่ Optimal รวมถึงหมดเวลา
→ ผู้ใช้รอ 2 เท่า (สูงสุด ~120 วิ) แล้วก็ fallback อยู่ดี
ตอนนี้ลองซ้ำเฉพาะ Infeasible · หมดเวลา = fallback ทันที · ทั้งสองทางเป้าหีบรวมต้องตรง (I1)
"""

from __future__ import annotations

import logging
import os
import sys
import unittest
from unittest import mock

import pandas as pd
import pulp

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.OR_engine import allocate_boxes  # noqa: E402

logging.disable(logging.CRITICAL)


class TestLpTimeoutNoRetry(unittest.TestCase):
    def _run(self, status):
        emp = pd.DataFrame({"emp_id": ["E1", "E2", "E3"], "yellow_target": [10000, 8000, 6000]})
        sku = pd.DataFrame([
            {"sku": "A", "supervisor_target_boxes": 100, "price_per_box": 100.0},
            {"sku": "B", "supervisor_target_boxes": 50, "price_per_box": 80.0},
        ])
        hist = pd.DataFrame([
            {"emp_id": e, "sku": s, "hist_boxes": h}
            for e in ("E1", "E2", "E3") for s, h in (("A", 30), ("B", 15))
        ])
        calls = [0]

        def fake_solve(self, *a, **k):
            calls[0] += 1
            self.status = status
            return status

        with mock.patch.object(pulp.LpProblem, "solve", fake_solve):
            out = allocate_boxes(emp, sku, hist, strategy="L3M", tiered_allocation=True)
        return calls[0], out.groupby("sku")["allocated_boxes"].sum().to_dict()

    def test_timeout_falls_back_without_retry(self):
        calls, totals = self._run(pulp.LpStatusNotSolved)
        self.assertEqual(calls, 1)
        self.assertEqual(totals, {"A": 100, "B": 50})

    def test_infeasible_still_retries_wider_band(self):
        calls, totals = self._run(pulp.LpStatusInfeasible)
        self.assertEqual(calls, 2)
        self.assertEqual(totals, {"A": 100, "B": 50})


if __name__ == "__main__":
    unittest.main()
