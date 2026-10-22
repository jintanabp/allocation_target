"""
/admin/health/deep ต้องไม่บอกว่า Target Sun「เชื่อมได้」เมื่ออ่านไม่ได้จริง (QA 21 ต.ค. 2026 M8)

fetch_targetsun_periods_overview เก็บ error ของแต่ละ division × หน่วยไว้ในรายการ ไม่ raise —
เดิม endpoint ดูแค่ว่าได้ list กลับมา ต่อไม่ได้หรือหมดเวลาก็ขึ้น ok: true ทำให้ IT ตรวจตอน
ย้ายเครื่องแล้วได้ผลบวกปลอม
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.routers import admin as admin_router  # noqa: E402
from backend.services import targetsun_read  # noqa: E402


def _entry(err=None):
    return {"division_code": "B", "sales_type": "S", "error": err,
            "target_year": None if err else 2026, "target_month": None if err else 11}


class _NoFabric:
    def __init__(self, *a, **k):
        raise RuntimeError("fabric down (test)")


class TestDeepHealthTargetSun(unittest.TestCase):
    def _run(self, periods):
        with patch.object(admin_router, "FabricDAXConnector", _NoFabric), \
                patch.object(targetsun_read, "is_enabled", return_value=True), \
                patch.object(targetsun_read, "fetch_targetsun_periods_overview", return_value=periods):
            return admin_router.admin_deep_health(_admin={}, target_month=11, target_year=2026)["targetsun_read"]

    def test_every_combo_failed_is_not_ok(self):
        ts = self._run([_entry("Connection refused"), _entry("Connection refused")])
        self.assertFalse(ts["ok"])
        self.assertIn("0/2", ts["detail"])
        self.assertIn("Connection refused", ts["detail"])

    def test_partial_failure_is_not_ok(self):
        ts = self._run([_entry(), _entry("Read timed out")])
        self.assertFalse(ts["ok"])
        self.assertIn("1/2", ts["detail"])

    def test_all_combos_read_is_ok(self):
        ts = self._run([_entry(), _entry()])
        self.assertTrue(ts["ok"])
        self.assertIn("2/2", ts["detail"])

    def test_empty_list_is_not_ok(self):
        self.assertFalse(self._run([])["ok"])


if __name__ == "__main__":
    unittest.main()
