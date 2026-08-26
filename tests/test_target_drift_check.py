"""
เป้าใน Target Sun เปลี่ยนระหว่างที่เปิดหน้ารวมภาคค้างไว้ — ต้องรู้ก่อนกดส่ง

คนที่เกลี่ยเป้าทั้งภาคเปิดหน้าค้างทีละหลายชั่วโมง ระหว่างนั้นฝั่ง Target Sun
อัปเดตเป้าได้ตลอด · ของเดิมรู้ได้สองทางและสายเกินไปทั้งคู่ — ตอนกด "คำนวณ"
(เทียบ snapshot ในเบราว์เซอร์ ซึ่งเป็นของรอบกระจายก่อน ไม่ใช่ของจริง) กับตอนกด
"ส่ง" (409) ซึ่งกว่าจะรู้ก็เกลี่ยหีบข้ามซุปไปหมดแล้ว

ตัวตรวจนี้อ่านอย่างเดียว ไม่เขียนอะไรกลับ และห้ามพังเมื่ออ่าน Target Sun ไม่ได้
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

from backend.services import lakehouse as lh  # noqa: E402

logging.disable(logging.CRITICAL)

MONTH, YEAR = 9, 2026


class TestTargetDriftForSups(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        self._tmpdir = tempfile.mkdtemp(prefix="drift_")
        os.chdir(self._tmpdir)
        os.makedirs("data", exist_ok=True)

    def tearDown(self):
        os.chdir(self._cwd)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _team(self, sup: str, boxes_by_sku: dict[str, int]) -> None:
        pd.DataFrame([
            {"sku": k, "supervisor_target_boxes": v, "price_per_box": 1.0}
            for k, v in boxes_by_sku.items()
        ]).to_csv(f"data/target_boxes_{sup}_{YEAR}_{MONTH:02d}.csv", index=False)
        pd.DataFrame([{
            "emp_id": "E1", "sku": next(iter(boxes_by_sku)), "qty": 1,
            "salestype": "S", "divisioncode": "B", "areacode": "10",
            "provincecode": "", "warehouse_code": "",
        }]).to_csv(f"data/tga_lines_{sup}_{YEAR}_{MONTH:02d}.csv", index=False)

    def test_reports_what_changed_per_team(self):
        self._team("SL527", {"A": 100, "B": 50})
        self._team("SL531", {"A": 20})
        live = {"SL527": {"A": 120, "B": 50}, "SL531": {"A": 20}}
        with patch.object(lh, "_live_target_boxes_by_sku",
                          side_effect=lambda sid, m, y, c: live.get(sid)):
            out = lh.target_drift_for_sups(["SL527", "SL531"], MONTH, YEAR)
        self.assertEqual(out["drift_count"], 1)
        self.assertEqual(out["drift_boxes"], 20)
        self.assertEqual(out["by_sup"]["SL527"], {"sku_count": 1, "diff_boxes": 20})
        self.assertNotIn("SL531", out["by_sup"], "ทีมที่ไม่เปลี่ยนต้องไม่ขึ้นมา")
        self.assertEqual(out["changed_skus"], ["A"])

    def test_no_change_is_reported_as_no_change(self):
        self._team("SL527", {"A": 100})
        with patch.object(lh, "_live_target_boxes_by_sku",
                          side_effect=lambda sid, m, y, c: {"A": 100}):
            out = lh.target_drift_for_sups(["SL527"], MONTH, YEAR)
        self.assertEqual(out["drift_count"], 0)
        self.assertEqual(out["drifted"], [])
        self.assertEqual(out["checked_sup_ids"], ["SL527"])

    def test_a_new_sku_in_target_sun_shows_up(self):
        """สินค้าที่เพิ่งมีเป้าเพิ่มเข้ามา ต้องเห็น ไม่ใช่เงียบ"""
        self._team("SL527", {"A": 100})
        with patch.object(lh, "_live_target_boxes_by_sku",
                          side_effect=lambda sid, m, y, c: {"A": 100, "Z": 30}):
            out = lh.target_drift_for_sups(["SL527"], MONTH, YEAR)
        self.assertEqual(out["drift_count"], 1)
        row = out["drifted"][0]
        self.assertEqual((row["sku"], row["loaded_boxes"], row["current_boxes"]), ("Z", 0, 30))

    def test_unreadable_team_is_listed_not_fatal(self):
        """Target Sun อ่านไม่ได้ = บอกว่าตรวจไม่ได้ ห้ามทำให้ทั้งหน้าพัง"""
        self._team("SL527", {"A": 100})
        self._team("SL531", {"A": 20})
        with patch.object(lh, "_live_target_boxes_by_sku",
                          side_effect=lambda sid, m, y, c: None if sid == "SL531" else {"A": 100}):
            out = lh.target_drift_for_sups(["SL527", "SL531"], MONTH, YEAR)
        self.assertEqual(out["checked_sup_ids"], ["SL527"])
        self.assertEqual([u["sup_id"] for u in out["unavailable"]], ["SL531"])

    def test_an_exception_from_target_sun_does_not_escape(self):
        self._team("SL527", {"A": 100})

        def boom(*a, **kw):
            raise RuntimeError("timeout")

        with patch.object(lh, "_live_target_boxes_by_sku", side_effect=boom):
            out = lh.target_drift_for_sups(["SL527"], MONTH, YEAR)
        self.assertEqual(out["drift_count"], 0)
        self.assertEqual([u["sup_id"] for u in out["unavailable"]], ["SL527"])

    def test_team_without_a_target_file_cannot_be_checked(self):
        out = lh.target_drift_for_sups(["SL999"], MONTH, YEAR)
        self.assertEqual(out["checked_sup_ids"], [])
        self.assertEqual([u["sup_id"] for u in out["unavailable"]], ["SL999"])

    def test_duplicate_sup_ids_are_checked_once(self):
        self._team("SL527", {"A": 100})
        calls = []

        def spy(sid, m, y, c):
            calls.append(sid)
            return {"A": 100}

        with patch.object(lh, "_live_target_boxes_by_sku", side_effect=spy):
            lh.target_drift_for_sups(["SL527", "sl527", " SL527 "], MONTH, YEAR)
        self.assertEqual(calls, ["SL527"], "ห้ามยิง Target Sun ซ้ำรหัสเดิม")


if __name__ == "__main__":
    unittest.main()
