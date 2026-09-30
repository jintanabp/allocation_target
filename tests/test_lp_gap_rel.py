"""
LP ต้องตั้ง gapRel — ไม่งั้น CBC ไล่พิสูจน์คำตอบดีที่สุดจนชนเพดานเวลา (60 วิ) เกือบทุกทีม
แล้วคืนคำตอบที่เจอ ณ ตอนนั้น ผลต่างกันทุกครั้งที่กด (ผู้ใช้อนุมัติ 30 ก.ย. 2026)
"""

from __future__ import annotations

import logging
import os
import sys
import unittest
from unittest.mock import patch

import pandas as pd
import pulp

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend import OR_engine as eng  # noqa: E402

logging.disable(logging.CRITICAL)


class TestLpGapRel(unittest.TestCase):
    def test_solver_gets_gap_rel(self):
        seen = []
        real = pulp.PULP_CBC_CMD

        def spy(*a, **kw):
            seen.append(kw)
            return real(*a, **kw)

        df_emp = pd.DataFrame({"emp_id": ["E1", "E2", "E3"], "yellow_target": [1000.0, 2000.0, 3000.0]})
        df_sku = pd.DataFrame({"sku": ["A", "B"], "supervisor_target_boxes": [30, 20],
                               "price_per_box": [100.0, 150.0]})
        df_hist = pd.DataFrame({"emp_id": ["E1", "E2", "E3", "E1", "E2", "E3"],
                                "sku": ["A", "A", "A", "B", "B", "B"],
                                "hist_boxes": [5, 10, 15, 3, 6, 9]})
        with patch.object(pulp, "PULP_CBC_CMD", side_effect=spy):
            out = eng.allocate_boxes(df_emp, df_sku, df_hist, strategy="L3M")
        self.assertTrue(seen, "ต้องเข้าเส้นทาง LP")
        for kw in seen:
            self.assertEqual(kw.get("gapRel"), eng._LP_GAP_REL)
            self.assertIn("timeLimit", kw, "เพดานเวลายังต้องอยู่เป็นตาข่าย")
        self.assertEqual(out.groupby("sku")["allocated_boxes"].sum().to_dict(), {"A": 30, "B": 20})

    def test_gap_is_small(self):
        self.assertLessEqual(eng._LP_GAP_REL, 0.005)


if __name__ == "__main__":
    unittest.main()
