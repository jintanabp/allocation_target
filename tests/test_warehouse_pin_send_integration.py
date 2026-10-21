"""
กติกาบังคับคลังเดียว — เทสเต็มสายผ่าน _build_tga_upload_dataframe จำลองเหตุการณ์จริง

ที่มา: ผู้ใช้ขอกติกาให้บางกลุ่มสินค้า (Dim_Product[Section]) ในภาค+division ที่กำหนด
ถูกส่งเป้าไปคลังเดียวเท่านั้น (ดูแผนที่อนุมัติ) — จุดเสี่ยงที่สุดคือต้องไม่ชนกับบั๊ก
SL380/SL530/SL525 (คลังใหม่ที่ปลายทางไม่เคยมี = insert ซ้อนไม่ใช่ update ทับ เพราะ
WAREHOUSECODE อยู่ในคีย์ upsert ตั้งแต่ 7 ก.ย. 2026) — เทสไฟล์นี้จึงเน้นพิสูจน์ว่า
คลังเก่าทุกคลังของคู่ที่ถูกปักหมุดได้ 0 อย่างชัดเจน ไม่ใช่หายเงียบ ๆ และยอดรวมต่อ SKU
ไม่เปลี่ยนไปจากที่กระจายไว้ (ผ่านด่าน _assert_file_preserves_payload_totals จริง)
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
from backend.services import warehouse_pin_rules_store as store  # noqa: E402

logging.disable(logging.CRITICAL)

SUP = "SLWHPINSEND"


def _grain(emp, sku, qty, wh, st="S1", div="S", area="3", prov="P1"):
    return {
        "emp_id": emp, "sku": sku, "qty": qty, "salestype": st,
        "divisioncode": div, "areacode": area, "provincecode": prov,
        "warehouse_code": wh,
    }


class WarehousePinSendIntegrationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_env = os.environ.get("WAREHOUSE_PIN_RULES_PATH")
        os.environ["WAREHOUSE_PIN_RULES_PATH"] = os.path.join(self._tmp.name, "rules.json")
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data", exist_ok=True)
        # กันไม่ให้เส้นทางเติม dim วิ่งไป Fabric ระหว่างเทส (ห้ามมี network ในเทส) —
        # เคสส่วนใหญ่ในไฟล์นี้ grain มี dim ครบอยู่แล้วจึงไม่ควรเข้าเส้นทางนี้เลย แต่กันไว้เผื่อ
        self._patch = patch.object(
            lh, "_enrich_emp_dimensions", side_effect=lambda df, rows_raw, **kw: df
        )
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        os.chdir(self._cwd)
        if self._old_env is None:
            os.environ.pop("WAREHOUSE_PIN_RULES_PATH", None)
        else:
            os.environ["WAREHOUSE_PIN_RULES_PATH"] = self._old_env
        self._tmp.cleanup()

    def _write_cache(self, grain_rows, target_rows):
        pd.DataFrame(grain_rows).to_csv(f"data/tga_lines_{SUP}_2026_10.csv", index=False)
        pd.DataFrame(target_rows).to_csv(f"data/target_boxes_{SUP}_2026_10.csv", index=False)

    def _rule(self, skus=None, section="702", area="3", div="S", wh="G010"):
        store.write_rules([{
            "section": section, "skus": skus if skus is not None else ["SKU1"],
            "areacode": area, "divisioncode": div, "warehouse_code": wh,
        }])

    def _req(self, allocations, **kw):
        return LakehouseUploadRequest(
            sup_id=SUP, target_month=10, target_year=2026, upload_user_code="TESTER",
            allocations=allocations, **kw,
        )

    def test_multi_warehouse_pair_consolidates_to_one_row_with_others_zeroed(self):
        self._write_cache(
            grain_rows=[_grain("E1", "SKU1", 6, "R082"), _grain("E1", "SKU1", 4, "G002")],
            target_rows=[{"sku": "SKU1", "section": "702", "supervisor_target_boxes": 10, "price_per_box": 100.0}],
        )
        self._rule(section="702", area="3", div="S", wh="G010")
        req = self._req([{"emp_id": "E1", "sku": "SKU1", "allocated_boxes": 10}])
        out, _dropped, _preview, _short = lh._build_tga_upload_dataframe(req, drop_incomplete_rows=True)

        rows = out[out["PRODUCTCODE"] == "SKU1"]
        pinned = rows[rows["WAREHOUSECODE"] == "G010"]
        others = rows[rows["WAREHOUSECODE"] != "G010"]
        self.assertEqual(len(pinned), 1)
        self.assertEqual(int(pinned.iloc[0]["QUANTITYCASE"]), 10)
        self.assertTrue((others["QUANTITYCASE"] == 0).all())
        self.assertEqual(set(others["WAREHOUSECODE"]), {"R082", "G002"})
        self.assertEqual(int(rows["QUANTITYCASE"].sum()), 10)  # ยอดรวมไม่เปลี่ยน

    def test_reproduces_sl380_shaped_scenario_blank_warehouse_row_gets_explicit_zero(self):
        """คู่นี้เดิมคลังว่าง (แบบ SL380) + กติกาปักไปคลังจริง — แถวคลังว่างต้องได้ 0 ชัดเจน
        ไม่ใช่หายไปเงียบ ๆ และต้องไม่ชนกับ trust_existing ที่แก้ไปแล้ว (commit 1c8a74f)"""
        self._write_cache(
            grain_rows=[_grain("E1", "SKU1", 10, "")],
            target_rows=[{"sku": "SKU1", "section": "702", "supervisor_target_boxes": 10, "price_per_box": 100.0}],
        )
        self._rule(section="702", area="3", div="S", wh="G010")
        req = self._req([{"emp_id": "E1", "sku": "SKU1", "allocated_boxes": 10, "warehouse_code": "G010"}])
        out, _dropped, _preview, _short = lh._build_tga_upload_dataframe(req, drop_incomplete_rows=True)

        rows = out[out["PRODUCTCODE"] == "SKU1"]
        blank_wh = rows[rows["WAREHOUSECODE"] == ""]
        pinned = rows[rows["WAREHOUSECODE"] == "G010"]
        self.assertEqual(len(blank_wh), 1)
        self.assertEqual(int(blank_wh.iloc[0]["QUANTITYCASE"]), 0)
        self.assertEqual(len(pinned), 1)
        self.assertEqual(int(pinned.iloc[0]["QUANTITYCASE"]), 10)

    def test_assert_file_preserves_payload_totals_still_passes(self):
        """ด่านที่ข้ามไม่ได้ (I1-adjacent) ต้องยังผ่านจริง ไม่ใช่แค่ไม่ throw เพราะ mock ไว้"""
        self._write_cache(
            grain_rows=[_grain("E1", "SKU1", 6, "R082"), _grain("E1", "SKU1", 4, "G002")],
            target_rows=[{"sku": "SKU1", "section": "702", "supervisor_target_boxes": 10, "price_per_box": 100.0}],
        )
        self._rule(section="702", area="3", div="S", wh="G010")
        req = self._req([{"emp_id": "E1", "sku": "SKU1", "allocated_boxes": 10}])
        # ไม่ mock _assert_file_preserves_payload_totals เลย — ปล่อยให้รันจริงภายใน
        # _build_tga_upload_dataframe แล้วถ้าล้มจะได้ HTTPException ทันที (เทสจะแดงเอง)
        out, _dropped, _preview, _short = lh._build_tga_upload_dataframe(req, drop_incomplete_rows=True)
        self.assertEqual(int(out[out["PRODUCTCODE"] == "SKU1"]["QUANTITYCASE"].sum()), 10)

    def test_non_matching_sku_is_completely_unaffected(self):
        self._write_cache(
            grain_rows=[
                _grain("E1", "SKU1", 6, "R082"), _grain("E1", "SKU1", 4, "G002"),
                _grain("E1", "OTHERSKU", 5, "R303"),
            ],
            target_rows=[
                {"sku": "SKU1", "section": "702", "supervisor_target_boxes": 10, "price_per_box": 100.0},
                {"sku": "OTHERSKU", "section": "999", "supervisor_target_boxes": 5, "price_per_box": 50.0},
            ],
        )
        self._rule(section="702", area="3", div="S", wh="G010")
        req = self._req(
            [
                {"emp_id": "E1", "sku": "SKU1", "allocated_boxes": 10},
                {"emp_id": "E1", "sku": "OTHERSKU", "allocated_boxes": 5},
            ]
        )
        out, _dropped, _preview, _short = lh._build_tga_upload_dataframe(req, drop_incomplete_rows=True)
        other_rows = out[out["PRODUCTCODE"] == "OTHERSKU"]
        self.assertEqual(len(other_rows), 1)
        self.assertEqual(other_rows.iloc[0]["WAREHOUSECODE"], "R303")
        self.assertEqual(int(other_rows.iloc[0]["QUANTITYCASE"]), 5)

    def test_same_section_different_sku_is_not_pinned(self):
        """กติกาผูก SKU1 เท่านั้น — SKU2 (อยู่ section 702 เดียวกัน) ต้องไม่โดนแตะเลย
        เพราะแอดมินอาจเลือกแค่บางตัวในกลุ่มสินค้า ไม่ใช่ทั้งกลุ่มเสมอไป"""
        self._write_cache(
            grain_rows=[
                _grain("E1", "SKU1", 6, "R082"), _grain("E1", "SKU1", 4, "G002"),
                _grain("E1", "SKU2", 6, "R082"), _grain("E1", "SKU2", 4, "G002"),
            ],
            target_rows=[
                {"sku": "SKU1", "section": "702", "supervisor_target_boxes": 10, "price_per_box": 100.0},
                {"sku": "SKU2", "section": "702", "supervisor_target_boxes": 10, "price_per_box": 100.0},
            ],
        )
        self._rule(skus=["SKU1"], area="3", div="S", wh="G010")
        req = self._req(
            [
                {"emp_id": "E1", "sku": "SKU1", "allocated_boxes": 10},
                {"emp_id": "E1", "sku": "SKU2", "allocated_boxes": 10},
            ]
        )
        out, _dropped, _preview, _short = lh._build_tga_upload_dataframe(req, drop_incomplete_rows=True)
        sku2_rows = out[out["PRODUCTCODE"] == "SKU2"]
        self.assertEqual(set(sku2_rows["WAREHOUSECODE"]), {"R082", "G002"})
        self.assertNotIn("G010", sku2_rows["WAREHOUSECODE"].tolist())
        self.assertEqual(int(sku2_rows["QUANTITYCASE"].sum()), 10)

    def test_excel_download_path_shows_the_same_consolidated_result(self):
        """ดาวน์โหลด Excel (drop_incomplete_rows=False) ต้องเห็นค่าจริงที่จะถูกส่งเหมือนกัน"""
        self._write_cache(
            grain_rows=[_grain("E1", "SKU1", 6, "R082"), _grain("E1", "SKU1", 4, "G002")],
            target_rows=[{"sku": "SKU1", "section": "702", "supervisor_target_boxes": 10, "price_per_box": 100.0}],
        )
        self._rule(section="702", area="3", div="S", wh="G010")
        req = self._req([{"emp_id": "E1", "sku": "SKU1", "allocated_boxes": 10}])
        out, _dropped, _preview, _short = lh._build_tga_upload_dataframe(req, drop_incomplete_rows=False)
        rows = out[out["PRODUCTCODE"] == "SKU1"]
        pinned = rows[rows["WAREHOUSECODE"] == "G010"]
        self.assertEqual(len(pinned), 1)
        self.assertEqual(int(pinned.iloc[0]["QUANTITYCASE"]), 10)
        self.assertTrue((rows[rows["WAREHOUSECODE"] != "G010"]["QUANTITYCASE"] == 0).all())

    def test_wh_pin_stats_are_attached_to_attrs(self):
        self._write_cache(
            grain_rows=[_grain("E1", "SKU1", 6, "R082"), _grain("E1", "SKU1", 4, "G002")],
            target_rows=[{"sku": "SKU1", "section": "702", "supervisor_target_boxes": 10, "price_per_box": 100.0}],
        )
        self._rule(section="702", area="3", div="S", wh="G010")
        req = self._req([{"emp_id": "E1", "sku": "SKU1", "allocated_boxes": 10}])
        out, _d, _p, _s = lh._build_tga_upload_dataframe(req, drop_incomplete_rows=True)
        self.assertEqual(out.attrs.get("wh_pin_matched_groups"), 1)
        self.assertEqual(out.attrs.get("wh_pin_boxes_moved"), 10)
        self.assertEqual(out.attrs.get("wh_pin_rows_zeroed"), 2)

    # ── เงื่อนไขเสริม (21 ต.ค. 2026): หน่วยเครดิต + เฉพาะพนักงาน — สถานการณ์ที่ผู้ใช้ขอจริง ──

    def _real_case_rules(self):
        store.write_rules([
            # ภาคเหนือ B: เฉพาะหน่วยเครดิต ทุกคน
            {"section": "702", "skus": ["SKU1"], "areacode": "5", "divisioncode": "B",
             "salestype": "S", "warehouse_code": "G010"},
            # ภาคกลาง B: เฉพาะหน่วยเครดิต เฉพาะพนักงานในรายชื่อ
            {"section": "702", "skus": ["SKU1"], "areacode": "3", "divisioncode": "B",
             "salestype": "S", "emp_ids": ["S344", "S345"], "warehouse_code": "G010"},
        ])

    def test_real_case_credit_and_employee_list_through_the_send_file(self):
        self._write_cache(
            grain_rows=[
                _grain("S344", "SKU1", 6, "R082", st="S", div="B", area="3"),  # ในรายชื่อ คลังอื่น
                _grain("S345", "SKU1", 4, "", st="S", div="B", area="3"),      # ในรายชื่อ คลังว่าง
                _grain("S999", "SKU1", 5, "R082", st="S", div="B", area="3"),  # ภาคกลาง ไม่อยู่ในรายชื่อ
                _grain("S998", "SKU1", 2, "", st="S", div="B", area="3"),      # ภาคกลาง ไม่อยู่ในรายชื่อ คลังว่าง
                _grain("V001", "SKU1", 3, "R303", st="C", div="B", area="3"),  # ภาคกลาง รถเงินสด
                _grain("N001", "SKU1", 7, "R500", st="S", div="B", area="5"),  # ภาคเหนือ เครดิต
                _grain("N002", "SKU1", 1, "R501", st="C", div="B", area="5"),  # ภาคเหนือ รถเงินสด
            ],
            target_rows=[{"sku": "SKU1", "section": "702", "supervisor_target_boxes": 28, "price_per_box": 100.0}],
        )
        self._real_case_rules()
        req = self._req([
            {"emp_id": e, "sku": "SKU1", "allocated_boxes": b}
            for e, b in (("S344", 6), ("S345", 4), ("S999", 5), ("S998", 2), ("V001", 3), ("N001", 7), ("N002", 1))
        ])
        # ไม่ mock ด่านยอดรวม — ถ้ายอดไม่ตรงเป้า/ไม่ตรง payload จะได้ HTTPException ทันที
        out, _dropped, _preview, _short = lh._build_tga_upload_dataframe(req, drop_incomplete_rows=True)
        rows = out[out["PRODUCTCODE"] == "SKU1"]
        got = {
            (r.SALESMANCODE, r.WAREHOUSECODE): int(r.QUANTITYCASE)
            for r in rows.itertuples()
        }
        self.assertEqual(got, {
            ("S344", "G010"): 6, ("S344", "R082"): 0,  # ย้ายไป G010 + ส่ง 0 ทับคลังเก่า
            ("S345", "G010"): 4, ("S345", ""): 0,      # คลังว่างเดิมได้ 0 ทับ
            ("S999", "R082"): 5,                       # ไม่อยู่ในรายชื่อ — คงคลังตามเป้า
            ("S998", ""): 2,                           # ไม่อยู่ในรายชื่อ — คงคลังว่างตามเป้า
            ("V001", "R303"): 3,                       # รถเงินสด — ไม่โดน
            ("N001", "G010"): 7, ("N001", "R500"): 0,  # ภาคเหนือเครดิต — ทุกคนโดน
            ("N002", "R501"): 1,                       # ภาคเหนือรถเงินสด — ไม่โดน
        })
        self.assertEqual(int(rows["QUANTITYCASE"].sum()), 28)

    def test_old_unrestricted_rule_still_behaves_exactly_as_before(self):
        """กติกาที่บันทึกไว้ก่อนมีช่องใหม่ (ไม่มี salestype/emp_ids ในไฟล์) ต้องยังจับทุกคนเหมือนเดิม"""
        self._write_cache(
            grain_rows=[
                _grain("S999", "SKU1", 5, "R082", st="S", div="B", area="3"),
                _grain("V001", "SKU1", 3, "R303", st="C", div="B", area="3"),
            ],
            target_rows=[{"sku": "SKU1", "section": "702", "supervisor_target_boxes": 8, "price_per_box": 100.0}],
        )
        import json
        with open(os.environ["WAREHOUSE_PIN_RULES_PATH"], "w", encoding="utf-8") as f:
            json.dump({"rev": 1, "rules": [{
                "id": "old1", "section": "702", "skus": ["SKU1"], "areacode": "3",
                "divisioncode": "B", "warehouse_code": "G010",
            }]}, f)
        req = self._req([
            {"emp_id": "S999", "sku": "SKU1", "allocated_boxes": 5},
            {"emp_id": "V001", "sku": "SKU1", "allocated_boxes": 3},
        ])
        out, _d, _p, _s = lh._build_tga_upload_dataframe(req, drop_incomplete_rows=True)
        live = out[(out["PRODUCTCODE"] == "SKU1") & (out["QUANTITYCASE"] > 0)]
        self.assertEqual(set(live["WAREHOUSECODE"]), {"G010"})
        self.assertEqual(int(out["QUANTITYCASE"].sum()), 8)

    def test_excel_download_matches_send_for_real_case(self):
        self._write_cache(
            grain_rows=[
                _grain("S344", "SKU1", 6, "R082", st="S", div="B", area="3"),
                _grain("S999", "SKU1", 5, "R082", st="S", div="B", area="3"),
            ],
            target_rows=[{"sku": "SKU1", "section": "702", "supervisor_target_boxes": 11, "price_per_box": 100.0}],
        )
        self._real_case_rules()
        req = self._req([
            {"emp_id": "S344", "sku": "SKU1", "allocated_boxes": 6},
            {"emp_id": "S999", "sku": "SKU1", "allocated_boxes": 5},
        ])
        sent, *_ = lh._build_tga_upload_dataframe(req, drop_incomplete_rows=True)
        excel, *_ = lh._build_tga_upload_dataframe(req, drop_incomplete_rows=False)
        key = lambda d: sorted(zip(d["SALESMANCODE"], d["WAREHOUSECODE"], d["QUANTITYCASE"].astype(int)))
        self.assertEqual(key(sent), key(excel))


if __name__ == "__main__":
    unittest.main()
