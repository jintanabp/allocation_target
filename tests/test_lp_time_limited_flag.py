"""
CBC หยุดที่เวลาแต่ได้คำตอบ ต้องติดธง lp_time_limited (OPEN_ITEMS 6.2 / แบบสำรวจ R9)

PuLP ติดป้าย "Optimal" ให้ผลที่หยุดเพราะหมดเวลาด้วย (sol_status = LpSolutionIntegerFeasible)
ผลใช้ได้ ยอดหีบตรงเป้า แต่กดใหม่อาจต่างเล็กน้อย — ผู้ใช้ต้องรู้ ไม่ใช่เข้าใจว่าระบบสุ่ม
"""

from __future__ import annotations

import inspect
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
from backend.services import optimize as opt  # noqa: E402

logging.disable(logging.CRITICAL)

_real_solve = pulp.LpProblem.solve


def _inputs():
    emp = pd.DataFrame({"emp_id": ["E1", "E2", "E3"], "yellow_target": [10000, 8000, 6000]})
    sku = pd.DataFrame([
        {"sku": "A", "supervisor_target_boxes": 100, "price_per_box": 100.0},
        {"sku": "B", "supervisor_target_boxes": 50, "price_per_box": 80.0},
    ])
    hist = pd.DataFrame([{"emp_id": e, "sku": s, "hist_boxes": h}
                         for e in ("E1", "E2", "E3") for s, h in (("A", 30), ("B", 15))])
    return emp, sku, hist


class TestLpTimeLimitedFlag(unittest.TestCase):
    def _run(self, sol_status):
        def solve(self, *a, **k):
            st = _real_solve(self, *a, **k)  # แก้จริง แล้วแกล้งว่าหยุดที่เวลา
            self.sol_status = sol_status
            return st

        with mock.patch.object(pulp.LpProblem, "solve", solve):
            return allocate_boxes(*_inputs(), strategy="L3M", tiered_allocation=True)

    def test_time_limited_solution_is_flagged(self):
        out = self._run(pulp.LpSolutionIntegerFeasible)
        self.assertTrue(out.attrs.get("lp_time_limited"))
        self.assertFalse(out.attrs.get("optimization_fallback"))
        self.assertEqual(out.groupby("sku")["allocated_boxes"].sum().to_dict(), {"A": 100, "B": 50})

    def test_proven_optimal_not_flagged(self):
        out = self._run(pulp.LpSolutionOptimal)
        self.assertFalse(out.attrs.get("lp_time_limited"))

    def _run_status(self, status):
        def solve(self, *a, **k):
            _real_solve(self, *a, **k)
            self.status = status  # แกล้งว่า CBC จบด้วยสถานะนี้
            return status

        with mock.patch.object(pulp.LpProblem, "solve", solve):
            return allocate_boxes(*_inputs(), strategy="L3M", tiered_allocation=True)

    def test_not_solved_fallback_is_marked_as_timeout(self):
        """QA M9: CBC หมดเวลาก่อนเจอคำตอบ ("Not Solved") ต้องแยกจากโจทย์ขัดกันเอง"""
        out = self._run_status(pulp.LpStatusNotSolved)
        self.assertTrue(out.attrs.get("optimization_fallback"))
        self.assertTrue(out.attrs.get("lp_fallback_timeout"))
        self.assertEqual(out.groupby("sku")["allocated_boxes"].sum().to_dict(), {"A": 100, "B": 50})

    def test_infeasible_fallback_is_not_a_timeout(self):
        out = self._run_status(pulp.LpStatusInfeasible)
        self.assertTrue(out.attrs.get("optimization_fallback"))
        self.assertFalse(out.attrs.get("lp_fallback_timeout"))

    def test_usage_log_says_timeout_instead_of_no_answer(self):
        from backend.routers import optimize as opt_router

        seen = []
        req = mock.Mock(target_sup_ids=[], only_skus=[], strategy="L3M")
        with mock.patch.object(opt_router, "log_from_user", lambda *a, **k: seen.append(k)):
            opt_router._log_optimize_ok({}, req, "SLA", 11, 2026,
                                        {"optimization_fallback": True, "lp_fallback_timeout": True}, 1.0)
            opt_router._log_optimize_ok({}, req, "SLA", 11, 2026,
                                        {"optimization_fallback": True, "lp_fallback_timeout": False}, 1.0)
        self.assertIn("หมดเวลา", seen[0]["message"])
        self.assertNotIn("หาคำตอบไม่ได้)", seen[0]["message"])
        self.assertIn("หาคำตอบไม่ได้", seen[1]["message"])
        self.assertTrue(seen[0]["context"]["lp_fallback_timeout"])

    def test_optimize_response_carries_flag(self):
        src = inspect.getsource(opt)
        self.assertIn('"lp_time_limited": lp_time_limited,', src)
        self.assertIn('if df_alloc_grp.attrs.get("lp_time_limited"):', src)  # โหมดหลายวิธี
        self.assertIn('lp_time_limited = bool(df_allocation.attrs.get("lp_time_limited"))', src)


if __name__ == "__main__":
    unittest.main()
