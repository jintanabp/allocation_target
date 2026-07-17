"""
บันทึกผลกระจายพร้อมกันต้องไม่ทำให้งานคนอื่นหายเงียบ ๆ

บั๊กเดิม: PUT /data/allocations เขียนทับทันทีโดยไม่เทียบอะไรเลย (last-writer-wins)
supervisor กับ manager แก้ SL เดียวกันคนละจอ → คนกดทีหลังลบงานคนแรกทิ้งโดยไม่มีใครรู้
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import allocation_store as store  # noqa: E402


def _body(sup="SL330", month=7, year=2026, boxes=1, by="a@x.com"):
    return {
        "sup_id": sup,
        "target_month": month,
        "target_year": year,
        "status": "optimized",
        "allocations": [{"emp_id": "E01", "sku": "S1", "allocated_boxes": boxes}],
        "yellow": {},
        "strategy": "L3M",
        "updated_by": by,
    }


class TestAllocationConcurrency(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._orig_dir = store.allocations_dir
        store.allocations_dir = lambda: self._tmpdir  # type: ignore[method-assign]
        os.environ.pop("ALLOC_REQUIRE_IF_MATCH", None)

    def tearDown(self):
        store.allocations_dir = self._orig_dir  # type: ignore[method-assign]
        os.environ.pop("ALLOC_REQUIRE_IF_MATCH", None)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_version_starts_at_one_and_increments(self):
        v1 = store.write_snapshot(_body(boxes=1))
        self.assertEqual(v1["version"], 1)
        v2 = store.write_snapshot(_body(boxes=2))
        self.assertEqual(v2["version"], 2)

    def test_legacy_snapshot_without_version_is_treated_as_zero(self):
        """snapshot เก่าที่บันทึกก่อน deploy ไม่มี field version — ต้องไม่ต้อง migrate"""
        path = store.allocation_snapshot_path("SL330", 7, 2026)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        legacy = _body()
        legacy["updated_at"] = "2026-07-01T00:00:00+00:00"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(legacy, f)

        saved = store.write_snapshot(_body(boxes=9), expected_version=0)
        self.assertEqual(saved["version"], 1)

    def test_cas_rejects_stale_version(self):
        store.write_snapshot(_body(boxes=1))  # version 1
        with self.assertRaises(store.SnapshotConflict) as cm:
            store.write_snapshot(_body(boxes=2), expected_version=0)
        self.assertEqual(cm.exception.current["version"], 1)

        ok = store.write_snapshot(_body(boxes=3), expected_version=1)
        self.assertEqual(ok["version"], 2)

    def test_no_precondition_is_permissive_by_default(self):
        """การันตีของ rollout เฟส 1: tab เก่าที่ไม่ส่ง version ต้องบันทึกได้เหมือนเดิม"""
        store.write_snapshot(_body(boxes=1))
        saved = store.write_snapshot(_body(boxes=2))  # ไม่ส่ง expected_version
        self.assertEqual(saved["version"], 2)
        self.assertEqual(saved["allocations"][0]["allocated_boxes"], 2)

    def test_require_if_match_env_rejects_blind_overwrite(self):
        store.write_snapshot(_body(boxes=1))
        os.environ["ALLOC_REQUIRE_IF_MATCH"] = "1"
        with self.assertRaises(store.SnapshotPreconditionRequired):
            store.write_snapshot(_body(boxes=2))
        # ส่ง version มาถูก → ผ่านได้ตามปกติ
        self.assertEqual(store.write_snapshot(_body(boxes=3), expected_version=1)["version"], 2)

    def test_require_if_match_still_allows_creating_new_snapshot(self):
        """snapshot ใหม่ไม่มี lost update ให้กัน — ต้องสร้างได้แม้เปิดโหมดบังคับ"""
        os.environ["ALLOC_REQUIRE_IF_MATCH"] = "1"
        saved = store.write_snapshot(_body(boxes=1))
        self.assertEqual(saved["version"], 1)

    def test_concurrent_cas_exactly_one_winner(self):
        """
        8 คนอ่าน version เดียวกันแล้วกดบันทึกพร้อมกัน — ต้องสำเร็จ 1 คน conflict 7 คน
        และไฟล์สุดท้ายต้องเป็นของผู้ชนะล้วน ไม่ใช่ข้อมูลปนกัน
        """
        store.write_snapshot(_body(boxes=0))  # version 1
        n = 8
        barrier = threading.Barrier(n, timeout=10)

        def attempt(idx: int):
            barrier.wait()
            try:
                store.write_snapshot(_body(boxes=100 + idx, by=f"u{idx}@x.com"), expected_version=1)
                return ("ok", idx)
            except store.SnapshotConflict:
                return ("conflict", idx)

        with ThreadPoolExecutor(max_workers=n) as ex:
            results = list(ex.map(attempt, range(n)))

        wins = [r for r in results if r[0] == "ok"]
        conflicts = [r for r in results if r[0] == "conflict"]
        self.assertEqual(len(wins), 1, f"ต้องมีผู้ชนะคนเดียว แต่ได้ {len(wins)}")
        self.assertEqual(len(conflicts), n - 1)

        final = store.read_snapshot("SL330", 7, 2026)
        self.assertEqual(final["version"], 2)
        winner_idx = wins[0][1]
        self.assertEqual(final["allocations"][0]["allocated_boxes"], 100 + winner_idx)
        self.assertEqual(final["updated_by"], f"u{winner_idx}@x.com")

    def test_concurrent_blind_writes_produce_clean_last_write(self):
        """
        ไม่ส่ง version = last-write-wins (พฤติกรรมเดิม) — ยอมรับได้ว่างานหาย
        แต่ต้องไม่ได้ไฟล์พังหรือข้อมูลปนกันสองคน
        """
        n = 8
        barrier = threading.Barrier(n, timeout=10)

        def attempt(idx: int):
            barrier.wait()
            for _ in range(5):
                store.write_snapshot(_body(boxes=200 + idx, by=f"u{idx}@x.com"))

        with ThreadPoolExecutor(max_workers=n) as ex:
            list(ex.map(attempt, range(n)))

        final = store.read_snapshot("SL330", 7, 2026)
        self.assertIsNotNone(final, "ไฟล์ต้องอ่านได้ ไม่พัง")
        boxes = final["allocations"][0]["allocated_boxes"]
        self.assertEqual(
            final["updated_by"],
            f"u{boxes - 200}@x.com",
            "ไฟล์สุดท้ายต้องเป็นของคนเดียวครบทั้งก้อน ไม่ใช่ field ปนกัน",
        )

    def test_sent_at_survives_later_edits(self):
        """
        "เคยส่ง Target Sun แล้ว" ต้องอยู่ถาวร แม้สถานะจะกลับเป็น draft หลัง super แก้ต่อ
        (ตารางสรุปใช้ field นี้ขึ้นป้าย「แบบร่าง (เคยส่งแล้ว)」)
        """
        sent = store.write_snapshot({**_body(boxes=1), "status": "sent_targetsun"})
        sent_at = sent["target_sun_sent_at"]
        self.assertTrue(sent_at)

        # super กลับมาแก้ต่อ → client ส่ง draft มาโดยไม่รู้จัก target_sun_sent_at
        after = store.write_snapshot({**_body(boxes=2), "status": "draft"})
        self.assertEqual(after["status"], "draft")
        self.assertEqual(
            after["target_sun_sent_at"],
            sent_at,
            "server ต้องเก็บประวัติการส่งเอง ไม่พึ่ง client ส่งกลับมา",
        )

    def test_sent_at_absent_when_never_sent(self):
        saved = store.write_snapshot({**_body(boxes=1), "status": "optimized"})
        self.assertIsNone(saved.get("target_sun_sent_at"))

    def test_resending_updates_sent_at(self):
        first = store.write_snapshot({**_body(boxes=1), "status": "sent_targetsun"})
        store.write_snapshot({**_body(boxes=2), "status": "draft"})
        again = store.write_snapshot(
            {**_body(boxes=2), "status": "sent_targetsun", "target_sun_sent_at": None}
        )
        self.assertTrue(again["target_sun_sent_at"])
        self.assertGreaterEqual(again["target_sun_sent_at"], first["target_sun_sent_at"])

    def test_mark_sent_targetsun_does_not_lose_allocations(self):
        """
        mark_sent_targetsun เป็น read-modify-write — ถ้าไม่อะตอมมิก allocations จะหาย
        เมื่อมี save คั่นกลางระหว่างที่มันอ่านกับเขียน
        """
        store.write_snapshot(_body(boxes=5))
        stop = threading.Event()
        errors: list[str] = []

        def saver():
            while not stop.is_set():
                try:
                    store.write_snapshot(_body(boxes=5))
                except Exception as e:
                    errors.append(f"saver {type(e).__name__}: {e}")

        def sender():
            for _ in range(25):
                try:
                    snap = store.mark_sent_targetsun("SL330", 7, 2026, updated_by="s@x.com")
                    if not snap.get("allocations"):
                        errors.append("allocations หายหลัง mark_sent_targetsun")
                except Exception as e:
                    errors.append(f"sender {type(e).__name__}: {e}")

        t1 = threading.Thread(target=saver, daemon=True)
        t2 = threading.Thread(target=sender)
        t1.start()
        t2.start()
        t2.join(timeout=15)
        stop.set()
        t1.join(timeout=5)

        self.assertEqual(errors[:5], [], f"พัง {len(errors)} ครั้ง")


if __name__ == "__main__":
    unittest.main()
