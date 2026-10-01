"""
กันชนเทสต์ (กลุ่ม 3 จากออดิต 26 ส.ค. 2026 — แก้ 1 ต.ค. 2026)

1. _netguard เป็น allowlist: ยอมแค่ loopback ทั้งชั้น requests และชั้น socket (urllib/httpx/socket ดิบ)
2. run_tests.py คุ้มทุกไฟล์ใน data/cache/ — แก้ = กู้คืน · สร้างใหม่ = ลบ · ลบทิ้ง = คืนให้

ทุกเคสที่ "ต้องถูกบล็อก" ใช้ IP ตรง (192.0.2.x = TEST-NET ตาม RFC 5737) — ไม่มีการถาม DNS
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

import run_tests  # noqa: E402
from tests import _netguard  # noqa: E402

_netguard.install()

OUTSIDE_IP = "192.0.2.10"


class TestAllowedHosts(unittest.TestCase):
    def test_loopback_and_local_names_allowed(self):
        for h in ("localhost", "127.0.0.1", "127.8.9.1", "::1", "[::1]", "testserver", "app.localhost"):
            with self.subTest(host=h):
                self.assertTrue(_netguard.is_allowed_host(h))

    def test_everything_else_blocked(self):
        for h in ("sahapat.com", "uat.example.com", "api.powerbi.com", OUTSIDE_IP, "10.0.0.5", "", None,
                  "localhost.evil.com"):
            with self.subTest(host=h):
                self.assertFalse(_netguard.is_allowed_host(h))


class TestBlockedAtEveryLayer(unittest.TestCase):
    def test_requests_to_unknown_host_blocked(self):
        import requests

        with self.assertRaisesRegex(RuntimeError, "ออกนอกเครื่อง"):
            requests.get(f"http://{OUTSIDE_IP}/api", timeout=1)

    def test_urllib_blocked(self):
        with self.assertRaisesRegex(Exception, "ออกนอกเครื่อง"):
            urllib.request.urlopen(f"http://{OUTSIDE_IP}/", timeout=1)

    def test_raw_socket_blocked(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with self.assertRaisesRegex(RuntimeError, "ออกนอกเครื่อง"):
                s.connect((OUTSIDE_IP, 443))
            with self.assertRaisesRegex(RuntimeError, "ออกนอกเครื่อง"):
                s.connect_ex((OUTSIDE_IP, 443))
        finally:
            s.close()

    def test_loopback_socket_still_works(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        t = threading.Thread(target=lambda: srv.accept()[0].close(), daemon=True)
        t.start()
        c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            c.connect(("127.0.0.1", port))
        finally:
            c.close()
            t.join(2)
            srv.close()

    def test_install_twice_does_not_double_wrap(self):
        before = socket.socket.connect
        _netguard.install()
        self.assertIs(socket.socket.connect, before)


class TestCacheDirProtected(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cache = os.path.join(self._tmp.name, "data", "cache")
        os.makedirs(self.cache)
        self._p = [patch.object(run_tests, "repo_root", return_value=self._tmp.name),
                   patch.object(run_tests, "_PROTECTED_CONFIGS", ())]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()
        self._tmp.cleanup()

    def _write(self, name, data: bytes):
        with open(os.path.join(self.cache, name), "wb") as f:
            f.write(data)

    def _read(self, name):
        with open(os.path.join(self.cache, name), "rb") as f:
            return f.read()

    def test_modified_new_and_deleted_files_restored(self):
        self._write("price_per_box_2026_10.json", b"real")
        self._write("dim_product_2026_10.json", b"real2")
        before = run_tests._snapshot_protected()

        self._write("price_per_box_2026_10.json", b"fake")            # เทสต์เขียนทับ
        self._write("price_per_box_2099_01.json", b"made by test")    # เทสต์สร้างใหม่
        os.remove(os.path.join(self.cache, "dim_product_2026_10.json"))  # เทสต์ลบ

        dirty = run_tests._restore_protected(before)
        self.assertEqual(sorted(dirty), ["data/cache/dim_product_2026_10.json",
                                         "data/cache/price_per_box_2026_10.json",
                                         "data/cache/price_per_box_2099_01.json"])
        self.assertEqual(self._read("price_per_box_2026_10.json"), b"real")
        self.assertEqual(self._read("dim_product_2026_10.json"), b"real2")
        self.assertFalse(os.path.exists(os.path.join(self.cache, "price_per_box_2099_01.json")))

    def test_untouched_cache_is_clean(self):
        self._write("price_per_box_2026_10.json", b"real")
        before = run_tests._snapshot_protected()
        self.assertEqual(run_tests._restore_protected(before), [])


if __name__ == "__main__":
    unittest.main()
