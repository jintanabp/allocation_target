"""
OPEN_ITEMS 9.4 (ผู้ใช้เลือก 12 ต.ค. 2026)

ก) ตัวหมุนหีบ SKU เป้าน้อยต้องไม่ส่งหีบไปให้คนที่ไม่มีประวัติแบรนด์/กลุ่มนั้นเลย
   เดิมเลือก "คนที่ได้แบรนด์น้อยสุด" ก่อน → คนไม่เคยขายได้ก่อนทุกครั้ง → ตอนกติกาไม่เคยขายปิด เกิดแถวใหม่ใน Target Sun
ง) /optimize บอกจำนวนคู่ที่ยังไม่มีแถวใน Target Sun (= แถวใหม่ตอนส่ง) — ไม่มี grain = None ไม่ใช่ 0
"""

from __future__ import annotations

import unittest
from unittest import mock

import pandas as pd

from backend.OR_engine import _rotate_small_skus_in_brand
from backend.services import optimize as opt

EMPS = [f"E{i}" for i in range(1, 7)]
SKUS = ["A", "B", "C"]


def _emp():
    return pd.DataFrame([{"emp_id": e, "yellow_target": 1000.0} for e in EMPS])


def _sku():
    return pd.DataFrame([
        {"sku": s, "supervisor_target_boxes": 3, "price_per_box": 100.0, "brand_name_thai": "มาม่า"} for s in SKUS
    ])


def _out(cells):
    return pd.DataFrame([{"emp_id": e, "sku": s, "allocated_boxes": b} for (e, s), b in cells.items()])


def _matrix(df):
    m = {}
    for e, s, b in zip(df["emp_id"], df["sku"], df["allocated_boxes"]):
        m[(str(e), str(s))] = m.get((str(e), str(s)), 0) + int(b)
    return m


class TestRotationSkipsNeverSoldBrand(unittest.TestCase):
    # E1–E3 ได้ทุก SKU (แบบที่ชั้นก่อนหน้าตัดสิน) · E4 เคยขายแบรนด์นี้ · E5, E6 ไม่เคยขายเลย
    CELLS = {(e, s): 1 for s in SKUS for e in EMPS[:3]}
    HIST = {("E1", "A"): 5, ("E2", "B"): 5, ("E3", "C"): 5, ("E4", "A"): 2}

    def test_never_sold_people_not_rotated_in(self):
        out, st = _rotate_small_skus_in_brand(_out(self.CELLS), _emp(), _sku(), hist_lookup=dict(self.HIST))
        m = _matrix(out)
        for s in SKUS:
            self.assertEqual(sum(m.get((e, s), 0) for e in EMPS), 3)  # ยอดต่อ SKU เท่าเดิม
        for e in ("E5", "E6"):
            self.assertEqual(sum(m.get((e, s), 0) for s in SKUS), 0, e)
        self.assertGreater(sum(m.get(("E4", s), 0) for s in SKUS), 0)  # คนเคยขายแบรนด์ยังได้หมุนเข้า

    def test_existing_holder_without_history_is_not_evicted_rule(self):
        """คนที่ได้หีบอยู่แล้วจากชั้นก่อน (แม้ไม่มีประวัติ) ยังอยู่ในวงหมุนได้ — ไม่ทำให้หีบหายที่ไป"""
        cells = dict(self.CELLS)
        cells.pop(("E3", "C"))
        cells[("E6", "C")] = 1  # ชั้นก่อนหน้าให้ E6 ไปแล้ว
        out, _ = _rotate_small_skus_in_brand(_out(cells), _emp(), _sku(), hist_lookup=dict(self.HIST))
        m = _matrix(out)
        self.assertEqual(sum(m.get((e, "C"), 0) for e in EMPS), 3)
        self.assertEqual(m.get(("E5", "A"), 0) + m.get(("E5", "B"), 0) + m.get(("E5", "C"), 0), 0)

    def test_people_covered_by_never_sold_rule_are_not_filtered_again(self):
        """กติกาไม่เคยขายครอบคลุม E5 แล้ว (คัดด้วย 12 เดือนผ่าน zero_pairs) — ห้ามคัดซ้ำด้วยประวัติช่วงสั้น"""
        out, _ = _rotate_small_skus_in_brand(
            _out(self.CELLS), _emp(), _sku(), hist_lookup=dict(self.HIST),
            rule_covered_emps=frozenset({"E5"}),
        )
        m = _matrix(out)
        self.assertGreater(sum(m.get(("E5", s), 0) for s in SKUS), 0)
        self.assertEqual(sum(m.get(("E6", s), 0) for s in SKUS), 0)  # ไม่อยู่ในกติกา + ไม่มีประวัติ = ไม่ได้

    def test_covered_set_from_rule_state(self):
        from backend.OR_engine import _never_sold_covered_emps

        sold = pd.DataFrame([{"emp_id": "E1", "sku": "A"}])
        self.assertEqual(_never_sold_covered_emps(None, None, _emp()), frozenset())        # กติกาปิด
        self.assertEqual(_never_sold_covered_emps(sold, None, _emp()), frozenset(EMPS))   # รู้ครบทุกคน
        self.assertEqual(_never_sold_covered_emps(sold, {"E1", "E2"}, _emp()), frozenset({"E1", "E2"}))

    def test_team_without_any_brand_history_keeps_old_behaviour(self):
        out, st = _rotate_small_skus_in_brand(_out(self.CELLS), _emp(), _sku(), hist_lookup={})
        m = _matrix(out)
        self.assertTrue(all(sum(m.get((e, s), 0) for s in SKUS) >= 1 for e in EMPS))


class TestNewTargetSunPairs(unittest.TestCase):
    def _final(self):
        return pd.DataFrame([
            {"emp_id": "E1", "sku": "A", "allocated_boxes": 3},
            {"emp_id": "E2", "sku": "A", "allocated_boxes": 1},   # ไม่มีใน grain → แถวใหม่
            {"emp_id": "E3", "sku": "B", "allocated_boxes": 0},   # 0 ไม่สร้างแถว
        ])

    def test_counts_pairs_missing_from_grain(self):
        grain = pd.DataFrame([{"emp_id": "E1", "sku": "A"}, {"emp_id": "E3", "sku": "B"}])
        with mock.patch.object(opt, "_read_tga_grain_across_teams", return_value=grain):
            got = opt._new_target_sun_pairs(self._final(), 11, 2026)
        self.assertEqual(got, {"count": 1, "sample": ["E2 × A"]})

    def test_no_grain_means_unknown(self):
        with mock.patch.object(opt, "_read_tga_grain_across_teams", return_value=pd.DataFrame()):
            self.assertIsNone(opt._new_target_sun_pairs(self._final(), 11, 2026))
        with mock.patch.object(opt, "_read_tga_grain_across_teams", side_effect=OSError("x")):
            self.assertIsNone(opt._new_target_sun_pairs(self._final(), 11, 2026))


if __name__ == "__main__":
    unittest.main()
