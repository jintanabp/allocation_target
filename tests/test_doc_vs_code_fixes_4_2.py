"""
ผลตรวจ §4.2 จุดที่เป็นบั๊กในโค้ด (จุดอื่นแก้เอกสาร)

- §4.2-13 EFFECTIVEDATE ปี พ.ศ. แปลงไม่ได้ (pd.to_datetime รองรับถึงปี 2262) → ด่านตรวจงวดถูกข้ามเงียบ ๆ
- §4.2-15 push_multiple: หน้าแอดมิน clamp 1–100 แต่ตัวกระจายใช้ค่าดิบ
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.core.tga_period import _parse_effective_raw  # noqa: E402
from backend.services import alloc_rules_store as ars  # noqa: E402


class TestBuddhistEraEffectiveDate(unittest.TestCase):
    def test_be_year_becomes_ce(self):
        for raw in ("2569-10-01", "2569/10/01 00:00:00", "2569-10"):
            ts = _parse_effective_raw(raw)
            self.assertIsNotNone(ts, raw)
            self.assertEqual((ts.year, ts.month), (2026, 10), raw)

    def test_ce_unchanged_and_bad_is_none(self):
        self.assertEqual(_parse_effective_raw("2026-10-01").year, 2026)
        self.assertIsNone(_parse_effective_raw("ไม่ใช่วันที่"))
        self.assertIsNone(_parse_effective_raw(None))


class TestPushMultipleClamp(unittest.TestCase):
    def _with(self, value):
        with patch.object(ars, "_read_raw", return_value={"never_sold_zero": {"push_multiple": value}}):
            return ars.push_multiple()

    def test_above_max_is_clamped_like_admin_page(self):
        self.assertEqual(self._with(200), ars.MAX_PUSH_MULTIPLE)

    def test_normal_value_kept(self):
        self.assertEqual(self._with(7.5), 7.5)

    def test_below_one_falls_back_to_default(self):
        self.assertEqual(self._with(0.5), ars.DEFAULT_PUSH_MULTIPLE)


if __name__ == "__main__":
    unittest.main()
