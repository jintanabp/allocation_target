"""
ประวัติขายที่เคยดึงสำเร็จแล้ว ต้องไม่หายเพราะ Fabric ล่มรอบถัดไป

เจอของจริง: เป้ามาจาก Target Sun แต่ "ยอดขายเฉลี่ย 3 เดือน / ปีที่แล้ว" มาจาก
Fabric สองเส้นทางนี้พังแยกกันได้ · ตอน Fabric ล่ม ของเดิมจะได้ตารางว่างแล้วเดินต่อ
เงียบ ๆ ทุกช่องบนตารางขั้นที่ 1 กลายเป็น 0 พร้อมกันทั้งทีม ทั้งที่ไฟล์ที่เคยดึง
สำเร็จยังนอนอยู่ในโฟลเดอร์ data/ ครบถ้วน และยอดขายของเดือนที่ปิดแล้วก็เป็นค่าคงที่
ใช้ซ้ำได้อยู่แล้ว
"""

import os
import shutil
import tempfile
import unittest

import pandas as pd

from backend.services.employees import _load_history

ROWS = [
    {"emp_id": "C413", "sku": "111294", "hist_boxes": 12.0, "hist_amount": 12600.0},
    {"emp_id": "C420", "sku": "111302", "hist_boxes": 8.0, "hist_amount": 8400.0},
]


class TestHistoryCacheFallback(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="histfb_")
        self.path = os.path.join(self._tmpdir, "hist_cache_SL397_2026_09.csv")

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _seed_cache(self):
        pd.DataFrame(ROWS).to_csv(self.path, index=False)

    def test_successful_fetch_is_stored(self):
        out = _load_history(
            "3 เดือน", self.path, lambda: pd.DataFrame(ROWS), [], "SL397"
        )
        self.assertEqual(len(out), 2)
        self.assertTrue(os.path.exists(self.path))
        self.assertEqual(len(pd.read_csv(self.path)), 2)

    def test_fetch_error_falls_back_to_stored_file(self):
        self._seed_cache()

        def boom():
            raise RuntimeError("Fabric ล่ม")

        out = _load_history("3 เดือน", self.path, boom, [], "SL397")
        self.assertEqual(len(out), 2)
        self.assertEqual(
            sorted(out["emp_id"].astype(str)), ["C413", "C420"]
        )

    def test_empty_fetch_falls_back_to_stored_file(self):
        """ดึงได้แต่ว่างเปล่า (เช่นกรองงวดไม่เจอ) ก็ต้องไม่ทิ้งของเดิม"""
        self._seed_cache()
        out = _load_history("3 เดือน", self.path, lambda: pd.DataFrame(), [], "SL397")
        self.assertEqual(len(out), 2)

    def test_failed_fetch_does_not_overwrite_stored_file(self):
        self._seed_cache()

        def boom():
            raise RuntimeError("Fabric ล่ม")

        _load_history("3 เดือน", self.path, boom, [], "SL397")
        self.assertEqual(len(pd.read_csv(self.path)), 2)

    def test_no_cache_and_failed_fetch_returns_empty_with_columns(self):
        """ไม่มีทั้งของใหม่และของเก่า — ต้องคืนตารางว่างที่มีคอลัมน์ครบ ไม่ใช่ None"""

        def boom():
            raise RuntimeError("Fabric ล่ม")

        out = _load_history("3 เดือน", self.path, boom, [], "SL397")
        self.assertTrue(out.empty)
        for col in ("emp_id", "sku", "hist_boxes", "hist_amount"):
            self.assertIn(col, out.columns)

    def test_corrupt_cache_does_not_raise(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("ไฟล์นี้ไม่ใช่ csv ที่อ่านได้\x00\x00")

        def boom():
            raise RuntimeError("Fabric ล่ม")

        out = _load_history("3 เดือน", self.path, boom, [], "SL397")
        self.assertTrue(out.empty)


if __name__ == "__main__":
    unittest.main()
