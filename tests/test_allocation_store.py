"""Tests for server allocation snapshots."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import allocation_store as store  # noqa: E402


class TestAllocationStore(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._orig_dir = store.allocations_dir
        store.allocations_dir = lambda: self._tmpdir  # type: ignore[method-assign]

    def tearDown(self):
        store.allocations_dir = self._orig_dir  # type: ignore[method-assign]

    def test_write_and_read_snapshot(self):
        body = {
            "sup_id": "SL330",
            "target_month": 7,
            "target_year": 2026,
            "status": "optimized",
            "allocations": [{"emp_id": "E01", "sku": "SKU1", "allocated_boxes": 5}],
            "yellow": {"E01": 1000},
            "strategy": "L3M",
            "updated_by": "test@example.com",
        }
        saved = store.write_snapshot(body)
        self.assertEqual(saved["sup_id"], "SL330")
        self.assertIn("updated_at", saved)

        loaded = store.read_snapshot("SL330", 7, 2026)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded["status"], "optimized")
        self.assertEqual(len(loaded["allocations"]), 1)

    def test_list_summaries_marks_missing(self):
        items = store.list_summaries(["SL330", "SL331"], 7, 2026)
        self.assertEqual(len(items), 2)
        self.assertFalse(items[0]["has_snapshot"])
        self.assertFalse(items[1]["has_snapshot"])

    def test_mark_sent_targetsun(self):
        store.write_snapshot(
            {
                "sup_id": "SL330",
                "target_month": 7,
                "target_year": 2026,
                "status": "optimized",
                "allocations": [{"emp_id": "E01", "sku": "SKU1", "allocated_boxes": 2}],
            }
        )
        sent = store.mark_sent_targetsun("SL330", 7, 2026, updated_by="u@x.com")
        self.assertEqual(sent["status"], "sent_targetsun")
        self.assertTrue(sent.get("target_sun_sent_at"))

    def test_invalid_status_raises(self):
        with self.assertRaises(ValueError):
            store.write_snapshot(
                {
                    "sup_id": "SL330",
                    "target_month": 7,
                    "target_year": 2026,
                    "status": "invalid",
                    "allocations": [],
                }
            )

    def test_delete_and_list_all(self):
        store.write_snapshot(
            {
                "sup_id": "SL330",
                "target_month": 7,
                "target_year": 2026,
                "status": "optimized",
                "allocations": [{"emp_id": "E01", "sku": "SKU1", "allocated_boxes": 1}],
            }
        )
        items = store.list_all_snapshots(month=7, year=2026)
        self.assertEqual(len(items), 1)
        self.assertTrue(store.delete_snapshot("SL330", 7, 2026))
        self.assertFalse(store.delete_snapshot("SL330", 7, 2026))
        self.assertEqual(store.list_all_snapshots(month=7, year=2026), [])


class TestAllocationApiAuth(unittest.TestCase):
    def test_peer_supervisor_in_allowed_can_write(self):
        """peer ใน division+ภาค+หน่วยเดียวกัน (อยู่ใน allowed_supervisor_codes) เขียนได้ —
        ดู tests/test_peer_visibility.py สำหรับกรณี allowed/blocked เพิ่มเติม"""
        from backend.deps import ensure_allocation_write_allowed

        user = {
            "auth_disabled": False,
            "allowed_supervisor_codes": {"SL397", "SL402"},
            "home_supervisor_codes": {"SL397"},
            "userpls_manager_pick": set(),
        }
        ensure_allocation_write_allowed(user, "SL397")
        ensure_allocation_write_allowed(user, "SL402")

    def test_supervisor_outside_allowed_cannot_write(self):
        from fastapi import HTTPException

        from backend.deps import ensure_allocation_write_allowed

        user = {
            "auth_disabled": False,
            "allowed_supervisor_codes": {"SL397", "SL402"},
            "home_supervisor_codes": {"SL397"},
            "userpls_manager_pick": set(),
        }
        with self.assertRaises(HTTPException) as ctx:
            ensure_allocation_write_allowed(user, "SL999")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_manager_can_write_allowed_sl(self):
        from backend.deps import ensure_allocation_write_allowed

        user = {
            "auth_disabled": False,
            "allowed_supervisor_codes": {"SL397", "SL402"},
            "home_supervisor_codes": set(),
            "userpls_manager_pick": {"MGR01"},
        }
        ensure_allocation_write_allowed(user, "SL402")


if __name__ == "__main__":
    unittest.main()
