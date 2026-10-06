"""
log ต้องไม่โตไม่หยุด (OPEN_ITEMS 7.12 — ผลตรวจ 5 ต.ค. 2026)
- data/app.log หมุนไฟล์ (RotatingFileHandler)
- data/logs/usage_*.jsonl เก็บ ~13 เดือน (ลบวันละครั้งตอนเขียน)
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import usage_log_store as uls  # noqa: E402


class TestUsageLogRetention(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.p = mock.patch.dict(os.environ, {"USAGE_LOGS_DIR": self._tmpdir})
        self.p.start()

    def tearDown(self):
        self.p.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _touch(self, name):
        with open(os.path.join(self._tmpdir, name), "w", encoding="utf-8") as f:
            f.write("{}\n")

    def test_prune_by_file_date(self):
        for n in ("usage_2025-01-01.jsonl", "usage_2025-09-30.jsonl", "usage_2026-10-01.jsonl",
                  "usage_bad.jsonl", "other.txt"):
            self._touch(n)
        today = datetime(2026, 10, 6, tzinfo=timezone.utc)
        n = uls.prune_old_logs(400, today=today)  # cutoff 2025-09-01
        self.assertEqual(n, 1)
        self.assertEqual(
            sorted(os.listdir(self._tmpdir)),
            ["other.txt", "usage_2025-09-30.jsonl", "usage_2026-10-01.jsonl", "usage_bad.jsonl"],
        )

    def test_append_prunes_once_per_day(self):
        self._touch("usage_2000-01-01.jsonl")
        uls._last_prune_day = None
        uls.append_log(action="t", message="m")
        self.assertFalse(os.path.exists(os.path.join(self._tmpdir, "usage_2000-01-01.jsonl")))
        self._touch("usage_2000-01-02.jsonl")
        uls.append_log(action="t", message="m")  # วันเดียวกัน — ไม่สแกนซ้ำ
        self.assertTrue(os.path.exists(os.path.join(self._tmpdir, "usage_2000-01-02.jsonl")))


class TestAppLogRotates(unittest.TestCase):
    def test_main_uses_rotating_handler(self):
        with open(os.path.join(REPO, "backend", "main.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn('RotatingFileHandler("data/app.log"', src)
        self.assertNotIn('logging.FileHandler("data/app.log"', src)


if __name__ == "__main__":
    unittest.main()
