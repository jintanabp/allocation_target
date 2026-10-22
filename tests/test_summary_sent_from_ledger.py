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
        self._ep = __import__("unittest").mock.patch(
            "backend.services.targetsun_endpoints.targetsun_endpoints_summary", return_value={"import_url": "U"}
        )
        self._ep.start()

    def tearDown(self):
        self._ep.stop()
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

    def test_send_to_other_destination_not_counted(self):
        sent_ledger.record_send("SLZZS", 11, 2026, [ROW], token="t", send_status="ok", import_url="UAT")
        [row] = allocation_store.list_summaries(["SLZZS"], 11, 2026)
        self.assertNotIn("target_sun_sent_at", row)

    def test_never_sent_no_field(self):
        [row] = allocation_store.list_summaries(["SLZZS"], 11, 2026)
        self.assertEqual(row, {"sup_id": "SLZZS", "has_snapshot": False})

    # ── QA 21 ต.ค. 2026 L5 ─────────────────────────────────────────────
    def _save(self, status="optimized", edited=False):
        allocation_store.write_snapshot({
            "sup_id": "SLZZS", "target_month": 11, "target_year": 2026, "status": status,
            "allocations": [{"emp_id": "E1", "sku": "A", "allocated_boxes": 5, "is_edited": edited}],
        })

    def test_sent_but_mark_put_failed_still_shows_sent(self):
        """ส่งสำเร็จ (ledger จดแล้ว) แต่คำสั่งประทับ「ส่งแล้ว」จากหน้าเว็บล้ม — ต้องขึ้นว่าส่งแล้ว"""
        self._save("optimized")
        import time
        time.sleep(1.1)  # updated_at ละเอียดระดับวินาที
        sent_ledger.record_send("SLZZS", 11, 2026, [ROW], token="t", send_status="ok", import_url="U")
        [row] = allocation_store.list_summaries(["SLZZS"], 11, 2026)
        self.assertEqual(row["status"], "sent_targetsun")

    def test_edited_after_send_keeps_draft(self):
        sent_ledger.record_send("SLZZS", 11, 2026, [ROW], token="t", send_status="ok", import_url="U")
        import time
        time.sleep(1.1)
        self._save("draft", edited=True)
        [row] = allocation_store.list_summaries(["SLZZS"], 11, 2026)
        self.assertEqual(row["status"], "draft")
        self.assertTrue(row.get("target_sun_sent_at"))


class TestSentClaimNeedsLedger(unittest.TestCase):
    """PUT /data/allocations: หน้าเว็บอ้าง「ส่งแล้ว」ได้เฉพาะเมื่อมี sent ledger (QA L5)"""

    setUp = TestSummarySentFromLedger.setUp
    tearDown = TestSummarySentFromLedger.tearDown

    def _claim(self, *, edited=False, sent_at="2026-01-01T00:00:00Z"):
        from backend.routers import data as data_router

        body = data_router.AllocationSnapshotBody(
            sup_id="SLZZS", target_month=11, target_year=2026, status="sent_targetsun",
            allocations=[{"emp_id": "E1", "sku": "A", "allocated_boxes": 5, "is_edited": edited}],
            target_sun_sent_at=sent_at,
        )
        payload = body.model_dump()
        with unittest.mock.patch("backend.services.usage_log_store.log_from_user"):
            data_router._verify_sent_claim({}, "SLZZS", body, payload)
        return payload

    def test_claim_without_ledger_is_downgraded(self):
        p = self._claim()
        self.assertEqual(p["status"], "optimized")
        self.assertIsNone(p["target_sun_sent_at"])
        self.assertEqual(self._claim(edited=True)["status"], "draft")

    def test_claim_with_ledger_uses_server_time(self):
        sent_ledger.record_send("SLZZS", 11, 2026, [ROW], token="t", send_status="ok", import_url="U")
        p = self._claim()
        self.assertEqual(p["status"], "sent_targetsun")
        self.assertNotEqual(p["target_sun_sent_at"], "2026-01-01T00:00:00Z")
        self.assertTrue(str(p["target_sun_sent_at"]).endswith("Z"))

    def test_failed_send_is_not_evidence(self):
        sent_ledger.record_send("SLZZS", 11, 2026, [ROW], token="t", send_status="failed", import_url="U")
        self.assertEqual(self._claim()["status"], "optimized")



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
        self._ep = __import__("unittest").mock.patch(
            "backend.services.targetsun_endpoints.targetsun_endpoints_summary", return_value={"import_url": "U"}
        )
        self._ep.start()

    def tearDown(self):
        self._ep.stop()
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
