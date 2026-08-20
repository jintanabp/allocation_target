"""
กระจายเฉพาะบางสินค้า (only_skus) — ปุ่ม "กระจายเฉพาะสินค้าที่เป้าเพิ่ม/เปลี่ยน"

ฝั่งเว็บ merge ผลกลับเข้าตารางเดิมเอง — server แค่จำกัดจักรวาล SKU ก่อนคำนวณ
ประตู I1 (validate_allocation_vs_targets) ยังบังคับเต็มบนเซ็ตที่เลือก
"""

from __future__ import annotations

import inspect
import logging
import os
import sys
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.schemas import OptimizeRequest  # noqa: E402
from backend.services import optimize as opt  # noqa: E402

logging.disable(logging.CRITICAL)


class TestOnlySkusSchema(unittest.TestCase):
    def test_default_is_empty_meaning_all_skus(self):
        req = OptimizeRequest(yellowTargets=[{"emp_id": "E1", "yellow_target": 1.0}])
        self.assertEqual(req.only_skus, [])


class TestOnlySkusServiceWiring(unittest.TestCase):
    def test_service_filters_sku_universe_before_allocating(self):
        src = inspect.getsource(opt.run_optimization_service)
        self.assertIn("req.only_skus", src)
        # ต้องกรองที่ df_sku (จักรวาล SKU) ไม่ใช่ไปตัดผลลัพธ์ทีหลัง
        self.assertIn('df_sku[df_sku["sku"].isin(set(only_skus))]', src)

    def test_unknown_only_skus_is_a_clear_400(self):
        src = inspect.getsource(opt.run_optimization_service)
        marker = 'df_sku[df_sku["sku"].isin(set(only_skus))]'
        after = src.split(marker, 1)[1]
        self.assertIn("HTTPException", after.split("logger.info", 1)[0])

    def test_filter_runs_after_duplicate_sku_collapse(self):
        """ยุบ SKU ซ้ำ (I6) ต้องมาก่อน — ไม่งั้นตัวกรองอาจเหลือแถวซ้ำของรหัสเดียวกัน"""
        src = inspect.getsource(opt.run_optimization_service)
        self.assertLess(
            src.index('drop_duplicates(subset=["sku"]'),
            src.index("req.only_skus"),
        )


if __name__ == "__main__":
    unittest.main()
