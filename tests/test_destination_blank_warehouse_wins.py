"""
คลังว่างจริงของปลายทาง (Target Sun) ต้องไม่ถูกทับด้วยค่าที่เดามาจากประวัติขาย/payload

ของจริงที่ทำให้ต้องแก้ (21 ก.ย. 2026): SL380/SL530/SL525 ไม่เคยแยกเป้าตามคลังเลย —
ทุกแถวที่ปลายทางมี WAREHOUSECODE ว่างหมด แต่ค่าที่ปนมากับ request (`wh_req`/`wh_hint`,
เดามาจากประวัติขาย 2 ปีของพนักงานตั้งแต่ขั้นที่ 1 — `get_warehouse_by_emp()`) ดันไม่ว่าง
โค้ดเดิมใช้ pattern `_cell_str(r.get("warehouse_code","")) or wh_req or ""` ซึ่งมองไม่เห็น
ความต่างระหว่าง "ปลายทางไม่รู้" กับ "ปลายทางรู้แน่ว่าว่าง" ผลคือคลังว่างจริงถูกทับด้วย
ค่าเดา (เช่น "G002") — ตั้งแต่ WAREHOUSECODE เข้าคีย์ upsert ของ Target Sun (7 ก.ย. 2026)
การทับนี้ทำให้ปลายทางมองว่าเป็นคนละแถว แล้ว insert แถวใหม่ซ้อนแถวเดิม (คู่เดียวกันมีเป้า
สองก้อน) เป้าที่อ่านกลับมาจึงเห็นเป็นเพิ่มขึ้น 2-4 เท่า

ต่างจาก tests/test_new_row_warehouse.py ซึ่งครอบเฉพาะกิ่ง "คู่ใหม่ที่ไม่เคยมีในปลายทางเลย"
(sub.empty) — ไฟล์นี้ครอบกิ่ง "คู่ที่ปลายทางมีอยู่แล้ว แต่คลังของปลายทางว่างจริง" ซึ่งไม่เคย
มีเทสมาก่อน
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

logging.disable(logging.CRITICAL)

SUP = "SLWHBLANK"


def _grain_row(emp, sku, qty, wh="", st="S1", div="D1", area="10", province="P1"):
    return {
        "emp_id": emp,
        "sku": sku,
        "qty": qty,
        "salestype": st,
        "divisioncode": div,
        "areacode": area,
        "provincecode": province,
        "warehouse_code": wh,
    }


def _dg(rows):
    return lh._normalize_grain_dtype(pd.DataFrame(rows))


class MatchedPairKeepsDestinationBlankTest(unittest.TestCase):
    """_expand_allocations_with_tga_grain — สามกิ่งที่ "คู่นี้มีอยู่แล้วในปลายทาง" """

    def test_qty_positive_pair_stays_blank(self):
        df_alloc = pd.DataFrame(
            [{"emp_id": "E1", "sku": "SKU1", "allocated_boxes": 5, "warehouse_code": "G002"}]
        )
        out, _ = lh._expand_allocations_with_tga_grain(
            df_alloc, SUP, 10, 2026, dg=_dg([_grain_row("E1", "SKU1", qty=5, wh="")])
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out.iloc[0]["warehouse_code"], "")

    def test_dims_only_zero_row_stays_blank_while_positive_row_is_untouched(self):
        """แถวหีบ>0 คลัง R082 อยู่คนละแถวกับแถวหีบ=0 คลังว่าง — ต้องไม่ปนกัน"""
        df_alloc = pd.DataFrame(
            [{"emp_id": "E1", "sku": "SKU1", "allocated_boxes": 5, "warehouse_code": "G002"}]
        )
        out, _ = lh._expand_allocations_with_tga_grain(
            df_alloc,
            SUP,
            10,
            2026,
            dg=_dg(
                [
                    _grain_row("E1", "SKU1", qty=5, wh="R082"),
                    _grain_row("E1", "SKU1", qty=0, wh=""),
                ]
            ),
        )
        self.assertEqual(len(out), 2)
        pos = out[out["allocated_boxes"] > 0].iloc[0]
        zero = out[out["allocated_boxes"] == 0].iloc[0]
        self.assertEqual(pos["warehouse_code"], "R082")
        self.assertEqual(zero["warehouse_code"], "")

    def test_all_zero_target_in_tga_stays_blank(self):
        df_alloc = pd.DataFrame(
            [{"emp_id": "E1", "sku": "SKU1", "allocated_boxes": 0, "warehouse_code": "G002"}]
        )
        out, _ = lh._expand_allocations_with_tga_grain(
            df_alloc, SUP, 10, 2026, dg=_dg([_grain_row("E1", "SKU1", qty=0, wh="")])
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out.iloc[0]["warehouse_code"], "")


class AlignZeroAllocationsKeepsDestinationBlankTest(unittest.TestCase):
    def test_zero_sum_pair_stays_blank(self):
        df = pd.DataFrame(
            [
                {
                    "emp_id": "E1",
                    "sku": "SKU1",
                    "allocated_boxes": 0,
                    "salestype": "S1",
                    "divisioncode": "D1",
                    "areacode": "10",
                    "provincecode": "P1",
                    "warehouse_code": "G002",
                }
            ]
        )
        dg = _dg([_grain_row("E1", "SKU1", qty=0, wh="")])
        out = lh._align_zero_allocations_to_tga_grain(df, SUP, 10, 2026, dg=dg)
        self.assertEqual(len(out), 1)
        self.assertEqual(out.iloc[0]["warehouse_code"], "")


class ApplyWhHintsTrustExistingTest(unittest.TestCase):
    def test_trust_existing_ignores_the_hint(self):
        df = pd.DataFrame([{"emp_id": "E1", "warehouse_code": ""}])
        rows_raw = [{"emp_id": "E1", "warehouse_code": "G002"}]
        out = lh._apply_wh_hints(df, rows_raw, trust_existing=True)
        self.assertEqual(out.iloc[0]["warehouse_code"], "")

    def test_default_still_fills_from_the_hint(self):
        """กิ่ง grain ว่างทั้งทีมจริง ๆ ยังต้องพึ่ง hint เหมือนเดิม — ห้ามเปลี่ยนพฤติกรรมนี้"""
        df = pd.DataFrame([{"emp_id": "E1", "warehouse_code": ""}])
        rows_raw = [{"emp_id": "E1", "warehouse_code": "G002"}]
        out = lh._apply_wh_hints(df, rows_raw, trust_existing=False)
        self.assertEqual(out.iloc[0]["warehouse_code"], "G002")


class _FakeFabricWarehouseGuess:
    """จำลอง get_warehouse_by_emp คืนคลังที่เดาจากประวัติขาย — ไม่ต่อเน็ตจริง"""

    def get_tga_lakehouse_dims_by_emp_sku(self, emp_list, sku_list):
        return pd.DataFrame()

    def get_tga_lakehouse_dims_by_emp(self, emp_list):
        return pd.DataFrame()

    def get_warehouse_by_emp(self, emp_list):
        return pd.DataFrame([{"emp_id": e, "warehouse_code": "G002"} for e in emp_list])


class EnrichEmpDimensionsSkipGateTest(unittest.TestCase):
    """skip_emp_sku_dim_merge=True ต้องกันไม่ให้ get_warehouse_by_emp ทับคลังว่างจริง"""

    def _df(self):
        return pd.DataFrame(
            [
                {
                    "emp_id": "E1",
                    "sku": "SKU1",
                    "allocated_boxes": 5,
                    "salestype": "S1",
                    "divisioncode": "",  # ขาด — บังคับให้ _needs_fabric_enrichment เป็นจริง
                    "areacode": "10",
                    "provincecode": "P1",
                    "warehouse_code": "",  # resolve จาก grain มาแล้วว่าง (ของจริง)
                }
            ]
        )

    def test_skip_true_keeps_the_destinations_blank_warehouse(self):
        with patch.object(lh, "FabricDAXConnector", return_value=_FakeFabricWarehouseGuess()):
            out = lh._enrich_emp_dimensions(
                self._df(), [{"emp_id": "E1", "warehouse_code": "G002"}],
                skip_emp_sku_dim_merge=True,
            )
        self.assertEqual(out.iloc[0]["warehouse_code"], "")

    def test_skip_false_still_uses_the_historical_guess(self):
        """กิ่ง grain ไม่พอทั้งทีมจริง ๆ (skip=False) ยังต้องพึ่งค่าเดาเหมือนเดิม"""
        with patch.object(lh, "FabricDAXConnector", return_value=_FakeFabricWarehouseGuess()):
            out = lh._enrich_emp_dimensions(
                self._df(), [{"emp_id": "E1", "warehouse_code": "G002"}],
                skip_emp_sku_dim_merge=False,
            )
        self.assertEqual(out.iloc[0]["warehouse_code"], "G002")


class FullPipelineReproducesTheSl380IncidentTest(unittest.TestCase):
    """
    จำลองเหตุการณ์จริงทั้งสาย: grain cache ของทีมมีครบ (fast path ไม่ยิง Fabric เลย)
    คลังว่างจริง แต่ payload (เหมือนที่ frontend ส่งจริงทุกวันนี้) มี warehouse_code="G002"
    ต้องได้ 1 แถวคลังว่าง ไม่ใช่ 2 แถว (คลังว่าง + คลัง G002 ที่ปลายทางเห็นเป็นแถวใหม่)
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data", exist_ok=True)
        pd.DataFrame([_grain_row("E1", "SKU1", qty=5, wh="")]).to_csv(
            f"data/tga_lines_{SUP}_2026_10.csv", index=False
        )
        pd.DataFrame(
            [{"sku": "SKU1", "supervisor_target_boxes": 5, "price_per_box": 100.0}]
        ).to_csv(f"data/target_boxes_{SUP}_2026_10.csv", index=False)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def test_one_row_at_the_true_blank_warehouse_not_two(self):
        req = LakehouseUploadRequest(
            sup_id=SUP,
            target_month=10,
            target_year=2026,
            upload_user_code="TESTER",
            allocations=[
                {"emp_id": "E1", "sku": "SKU1", "allocated_boxes": 5, "warehouse_code": "G002"}
            ],
        )
        out, _dropped, _preview, _short = lh._build_tga_upload_dataframe(
            req, drop_incomplete_rows=True
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out.iloc[0]["WAREHOUSECODE"], "")
        self.assertEqual(int(out.iloc[0]["QUANTITYCASE"]), 5)


if __name__ == "__main__":
    unittest.main()
