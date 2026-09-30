"""
เฟส F4: นับการกระทำบนตารางผลแบบรวมยอดต่อรอบ (ไม่ log ทุกคลิก) — ลองบนหน้าเว็บแล้ว:
แก้ 1 · ล็อก 1 · คืนค่า 1 · ปรับยอด 4 หีบ → กระจายใหม่ = ส่ง client_edit_round หนึ่งบรรทัด ·
รอบที่ไม่ได้ทำอะไรไม่ส่ง
"""

from __future__ import annotations

import os
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
CRLF, LF = chr(13) + chr(10), chr(10)


def _fn(src: str, name: str) -> str:
    i = src.index(f"function {name}(")
    return src[i:src.index(LF + "}" + LF, i)]


class TestEditRoundWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO, "frontend", "app.js"), encoding="utf-8") as fh:
            cls.src = fh.read().replace(CRLF, LF)

    def test_counts_where_user_acts(self):
        self.assertIn('_tally("edits");', _fn(self.src, "onResultEdit"))
        self.assertIn('_tally("locks");', _fn(self.src, "onResultEdit"))
        self.assertIn('_tally("reverts");', _fn(self.src, "revertResultCell"))
        self.assertIn('_tally("revert_all");', _fn(self.src, "revertAllResultCells"))
        self.assertIn('_tally("rebalance_boxes"', _fn(self.src, "autoRebalance"))

    def test_flushes_at_round_boundaries(self):
        self.assertIn('_tallyFlush("recalc");', _fn(self.src, "_stampEngineRun"))
        self.assertIn('_tallyFlush("sent");', _fn(self.src, "_markAllocationSentTargetSun"))
        self.assertIn('_tallyFlush("page_hidden");', _fn(self.src, "_installDriftRecheckOnReturn"))

    def test_one_line_per_round_and_silent_when_idle(self):
        body = _fn(self.src, "_tallyFlush")
        self.assertIn('"client_edit_round"', body)
        self.assertIn("if (!acted) return;", body)


if __name__ == "__main__":
    unittest.main()
