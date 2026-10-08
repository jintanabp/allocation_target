"""
ผลตรวจ 7 ต.ค. 2026 ข5 — กด「เริ่มกระจายใหม่」(ลบผลกระจาย) แล้วหน้าผู้จัดการ/แอดมินต้องยังเห็นว่า "เคยส่งแล้ว"

เดิมสถานะส่งแล้วอยู่ใน snapshot อย่างเดียว ลบ snapshot = ทีมดูเหมือนไม่เคยส่ง ทั้งที่เป้าอยู่ใน Target Sun แล้ว
"""

from __future__ import annotations

import os
import tempfile
import unittest

from backend.services import allocation_store, sent_ledger

ROW = dict(PRODUCTCODE="A", SALESTYPE="S", DIVISIONCODE="D", SALESMANCODE="E1", AREACODE="10",
           PROVINCECODE="", WAREHOUSECODE="", QUANTITYCASE=5)


class TestSummarySentFromLedger(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data")

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def test_no_snapshot_but_sent_shows_sent_at(self):
        sent_ledger.record_send("SLZZS", 11, 2026, [ROW], token="t", send_status="ok", import_url="U")
        [row] = allocation_store.list_summaries(["SLZZS"], 11, 2026)
        self.assertFalse(row["has_snapshot"])
        self.assertTrue(str(row.get("target_sun_sent_at") or "").endswith("Z"))

    def test_failed_send_not_counted(self):
        sent_ledger.record_send("SLZZS", 11, 2026, [ROW], token="t", send_status="failed", import_url="U")
        [row] = allocation_store.list_summaries(["SLZZS"], 11, 2026)
        self.assertNotIn("target_sun_sent_at", row)

    def test_never_sent_no_field(self):
        [row] = allocation_store.list_summaries(["SLZZS"], 11, 2026)
        self.assertEqual(row, {"sup_id": "SLZZS", "has_snapshot": False})


if __name__ == "__main__":
    unittest.main()
