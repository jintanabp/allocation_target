"""
สัญญาของ PUT /data/allocations ที่ frontend พึ่งอยู่ — รูปร่าง 409/428 ต้องไม่เปลี่ยนมั่ว

frontend อ่าน detail.current.version ไปใช้ตอนกด「เขียนทับ」 ถ้า field นี้หาย ปุ่มนั้นจะพัง
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from fastapi.testclient import TestClient  # noqa: E402

from backend.app_factory import create_app  # noqa: E402
from backend.services import allocation_store as store  # noqa: E402

_AUTH_ENV = "AZURE_AUTH_DISABLED"


def _body(boxes=1, version=None):
    b = {
        "sup_id": "SL330",
        "target_month": 7,
        "target_year": 2026,
        "status": "optimized",
        "allocations": [{"emp_id": "E01", "sku": "S1", "allocated_boxes": boxes}],
        "yellow": {},
        "strategy": "L3M",
    }
    if version is not None:
        b["if_match_version"] = version
    return b


class TestAllocationConflictApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # ปิด auth เฉพาะคลาสนี้ แล้วคืนค่าเดิม — ห้ามตั้งตอน import ไม่งั้นรั่วไปทั้งโปรเซส
        # auth_entra อ่าน env ตอน request จึงตั้งหลังสร้าง app ได้
        cls._auth_prev = os.environ.get(_AUTH_ENV)
        os.environ[_AUTH_ENV] = "1"
        cls.client = TestClient(create_app())

    @classmethod
    def tearDownClass(cls):
        if cls._auth_prev is None:
            os.environ.pop(_AUTH_ENV, None)
        else:
            os.environ[_AUTH_ENV] = cls._auth_prev

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._orig_dir = store.allocations_dir
        store.allocations_dir = lambda: self._tmpdir  # type: ignore[method-assign]
        os.environ.pop("ALLOC_REQUIRE_IF_MATCH", None)

    def tearDown(self):
        store.allocations_dir = self._orig_dir  # type: ignore[method-assign]
        os.environ.pop("ALLOC_REQUIRE_IF_MATCH", None)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_put_returns_version(self):
        r = self.client.put("/data/allocations", json=_body(boxes=1))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["version"], 1)

    def test_old_client_without_version_still_saves(self):
        """เฟส 1 ของ rollout: tab เก่าต้องบันทึกได้เหมือนเดิม"""
        self.client.put("/data/allocations", json=_body(boxes=1))
        r = self.client.put("/data/allocations", json=_body(boxes=2))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["version"], 2)

    def test_stale_version_returns_409_with_current(self):
        self.client.put("/data/allocations", json=_body(boxes=1))  # version 1
        r = self.client.put("/data/allocations", json=_body(boxes=2, version=0))
        self.assertEqual(r.status_code, 409, r.text)
        detail = r.json()["detail"]
        self.assertEqual(detail["code"], "snapshot_conflict")
        # frontend ใช้ค่านี้ยิงซ้ำตอนกด「เขียนทับ」
        self.assertEqual(detail["current"]["version"], 1)
        self.assertIn("updated_at", detail["current"])

    def test_matching_version_succeeds(self):
        self.client.put("/data/allocations", json=_body(boxes=1))
        r = self.client.put("/data/allocations", json=_body(boxes=2, version=1))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["version"], 2)

    def test_overwrite_after_conflict_succeeds(self):
        """จำลองปุ่ม「เขียนทับ」: 409 → ยิงซ้ำด้วย version จาก detail.current"""
        self.client.put("/data/allocations", json=_body(boxes=1))
        r409 = self.client.put("/data/allocations", json=_body(boxes=2, version=0))
        cur = r409.json()["detail"]["current"]["version"]
        r = self.client.put("/data/allocations", json=_body(boxes=2, version=cur))
        self.assertEqual(r.status_code, 200, r.text)

    def test_require_if_match_returns_428_for_old_client(self):
        self.client.put("/data/allocations", json=_body(boxes=1))
        os.environ["ALLOC_REQUIRE_IF_MATCH"] = "1"
        r = self.client.put("/data/allocations", json=_body(boxes=2))
        self.assertEqual(r.status_code, 428, r.text)
        self.assertEqual(r.json()["detail"]["code"], "precondition_required")


if __name__ == "__main__":
    unittest.main()
