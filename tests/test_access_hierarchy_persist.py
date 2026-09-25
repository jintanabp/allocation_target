"""
persist_hierarchy เขียน access_hierarchy.json + managers_cache.json แบบ atomic

เดิมใช้ open(..., "w") ตรง ๆ — ไฟล์ถูกตัดเหลือ 0 ไบต์ก่อนเขียน คนที่ login ตรงจังหวะนั้น
อ่านได้ไฟล์ครึ่งใบ และไม่มีล็อกกันสองคำขอ rebuild พร้อมกัน (ผลตรวจสอบระบบ 24 ก.ย. 2026)

⚠ ห้ามปล่อยให้ _repo_root() ชี้ repo จริง — ไม่งั้นเทสจะเขียนทับ data/managers_cache.json
ของจริง (ดูบทเรียนใน docs เรื่อง path สัมพัทธ์/_repo_root())
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import access_hierarchy as ah  # noqa: E402


class PersistHierarchyTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.hier = os.path.join(self.root, "config", "access_hierarchy.json")
        self.cache = os.path.join(self.root, "data", "managers_cache.json")
        self._patches = [
            patch.dict(os.environ, {"ACCESS_HIERARCHY_JSON_PATH": self.hier}),
            patch.object(ah, "_repo_root", return_value=self.root),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()

    def _payload(self, tag):
        return {"manager_codes": [tag], "supervisors": [f"SL{tag}"], "by_manager": {tag: [f"SL{tag}"]}}

    def test_writes_both_files_through_the_atomic_writer(self):
        calls = []
        real = ah.atomic_write_json

        def spy(path, obj, **kw):
            calls.append(os.path.normcase(os.path.abspath(path)))
            return real(path, obj, **kw)

        with patch.object(ah, "atomic_write_json", side_effect=spy):
            ah.persist_hierarchy(self._payload("M1"))
        self.assertEqual(
            sorted(calls),
            sorted(os.path.normcase(os.path.abspath(p)) for p in (self.hier, self.cache)),
        )
        for p in (self.hier, self.cache):
            with open(p, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["manager_codes"], ["M1"])

    def test_concurrent_rebuilds_leave_both_files_from_the_same_round(self):
        """สองคำขอ rebuild พร้อมกัน — สองไฟล์ต้องมาจาก payload ชุดเดียวกันเสมอ"""
        barrier = threading.Barrier(8, timeout=5)

        def run(i):
            barrier.wait()
            ah.persist_hierarchy(self._payload(f"M{i}"))

        threads = [threading.Thread(target=run, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        with open(self.hier, encoding="utf-8") as f:
            a = json.load(f)
        with open(self.cache, encoding="utf-8") as f:
            b = json.load(f)
        self.assertEqual(a, b)

    def test_reader_uses_the_same_path_lock(self):
        ah.persist_hierarchy(self._payload("M1"))
        with patch.object(ah, "read_locked", wraps=ah.read_locked) as spy:
            out = ah.load_hierarchy_payload()
        self.assertEqual(out["manager_codes"], ["M1"])
        spy.assert_called()


if __name__ == "__main__":
    unittest.main()
