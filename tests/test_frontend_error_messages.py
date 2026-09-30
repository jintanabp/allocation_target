"""
ข้อความ error บนหน้าจอต้องเป็นเหตุผลจริง (ผลตรวจ 28 ก.ย. 2026 §3.5 §3.7)

- §3.5 หน้าแอดมิน: 422 ทุกตัวเคยถูกแทนด้วย "เซิร์ฟเวอร์ยังไม่อัปเดต" ข้อความตรวจค่าจริงหาย
- §3.7 ดาวน์โหลด Excel: ข้อความจริงถูกทิ้ง · qs("#dlBtn") หาไม่เจอ ปุ่มจึงไม่ถูกปิด กดซ้ำได้
"""

from __future__ import annotations

import os
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))


def _read(*parts) -> str:
    with open(os.path.join(REPO, *parts), encoding="utf-8") as fh:
        return fh.read().replace("\r\n", "\n")


def _fn(src: str, name: str) -> str:
    i = src.index(f"function {name}(")
    return src[i:src.index("\n}\n", i)]


class TestAdmin422(unittest.TestCase):
    def test_validation_message_is_shown(self):
        body = _fn(_read("frontend", "app.js"), "_adminJsonFetch")
        self.assertIn("ข้อมูลไม่ถูกต้อง: ${msg}", body)
        self.assertIn('"extra_forbidden"', body, "ข้อความ server เก่าเหลือเฉพาะกรณีช่องที่ server ไม่รู้จัก")


class TestExcelExport(unittest.TestCase):
    def test_download_button_has_the_id_the_code_uses(self):
        html = _read("frontend", "index.html")
        self.assertIn('id="dlBtn" onclick="showExportModal()"', html)

    def test_double_click_is_guarded(self):
        body = _fn(_read("frontend", "app.js"), "doExport")
        self.assertIn("if (_exportInFlight)", body)

    def test_real_server_reason_is_shown(self):
        body = _fn(_read("frontend", "app.js"), "_doExportInner")
        self.assertNotIn("_userFacingError(null", body)
        self.assertEqual(body.count("_formatApiErrorDetail(j)"), 2)


if __name__ == "__main__":
    unittest.main()
