"""
รันเทสหน้าเว็บที่ "รันฟังก์ชันจริง" ทุกไฟล์ใน tests/js/ ผ่าน run_tests.py (OPEN_ITEMS 8.5 — ผลตรวจ 6 ต.ค. 2026 ข5)

เดิมเทสใน tests/js/ บางไฟล์ไม่มีใครเรียกจาก run_tests.py เลย — ต้องจำรัน node เอง ลืมเมื่อไรก็ไม่มีใครรู้ว่าพัง
ตอนนี้ไฟล์ใหม่ที่วางใน tests/js/ ชื่อ *.test.js ถูกรันอัตโนมัติ (ไม่ต้องมาแก้ไฟล์นี้)
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
JS_DIR = os.path.join(REPO, "tests", "js")


class TestFrontendRunnableJs(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "ไม่มี node ในเครื่องนี้")
    def test_every_js_test_passes(self):
        files = sorted(glob.glob(os.path.join(JS_DIR, "*.test.js")))
        self.assertGreaterEqual(len(files), 5, "ไม่พบเทส JS ที่รันได้")
        for f in files:
            with self.subTest(js=os.path.basename(f)):
                r = subprocess.run(
                    ["node", f], capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=REPO,
                )
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    @unittest.skipUnless(shutil.which("node"), "ไม่มี node ในเครื่องนี้")
    def test_logic_unit_tests_pass(self):
        r = subprocess.run(
            ["node", "--test", os.path.join(REPO, "tests", "logic.test.js")],
            capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=REPO,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
