"""
L1 — โหลดรวมภาคแบบขนาน ต้องคงพฤติกรรมเดิมทุกอย่าง:
  1. ลำดับ payload เรียงตาม sup_ids (เรียง+unique) เหมือนตอนวน for
  2. ทีมที่ล้มเหลวถูกใส่ skipped ไม่ทำให้ทีมอื่นพัง
  3. ทุกทีมล้มเหลว → 404 เหมือนเดิม
  4. AGGREGATE_LOAD_WORKERS=1 กลับไปทำงานทีละทีม (escape hatch)
"""

from __future__ import annotations

import os
import sys
import threading
import unittest
from unittest import mock

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from fastapi import HTTPException  # noqa: E402

from backend.services import employees as emp_svc  # noqa: E402


def _fake_payload(sup_id: str) -> dict:
    return {"employees": [{"emp_id": f"E-{sup_id}"}], "skus": [], "sup_id": sup_id}


class TestAggregateParallelLoad(unittest.TestCase):
    def setUp(self):
        os.environ.pop("AGGREGATE_LOAD_WORKERS", None)

    def tearDown(self):
        os.environ.pop("AGGREGATE_LOAD_WORKERS", None)

    def test_worker_count_bounds(self):
        self.assertEqual(emp_svc._aggregate_load_workers(2), 2, "ไม่เกินจำนวนทีมจริง")
        self.assertEqual(emp_svc._aggregate_load_workers(20), 6, "ค่าเริ่มต้น 6")
        os.environ["AGGREGATE_LOAD_WORKERS"] = "99"
        self.assertEqual(emp_svc._aggregate_load_workers(20), 8, "เพดาน 8")
        os.environ["AGGREGATE_LOAD_WORKERS"] = "ไม่ใช่ตัวเลข"
        self.assertEqual(emp_svc._aggregate_load_workers(20), 6, "ค่าพังกลับไป default")

    def test_order_preserved_and_runs_parallel(self):
        seen_threads: set[int] = set()
        barrier = threading.Barrier(3, timeout=10)

        def fake_load(sup_id, month, year, refresh=False):
            seen_threads.add(threading.get_ident())
            # ถ้าไม่ขนานจริง barrier จะ timeout
            barrier.wait()
            return _fake_payload(sup_id)

        with mock.patch.object(emp_svc, "load_employees_payload", side_effect=fake_load):
            out = emp_svc.load_employees_bulk(
                ["SL330", "SL225", "SL384"], 7, 2026, aggregate_label="รวมภาค (ทดสอบ)"
            )

        self.assertEqual(len(seen_threads), 3, "ต้องยิงขนานกันจริง 3 thread")
        self.assertEqual(out["aggregate_sup_ids"], ["SL225", "SL330", "SL384"])
        self.assertEqual(out["skipped_supervisors"], [])

    def test_failed_team_is_skipped_not_fatal(self):
        def fake_load(sup_id, month, year, refresh=False):
            if sup_id == "SL330":
                raise HTTPException(400, detail="ไม่มีเป้าหีบงวดนี้")
            if sup_id == "SL384":
                raise RuntimeError("DAX ล่ม")
            return _fake_payload(sup_id)

        with mock.patch.object(emp_svc, "load_employees_payload", side_effect=fake_load):
            out = emp_svc.load_employees_bulk(
                ["SL330", "SL225", "SL384"], 7, 2026, aggregate_label="รวมภาค (ทดสอบ)"
            )

        self.assertEqual(out["aggregate_sup_ids"], ["SL225"], "เหลือทีมที่โหลดได้")
        skipped = {s["sup_id"]: s["detail"] for s in out["skipped_supervisors"]}
        self.assertEqual(set(skipped), {"SL330", "SL384"})
        self.assertIn("ไม่มีเป้าหีบงวดนี้", skipped["SL330"])
        self.assertIn("DAX ล่ม", skipped["SL384"])

    def test_all_teams_fail_raises_404(self):
        def fake_load(sup_id, month, year, refresh=False):
            raise HTTPException(400, detail="พัง")

        with mock.patch.object(emp_svc, "load_employees_payload", side_effect=fake_load):
            with self.assertRaises(HTTPException) as ctx:
                emp_svc.load_employees_bulk(
                    ["SL330", "SL225"], 7, 2026, aggregate_label="รวมภาค (ทดสอบ)"
                )
        self.assertEqual(ctx.exception.status_code, 404)

    def test_serial_mode_gives_same_result(self):
        os.environ["AGGREGATE_LOAD_WORKERS"] = "1"
        used: list[int] = []

        def fake_load(sup_id, month, year, refresh=False):
            used.append(threading.get_ident())
            return _fake_payload(sup_id)

        with mock.patch.object(emp_svc, "load_employees_payload", side_effect=fake_load):
            out = emp_svc.load_employees_bulk(
                ["SL330", "SL225"], 7, 2026, aggregate_label="รวมภาค (ทดสอบ)"
            )

        self.assertEqual(len(set(used)), 1, "workers=1 ต้องรันใน thread เดียว")
        self.assertEqual(out["aggregate_sup_ids"], ["SL225", "SL330"])


if __name__ == "__main__":
    unittest.main()
