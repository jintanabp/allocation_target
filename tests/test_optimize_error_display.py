"""
_callOptimizeApi เคยส่ง errBody.detail (มักเป็น object เช่น {code, message,
sku_total_checks, mismatch_count}) เข้า _userFacingError ตรง ๆ ทั้งที่ฟังก์ชันนั้นรู้จัก
แค่ string — String(object) กลายเป็น "[object Object]" ผู้ใช้เห็นข้อความนี้แทนเหตุผล
จริง (เช่น 409 "ผลกระจายไม่ตรงเป้าหีบ...") ทุกครั้งที่ /optimize ตอบ error เป็น object

ต้องแกะด้วย _formatApiErrorDetail ก่อนเหมือนอีก ~15 จุดในไฟล์เดียวกัน — เทสนี้อ่านซอร์ส
เพราะ frontend/app.js ไม่ใช่ CommonJS module (ไม่มี module.exports ให้ require ตรง ๆ
แบบ frontend/logic.js) เทสโค้ด Python อื่นในโปรเจกต์นี้ก็อ่านซอร์ส app.js แบบเดียวกัน
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


def _fn(name: str, max_len: int = 3000) -> str:
    i = APP.index(f"function {name}(")
    return APP[i : i + max_len]


class TestCallOptimizeApiErrorFormatting(unittest.TestCase):
    def test_error_detail_is_formatted_before_use(self):
        body = _fn("_callOptimizeApi", 4000)
        self.assertIn("_formatApiErrorDetail(errBody)", body)

    def test_raw_detail_object_is_never_passed_to_userFacingError_directly(self):
        body = _fn("_callOptimizeApi", 4000)
        self.assertNotIn("{ message: errBody.detail }", body)

    def test_format_api_error_detail_handles_object_shaped_detail(self):
        """ตรวจว่า _formatApiErrorDetail เองยังรองรับ detail เป็น object ทั้ง message/hint_th"""
        body = _fn("_formatApiErrorDetail", 2000)
        self.assertIn('typeof d.message === "string"', body)


if __name__ == "__main__":
    unittest.main()
