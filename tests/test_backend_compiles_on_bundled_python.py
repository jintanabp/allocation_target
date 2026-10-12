"""
backend ทั้งหมดต้อง compile ได้ด้วย Python ที่มากับโปรเจกต์ (runtime/python — 3.11) ที่ Run_Local.bat ใช้

ตรวจซ้ำ 12 ต.ค. 2026: f-string ซ้อนเครื่องหมายคำพูดชนิดเดียวกัน (ใช้ได้ตั้งแต่ 3.12) ผ่านเทสทุกตัวในเครื่อง dev
ที่เป็น 3.12 แต่บน runtime 3.11 = SyntaxError ทั้งไฟล์ แอปเปิดไม่ขึ้น · ไม่มี runtime/python ในเครื่อง = ข้าม
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUNDLED = os.path.join(REPO, "runtime", "python", "python.exe" if os.name == "nt" else "bin/python3")


class TestBackendCompilesOnBundledPython(unittest.TestCase):
    def test_compile_all_backend(self):
        if not os.path.isfile(BUNDLED):
            self.skipTest("ไม่มี runtime/python ในเครื่องนี้")
        code = (
            "import compileall, sys; "
            "sys.exit(0 if compileall.compile_dir('backend', quiet=1, force=True, legacy=False) else 1)"
        )
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPYCACHEPREFIX=os.path.join(
            os.environ.get("TEMP", "/tmp"), "pycache_bundled_check"))
        r = subprocess.run([BUNDLED, "-c", code], cwd=REPO, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300, env=env)
        self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-3000:])


if __name__ == "__main__":
    unittest.main()
