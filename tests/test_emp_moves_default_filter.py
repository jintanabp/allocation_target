"""
หน้า「ย้ายพนักงาน」ต้องเปิดมาเห็นเฉพาะคนที่ถูกตั้งค่าย้ายไว้

เดิมเปิดมาขึ้นรายชื่อทั้งหมด (หลายร้อยแถว ตัดที่ MAX = 300) ทั้งที่คนที่ถูกย้ายจริง
มีอยู่ไม่กี่คน — คำถามที่คนเปิดหน้านี้ถามบ่อยที่สุดคือ "ตอนนี้ย้ายใครไว้บ้าง"
แต่ต้องกวาดตาหาหรือพิมพ์ค้นหาทุกครั้ง

ตัวกรองมีอยู่แล้ว (checkbox「เฉพาะที่ย้ายแล้ว」) ขาดแค่ค่าตั้งต้น

**กับดักที่เทสชุดนี้เฝ้า:** พอกรองมาตั้งแต่แรก หน้าจะว่างเปล่าตอนยังไม่มีใครถูกย้าย
ถ้าไม่บอกทางไปต่อ ผู้ใช้จะคิดว่าหน้าเสียหรือข้อมูลไม่โหลด (ผู้ใช้กลุ่มนี้สูงวัย
และเคยมีปัญหาเรื่องข้อความว่าง ๆ ที่ไม่บอกสาเหตุมาแล้ว)
"""

from __future__ import annotations

import os
import re
import sys
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)


def _read(rel: str) -> str:
    with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
        return fh.read()


APP = _read("frontend/app.js")
HTML = _read("frontend/index.html")


class TestTheFilterIsOnByDefault(unittest.TestCase):
    def test_only_moved_is_checked_in_the_markup(self):
        m = re.search(r'<input type="checkbox" id="empMoveOnlyMoved"[^>]*>', HTML)
        self.assertIsNotNone(m, "ต้องมี checkbox「เฉพาะที่ย้ายแล้ว」อยู่")
        self.assertIn(
            "checked", m.group(0),
            "ต้องติ๊กมาตั้งแต่เปิดหน้า ไม่งั้นขึ้นรายชื่อทั้งหมดเหมือนเดิม",
        )

    def test_toggling_it_still_redraws(self):
        """ติ๊กมาแล้วต้องเอาออกได้ ไม่งั้นเพิ่มคนใหม่ไม่ได้เลย"""
        m = re.search(r'<input type="checkbox" id="empMoveOnlyMoved"[^>]*>', HTML)
        self.assertIn("renderEmpMoves()", m.group(0))


class TestTheScreenTellsYouWhereToGoNext(unittest.TestCase):
    """หน้าว่างเพราะกรองอยู่ ไม่ใช่เพราะพัง — ต้องแยกให้ออกและบอกทางไปต่อ"""

    def _render_body(self) -> str:
        i = APP.index("function renderEmpMoves(")
        return APP[i : i + 4000]

    def test_the_empty_state_says_how_to_add_someone(self):
        body = self._render_body()
        i = body.index("ยังไม่มีใครถูกย้ายไปเกลี่ยเป้ากับทีมอื่น")
        line = body[i : i + 200]
        self.assertIn(
            "เฉพาะที่ย้ายแล้ว", line,
            "ข้อความตอนว่างต้องบอกให้เอาติ๊กออกเพื่อเลือกคนใหม่",
        )

    def test_the_summary_line_says_the_list_is_filtered(self):
        body = self._render_body()
        self.assertIn("กำลังแสดงเฉพาะคนที่ถูกย้าย", body)

    def test_the_three_empty_reasons_are_still_separate(self):
        """ค้นหาไม่เจอ / ยังไม่มีใครถูกย้าย / ยังไม่ได้โหลด — คนละสาเหตุ คนละข้อความ"""
        body = self._render_body()
        self.assertIn("ไม่พบพนักงานตามที่ค้นหา", body)
        self.assertIn("ยังไม่มีใครถูกย้ายไปเกลี่ยเป้ากับทีมอื่น", body)
        self.assertIn("ยังไม่มีรายชื่อพนักงาน", body)


class TestTheFilterStillComesFromTheCheckbox(unittest.TestCase):
    """กันคนลบ checkbox ทิ้งแล้วฮาร์ดโค้ดตัวกรองไว้ในโค้ด — จะเอาออกไม่ได้อีกเลย"""

    def test_render_reads_the_checkbox(self):
        i = APP.index("function renderEmpMoves(")
        body = APP[i : i + 1200]
        self.assertIn('getElementById("empMoveOnlyMoved")', body)
        self.assertIn("checked", body)

    def test_the_filter_compares_against_the_home_team(self):
        """ปลายทาง = ทีมเดิม ไม่นับว่าย้าย (แถว no-op ที่ค้างจากคนย้ายทีมจริง)"""
        i = APP.index("function renderEmpMoves(")
        body = APP[i : i + 1200]
        self.assertRegex(body, r"r\.to_sup\s*&&\s*r\.to_sup\s*!==\s*r\.home_sup")


if __name__ == "__main__":
    unittest.main()
