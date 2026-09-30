"""
§4.1-5 _fill_missing_peer_hist ต้องไม่สร้างไฟล์ประวัติครึ่ง ๆ กลาง ๆ และไม่ทำประวัติคนอื่นหาย

เดิม:
  - ทีมที่ยังไม่มีไฟล์ประวัติ → สร้างไฟล์ที่มีแค่ SKU นอกเป้าของทีม ค้างถาวร
    (SKU ของทีมเองดูเหมือนไม่เคยขาย และกติกาไม่เคยขายนับทีมนี้ว่า "มีข้อมูล")
  - เขียนทับด้วยชุดที่กรองเหลือเฉพาะพนักงานที่มีเป้า → ประวัติของคนอื่นในไฟล์หาย
  - to_csv ตรง ๆ ไม่ atomic
ออฟไลน์ล้วน — Fabric เป็นของปลอม
"""

from __future__ import annotations

import inspect
import logging
import os
import sys
import tempfile
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.core.paths import hist_cache_path, target_boxes_cache_path  # noqa: E402
from backend.services import optimize as opt  # noqa: E402

logging.disable(logging.CRITICAL)


class _FakeFabric:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def get_historical_sales(self, month, year, sku_list, emp_list, n_months):
        self.calls.append((tuple(sku_list), tuple(emp_list), n_months))
        return pd.DataFrame([r for r in self.rows if r["sku"] in sku_list and r["emp_id"] in emp_list])


class TestFillPeerHist(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data", exist_ok=True)
        # ทีม SLB มีเป้าเงินเองแค่ SKU "OWN"
        pd.DataFrame([{"sku": "OWN", "supervisor_target_boxes": 5, "price_per_box": 1.0}]).to_csv(
            target_boxes_cache_path("SLB", 10, 2026), index=False
        )
        self.path = hist_cache_path("SLB", 10, 2026, n_months=12)
        self.fabric = _FakeFabric([{"emp_id": "B1", "sku": "GAP", "hist_boxes": 7}])

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _fill(self):
        opt._fill_missing_peer_hist(
            self.fabric, ["SLA", "SLB"], {"SLB": ["B1"]}, {"OWN", "GAP"},
            10, 2026, n_months=12, sku_links=[],
        )

    def test_missing_file_is_not_created(self):
        self._fill()
        self.assertFalse(os.path.exists(self.path), "ไม่มีไฟล์ = ยังไม่โหลดขั้นที่ 1 ห้ามสร้างไฟล์ครึ่ง ๆ")
        self.assertEqual(self.fabric.calls, [])

    def test_other_peoples_history_survives(self):
        pd.DataFrame([
            {"emp_id": "B1", "sku": "OWN", "hist_boxes": 3},
            {"emp_id": "X9", "sku": "OWN", "hist_boxes": 4},   # คนที่ไม่อยู่ในรายชื่อที่มีเป้ารอบนี้
        ]).to_csv(self.path, index=False)
        self._fill()
        df = pd.read_csv(self.path, dtype={"sku": str, "emp_id": str})
        got = {(r.emp_id, r.sku): r.hist_boxes for r in df.itertuples()}
        self.assertEqual(got, {("B1", "OWN"): 3, ("X9", "OWN"): 4, ("B1", "GAP"): 7})

    def test_no_temp_files_left(self):
        pd.DataFrame([{"emp_id": "B1", "sku": "OWN", "hist_boxes": 3}]).to_csv(self.path, index=False)
        self._fill()
        self.assertEqual([f for f in os.listdir("data") if f.endswith(".tmp")], [])

    def test_writes_atomically(self):
        src = inspect.getsource(opt._fill_missing_peer_hist)
        self.assertIn("atomic_write_csv(cache_path", src)
        self.assertNotIn("combined.to_csv(", src)


if __name__ == "__main__":
    unittest.main()
