"""
เลือกวิธี "ปีที่แล้ว" / "6 เดือน" แล้วไฟล์ประวัติไม่มี — ต้องไม่ถอยไป 3 เดือนเงียบ ๆ

เดิมบอกไว้แค่ใน log ผู้ใช้จึงเห็นป้าย "วิธี: ปีที่แล้ว" บนจอทั้งที่ตัวเลขคิดจาก
ประวัติ 3 เดือนล่าสุด แล้วเอาไปอธิบายต่อให้ทีมไม่ได้ว่าทำไมเป้าออกมาแบบนี้
(และไม่รู้ด้วยว่าต้องไปโหลดหน้า Dashboard ใหม่เพื่อสร้างไฟล์ประวัตินั้น)
"""

from __future__ import annotations

import logging
import os
import sys
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services.optimize import _hist_input_for_strategy  # noqa: E402

logging.disable(logging.CRITICAL)

COLS = ["emp_id", "sku", "hist_boxes"]


def _hist(boxes: float) -> pd.DataFrame:
    return pd.DataFrame([{"emp_id": "E1", "sku": "734046", "hist_boxes": boxes}])


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=COLS)


def _call(strategy, *, h3, h6, ly, fallbacks=None):
    return _hist_input_for_strategy(
        strategy, h3, h6, ly,
        sup_id="SL397", target_month=9, target_year=2026,
        fallbacks=fallbacks,
    )


class TestNoSilentFallback(unittest.TestCase):
    def test_last_year_falls_back_to_3m_and_says_so(self):
        notes: list[str] = []
        df, months = _call("LY", h3=_hist(10), h6=_hist(20), ly=_empty(), fallbacks=notes)
        self.assertEqual(months, 3)
        self.assertEqual(notes, ["LY→3M"])

    def test_six_month_falls_back_to_3m_and_says_so(self):
        notes: list[str] = []
        df, months = _call("L6M", h3=_hist(10), h6=_empty(), ly=_empty(), fallbacks=notes)
        self.assertEqual(months, 3)
        self.assertEqual(notes, ["L6M→3M"])

    def test_no_note_when_the_chosen_history_exists(self):
        notes: list[str] = []
        _call("LY", h3=_hist(10), h6=_hist(20), ly=_hist(30), fallbacks=notes)
        _call("L6M", h3=_hist(10), h6=_hist(20), ly=_empty(), fallbacks=notes)
        _call("L3M", h3=_hist(10), h6=_hist(20), ly=_empty(), fallbacks=notes)
        self.assertEqual(notes, [], "ได้วิธีที่เลือกแล้ว ไม่ต้องเตือนอะไร")

    def test_the_same_fallback_is_not_reported_twice(self):
        """โหมดหลายกลยุทธ์เรียกซ้ำหลายรอบต่อการกระจายครั้งเดียว"""
        notes: list[str] = []
        for _ in range(3):
            _call("LY", h3=_hist(10), h6=_empty(), ly=_empty(), fallbacks=notes)
        self.assertEqual(notes, ["LY→3M"])

    def test_last_year_never_claims_six_months(self):
        """
        โค้ดเดิมมีสาขา want_6m ซ่อนอยู่ในบล็อก LY ซึ่งเป็นเท็จเสมอ (LY ไม่ใช่ L6M)
        อ่านแล้วชวนเข้าใจว่า LY ถอยไป 6M ได้ ทั้งที่ทำไม่ได้
        """
        _, months = _call("LY", h3=_empty(), h6=_hist(20), ly=_empty())
        self.assertEqual(months, 3)

    def test_six_month_with_no_history_at_all_keeps_its_window(self):
        df, months = _call("L6M", h3=_empty(), h6=_empty(), ly=_empty())
        self.assertTrue(df.empty)
        self.assertEqual(months, 6)

    def test_fallbacks_argument_is_optional(self):
        """ผู้เรียกเก่าที่ยังไม่ส่ง list มา ต้องไม่พัง"""
        _call("LY", h3=_hist(10), h6=_empty(), ly=_empty())


if __name__ == "__main__":
    unittest.main()
