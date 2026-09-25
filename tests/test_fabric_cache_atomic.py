"""
fabric_cache เขียนผ่าน atomic_io (retry ตอน PermissionError) และไม่ deadlock

- เดิมเขียน temp+replace เอง ไม่มี retry ตอน os.replace โดน PermissionError บน Windows
  (antivirus/ตัวทำ index ถือไฟล์ค้าง) เหมือนที่ atomic_io มี
- write_tga_skus_csv ถือ _LOCK (threading.Lock ธรรมดา) แล้วเรียก _write_json_cache ที่จับ
  _LOCK ซ้ำ = deadlock ตั้งแต่ครั้งแรกที่ถูกเรียก (ตอนนี้ยังไม่มีใครเรียก จึงยังไม่เคยเห็น)

ใช้ FABRIC_CACHE_DIR ชี้ temp เสมอ — ห้ามเขียนลง data/cache ของจริง
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.core import atomic_io  # noqa: E402
from backend.services import fabric_cache as fc  # noqa: E402


class FabricCacheAtomicTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(os.environ, {
            "FABRIC_CACHE_DIR": self._tmp.name, "FABRIC_STATIC_CACHE_TTL_SEC": "86400",
        })
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def test_write_tga_skus_csv_does_not_deadlock(self):
        df = pd.DataFrame({"sku": ["100001", "100002"]})
        t = threading.Thread(target=fc.write_tga_skus_csv, args=(2026, 9, df), daemon=True)
        t.start()
        t.join(5)
        self.assertFalse(t.is_alive(), "write_tga_skus_csv ค้าง (deadlock บน _LOCK)")
        back = fc.read_tga_skus_csv(2026, 9)
        self.assertEqual(back["sku"].tolist(), ["100001", "100002"])

    def test_json_cache_survives_a_transient_permission_error_on_replace(self):
        real_replace = os.replace
        calls = {"n": 0}

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] == 1:
                raise PermissionError("locked by antivirus")
            return real_replace(src, dst)

        with patch.object(atomic_io.os, "replace", side_effect=flaky):
            fc.write_price_map(2026, 9, {"100001": 123.5})
        self.assertGreaterEqual(calls["n"], 2, "ต้อง retry หลัง PermissionError")
        path = fc._price_path(2026, 9)
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        self.assertIn("cached_at", doc)
        self.assertEqual(fc.read_price_map(2026, 9), {"100001": 123.5})


if __name__ == "__main__":
    unittest.main()
