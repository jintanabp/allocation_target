"""
หน้า quick-distribute (ผลตรวจ 28 ก.ย. 2026 §3.1 §3.4 §3.8)

- §3.1 แถวหลายคลังต้องไม่ถูกรวมเป็นแถวเดียว — รันตารางจริงผ่าน node (tests/js/)
- §3.4 ต่ออายุ token ก่อนเรียก และลองใหม่เมื่อ 401
- §3.8 ข้อความ error ที่ detail เป็น list · ส่ง supervisor_code ตอนรวมภาค · ส่ง unit เมื่อภาคปนหน่วย
"""

from __future__ import annotations

import os
import shutil
import subprocess
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
QD = os.path.join(REPO, "frontend", "quick-distribute.js")


def _src() -> str:
    with open(QD, encoding="utf-8") as fh:
        return fh.read().replace("\r\n", "\n")


class TestMultiWarehouseRows(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "ไม่มี node ในเครื่องนี้")
    def test_table_renders_one_row_per_warehouse(self):
        r = subprocess.run(
            ["node", os.path.join(REPO, "tests", "js", "qd_multi_warehouse.test.js")],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class TestQuickDistributeSource(unittest.TestCase):
    def test_token_is_refreshed_and_401_retried(self):
        src = _src()
        fetch = src[src.index("async function qdFetch("):]
        fetch = fetch[:fetch.index("\n}\n")]
        self.assertIn("await qdRefreshToken(false)", fetch)
        self.assertIn("res.status === 401", fetch)

    def test_error_text_handles_list_detail(self):
        src = _src()
        self.assertIn("function qdErrorText(", src)
        self.assertNotIn("body?.detail?.message || body?.detail ||", src,
                         "รูปแบบเดิมให้ [object Object] เมื่อ detail เป็น list")

    def test_regional_payload_carries_supervisor_code(self):
        self.assertIn("row.supervisor_code = sup", _src())

    def test_mixed_unit_region_retries_with_own_unit(self):
        src = _src()
        self.assertIn("&unit=${encodeURIComponent(unit)}", src)
        self.assertIn('{ S: "credit", C: "van"', src)


if __name__ == "__main__":
    unittest.main()
