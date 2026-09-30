"""
ไฟล์ Excel ที่ export ผูกกับงวด (ผลตรวจ 28 ก.ย. 2026 §2.7)

เดิม Target_{sup}_{brand}.xlsx ไม่มีงวด — สองแท็บ/สองคน export คนละงวดของทีมเดียวกัน
ไฟล์ทับกัน แล้วอาจดาวน์โหลดได้ไฟล์ของอีกงวด
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from fastapi import HTTPException  # noqa: E402

from backend.core.paths import excel_export_path, export_result_path  # noqa: E402
from backend.services import exporting  # noqa: E402


class TestExportPeriod(unittest.TestCase):
    def test_paths_differ_by_period(self):
        a = excel_export_path("SL397", "ALL", 8, 2026)
        b = excel_export_path("SL397", "ALL", 9, 2026)
        self.assertNotEqual(a, b)
        self.assertEqual(b, "data/Target_SL397_2026_09_ALL.xlsx")
        self.assertIn("2026_09", export_result_path("SL397", "ALL", 9, 2026))

    def test_without_period_is_the_old_name(self):
        self.assertEqual(excel_export_path("SL397", "ALL"), "data/Target_SL397_ALL.xlsx")

    def test_download_with_period_never_falls_back_to_another_period(self):
        tmp = tempfile.TemporaryDirectory()
        cwd = os.getcwd()
        os.chdir(tmp.name)
        try:
            os.makedirs("data")
            with open("data/Target_SL397_2026_08_ALL.xlsx", "wb") as fh:
                fh.write(b"x")
            with self.assertRaises(HTTPException) as ctx:
                exporting.download_excel_response("SL397", "ALL", 9, 2026)
            self.assertEqual(ctx.exception.status_code, 404)
            resp = exporting.download_excel_response("SL397", "ALL", 8, 2026)
            self.assertTrue(str(resp.path).endswith("Target_SL397_2026_08_ALL.xlsx"))
        finally:
            os.chdir(cwd)
            tmp.cleanup()

    def test_frontend_sends_the_period_when_downloading(self):
        with open(os.path.join(REPO, "frontend", "app.js"), encoding="utf-8") as fh:
            src = fh.read()
        i = src.index("/download/excel?sup_id=")
        self.assertIn("target_month=${S.targetMonth}", src[i:i + 300])


if __name__ == "__main__":
    unittest.main()
