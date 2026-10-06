"""
ล้างแคช payload แล้วลบไฟล์ไม่ได้ (Windows ถือไฟล์) ต้องไม่ใช้แคชเก่าต่อ (OPEN_ITEMS 7.13 — ผลตรวจ 5 ต.ค. 2026)
ไฟล์อยู่ใต้ data/ แบบ path สัมพัทธ์ → chdir ไปโฟลเดอร์ชั่วคราว ไม่แตะ data/ จริง
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import employee_payload_cache as epc  # noqa: E402

_real_remove = os.remove
_real_sleep = time.sleep


class TestPayloadCacheInvalidateLocked(unittest.TestCase):
    def setUp(self):
        self.old = os.getcwd()
        self._tmpdir = tempfile.mkdtemp()
        os.chdir(self._tmpdir)
        os.makedirs("data")
        self.env = mock.patch.dict(os.environ, {"EMPLOYEE_PAYLOAD_CACHE_TTL_SEC": "3600"})
        self.env.start()
        epc._INVALIDATED_AT.clear()
        self.sleep = mock.patch.object(epc.time, "sleep")
        self.sleep.start()

    def tearDown(self):
        self.sleep.stop()
        self.env.stop()
        epc._INVALIDATED_AT.clear()
        os.chdir(self.old)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _write(self):
        epc.write_cached_employee_payload("SLX", 11, 2026, {"employees": [{"emp_id": "E1"}], "skus": []})
        self.assertIsNotNone(epc.read_cached_employee_payload("SLX", 11, 2026))

    def test_locked_file_is_not_served_after_invalidate(self):
        self._write()
        with mock.patch.object(epc.os, "remove", side_effect=PermissionError("in use")) as rm:
            n = epc.invalidate_employee_payload_cache("SLX", None, None)
        self.assertEqual(n, 1)
        self.assertEqual(rm.call_count, epc._DELETE_RETRIES)
        self.assertTrue(any(f.startswith("payload_cache_") for f in os.listdir("data")))  # ไฟล์ยังอยู่
        self.assertIsNone(epc.read_cached_employee_payload("SLX", 11, 2026))  # แต่ไม่ถูกใช้

    def test_transient_lock_retried(self):
        self._write()
        calls = {"n": 0}

        def flaky(p):
            calls["n"] += 1
            if calls["n"] == 1:
                raise PermissionError("in use")
            return _real_remove(p)

        with mock.patch.object(epc.os, "remove", side_effect=flaky):
            self.assertEqual(epc.invalidate_employee_payload_cache("SLX", None, None), 1)
        self.assertFalse(any(f.startswith("payload_cache_") for f in os.listdir("data")))

    def test_new_cache_after_invalidate_is_used(self):
        self._write()
        with mock.patch.object(epc.os, "remove", side_effect=PermissionError("in use")):
            epc.invalidate_employee_payload_cache("SLX", None, None)
        _real_sleep(1.1)  # cached_at ละเอียดระดับวินาที
        epc.write_cached_employee_payload("SLX", 11, 2026, {"employees": [], "skus": []})
        self.assertIsNotNone(epc.read_cached_employee_payload("SLX", 11, 2026))


if __name__ == "__main__":
    unittest.main()
