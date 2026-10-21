"""
คลังว่างเป็นแถวแยกของคนแยกคลัง (ผู้ใช้ขอ 21 ต.ค. 2026)

คนที่ Target Sun มีทั้งแถวคลังว่างและแถวรหัสคลังจริง (เช่น ว่าง + G010) — Step 2/3 แยกเป็นสองแถว
「G010」กับ「(ว่าง)」 คนจะได้ไม่เข้าใจว่าทุกสินค้าลง G010 · ชื่อแถว「(ว่าง)」ใช้แค่บนจอ/ในการคำนวณ
ตอนสร้างไฟล์ส่งต้องกลับเป็นคลังว่างจริง ห้ามหลุดถึง Target Sun · ทั้งหมดออฟไลน์ ไม่ส่งจริง
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
from backend.services import optimize as opt  # noqa: E402
from backend.services import warehouse_pin_rules_store as store  # noqa: E402
from backend.services.wh_split import BLANK_WH_TOKEN  # noqa: E402

logging.disable(logging.CRITICAL)

SUP = "SLBLANKROW"
PIN = {"section": "702", "skus": ["P"], "areacode": "5", "divisioncode": "B",
       "salestype": "S", "warehouse_code": "G010"}


def _g(sku, qty, wh, emp="S554"):
    return {"emp_id": emp, "sku": sku, "qty": qty, "salestype": "S", "divisioncode": "B",
            "areacode": "5", "provincecode": "", "warehouse_code": wh}


class BlankWarehouseRowTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data", exist_ok=True)
        self._old_env = os.environ.get("WAREHOUSE_PIN_RULES_PATH")
        os.environ["WAREHOUSE_PIN_RULES_PATH"] = os.path.join(self._tmp.name, "rules.json")
        self._p = patch.object(lh, "_enrich_emp_dimensions", side_effect=lambda df, rows_raw, **kw: df)
        self._p.start()
        pd.DataFrame([_g("A", 10, "G010"), _g("B", 5, ""), _g("P", 4, "")]).to_csv(
            f"data/tga_lines_{SUP}_2026_11.csv", index=False)
        pd.DataFrame([
            {"sku": s, "section": "702", "supervisor_target_boxes": b, "price_per_box": 1.0}
            for s, b in (("A", 10), ("B", 5), ("P", 4))
        ]).to_csv(f"data/target_boxes_{SUP}_2026_11.csv", index=False)

    def tearDown(self):
        self._p.stop()
        os.chdir(self._cwd)
        if self._old_env is None:
            os.environ.pop("WAREHOUSE_PIN_RULES_PATH", None)
        else:
            os.environ["WAREHOUSE_PIN_RULES_PATH"] = self._old_env
        self._tmp.cleanup()

    def _send(self, allocs):
        req = LakehouseUploadRequest(sup_id=SUP, target_month=11, target_year=2026,
                                     upload_user_code="T", allocations=allocs)
        out, *_ = lh._build_tga_upload_dataframe(req, drop_incomplete_rows=True)
        return {(r.PRODUCTCODE, r.WAREHOUSECODE): int(r.QUANTITYCASE) for r in out.itertuples()}

    def test_send_file_turns_blank_row_back_into_real_blank(self):
        got = self._send([
            {"emp_id": "S554", "sku": "A", "allocated_boxes": 10, "warehouse_code": "G010"},
            {"emp_id": "S554", "sku": "B", "allocated_boxes": 5, "warehouse_code": BLANK_WH_TOKEN},
            {"emp_id": "S554", "sku": "P", "allocated_boxes": 4, "warehouse_code": BLANK_WH_TOKEN},
        ])
        self.assertEqual(got, {("A", "G010"): 10, ("B", ""): 5, ("P", ""): 4})
        self.assertNotIn(BLANK_WH_TOKEN, {w for _s, w in got})

    def test_pin_rule_moves_blank_row_sku_to_g010_and_zeros_blank(self):
        store.write_rules([PIN])
        got = self._send([
            {"emp_id": "S554", "sku": "A", "allocated_boxes": 10, "warehouse_code": "G010"},
            {"emp_id": "S554", "sku": "B", "allocated_boxes": 5, "warehouse_code": BLANK_WH_TOKEN},
            {"emp_id": "S554", "sku": "P", "allocated_boxes": 4, "warehouse_code": BLANK_WH_TOKEN},
            {"emp_id": "S554", "sku": "P", "allocated_boxes": 0, "warehouse_code": "G010"},
        ])
        self.assertEqual(got, {("A", "G010"): 10, ("B", ""): 5, ("P", "G010"): 4, ("P", ""): 0})

    def test_step3_keeps_two_rows_and_moves_pinned_sku_into_the_g010_row(self):
        store.write_rules([PIN])
        df = pd.DataFrame([
            {"emp_id": "S554", "sku": "A", "allocated_boxes": 10, "warehouse_code": "G010"},
            {"emp_id": "S554", "sku": "A", "allocated_boxes": 0, "warehouse_code": BLANK_WH_TOKEN},
            {"emp_id": "S554", "sku": "B", "allocated_boxes": 0, "warehouse_code": "G010"},
            {"emp_id": "S554", "sku": "B", "allocated_boxes": 5, "warehouse_code": BLANK_WH_TOKEN},
            {"emp_id": "S554", "sku": "P", "allocated_boxes": 0, "warehouse_code": "G010"},
            {"emp_id": "S554", "sku": "P", "allocated_boxes": 4, "warehouse_code": BLANK_WH_TOKEN},
        ])
        out = opt._apply_wh_pin_preview(df, SUP, 11, 2026)
        self.assertEqual(set(out["warehouse_code"]), {"G010", BLANK_WH_TOKEN})   # สองแถว ไม่มีแถวที่สาม
        live = {(r.sku, r.warehouse_code): int(r.allocated_boxes) for r in out.itertuples() if r.allocated_boxes}
        self.assertEqual(live, {("A", "G010"): 10, ("B", BLANK_WH_TOKEN): 5, ("P", "G010"): 4})
        self.assertEqual(int(out["allocated_boxes"].sum()), 19)


if __name__ == "__main__":
    unittest.main()
