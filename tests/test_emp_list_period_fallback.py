"""
Fabric ล่ม + ทีมยังไม่เคยเปิดงวดนี้ = เปิดหน้าไม่ได้เลยทั้งวัน

emp_cache ผูกกับงวด ตัวถอย "ใช้แคช" เดิมจึงช่วยได้เฉพาะทีมที่เคยเปิดงวดนั้นมาก่อน
ทีมที่ยังไม่เคยเปิดจะเจอ 503 ตรง ๆ ทั้งที่รายชื่อพนักงานแทบไม่เปลี่ยนข้ามงวด
(อาการเดียวกับตอนราคา/ประวัติที่แก้ไปแล้ว เหลือรายชื่อพนักงานเป็นรูสุดท้าย)

ถอยข้ามงวดได้เฉพาะ "รหัสทีมเดียวกัน" เท่านั้น และต้องติดธงให้เห็นบนจอเสมอ
เพราะคนเข้า/ออกระหว่างงวดได้
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services.employees import (  # noqa: E402
    _newest_emp_cache_other_period,
)

logging.disable(logging.CRITICAL)


class TestNewestEmpCacheOtherPeriod(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        self._tmpdir = tempfile.mkdtemp(prefix="emp_fb_")
        os.makedirs(os.path.join(self._tmpdir, "data"), exist_ok=True)
        os.chdir(self._tmpdir)

    def tearDown(self):
        os.chdir(self._cwd)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _emp_file(self, sup: str, year: int, month: int) -> None:
        pd.DataFrame([{"emp_id": "E1", "supervisor_code": sup}]).to_csv(
            f"data/emp_cache_{sup}_{year}_{month:02d}.csv", index=False
        )

    def test_no_other_period_means_no_fallback(self):
        self._emp_file("SL397", 2026, 9)
        self.assertIsNone(_newest_emp_cache_other_period("SL397", 9, 2026))

    def test_picks_the_newest_earlier_period(self):
        self._emp_file("SL397", 2026, 6)
        self._emp_file("SL397", 2026, 8)
        self._emp_file("SL397", 2025, 12)
        got = _newest_emp_cache_other_period("SL397", 9, 2026)
        self.assertIsNotNone(got)
        self.assertEqual(got[1], "2026-08")

    def test_year_boundary_is_compared_correctly(self):
        """เทียบเป็นสตริง YYYY_MM — ธ.ค. ปีก่อนต้องแพ้ ม.ค. ปีนี้"""
        self._emp_file("SL397", 2025, 12)
        self._emp_file("SL397", 2026, 1)
        got = _newest_emp_cache_other_period("SL397", 9, 2026)
        self.assertEqual(got[1], "2026-01")

    def test_never_borrows_another_team(self):
        """คนละทีมคือคนละคนจริง ๆ — ห้ามหยิบมาใช้เด็ดขาด"""
        self._emp_file("SL460", 2026, 8)
        self.assertIsNone(_newest_emp_cache_other_period("SL397", 9, 2026))

    def test_a_later_period_can_also_be_borrowed(self):
        """เปิดงวดหน้าไว้ก่อนแล้วย้อนกลับมางวดนี้ ก็ยังดีกว่าเปิดไม่ได้เลย"""
        self._emp_file("SL397", 2026, 10)
        got = _newest_emp_cache_other_period("SL397", 9, 2026)
        self.assertEqual(got[1], "2026-10")

    def test_unrelated_files_are_ignored(self):
        pd.DataFrame([{"emp_id": "E1"}]).to_csv(
            "data/tga_lines_SL397_2026_08.csv", index=False
        )
        pd.DataFrame([{"emp_id": "E1"}]).to_csv(
            "data/emp_cache_SL397_notaperiod.csv", index=False
        )
        self.assertIsNone(_newest_emp_cache_other_period("SL397", 9, 2026))

    def test_missing_data_dir_is_not_an_error(self):
        shutil.rmtree("data")
        self.assertIsNone(_newest_emp_cache_other_period("SL397", 9, 2026))


if __name__ == "__main__":
    unittest.main()
