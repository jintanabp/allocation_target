"""
งานค้างจากออดิตความทนทาน 26 ส.ค. 2026 (กลุ่ม 1 — ผู้ใช้สั่งแก้ 1 ต.ค. 2026)

1. กระจายเฉพาะสินค้าแล้ว S.newProductSkus กลายเป็น Array → ป้าย「ใหม่」หาย และปุ่มปรับยอดอัตโนมัติ
   เลิกแบ่งเท่าสินค้าใหม่ (ใช้ .has)
2. กดปุ่มกระจายซ้อนกันได้ระหว่าง modal ยืนยัน / ระหว่างบันทึกผลรวมภาค
3. ล็อกช่องเดียวกันซ้ำ ด่าน I2 บวกซ้ำ → 400 "ล็อกเกินเป้า" ที่ผู้ใช้แก้เองไม่ได้
"""

from __future__ import annotations

import os
import re
import sys
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend import OR_engine as eng  # noqa: E402
from backend.services.optimize import _dedupe_locks  # noqa: E402

with open(os.path.join(REPO, "frontend", "app.js"), encoding="utf-8") as _f:
    APP = _f.read()


def _fn(name: str) -> str:
    m = re.search(rf"^(?:async )?function {re.escape(name)}\(", APP, re.M)
    assert m, name
    return APP[m.start():APP.index("\n}\n", m.start()) + 3]


class TestDuplicateLocks(unittest.TestCase):
    def _case(self):
        df_emp = pd.DataFrame([{"emp_id": "E1", "yellow_target": 1000.0},
                               {"emp_id": "E2", "yellow_target": 1000.0}])
        df_sku = pd.DataFrame([{"sku": "S1", "supervisor_target_boxes": 10, "price_per_box": 100.0}])
        df_hist = pd.DataFrame(columns=["emp_id", "sku", "hist_boxes"])
        return df_emp, df_sku, df_hist

    def test_engine_same_cell_locked_twice_is_one_lock(self):
        # 6 + 6 = 12 > เป้า 10 ถ้าบวกซ้ำ — แต่เป็นช่องเดียว ต้องผ่าน
        locks = [{"emp_id": "E1", "sku": "S1", "locked_boxes": 6},
                 {"emp_id": "E1", "sku": " S1 ", "locked_boxes": 6}]
        _, _, _, out = eng._normalize_engine_inputs(*self._case(), locks)
        self.assertEqual(out, [{"emp_id": "E1", "sku": "S1", "locked_boxes": 6}])

    def test_engine_keeps_last_value_like_locked_map(self):
        locks = [{"emp_id": "E1", "sku": "S1", "locked_boxes": 3},
                 {"emp_id": "E1", "sku": "S1", "locked_boxes": 7}]
        _, _, _, out = eng._normalize_engine_inputs(*self._case(), locks)
        self.assertEqual([lk["locked_boxes"] for lk in out], [7])

    def test_engine_still_rejects_real_overlock(self):
        locks = [{"emp_id": "E1", "sku": "S1", "locked_boxes": 6},
                 {"emp_id": "E2", "sku": "S1", "locked_boxes": 6}]
        with self.assertRaises(eng.LockedEditsExceedTarget):
            eng._normalize_engine_inputs(*self._case(), locks)

    def test_router_dedupe_keeps_last_and_first_order(self):
        got = _dedupe_locks([
            {"emp_id": "E1", "sku": "S1", "locked_boxes": 3},
            {"emp_id": "E2", "sku": "S1", "locked_boxes": 1},
            {"emp_id": "E1", "sku": "S1 ", "locked_boxes": 6},
        ])
        self.assertEqual([(d["emp_id"], d["locked_boxes"]) for d in got], [("E1", 6), ("E2", 1)])

    def test_router_dedupe_runs_before_i2_check(self):
        src = open(os.path.join(REPO, "backend", "services", "optimize.py"), encoding="utf-8").read()
        self.assertLess(src.index("locked_edits_data = _dedupe_locks(locked_edits_data)"),
                        src.index("_locked_by_sku: dict[str, int] = {}"))


class TestNewProductSkusStaysASet(unittest.TestCase):
    def test_partial_run_keeps_a_set(self):
        body = _fn("runReAllocationForSkus")
        self.assertIn("S.newProductSkus = new Set([...prevNewSkus", body)
        self.assertNotIn("S.newProductSkus = [...", body)
        self.assertNotIn("Array.isArray(S.newProductSkus)", body)

    def test_no_code_assigns_an_array(self):
        self.assertIsNone(re.search(r"S\.newProductSkus\s*=\s*\[", APP))


class TestAllocRunGuard(unittest.TestCase):
    ENTRY = ("runOptimization", "runReAllocationKeepEdits", "reloadThenReallocChanged",
             "runReAllocationOnlyChanged", "runReAllocationForSkus")

    def test_every_distribute_entry_point_is_guarded(self):
        for name in self.ENTRY:
            with self.subTest(fn=name):
                self.assertIn("return _runAllocGuarded(", _fn(name).split("\n")[1])

    def test_inner_calls_pass_nested_so_they_do_not_block_themselves(self):
        self.assertIn("runReAllocationOnlyChanged({ _nested: true })", _fn("runOptimization"))
        for name in ("reloadThenReallocChanged", "runReAllocationOnlyChanged"):
            with self.subTest(fn=name):
                body = _fn(name)
                call = body[body.index("await runReAllocationForSkus("):]
                self.assertIn("_nested: true", call[:200])

    def test_guard_releases_the_flag_in_finally(self):
        body = _fn("_runAllocGuarded")
        self.assertIn("if (nested) return fn();", body)
        self.assertLess(body.index("_allocRunInFlight = true;"), body.index("finally"))
        self.assertIn("_allocRunInFlight = false;", body[body.index("finally"):])


if __name__ == "__main__":
    unittest.main()
