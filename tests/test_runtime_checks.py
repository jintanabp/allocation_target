"""
ตรวจสภาพแวดล้อมตอนเปิดแอป (ผลตรวจ §5.2) — data/ สองที่ และหลายโปรเซสใช้ข้อมูลชุดเดียวกัน
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.core import runtime_checks as rc  # noqa: E402


class TestDataDir(unittest.TestCase):
    def test_same_when_running_from_repo(self):
        cwd = os.getcwd()
        try:
            os.chdir(REPO)
            self.assertTrue(rc.data_dir_status()["same"])
        finally:
            os.chdir(cwd)

    def test_different_when_running_elsewhere(self):
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as d:
            try:
                os.chdir(d)
                self.assertFalse(rc.data_dir_status()["same"])
            finally:
                os.chdir(cwd)


class TestSingleProcessLock(unittest.TestCase):
    def test_second_process_cannot_take_the_lock(self):
        with tempfile.TemporaryDirectory() as d:
            code = (
                "import sys,time; sys.path.insert(0, %r)\n"
                "from backend.core import runtime_checks as rc\n"
                "print(rc.acquire_single_process_lock(%r), flush=True)\n"
                "time.sleep(%s)\n"
            )
            first = subprocess.Popen([sys.executable, "-c", code % (REPO, d, 5)], stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(first.stdout.readline().strip(), "True")
                second = subprocess.run([sys.executable, "-c", code % (REPO, d, 0)],
                                        capture_output=True, text=True, timeout=60)
                self.assertEqual(second.stdout.strip(), "False", "โปรเซสที่สองต้องรู้ว่ามีอีกโปรเซสถือล็อกอยู่")
            finally:
                first.kill()
                first.wait()
            third = subprocess.run([sys.executable, "-c", code % (REPO, d, 0)],
                                   capture_output=True, text=True, timeout=60)
            self.assertEqual(third.stdout.strip(), "True", "โปรเซสแรกจบแล้ว ล็อกต้องหลุด")


if __name__ == "__main__":
    unittest.main()
