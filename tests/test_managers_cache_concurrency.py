"""
managers_cache.json อยู่บน hot path ของ auth — ทุก request ที่ผ่าน login อ่านไฟล์นี้
(access_control.build_user_access_context → managers.load_full_managers_payload)

เดิมเขียนด้วย open(..., "w") + json.dump ตรง ๆ ไม่มี lock ไม่มี temp+replace
→ คนที่ login พร้อมกันอ่านได้ไฟล์ครึ่งใบ แล้วตกไป rebuild ทุกครั้ง
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import managers as m  # noqa: E402


def _payload(n: int) -> dict:
    return {
        "by_manager": {f"M{i:03d}": [f"SL{i:03d}", f"SL{i + 1:03d}"] for i in range(n)},
        "manager_codes": [f"M{i:03d}" for i in range(n)],
        "supervisors": [f"SL{i:03d}" for i in range(n)],
    }


class TestManagersCacheConcurrency(unittest.TestCase):
    """paths ใน managers.py เป็น relative จึงต้องรันใน cwd ชั่วคราว"""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._cwd = os.getcwd()
        os.chdir(self._tmpdir)
        os.makedirs("data", exist_ok=True)

    def tearDown(self):
        os.chdir(self._cwd)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_persist_is_atomic_roundtrip(self):
        m.persist_managers_payload(_payload(5))
        got = m.load_full_managers_payload()
        self.assertEqual(len(got["manager_codes"]), 5)

    def test_concurrent_login_reads_never_see_torn_cache(self):
        """
        1 writer เขียน cache วน + 4 readers อ่านแบบเดียวกับตอน login
        readers ต้องได้ payload ครบเสมอ และ writer ต้องไม่พัง (Windows: replace ชน reader)
        """
        m.persist_managers_payload(_payload(40))
        stop = threading.Event()
        errors: list[str] = []
        reads = [0]
        writes = [0]
        lock = threading.Lock()

        def writer():
            i = 1
            while not stop.is_set():
                i += 1
                try:
                    m.persist_managers_payload(_payload((i % 60) + 5))
                    with lock:
                        writes[0] += 1
                except Exception as e:
                    with lock:
                        errors.append(f"writer {type(e).__name__}: {e}")

        def reader():
            while not stop.is_set():
                try:
                    data = m.load_full_managers_payload()
                    # by_manager หายหรือว่าง = อ่านโดนไฟล์ครึ่งใบแล้ว fallback ไป rebuild
                    if not isinstance(data, dict) or data.get("by_manager") is None:
                        with lock:
                            errors.append("payload ไม่ครบ (by_manager หาย)")
                    with lock:
                        reads[0] += 1
                except Exception as e:
                    with lock:
                        errors.append(f"reader {type(e).__name__}: {e}")

        threads = [threading.Thread(target=writer, daemon=True)]
        threads += [threading.Thread(target=reader, daemon=True) for _ in range(4)]
        for t in threads:
            t.start()
        time.sleep(1.5)
        stop.set()
        for t in threads:
            t.join(timeout=5)

        self.assertGreater(writes[0], 5, "เขียนน้อยเกินไป — test ไม่ได้ทดสอบอะไร")
        self.assertGreater(reads[0], 20, "อ่านน้อยเกินไป — test ไม่ได้ทดสอบอะไร")
        self.assertEqual(errors[:5], [], f"พัง {len(errors)} ครั้ง")


if __name__ == "__main__":
    unittest.main()
