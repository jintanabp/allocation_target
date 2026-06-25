"""Tests for employee payload JSON cache."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.core.paths import employee_payload_cache_path  # noqa: E402
from backend.services import employee_payload_cache as cache  # noqa: E402


class TestEmployeePayloadCache(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_cwd = os.getcwd()
        os.chdir(self._tmpdir.name)
        os.makedirs("data", exist_ok=True)

    def tearDown(self):
        os.chdir(self._orig_cwd)
        self._tmpdir.cleanup()

    def test_write_and_read_hit(self):
        with mock.patch.object(cache, "employee_payload_cache_ttl_sec", return_value=900):
            payload = {
                "employees": [{"emp_id": "E1"}],
                "skus": [{"sku": "S1"}],
                "sku_warnings": [],
            }
            cache.write_cached_employee_payload("SL350", 7, 2026, payload)
            hit = cache.read_cached_employee_payload("SL350", 7, 2026)
            self.assertIsNotNone(hit)
            assert hit is not None
            self.assertTrue(hit["data_from_cache"])
            self.assertIsNotNone(hit["data_cached_at"])
            self.assertEqual(hit["employees"][0]["emp_id"], "E1")

    def test_expired_cache_miss(self):
        with mock.patch.object(cache, "employee_payload_cache_ttl_sec", return_value=60):
            path = employee_payload_cache_path("SL350", 7, 2026)
            old = (datetime.now(timezone.utc) - timedelta(seconds=120)).replace(microsecond=0)
            doc = {
                "cached_at": old.isoformat().replace("+00:00", "Z"),
                "sup_id": "SL350",
                "target_month": 7,
                "target_year": 2026,
                "payload": {"employees": [], "skus": []},
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(doc, f)
            self.assertIsNone(cache.read_cached_employee_payload("SL350", 7, 2026))

    def test_ttl_zero_disables_cache(self):
        with mock.patch.object(cache, "employee_payload_cache_ttl_sec", return_value=0):
            cache.write_cached_employee_payload("SL350", 7, 2026, {"employees": [], "skus": []})
            path = employee_payload_cache_path("SL350", 7, 2026)
            self.assertFalse(os.path.isfile(path))
            self.assertIsNone(cache.read_cached_employee_payload("SL350", 7, 2026))

    def test_invalidate_by_sup_and_period(self):
        with mock.patch.object(cache, "employee_payload_cache_ttl_sec", return_value=900):
            cache.write_cached_employee_payload("SL350", 7, 2026, {"employees": [], "skus": []})
            cache.write_cached_employee_payload("SL351", 7, 2026, {"employees": [], "skus": []})
            n = cache.invalidate_employee_payload_cache("SL350", 7, 2026)
            self.assertEqual(n, 1)
            self.assertIsNone(cache.read_cached_employee_payload("SL350", 7, 2026))
            self.assertIsNotNone(cache.read_cached_employee_payload("SL351", 7, 2026))


if __name__ == "__main__":
    unittest.main()
