"""
§4.1-4 กติกาไม่เคยขาย=เป้า 0 ในรอบหลายทีม: ทีมที่ไม่มีไฟล์ประวัติ 12 เดือนต้องไม่ถูกตัดเป็น 0

ไฟล์ประวัติ 12 เดือนแยกตามทีม และเก็บเฉพาะคู่ที่ขายจริง เดิมเช็คแค่ว่ารวมทุกทีมแล้วว่างไหม
ถ้าทีมหนึ่งมีไฟล์ อีกทีมไม่มี คนทีมที่ไม่มีไฟล์ถูกนับว่า "ไม่เคยขาย" ทุกสินค้า → เป้า 0 ทั้งทีม
หลัก: ไม่มีข้อมูล ≠ ไม่เคยขาย
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend import OR_engine as eng  # noqa: E402
from backend.core.paths import hist_cache_path  # noqa: E402
from backend.services import optimize as opt  # noqa: E402

logging.disable(logging.CRITICAL)


def _emp(ids):
    return pd.DataFrame({"emp_id": ids, "yellow_target": [1000.0] * len(ids)})


def _sku(sku, boxes, price=100.0):
    return pd.DataFrame({"sku": [sku], "supervisor_target_boxes": [boxes], "price_per_box": [price]})


def _sold(rows):
    return pd.DataFrame([{"emp_id": e, "sku": s, "hist_boxes": b} for e, s, b in rows])


class TestPlanKnownEmps(unittest.TestCase):
    def test_default_all_known_is_unchanged(self):
        zero, even, summ = eng._never_sold_plan(
            _sold([("E1", "A", 24)]), _emp(["E1", "E2"]), _sku("A", 4), {}, 5.0
        )
        self.assertEqual(zero, {("E2", "A")})
        self.assertEqual(summ["A"]["reason"], "zeroed")

    def test_unknown_employee_is_not_zeroed(self):
        zero, even, summ = eng._never_sold_plan(
            _sold([("E1", "A", 24)]), _emp(["E1", "E2", "E3"]), _sku("A", 4), {}, 5.0,
            known_emps={"E1", "E2"},
        )
        self.assertEqual(zero, {("E2", "A")}, "E2 รู้ว่าไม่เคยขาย = ตัด · E3 ไม่มีข้อมูล = ไม่ตัด")
        self.assertEqual(summ["A"]["blocked"], 1)

    def test_no_known_seller_with_unknown_skips_rule(self):
        zero, even, summ = eng._never_sold_plan(
            _sold([("E1", "B", 24)]), _emp(["E1", "E2", "E3"]), _sku("A", 4), {}, 5.0,
            known_emps={"E1", "E2"},
        )
        self.assertEqual(zero, set())
        self.assertEqual(even, frozenset(), "ไม่รู้ว่า E3 ขายไหม จะสรุปว่าทั้งทีมไม่มีใครขายไม่ได้")
        self.assertEqual(summ["A"]["reason"], "hist_unknown")

    def test_push_target_with_unknown_skips_rule(self):
        # ความจุคนเคยขาย = 12/12 = 1 หีบ/เดือน · เป้า 50 > 5 เท่า แต่ E3 อาจขายเยอะ
        zero, even, summ = eng._never_sold_plan(
            _sold([("E1", "A", 12)]), _emp(["E1", "E2", "E3"]), _sku("A", 50), {}, 5.0,
            known_emps={"E1", "E2"},
        )
        self.assertEqual((zero, even), (set(), frozenset()))
        self.assertEqual(summ["A"]["reason"], "hist_unknown")

    def test_nobody_known_does_nothing(self):
        self.assertEqual(
            eng._never_sold_plan(_sold([("X", "A", 1)]), _emp(["E1"]), _sku("A", 4), {}, 5.0,
                                 known_emps=set()),
            (set(), frozenset(), {}),
        )

    def test_allocate_gives_unknown_team_boxes(self):
        df_emp = _emp(["E1", "E2", "E3"])
        df_hist = pd.DataFrame({"emp_id": ["E1", "E2", "E3"], "sku": ["A"] * 3, "hist_boxes": [3, 3, 3]})
        out = eng.allocate_boxes(df_emp, _sku("A", 9), df_hist, strategy="L3M",
                                 df_sold_12m=_sold([("E1", "A", 24)]),
                                 never_sold_known_emps={"E1", "E2"})
        got = dict(zip(out["emp_id"], out["allocated_boxes"]))
        self.assertEqual(got.get("E2", 0), 0)
        self.assertGreater(got.get("E3", 0), 0, "ทีมที่ไม่มีไฟล์ต้องไม่ถูกตัดเป็น 0")
        self.assertEqual(int(out["allocated_boxes"].sum()), 9)


class TestKnownEmpsFromFiles(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data", exist_ok=True)
        pd.DataFrame([{"emp_id": "A1", "sku": "X", "hist_boxes": 5}]).to_csv(
            hist_cache_path("SLA", 10, 2026, n_months=12), index=False
        )

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _targets(self):
        return pd.DataFrame({
            "emp_id": ["A1", "A2", "B1", "B2"],
            "supervisor_code": ["SLA", "SLA", "SLB", "slb"],
        })

    def test_team_without_file_is_unknown(self):
        raw = pd.DataFrame([{"emp_id": "A1", "sku": "X", "hist_boxes": 5}])
        reverse = {"A1": ("A1", ""), "A2": ("A2", ""), "B1|W1": ("B1", "W1"), "B1|W2": ("B1", "W2"),
                   "B2": ("B2", "")}
        known, missing = opt._never_sold_known_emps(
            raw, self._targets(), "SLA", ["SLA", "SLB"], reverse, 10, 2026
        )
        self.assertEqual(known, {"A1", "A2"}, "SLB ไม่มีไฟล์ — ทุกขา (แยกคลัง) ของคนทีมนั้นไม่รู้")
        self.assertEqual(missing, {"sups": ["SLB"], "employees": 2})

    def test_person_seen_in_any_file_is_known(self):
        raw = pd.DataFrame([{"emp_id": "B1", "sku": "X", "hist_boxes": 5}])
        reverse = {e: (e, "") for e in ("A1", "A2", "B1", "B2")}
        known, missing = opt._never_sold_known_emps(
            raw, self._targets(), "SLA", ["SLA", "SLB"], reverse, 10, 2026
        )
        self.assertEqual(known, {"A1", "A2", "B1"})
        self.assertEqual(missing["employees"], 1)

    def test_all_known_returns_none(self):
        df_t = pd.DataFrame({"emp_id": ["A1", "A2"]})  # ทีมเดียว ไม่มี supervisor_code
        self.assertEqual(
            opt._never_sold_known_emps(pd.DataFrame(), df_t, "SLA", ["SLA"], {"A1": ("A1", "")}, 10, 2026),
            (None, None),
        )


if __name__ == "__main__":
    unittest.main()
