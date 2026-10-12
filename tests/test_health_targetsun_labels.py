"""
/health บอกปลายทางส่ง Target Sun เป็นป้าย Prod/UAT (ตรวจก่อนใช้จริง 12 ต.ค. 2026)
หน้าต่างส่งเอาไปบอกผู้ใช้ — ห้ามส่ง URL/ชื่อเครื่องออกไป (/health ไม่ต้องล็อกอิน)
"""

from __future__ import annotations

import unittest
from unittest import mock

from backend.routers import health


class TestTargetSunLabels(unittest.TestCase):
    def _labels(self, summary):
        with mock.patch("backend.services.targetsun_endpoints.targetsun_endpoints_summary", return_value=summary):
            return health._targetsun_labels()

    def test_prod(self):
        got = self._labels({"import_host_label": "Prod", "read_host_label": "Prod", "using_default_settings": "0"})
        self.assertEqual(got, {"send": "Prod", "read": "Prod", "using_default_settings": False})

    def test_default_settings_flagged(self):
        got = self._labels({"import_host_label": "UAT", "read_host_label": "Prod", "using_default_settings": "1"})
        self.assertEqual(got["send"], "UAT")
        self.assertTrue(got["using_default_settings"])

    def test_never_leaks_hostname(self):
        got = self._labels({"import_host_label": "internal.host.local", "read_host_label": "x", "using_default_settings": "0"})
        self.assertEqual((got["send"], got["read"]), ("other", "other"))

    def test_failure_is_unknown(self):
        with mock.patch("backend.services.targetsun_endpoints.targetsun_endpoints_summary", side_effect=RuntimeError("x")):
            self.assertEqual(health._targetsun_labels()["send"], "unknown")


if __name__ == "__main__":
    unittest.main()
