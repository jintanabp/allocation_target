"""
ผลตรวจ 7 ต.ค. 2026 ข5 — กด「เริ่มกระจายใหม่」(ลบผลกระจาย) แล้วหน้าผู้จัดการ/แอดมินต้องยังเห็นว่า "เคยส่งแล้ว"

เดิมสถานะส่งแล้วอยู่ใน snapshot อย่างเดียว ลบ snapshot = ทีมดูเหมือนไม่เคยส่ง ทั้งที่เป้าอยู่ใน Target Sun แล้ว
"""

from __future__ import annotations

import os
import tempfile
import unittest
import unittest.mock

from backend.services import allocation_store, sent_ledger

ROW = dict(PRODUCTCODE="A", SALESTYPE="S", DIVISIONCODE="D", SALESMANCODE="E1", AREACODE="10",
           PROVINCECODE="", WAREHOUSECODE="", QUANTITYCASE=5)


class TestSummarySentFromLedger(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data")
        # snapshot อยู่ใต้รากโปรเจกต์ (ไม่ใช่ cwd) — ต้องชี้ไปโฟลเดอร์ชั่วคราว ไม่งั้นเขียนลง data/ จริง
        self._env_alloc = __import__("unittest").mock.patch.dict(
            os.environ, {"ALLOCATIONS_DATA_DIR": os.path.join(self._tmp.name, "data", "allocations")}
        )
        self._env_alloc.start()

    def tearDown(self):
        self._env_alloc.stop()
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



class TestStrictModeAllowsIntentionalUnconditional(unittest.TestCase):
    """ผลตรวจ 7 ต.ค. 2026 ข15 — เปิด ALLOC_REQUIRE_IF_MATCH=1 แล้วประทับส่งแล้ว/บันทึกรวมภาคต้องยังผ่าน"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data")
        # snapshot อยู่ใต้รากโปรเจกต์ (ไม่ใช่ cwd) — ต้องชี้ไปโฟลเดอร์ชั่วคราว ไม่งั้นเขียนลง data/ จริง
        self._env_alloc = __import__("unittest").mock.patch.dict(
            os.environ, {"ALLOCATIONS_DATA_DIR": os.path.join(self._tmp.name, "data", "allocations")}
        )
        self._env_alloc.start()

    def tearDown(self):
        self._env_alloc.stop()
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def test_strict_mode(self):
        from unittest import mock

        body = {"sup_id": "SLZZQ", "target_month": 11, "target_year": 2026, "status": "draft",
                "allocations": [{"emp_id": "E1", "sku": "A", "allocated_boxes": 1}]}
        allocation_store.write_snapshot(dict(body))
        with mock.patch.dict(os.environ, {"ALLOC_REQUIRE_IF_MATCH": "1"}):
            with self.assertRaises(allocation_store.SnapshotPreconditionRequired):
                allocation_store.write_snapshot(dict(body))
            saved = allocation_store.write_snapshot(dict(body, status="sent_targetsun"), allow_unconditional=True)
        self.assertEqual(saved["version"], 2)


if __name__ == "__main__":
    unittest.main()
