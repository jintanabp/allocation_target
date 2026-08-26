"""
ส่งผลกระจายรวมภาค/รวมหน่วย: พนักงานทีมอื่นต้องไม่หายจากไฟล์ที่ส่ง

grain (SALESTYPE / DIVISIONCODE / AREACODE ของแต่ละคู่พนักงาน×สินค้า) ถูกเก็บแยก
ไฟล์ต่อทีม — data/tga_lines_{SL}_{Y}_{MM}.csv สร้างตอนโหลดข้อมูลขั้นที่ 1 ของทีมนั้น
แต่ตอนส่ง ระบบอ่านแค่ไฟล์ของ req.sup_id (ทีมเจ้าของก้อน) พนักงานของทีมอื่นจึงไม่มี
dim เลย → แถวถูกตัด → SKU ถูกตัดทั้งตัว → ขึ้น "ไม่ได้ส่งบางรายการ" ทั้งที่เปิด
allow_new_targetsun_rows แล้ว (flag นั้นเดา dim จาก "แถวอื่นของคนเดียวกัน" ซึ่งก็ไม่มี
เพราะคนนั้นไม่ได้อยู่ในไฟล์ที่อ่าน)

dim พวกนี้เป็นคุณสมบัติของพนักงาน ไม่ได้ผูกกับว่าใครเป็นหัวหน้า หยิบจากไฟล์ทีมไหน
จึงให้ผลเดียวกัน
"""

from __future__ import annotations

import logging
import os
import shutil
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

OWNER, OTHER = "SLOWN", "SLOTH"
MONTH, YEAR = 8, 2026


def _grain(emp: str, sku: str, area: str = "10", province: str = "P1") -> dict:
    return {
        "emp_id": emp, "sku": sku, "qty": 5, "salestype": "S",
        "divisioncode": "B", "areacode": area, "provincecode": province,
        "warehouse_code": "",
    }


class _TempData(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        self._tmpdir = tempfile.mkdtemp(prefix="grain_teams_")
        os.chdir(self._tmpdir)
        os.makedirs("data", exist_ok=True)

    def tearDown(self):
        os.chdir(self._cwd)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _write_grain(self, sup: str, rows: list[dict], month: int = MONTH) -> None:
        pd.DataFrame(rows).to_csv(
            f"data/tga_lines_{sup}_{YEAR}_{month:02d}.csv", index=False
        )


class TestReadGrainAcrossTeams(_TempData):
    def test_finds_an_employee_who_lives_in_another_teams_file(self):
        self._write_grain(OWNER, [_grain("E1", "A")])
        self._write_grain(OTHER, [_grain("E2", "B", area="20", province="P9")])
        out = lh._read_tga_grain_across_teams(MONTH, YEAR, {"E2"})
        self.assertEqual(sorted(out["emp_id"].unique().tolist()), ["E2"])
        self.assertEqual(out["areacode"].iloc[0], "20")

    def test_only_the_requested_employees_come_back(self):
        """ห้ามลากทั้งบริษัทมา — ภาคหนึ่งมีได้หลายหมื่นแถว"""
        self._write_grain(OWNER, [_grain("E1", "A")])
        self._write_grain(OTHER, [_grain("E2", "B"), _grain("E3", "C")])
        out = lh._read_tga_grain_across_teams(MONTH, YEAR, {"E2"})
        self.assertEqual(sorted(out["emp_id"].unique().tolist()), ["E2"])

    def test_other_periods_are_ignored(self):
        self._write_grain(OTHER, [_grain("E2", "B")], month=7)
        self.assertTrue(lh._read_tga_grain_across_teams(MONTH, YEAR, {"E2"}).empty)

    def test_an_employee_in_two_teams_files_is_not_duplicated(self):
        """ช่วงย้ายทีม คนเดียวโผล่ได้สองไฟล์ — แถวซ้ำต้องเหลือแถวเดียว"""
        self._write_grain(OWNER, [_grain("E2", "B")])
        self._write_grain(OTHER, [_grain("E2", "B")])
        out = lh._read_tga_grain_across_teams(MONTH, YEAR, {"E2"})
        self.assertEqual(len(out), 1)

    def test_missing_data_dir_is_not_an_error(self):
        shutil.rmtree("data")
        self.assertTrue(lh._read_tga_grain_across_teams(MONTH, YEAR, {"E2"}).empty)

    def test_no_employees_asked_means_no_work(self):
        self._write_grain(OTHER, [_grain("E2", "B")])
        self.assertTrue(lh._read_tga_grain_across_teams(MONTH, YEAR, set()).empty)


class TestEmployeeMovedBetweenSupervisors(_TempData):
    """
    ย้ายซุปกลางงวด (ในหน่วยเดียวกันย้ายกันบ่อย) — คนเดียวโผล่ทั้งไฟล์ทีมเก่าและทีมใหม่

    ถ้าเอา grain มารวมกันดื้อ ๆ คนนั้นจะได้แถวเป้าสองแถว (เขตเก่า + เขตใหม่)
    แล้วหีบถูกแบ่งครึ่งไปลงทั้งคู่ — ครึ่งหนึ่งไปเขียนทับแถวของเขตที่เขาย้ายออกมาแล้ว
    ส่วนแถวจริงได้เป้าแค่ครึ่งเดียว · ยอดรวมยังครบ ด่านไหนจึงไม่จับ
    """

    OLD, NEW = "SLOLD", "SLNEW"

    def _both_files(self):
        self._write_grain(self.OLD, [_grain("E9", "A", area="10", province="P1")])
        self._write_grain(self.NEW, [_grain("E9", "A", area="20", province="P2")])
        old_p = f"data/tga_lines_{self.OLD}_{YEAR}_{MONTH:02d}.csv"
        new_p = f"data/tga_lines_{self.NEW}_{YEAR}_{MONTH:02d}.csv"
        t = os.path.getmtime(new_p)
        os.utime(old_p, (t - 3600, t - 3600))

    def test_only_the_newest_file_of_that_employee_is_used(self):
        self._both_files()
        out = lh._read_tga_grain_across_teams(MONTH, YEAR, {"E9"})
        self.assertEqual(len(out), 1, "ต้องเหลือชุดเดียว ไม่ใช่เอาทั้งสองเขตมารวม")
        self.assertEqual(out["areacode"].iloc[0], "20", "ต้องเป็นเขตของทีมใหม่")

    def test_dims_can_be_inferred_again_after_the_stale_rows_go(self):
        """ถ้ายังปนสองเขตอยู่ ตัวเดาจะเห็นว่าขัดกันเองแล้วยอมแพ้ → แถวถูกตัด"""
        self._both_files()
        dims = lh.emp_dims_from_own_grain(
            lh._read_tga_grain_across_teams(MONTH, YEAR, {"E9"})
        )
        self.assertIn("E9", dims)
        self.assertEqual(dims["E9"]["areacode"], "20")

    def test_boxes_are_not_split_across_the_old_and_new_area(self):
        self._both_files()
        # เจ้าของก้อนเป็นทีมที่สาม ที่ไม่รู้จัก E9 เลย
        self._write_grain(OWNER, [_grain("E1", "A", area="99", province="P9")])
        pd.DataFrame([
            {"sku": "A", "supervisor_target_boxes": 10, "price_per_box": 1.0},
        ]).to_csv(f"data/target_boxes_{OWNER}_{YEAR}_{MONTH:02d}.csv", index=False)
        with patch.object(
            lh, "_enrich_emp_dimensions", side_effect=lambda df, rows_raw, **kw: df
        ):
            out, dropped, _preview, _s = lh._build_tga_upload_dataframe(
                LakehouseUploadRequest(
                    sup_id=OWNER, target_month=MONTH, target_year=YEAR,
                    upload_user_code="T",
                    allocations=[{"emp_id": "E9", "sku": "A", "allocated_boxes": 10}],
                    allow_new_targetsun_rows=True,
                ),
                drop_incomplete_rows=True,
            )
        self.assertEqual(dropped, 0)
        self.assertEqual(len(out), 1, "ต้องได้แถวเดียว ไม่ใช่แบ่งครึ่งไปเขตเก่ากับเขตใหม่")
        self.assertEqual(int(out["QUANTITYCASE"].iloc[0]), 10)
        self.assertEqual(out["AREACODE"].iloc[0], "20")

    def test_the_owning_teams_own_grain_always_wins(self):
        """ทีมเจ้าของก้อนรู้จักคนนี้อยู่แล้ว = ไม่ต้องไปถามไฟล์ทีมอื่นเลย"""
        self._both_files()
        pd.DataFrame([
            {"sku": "A", "supervisor_target_boxes": 10, "price_per_box": 1.0},
        ]).to_csv(f"data/target_boxes_{self.NEW}_{YEAR}_{MONTH:02d}.csv", index=False)
        with patch.object(
            lh, "_enrich_emp_dimensions", side_effect=lambda df, rows_raw, **kw: df
        ):
            out, dropped, _p, _s = lh._build_tga_upload_dataframe(
                LakehouseUploadRequest(
                    sup_id=self.NEW, target_month=MONTH, target_year=YEAR,
                    upload_user_code="T",
                    allocations=[{"emp_id": "E9", "sku": "A", "allocated_boxes": 10}],
                    allow_new_targetsun_rows=True,
                ),
                drop_incomplete_rows=True,
            )
        self.assertEqual(dropped, 0)
        self.assertEqual(len(out), 1)
        self.assertEqual(out["AREACODE"].iloc[0], "20")


class TestOtherTeamRowsSurviveTheSend(_TempData):
    """เคสจริง: กระจายรวมหน่วย แล้วส่งจากทีมเจ้าของก้อน"""

    def setUp(self):
        super().setUp()
        # E1 อยู่ทีมเจ้าของ · E2 อยู่อีกทีม (คนละไฟล์ grain) และไม่เคยมีเป้าสินค้า A
        self._write_grain(OWNER, [_grain("E1", "A")])
        self._write_grain(OTHER, [_grain("E2", "B", area="20", province="P9")])
        pd.DataFrame([
            {"sku": "A", "supervisor_target_boxes": 20, "price_per_box": 1.0},
        ]).to_csv(f"data/target_boxes_{OWNER}_{YEAR}_{MONTH:02d}.csv", index=False)
        self._patch = patch.object(
            lh, "_enrich_emp_dimensions", side_effect=lambda df, rows_raw, **kw: df
        )
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        super().tearDown()

    def _req(self, **kw):
        return LakehouseUploadRequest(
            sup_id=OWNER, target_month=MONTH, target_year=YEAR, upload_user_code="T",
            allocations=[
                {"emp_id": "E1", "sku": "A", "allocated_boxes": 12},
                {"emp_id": "E2", "sku": "A", "allocated_boxes": 8},
            ],
            **kw,
        )

    def test_every_allocated_box_reaches_the_file(self):
        out, dropped, preview, shortfall = lh._build_tga_upload_dataframe(
            self._req(allow_new_targetsun_rows=True), drop_incomplete_rows=True
        )
        self.assertEqual(dropped, 0, f"ไม่ควรตัดแถวไหนเลย แต่ตัด: {preview}")
        self.assertEqual(shortfall, [])
        self.assertEqual(
            int(out["QUANTITYCASE"].sum()), 20,
            "ยอดหีบที่ส่งต้องเท่ากับผลกระจายรวม",
        )
        self.assertEqual(sorted(out["SALESMANCODE"].unique().tolist()), ["E1", "E2"])

    def test_the_other_teams_employee_keeps_their_own_area(self):
        """ต้องใช้เขตของ E2 เอง ไม่ใช่ลอกของ E1 มา"""
        out, _d, _p, _s = lh._build_tga_upload_dataframe(
            self._req(allow_new_targetsun_rows=True), drop_incomplete_rows=True
        )
        e2 = out[out["SALESMANCODE"] == "E2"]
        self.assertEqual(len(e2), 1)
        self.assertEqual(e2["AREACODE"].iloc[0], "20")
        self.assertEqual(e2["PROVINCECODE"].iloc[0], "P9")

    def test_the_upsert_key_stays_unique(self):
        out, _d, _p, _s = lh._build_tga_upload_dataframe(
            self._req(allow_new_targetsun_rows=True), drop_incomplete_rows=True
        )
        key = ["PRODUCTCODE", "SALESTYPE", "DIVISIONCODE", "SALESMANCODE",
               "AREACODE", "PROVINCECODE"]
        self.assertEqual(int(out.duplicated(subset=key).sum()), 0)


if __name__ == "__main__":
    unittest.main()
