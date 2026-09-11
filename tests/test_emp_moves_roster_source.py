"""
หน้า「ย้ายพนักงาน」ต้องเห็นคนของทีมที่ยังไม่เคยถูกเปิดใช้งาน

เคสที่ทำให้ต้องมาแก้ (ผลสำรวจ 1-2 ก.ย. 2026): SL225 ขอให้ย้าย **S556** มาให้ตัวเอง
แต่ S556 อยู่ใต้ทีม SL394 ซึ่งยังไม่เคยถูกเปิดบนเซิร์ฟเวอร์ — หน้านี้อ่านจากแคชไฟล์
(`emp_cache_*` / `tga_lines_*`) ซึ่งมีเฉพาะทีมที่เคยเปิด S556 จึงไม่มีแถวให้กดย้าย

ทางแก้เดิมคือ "ให้ใครสักคนไปเปิดทีม SL394 หนึ่งครั้งก่อน" ซึ่งเป็นขั้นตอนที่คนนอก
ทีมพัฒนาไม่มีทางเดาได้เอง และจะต้องทำซ้ำทุกครั้งที่เจอทีมใหม่

ทะเบียนพนักงานทั้งบริษัท (`company_roster`) มีอยู่แล้วและยิง Fabric คำสั่งเดียว
ทั้งบริษัท — เอามาเติมช่องว่างได้โดยไม่ต้องไล่ดึงเป้ารายทีมมาเก็บ
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.routers import admin as admin_router  # noqa: E402
from backend.services import company_roster  # noqa: E402


class EmpMovesSeesEveryoneTest(unittest.TestCase):
    """รหัสสมมติที่ไม่มีทางอยู่ในแคชไฟล์จริง — ถ้าโผล่ แปลว่ามาจากทะเบียนแน่นอน"""

    ROSTER = [
        {"emp_id": "ZZTEST1", "emp_name": "ทดสอบ ทะเบียน", "super_code": "SLZZTEST"},
        {"emp_id": "ZZTEST2", "emp_name": "", "super_code": "SLZZTEST"},
        {"emp_id": "", "emp_name": "ไม่มีรหัส", "super_code": "SLZZTEST"},
        {"emp_id": "ZZTEST3", "emp_name": "ไม่มีทีม", "super_code": ""},
    ]

    def _dir(self, roster):
        with mock.patch.object(
            company_roster, "get_company_roster",
            return_value={"available": True, "rows": roster, "row_count": len(roster)},
        ):
            return admin_router._employee_directory()

    def test_someone_whose_team_was_never_opened_still_shows_up(self):
        rows = {r["emp_id"]: r for r in self._dir(self.ROSTER)}
        self.assertIn("ZZTEST1", rows, "คนจากทะเบียนต้องมีแถวให้กดย้าย")
        self.assertEqual(rows["ZZTEST1"]["home_sup"], "SLZZTEST")
        self.assertEqual(rows["ZZTEST1"]["emp_name"], "ทดสอบ ทะเบียน")

    def test_rows_without_an_id_or_a_team_are_skipped(self):
        """ไม่มีทีม = ย้ายจากไหนไม่รู้ · แถวแบบนั้นโผล่มาก็กดอะไรไม่ได้"""
        ids = {r["emp_id"] for r in self._dir(self.ROSTER)}
        self.assertIn("ZZTEST2", ids)
        self.assertNotIn("ZZTEST3", ids, "ไม่มี super_code ต้องไม่ถูกเติม")
        self.assertNotIn("", ids)

    def test_it_says_where_the_row_came_from(self):
        rows = {r["emp_id"]: r for r in self._dir(self.ROSTER)}
        self.assertTrue(rows["ZZTEST1"]["from_roster"])
        self.assertEqual(rows["ZZTEST1"]["seen_period"], "ทะเบียนบริษัท")

    def test_the_file_cache_still_wins(self):
        """
        ทะเบียนเป็นตัวเติม ไม่ใช่ตัวทับ — ถ้าทับ คนที่ทะเบียนบอกว่าย้ายทีมแล้ว
        แต่แอปยังคำนวณด้วยทีมเดิม จะแสดงต้นทางผิด แล้วแอดมินกดย้ายจากทีมที่ไม่ใช่
        """
        base = self._dir([])
        if not base:
            self.skipTest("เครื่องนี้ไม่มีแคชไฟล์ให้เทียบ")
        first = base[0]
        hijack = [{"emp_id": first["emp_id"], "emp_name": "ทับ", "super_code": "SLZZHIJACK"}]
        after = {r["emp_id"]: r for r in self._dir(hijack)}
        self.assertEqual(
            after[first["emp_id"]]["home_sup"], first["home_sup"],
            "ของจากแคชไฟล์ต้องชนะทะเบียนเสมอ",
        )
        self.assertFalse(after[first["emp_id"]]["from_roster"])

    def test_a_broken_roster_cache_does_not_break_the_page(self):
        """แคชทะเบียนพัง/หาย ต้องเสียแค่คนที่เติมเข้ามา ไม่ใช่เปิดหน้าไม่ได้"""
        with mock.patch.object(
            company_roster, "get_company_roster", side_effect=RuntimeError("แคชพัง")
        ):
            rows = admin_router._employee_directory()
        self.assertIsInstance(rows, list)

    def test_it_reads_the_cache_only_and_never_hits_fabric(self):
        """หน้านี้ต้องเปิดได้ตอน Fabric ล่ม — ห้ามเผลอใส่ refresh=True"""
        with mock.patch.object(
            company_roster, "get_company_roster", return_value={"rows": []}
        ) as spy:
            admin_router._employee_directory()
        spy.assert_called_once_with()


class TheScreenSaysWhatToDoTest(unittest.TestCase):
    """
    ถ้าแคชทะเบียนยังว่าง ฟีเจอร์นี้จะไม่ทำอะไรเลยแบบเงียบ ๆ — แอดมินจะหาคนไม่เจอ
    แล้วสรุปว่าหน้าเสีย · ต้องบอกว่าต้องไปกดปุ่มไหน (บทเรียนเดียวกับ 💬 ที่หายทั้งหน้า)
    """

    def test_the_endpoint_reports_whether_the_roster_is_ready(self):
        src = _read("backend/routers/admin.py")
        block = src.split('@router.get("/emp-assignments")')[1].split("@router.")[0]
        self.assertIn('"roster"', block)
        self.assertIn('"available"', block)

    def test_the_page_tells_the_admin_where_the_button_is(self):
        js = _read("frontend/app.js")
        i = js.index("function renderEmpMoves(")
        block = js[i : i + 6000]
        self.assertIn("rosterNote", block)
        self.assertIn("ดึงข้อมูลพนักงาน", block, "ต้องบอกชื่อปุ่มตรง ๆ")
        self.assertIn("สรุปการใช้งาน", block, "ต้องบอกว่าปุ่มอยู่แท็บไหน")

    def test_the_roster_is_read_once_per_request_not_twice(self):
        """ทะเบียนทั้งบริษัทเป็นก้อนใหญ่ — อ่านซ้ำต่อการเปิดหน้าเป็นของฟรีที่ไม่ควรจ่าย"""
        src = _read("backend/routers/admin.py")
        block = src.split('@router.get("/emp-assignments")')[1].split("@router.")[0]
        self.assertEqual(block.count("_company_roster_status()"), 1)
        self.assertIn("_employee_directory(roster)", block)


def _read(rel: str) -> str:
    with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
        return fh.read()


if __name__ == "__main__":
    unittest.main()
