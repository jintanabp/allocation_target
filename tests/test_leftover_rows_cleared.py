"""
ยอดใน Target Sun "หลังส่ง" ต้องเท่าเป้า ไม่ใช่แค่ไฟล์ที่ส่ง (ผลตรวจ 6 ต.ค. 2026 ก1/ก2)

Target Sun เก็บแถวเก่าที่ไฟล์ไม่ได้ทับไว้เสมอ — เทสนี้จำลองการ upsert (คีย์เต็มรวมคลัง)
แล้วตรวจยอดต่อสินค้าหลังอัปเดต เทียบกับเป้า

  ก1 พนักงานที่มีแถวเป้าเดิมแต่ไม่อยู่ในรอบนี้ (เป้าเงิน 0 เพราะสินค้าไม่มีราคา) → เดิม A=14 จากเป้า 10
  ก2 ส่งรอบสองโดยไม่โหลดขั้นที่ 1 ใหม่ แถวที่รอบแรกสร้างเองค้าง → เดิม B=7 จากเป้า 4

ทำงานในโฟลเดอร์ชั่วคราวทั้งหมด (data/ เป็น path สัมพัทธ์) · ไม่ยิงเน็ต
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.schemas import LakehouseUploadRequest  # noqa: E402
from backend.services import lakehouse as lh  # noqa: E402
from backend.services import sent_ledger  # noqa: E402

logging.disable(logging.CRITICAL)


def _g(emp, sku, qty, wh="WH1"):
    return dict(emp_id=emp, sku=sku, qty=qty, salestype="S1", divisioncode="D1",
                areacode="10", provincecode="P1", warehouse_code=wh)


class _Base(unittest.TestCase):
    SUP = "SLZZ1"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data")

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _setup(self, grain, targets):
        pd.DataFrame(grain).to_csv(f"data/tga_lines_{self.SUP}_2026_11.csv", index=False)
        pd.DataFrame([
            {"sku": s, "supervisor_target_boxes": b, "price_per_box": 1} for s, b in targets.items()
        ]).to_csv(f"data/target_boxes_{self.SUP}_2026_11.csv", index=False)
        self.live = {(r["sku"], r["emp_id"], r["warehouse_code"]): r["qty"] for r in grain}

    def _send(self, allocs, *, record=True, sku_filter=None):
        req = LakehouseUploadRequest(
            sup_id=self.SUP, target_month=11, target_year=2026, upload_user_code="T",
            allow_new_targetsun_rows=True, allocations=allocs, sku_filter=list(sku_filter or []),
        )
        with patch.object(lh, "_enrich_emp_dimensions", side_effect=lambda df, rows_raw, **kw: df):
            out, *_ = lh._build_tga_upload_dataframe(req, drop_incomplete_rows=True, enforce_targets=True)
        for r in out.itertuples():
            self.live[(r.PRODUCTCODE, r.SALESMANCODE, r.WAREHOUSECODE)] = int(r.QUANTITYCASE)
        if record:
            sent_ledger.record_send(self.SUP, 11, 2026, out.astype(str).to_dict("records"), token="t", send_status="ok")
        return out

    def _totals(self):
        tot: dict = {}
        for (sku, _e, _w), q in self.live.items():
            tot[sku] = tot.get(sku, 0) + q
        return tot


class TestLeftoverRowsCleared(_Base):
    def test_employee_not_in_round_is_zeroed(self):
        """ก1: E2 มีแถวเป้าเดิม A=4 แต่รอบนี้ไม่ได้อยู่ในไฟล์ → ต้องส่ง A/E2=0"""
        self._setup([_g("E1", "A", 6), _g("E1", "B", 5), _g("E2", "A", 4)], {"A": 10, "B": 5})
        out = self._send([dict(emp_id="E1", sku="A", allocated_boxes=10),
                          dict(emp_id="E1", sku="B", allocated_boxes=5)])
        self.assertEqual(self._totals(), {"A": 10, "B": 5})
        e2 = out[(out.SALESMANCODE == "E2") & (out.PRODUCTCODE == "A")]
        self.assertEqual(list(e2.QUANTITYCASE), [0])

    def test_row_created_by_previous_send_is_zeroed(self):
        """ก2: รอบแรกสร้าง B/E1 คลังว่าง=3 · รอบสอง E1 ได้ B=0 โดยไม่โหลดขั้นที่ 1 ใหม่"""
        self._setup([_g("E1", "A", 5), _g("E2", "A", 5), _g("E2", "B", 4)], {"A": 10, "B": 4})
        self._send([dict(emp_id="E1", sku="A", allocated_boxes=5), dict(emp_id="E2", sku="A", allocated_boxes=5),
                    dict(emp_id="E1", sku="B", allocated_boxes=3), dict(emp_id="E2", sku="B", allocated_boxes=1)])
        self.assertEqual(self._totals(), {"A": 10, "B": 4})
        self._send([dict(emp_id="E1", sku="A", allocated_boxes=5), dict(emp_id="E2", sku="A", allocated_boxes=5),
                    dict(emp_id="E1", sku="B", allocated_boxes=0), dict(emp_id="E2", sku="B", allocated_boxes=4)])
        self.assertEqual(self._totals(), {"A": 10, "B": 4})

    def test_other_skus_are_untouched_in_a_brand_send(self):
        """ส่งเฉพาะสินค้า A — แถวของ B ที่ไม่อยู่ในรอบนี้ต้องไม่ถูกส่ง 0 ไปทับ"""
        self._setup([_g("E1", "A", 6), _g("E2", "A", 4), _g("E2", "B", 5)], {"A": 10, "B": 5})
        out = self._send([dict(emp_id="E1", sku="A", allocated_boxes=10)], sku_filter=["A"])
        self.assertNotIn("B", set(out.PRODUCTCODE))
        self.assertEqual(self._totals(), {"A": 10, "B": 5})

    def test_shared_employee_is_left_alone(self):
        """พนักงานที่อยู่สองทีมในงวดนี้ — แถวของเขาอาจเป็นของอีกทีม ห้ามล้าง"""
        self._setup([_g("E1", "A", 6), _g("E2", "A", 4)], {"A": 10})
        pd.DataFrame([_g("E2", "A", 4)]).to_csv("data/tga_lines_SLOTHER_2026_11.csv", index=False)
        out = self._send([dict(emp_id="E1", sku="A", allocated_boxes=10)])
        self.assertTrue(out[(out.SALESMANCODE == "E2")].empty)

    def test_file_totals_still_equal_target(self):
        """แถว 0 ที่เพิ่มต้องไม่ทำให้ยอดในไฟล์เปลี่ยน (ด่าน I1 ของไฟล์ยังผ่าน)"""
        self._setup([_g("E1", "A", 6), _g("E2", "A", 4)], {"A": 10})
        out = self._send([dict(emp_id="E1", sku="A", allocated_boxes=10)])
        self.assertEqual(int(out[out.PRODUCTCODE == "A"].QUANTITYCASE.sum()), 10)


if __name__ == "__main__":
    unittest.main()
