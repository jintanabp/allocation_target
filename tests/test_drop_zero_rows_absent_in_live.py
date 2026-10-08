"""
ก่อนส่ง: แถวหีบ 0 ที่คีย์ไม่มีใน Target Sun (อ่านสด) ต้องถูกตัด — ไม่ให้จำนวนแถวเพิ่มโดยไม่จำเป็น (ผลตรวจซ้ำ 8 ต.ค. 2026)

ที่มา: ตัวล้างแถวค้างส่ง 0 ไปทุกคีย์ใน sent ledger · คีย์ที่ส่งครั้งก่อนแต่ไม่ได้ลงจริง (หมดเวลา) หรือถูกลบใน TS แล้ว
→ ส่ง 0 = สร้างแถวใหม่เปล่า (จำลองโดยผู้ตรวจอิสระ: แถว 1 → 2 ยอดเท่าเดิม ไม่มีด่านไหนจับได้)
"""

from __future__ import annotations

import unittest

import pandas as pd

from backend.services.lakehouse import LAKEHOUSE_CSV_COLUMNS, _live_target_row_key
from backend.services.targetsun_import import _drop_zero_rows_absent_in_live


def _row(emp, sku, qty, wh=""):
    r = {c: "" for c in LAKEHOUSE_CSV_COLUMNS}
    r.update(PRODUCTCODE=sku, SALESTYPE="S", DIVISIONCODE="D", SALESMANCODE=emp, AREACODE="10",
             WAREHOUSECODE=wh, QUANTITYCASE=qty)
    return r


class TestDropZeroRowsAbsentInLive(unittest.TestCase):
    def setUp(self):
        self.df = pd.DataFrame([_row("E1", "A", 10, "W1"), _row("E2", "A", 0, "W1"), _row("E9", "A", 0, "WX")])
        self.df.attrs["import_row_keys"] = ["x"]
        live_rows = [_row("E1", "A", 4, "W1"), _row("E2", "A", 6, "W1")]
        self.snap = {"keys": {_live_target_row_key(r) for r in live_rows}}

    def test_zero_row_with_unknown_key_dropped(self):
        content, out, n = _drop_zero_rows_absent_in_live(b"old", self.df, self.snap)
        self.assertEqual(n, 1)
        self.assertEqual(list(out.SALESMANCODE), ["E1", "E2"])     # E2=0 ยังอยู่ (ทับเป้าเดิมใน TS)
        self.assertEqual(int(out.QUANTITYCASE.sum()), 10)           # ยอดไม่เปลี่ยน
        self.assertNotEqual(content, b"old")                         # สร้างไฟล์ใหม่
        self.assertEqual(len(out.attrs["import_row_keys"]), 2)

    def test_boxes_row_with_unknown_key_kept(self):
        # แถวมีหีบ = คู่ใหม่ที่ตั้งใจ (กติกาเดิม) — ห้ามตัด ไม่งั้นยอดขาดเป้า
        df = pd.DataFrame([_row("E1", "A", 10, "W1"), _row("NEW", "A", 3, "")])
        _c, out, n = _drop_zero_rows_absent_in_live(b"old", df, self.snap)
        self.assertEqual(n, 0)
        self.assertEqual(len(out), 2)

    def test_unreadable_live_keeps_everything(self):
        content, out, n = _drop_zero_rows_absent_in_live(b"old", self.df, None)
        self.assertEqual((content, n, len(out)), (b"old", 0, 3))


if __name__ == "__main__":
    unittest.main()
