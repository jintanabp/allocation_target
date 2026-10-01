"""
ส่งไม่ครบแก้ยาก เพราะ Target Sun ทับได้แต่ลบไม่ได้ (ผู้ใช้ขอ 29 ก.ย. 2026)

- กันก่อนส่ง: ผิดแถวเดียวตามกติกาที่ Target Sun ใช้ข้ามแถว = ไม่ส่งทั้งไฟล์
  (ยึดพฤติกรรมจริง: PROVINCECODE ว่างได้ · ไม่ตรวจความยาวรหัส)
- ส่งไปแล้วตกหล่น: หาแถวที่ยังไม่ลง/ลงไม่ตรงจากการอ่านกลับ แล้วส่งซ้ำเฉพาะแถวนั้น
  จากไฟล์ที่ server เก็บไว้ — ไม่ยิง Target Sun จริง (mock ทั้งหมด)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from fastapi import HTTPException  # noqa: E402

from backend.services import lakehouse as lh  # noqa: E402
from backend.services import targetsun_import as ti  # noqa: E402


def _row(sku="734046", emp="C442", wh="R408", qty="3", **over):
    r = {"PRODUCTCODE": sku, "SALESTYPE": "S", "DIVISIONCODE": "B", "SALESMANCODE": emp,
         "AREACODE": "1", "PROVINCECODE": "", "WAREHOUSECODE": wh, "QUANTITYCASE": qty,
         "EFFECTIVEDATE": "1/10/2569", "UPDATEDATE": "", "USERCODE": "SL397"}
    r.update(over)
    return r


class TestImportableCheck(unittest.TestCase):
    def test_real_world_shape_passes(self):
        """PROVINCECODE ว่าง + รหัสพนักงาน 4 ตัว = เหมือนข้อมูลจริงทุกแถว ต้องผ่าน"""
        lh.assert_rows_importable(pd.DataFrame([_row(), _row(sku="111294")]))

    def test_missing_required_field_blocks_the_whole_file(self):
        with self.assertRaises(HTTPException) as ctx:
            lh.assert_rows_importable(pd.DataFrame([_row(), _row(sku="111294", SALESTYPE="")]))
        d = ctx.exception.detail
        self.assertEqual(d["code"], "send_rows_not_importable")
        self.assertEqual(d["rows"][0]["row"], 3)
        self.assertIn("SALESTYPE", d["rows"][0]["reason"])

    def test_duplicate_keys_block(self):
        with self.assertRaises(HTTPException) as ctx:
            lh.assert_rows_importable(pd.DataFrame([_row(), _row(qty="5")]))
        self.assertEqual(ctx.exception.detail["row_count"], 2)

    def test_bad_quantity_blocks(self):
        with self.assertRaises(HTTPException):
            lh.assert_rows_importable(pd.DataFrame([_row(qty="2.5")]))

    def test_send_path_runs_the_check(self):
        import inspect

        src = inspect.getsource(lh._build_tga_upload_dataframe)
        self.assertIn("assert_rows_importable(final, req.sup_id)", src)


class TestUnlandedRows(unittest.TestCase):
    def test_missing_and_wrong_quantity_are_listed(self):
        f = {"A|E1|S|B|1||R1": 3, "B|E1|S|B|1||R1": 2, "C|E1|S|B|1||R1": 0, "D|E1|S|B|1||R1": 4}
        live = {"A|E1|S|B|1||R1": 3, "B|E1|S|B|1||R1": 1}
        out = {u["sku"]: u for u in lh.unlanded_rows(f, live)}
        self.assertEqual(set(out), {"B", "D"}, "C ส่ง 0 ไปคีย์ที่ไม่มี = ไม่ต้องทำอะไร")
        self.assertEqual((out["B"]["sent"], out["B"]["in_targetsun"]), (2, 1))
        self.assertIsNone(out["D"]["in_targetsun"])


class TestResendOnlyMissedRows(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        rows = [_row(sku="A"), _row(sku="B", qty="2"), _row(sku="C", qty="7")]
        os.makedirs("data/ts_sent")
        with open("data/ts_sent/tok12345.json", "w", encoding="utf-8") as fh:
            json.dump({"sup_id": "SL397", "target_month": 10, "target_year": 2026, "rows": rows,
                       "token": "tok12345", "sent_at": 1000.0, "import_url": "https://uat.example.test/import"}, fh)
        self.keys = lh.import_row_key_series(pd.DataFrame(rows)).tolist()

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def test_only_rows_that_did_not_land_are_resent(self):
        before = {"qty_by_key": {self.keys[0]: 3, self.keys[1]: 1}}   # B ผิด C หาย
        after = {"qty_by_key": {self.keys[0]: 3, self.keys[1]: 2, self.keys[2]: 7}}
        sent = {}

        def fake_post(content, fname, **kw):
            import io
            sent["df"] = pd.read_excel(io.BytesIO(content), dtype=str)
            return {"send_status": "ok", "targetsun": {"success": True}}

        with patch.object(ti, "_live_target_snapshot", side_effect=[before, after]), \
                patch.object(ti, "_post_targetsun_multipart", side_effect=fake_post), \
                patch("backend.services.targetsun_endpoints.targetsun_endpoints_summary",
                      return_value={"cross_env": "0", "import_url": "https://uat.example.test/import"}):
            out = ti.resend_unlanded_rows("SL397", "tok12345")
        self.assertEqual(sorted(sent["df"]["PRODUCTCODE"]), ["B", "C"])
        self.assertEqual(out["resent_rows"], 2)
        self.assertEqual(out["remaining_unlanded"], 0)

    def test_nothing_to_resend_does_not_post(self):
        full = {"qty_by_key": {self.keys[0]: 3, self.keys[1]: 2, self.keys[2]: 7}}
        with patch.object(ti, "_live_target_snapshot", return_value=full), \
                patch.object(ti, "_post_targetsun_multipart") as post, \
                patch("backend.services.targetsun_endpoints.targetsun_endpoints_summary",
                      return_value={"cross_env": "0", "import_url": "https://uat.example.test/import"}):
            out = ti.resend_unlanded_rows("SL397", "tok12345")
        post.assert_not_called()
        self.assertEqual(out["resent_rows"], 0)

    def test_other_team_cannot_use_the_record(self):
        with self.assertRaises(HTTPException) as ctx:
            ti.load_sent_record("tok12345", "SL999")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_refused_across_systems(self):
        with patch("backend.services.targetsun_endpoints.targetsun_endpoints_summary",
                   return_value={"cross_env": "1"}):
            with self.assertRaises(HTTPException):
                ti.resend_unlanded_rows("SL397", "tok12345")


if __name__ == "__main__":
    unittest.main()
