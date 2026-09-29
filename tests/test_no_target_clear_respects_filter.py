"""
ล้างเป้าค้างของคน「ไม่ต้องตั้งเป้า」ต้องเคารพตัวกรองแบรนด์/สินค้า (ผลตรวจ 28 ก.ย. 2026 §2.1)

เดิม: ส่งแบรนด์เดียวแล้วระบบเขียนแถว 0 ให้ SKU แบรนด์อื่นของคนนั้นด้วย
→ ด่านยอดรวมทั้งชุดตอบ 409 และตัวตรวจหลังส่งฟ้อง "ยอดลงจริงไม่ตรงไฟล์" ทั้งที่ข้อมูลถูก
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import lakehouse as lh  # noqa: E402
from backend.services import no_target_store  # noqa: E402

GRAIN = pd.DataFrame([
    {"emp_id": "N1", "sku": "A", "salestype": "S", "divisioncode": "B", "areacode": "1",
     "provincecode": "", "warehouse_code": ""},
    {"emp_id": "N1", "sku": "Z", "salestype": "S", "divisioncode": "B", "areacode": "1",
     "provincecode": "", "warehouse_code": ""},
])
PAYLOAD = pd.DataFrame([{"emp_id": "E1", "sku": "A", "allocated_boxes": 5}])


class TestNoTargetClearScope(unittest.TestCase):
    def setUp(self):
        self._p = patch.object(no_target_store, "no_target_emp_ids", return_value={"N1"})
        self._p.start()

    def tearDown(self):
        self._p.stop()

    def test_full_send_clears_every_sku(self):
        out, cleared = lh._clear_no_target_employees_in_tga(PAYLOAD, "SLA", dg=GRAIN)
        self.assertEqual(sorted(out[out["emp_id"] == "N1"]["sku"]), ["A", "Z"])
        self.assertEqual(cleared, ["N1"])

    def test_brand_send_only_clears_skus_in_this_send(self):
        out, _ = lh._clear_no_target_employees_in_tga(PAYLOAD, "SLA", dg=GRAIN, only_skus={"A"})
        self.assertEqual(list(out[out["emp_id"] == "N1"]["sku"]), ["A"],
                         "SKU Z อยู่แบรนด์อื่นที่ไม่ได้ส่งรอบนี้ ต้องไม่ถูกเขียนแถว 0")

    def test_send_path_passes_the_scope(self):
        import inspect

        src = inspect.getsource(lh._build_tga_upload_dataframe)
        self.assertIn("only_skus=None if _full_send else", src)


if __name__ == "__main__":
    unittest.main()
