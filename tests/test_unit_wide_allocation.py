"""
กระจายรวมทั้งหน่วยในภาค

โจทย์: บางงวดเป้าเข้ามาใต้ซุปคนเดียว แต่ต้องเกลี่ยให้พนักงานทุกทีมในหน่วย+ภาคเดียวกัน
สองอย่างที่ทำให้ทำได้จริง:
  1. ประวัติขายต้องอ่านจาก cache ของทุกทีมที่เกี่ยว ไม่งั้นคนทีมอื่นถูกมองว่าไม่มีประวัติ
     แล้วได้น้ำหนักขั้นต่ำจนกระจายออกมาเบี้ยว
  2. คู่ (พนักงาน×สินค้า) ที่ Target Sun ยังไม่เคยมี ต้องเติมเขต/พื้นที่จากแถวอื่น
     ของพนักงานคนเดียวกันได้ ไม่งั้นส่งไม่ได้ (แล้ว SKU ทั้งตัวจะถูกตัดตามนโยบาย)

กติกาที่ต้องคงไว้: เดาเขตได้เฉพาะเมื่อแถวของคนนั้น "ตรงกันหมด" — ขัดกันเองเมื่อไร
ห้ามเดา เพราะสร้างแถวผิดเขตใน Oracle แย่กว่าไม่ส่ง
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

from backend.schemas import LakehouseUploadRequest, OptimizeRequest  # noqa: E402
from backend.services import lakehouse as lh  # noqa: E402
from backend.services import optimize as opt  # noqa: E402

logging.disable(logging.CRITICAL)


def _grain(emp, sku, qty=5, area="10", province="P1", st="S1", div="D1", wh="WH1"):
    return {
        "emp_id": emp, "sku": sku, "qty": qty,
        "salestype": st, "divisioncode": div, "areacode": area,
        "provincecode": province, "warehouse_code": wh,
    }


class TestEmpDimsInference(unittest.TestCase):
    def test_dims_taken_from_the_persons_other_products(self):
        dg = lh._normalize_grain_dtype(pd.DataFrame([
            _grain("E1", "A"), _grain("E1", "B"),
        ]))
        dims = lh.emp_dims_from_own_grain(dg)
        self.assertEqual(
            dims["E1"],
            {"salestype": "S1", "divisioncode": "D1", "areacode": "10", "provincecode": "P1"},
        )

    def test_conflicting_rows_are_never_guessed(self):
        """คนที่ขายหลายจังหวัด — เดาไม่ได้ว่าแถวใหม่ควรเป็นจังหวัดไหน"""
        dg = lh._normalize_grain_dtype(pd.DataFrame([
            _grain("E1", "A", province="P1"),
            _grain("E1", "B", province="P2"),
        ]))
        self.assertNotIn("E1", lh.emp_dims_from_own_grain(dg))

    def test_each_person_resolved_independently(self):
        dg = lh._normalize_grain_dtype(pd.DataFrame([
            _grain("E1", "A", province="P1"),
            _grain("E2", "A", province="P2"),
            _grain("E2", "B", province="P3"),
        ]))
        dims = lh.emp_dims_from_own_grain(dg)
        self.assertIn("E1", dims)
        self.assertNotIn("E2", dims, "E2 ขัดกันเอง ต้องไม่เดา")

    def test_empty_grain_is_safe(self):
        self.assertEqual(lh.emp_dims_from_own_grain(pd.DataFrame()), {})


class TestNewRowsForOtherTeams(unittest.TestCase):
    SUP = "SLUNIT"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data", exist_ok=True)
        # E1 มีเป้าสินค้า A อยู่แล้ว · E2 มีแค่สินค้า B (ไม่เคยมี A)
        pd.DataFrame([
            _grain("E1", "A"),
            _grain("E2", "B", province="P9", area="20"),
        ]).to_csv(f"data/tga_lines_{self.SUP}_2026_08.csv", index=False)
        pd.DataFrame([
            {"sku": "A", "supervisor_target_boxes": 20, "price_per_box": 1.0},
        ]).to_csv(f"data/target_boxes_{self.SUP}_2026_08.csv", index=False)
        self._patch = patch.object(
            lh, "_enrich_emp_dimensions", side_effect=lambda df, rows_raw, **kw: df
        )
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _req(self, **kw):
        return LakehouseUploadRequest(
            sup_id=self.SUP, target_month=8, target_year=2026, upload_user_code="T",
            allocations=[
                {"emp_id": "E1", "sku": "A", "allocated_boxes": 12},
                {"emp_id": "E2", "sku": "A", "allocated_boxes": 8},
            ],
            **kw,
        )

    def test_without_the_flag_the_sku_is_dropped_as_before(self):
        """E2 ส่งไม่ได้ → ตัด A ทั้งตัว → ไม่เหลืออะไรให้ส่ง (400 พร้อมบอกว่าตัดอะไรไป)"""
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            lh._build_tga_upload_dataframe(self._req(), drop_incomplete_rows=True)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.detail["excluded_skus"], ["A"])

    def test_with_the_flag_a_new_row_is_created_for_the_other_team(self):
        out, _d, _p, shortfall = lh._build_tga_upload_dataframe(
            self._req(allow_new_targetsun_rows=True), drop_incomplete_rows=True
        )
        self.assertEqual(int(out["QUANTITYCASE"].sum()), 20, "ยอดต้องครบเป้า")
        e2 = out[out["SALESMANCODE"] == "E2"]
        self.assertEqual(len(e2), 1)
        self.assertEqual(int(e2["QUANTITYCASE"].iloc[0]), 8)
        self.assertEqual(
            e2["PROVINCECODE"].iloc[0], "P9",
            "ต้องใช้จังหวัดของ E2 เอง ไม่ใช่ของคนอื่น",
        )
        self.assertEqual(shortfall, [])

    def test_inferred_row_still_has_no_duplicate_upsert_key(self):
        out, _d, _p, _s = lh._build_tga_upload_dataframe(
            self._req(allow_new_targetsun_rows=True), drop_incomplete_rows=True
        )
        key = ["PRODUCTCODE", "SALESTYPE", "DIVISIONCODE", "SALESMANCODE", "AREACODE", "PROVINCECODE"]
        self.assertEqual(int(out.duplicated(subset=key).sum()), 0)


class TestHistoryPooledAcrossTeams(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data", exist_ok=True)
        pd.DataFrame([{"emp_id": "E1", "sku": "A", "hist_boxes": 10}]).to_csv(
            "data/hist_cache_SLA_2026_08.csv", index=False
        )
        pd.DataFrame([{"emp_id": "E2", "sku": "A", "hist_boxes": 6}]).to_csv(
            "data/hist_cache_SLB_2026_08.csv", index=False
        )

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _read(self, sup_ids, emps):
        from backend.core.paths import hist_cache_path

        return opt._read_hist_cache_across_teams(
            lambda sid: hist_cache_path(sid, 8, 2026, n_months=3), sup_ids, emps
        )

    def test_own_team_only_misses_the_other_teams_history(self):
        df = self._read(["SLA"], ["E1", "E2"])
        self.assertEqual(df["emp_id"].tolist(), ["E1"])

    def test_pooling_picks_up_both_teams(self):
        df = self._read(["SLA", "SLB"], ["E1", "E2"])
        self.assertEqual(sorted(df["emp_id"].tolist()), ["E1", "E2"])
        self.assertEqual(int(df["hist_boxes"].sum()), 16)

    def test_duplicate_pairs_are_not_counted_twice(self):
        pd.DataFrame([{"emp_id": "E1", "sku": "A", "hist_boxes": 99}]).to_csv(
            "data/hist_cache_SLC_2026_08.csv", index=False
        )
        df = self._read(["SLA", "SLC"], ["E1"])
        self.assertEqual(len(df), 1)
        self.assertEqual(int(df["hist_boxes"].iloc[0]), 10, "ทีมแรกในลิสต์ต้องชนะ")

    def test_missing_files_are_ignored(self):
        self.assertTrue(self._read(["SLNOPE"], ["E1"]).empty)


class TestSummedRegionalTarget(unittest.TestCase):
    """
    รวมเป้าทั้งภาค: เป้าต่อ SKU = ผลบวกของทุกทีม

    เดิมโหมดนี้ใช้ "เป้าของทีมเดียว" คู่กับพนักงานทั้งภาค — เป้าเงินกับเป้าหีบ
    คนละสเกลกันคนละเท่าตัว ผลกระจายจึงเบี้ยวโดยที่ประตู I1 ยังบอกว่าตรงเป้า
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data", exist_ok=True)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    @staticmethod
    def _write(sid, rows):
        pd.DataFrame(rows).to_csv(f"data/target_boxes_{sid}_2026_08.csv", index=False)

    def _sum(self, ids):
        from backend.core.targets import load_summed_target_boxes

        return load_summed_target_boxes(ids, 8, 2026)

    def test_boxes_are_added_across_teams(self):
        self._write("SLA", [{"sku": "A", "supervisor_target_boxes": 10, "price_per_box": 5.0}])
        self._write("SLB", [{"sku": "A", "supervisor_target_boxes": 7, "price_per_box": 5.0}])
        df, missing = self._sum(["SLA", "SLB"])
        self.assertEqual(missing, [])
        self.assertEqual(int(df.loc[df["sku"] == "A", "supervisor_target_boxes"].iloc[0]), 17)

    def test_sku_only_one_team_has_still_shows_up(self):
        self._write("SLA", [{"sku": "A", "supervisor_target_boxes": 10}])
        self._write("SLB", [{"sku": "B", "supervisor_target_boxes": 4}])
        df, _ = self._sum(["SLA", "SLB"])
        self.assertEqual(sorted(df["sku"].tolist()), ["A", "B"])

    def test_missing_team_file_is_reported_not_guessed(self):
        """ทีมที่ยังไม่มีไฟล์เป้า = เป้าหายไปเงียบ ๆ — ผู้เรียกต้องได้รู้เพื่อปฏิเสธ"""
        self._write("SLA", [{"sku": "A", "supervisor_target_boxes": 10}])
        df, missing = self._sum(["SLA", "SLB"])
        self.assertEqual(missing, ["SLB"])
        self.assertEqual(int(df["supervisor_target_boxes"].sum()), 10)

    def test_never_falls_back_to_the_global_file(self):
        """
        ถ้าตกไปอ่าน data/target_boxes.csv ทุกทีม ผลรวมจะเป็นเป้าเดิม x จำนวนทีม
        แล้ว I1 จะบังคับให้กระจายหีบเกินจริงออกไปทั้งภาค
        """
        pd.DataFrame([{"sku": "A", "supervisor_target_boxes": 99}]).to_csv(
            "data/target_boxes.csv", index=False
        )
        df, missing = self._sum(["SLA", "SLB"])
        self.assertIsNone(df)
        self.assertEqual(missing, ["SLA", "SLB"])

    def test_duplicate_sku_inside_one_team_is_collapsed_first(self):
        """ข้อมูลเสียของทีมเดียวต้องไม่บวกเข้าเป้าของทั้งภาค (I6)"""
        self._write("SLA", [
            {"sku": "A", "supervisor_target_boxes": 10},
            {"sku": "A", "supervisor_target_boxes": 3},
        ])
        self._write("SLB", [{"sku": "A", "supervisor_target_boxes": 5}])
        df, _ = self._sum(["SLA", "SLB"])
        self.assertEqual(int(df["supervisor_target_boxes"].sum()), 8, "3 (แถวหลัง) + 5")

    def test_same_team_listed_twice_is_not_counted_twice(self):
        self._write("SLA", [{"sku": "A", "supervisor_target_boxes": 10}])
        df, _ = self._sum(["SLA", "sla", " SLA "])
        self.assertEqual(int(df["supervisor_target_boxes"].sum()), 10)

    def test_metadata_comes_from_the_first_team_that_has_it(self):
        self._write("SLA", [{"sku": "A", "supervisor_target_boxes": 1, "price_per_box": 0}])
        self._write("SLB", [{"sku": "A", "supervisor_target_boxes": 1, "price_per_box": 12.5}])
        df, _ = self._sum(["SLA", "SLB"])
        self.assertAlmostEqual(float(df["price_per_box"].iloc[0]), 12.5)


class TestResolveTargetSupIds(unittest.TestCase):
    def test_own_team_always_first_even_if_not_sent(self):
        self.assertEqual(opt._resolve_target_sup_ids("SLA", ["SLB"]), ["SLA", "SLB"])

    def test_duplicates_and_case_are_normalised(self):
        self.assertEqual(
            opt._resolve_target_sup_ids("sla", ["SLB", " slb ", "SLA"]), ["SLA", "SLB"]
        )

    def test_empty_means_single_team_mode(self):
        self.assertEqual(opt._resolve_target_sup_ids("SLA", []), ["SLA"])


class TestTargetSupIdsPermission(unittest.TestCase):
    """เป้าของทีมอื่นเป็นฐานคำนวณ — ต้องตรวจสิทธิ์ทุกรหัส ไม่ใช่แค่รหัสที่ยิง request"""

    def test_router_checks_every_target_sup_id(self):
        import inspect

        from backend.routers import optimize as router

        src = inspect.getsource(router.run_optimization)
        self.assertIn("for peer in req.target_sup_ids", src)
        self.assertIn("ensure_supervisor_allowed(user, pid)", src)

    def test_schema_defaults_to_single_team(self):
        req = OptimizeRequest(yellowTargets=[{"emp_id": "E1", "yellow_target": 1.0}])
        self.assertEqual(req.target_sup_ids, [])


class TestPeerSupIdsWiring(unittest.TestCase):
    def test_schema_defaults_to_own_team_only(self):
        req = OptimizeRequest(yellowTargets=[{"emp_id": "E1", "yellow_target": 1.0}])
        self.assertEqual(req.peer_sup_ids, [])

    def test_service_puts_own_team_first(self):
        import inspect

        src = inspect.getsource(opt.run_optimization_service)
        self.assertIn("hist_sup_ids = [sup_id]", src)
        self.assertIn("peer_sup_ids", src)


if __name__ == "__main__":
    unittest.main()
