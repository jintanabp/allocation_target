"""
autosave ที่ค้างอยู่ต้องไม่ไปทับทีม/งวดใหม่หลังสลับ (ผลตรวจ 28 ก.ย. 2026 §3.3, §3.6)

เดิม: ตัวจับเวลา 800ms อ่าน S.supId / S.allocations ตอน "ทำงาน" — สลับทีมภายใน 800ms
ของทีมเดิมถูกบันทึกลงชื่อทีมใหม่ · server ก็รับรายการว่างทับของเดิมโดยไม่ตรวจ
และหน้าเว็บเอา 428/409 ทุกแบบไปขึ้นกล่อง "มีคนบันทึกทับ" (ผู้บันทึก: ไม่ระบุ)
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from fastapi import HTTPException  # noqa: E402

from backend.routers import data as rd  # noqa: E402
from backend.services import allocation_store  # noqa: E402


def _fn(src: str, name: str) -> str:
    i = src.index(f"function {name}(")
    j = src.index("\n}\n", i)
    return src[i:j]


class TestFrontendCapturesContext(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO, "frontend", "app.js"), encoding="utf-8") as fh:
            cls.src = fh.read().replace("\r\n", "\n")

    def test_single_team_save_captures_team_period_and_data_when_queued(self):
        body = _fn(self.src, "queueServerAllocationSave")
        before_timer = body[:body.index("setTimeout(")]
        for field in ("supId: S.supId", "targetMonth: S.targetMonth", "allocations: S.allocations",
                      "yellow: S.yellow"):
            self.assertIn(field, before_timer, f"ต้องจับ {field} ตอนเข้าคิว")
        self.assertIn("_allocSaveContextKey() === ctx.key", body)

    def test_save_body_uses_the_captured_period(self):
        body = _fn(self.src, "saveServerAllocationSnapshot")
        self.assertIn("target_month: opts.targetMonth || S.targetMonth", body)

    def test_regional_save_is_skipped_after_a_switch(self):
        body = _fn(self.src, "queueRegionalAllocationSave")
        self.assertRegex(body, r"_allocSaveContextKey\(\) !== ctxKey\) \{\s*console\.warn")

    def test_only_real_conflicts_open_the_conflict_dialog(self):
        body = _fn(self.src, "saveServerAllocationSnapshot")
        self.assertIn('=== "snapshot_conflict"', body)


class TestServerRefusesEmptyOverwrite(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._p = patch.object(allocation_store, "allocations_dir", return_value=self._tmp.name)
        self._p.start()
        allocation_store.write_snapshot({
            "sup_id": "SLA", "target_month": 9, "target_year": 2026, "status": "optimized",
            "allocations": [{"emp_id": "E1", "sku": "A", "allocated_boxes": 3}],
        })

    def tearDown(self):
        self._p.stop()
        self._tmp.cleanup()

    def _put(self, allocations):
        body = rd.AllocationSnapshotBody(
            sup_id="SLA", target_month=9, target_year=2026, status="draft", allocations=allocations,
        )
        with patch.object(rd, "ensure_allocation_write_allowed"), \
                patch("backend.services.usage_log_store.log_from_user"):
            return rd.put_allocation_snapshot(body, user={"email": "s@x.co"})

    def test_empty_list_over_existing_rows_is_refused(self):
        with self.assertRaises(HTTPException) as ctx:
            self._put([])
        self.assertEqual(ctx.exception.detail["code"], "empty_allocation_overwrite")
        snap = allocation_store.read_snapshot("SLA", 9, 2026)
        self.assertEqual(len(snap["allocations"]), 1, "ของเดิมต้องไม่หาย")


if __name__ == "__main__":
    unittest.main()
