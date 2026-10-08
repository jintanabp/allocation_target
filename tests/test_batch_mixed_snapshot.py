"""
ผลตรวจ 7 ต.ค. 2026 ก2 — ส่งรวมภาคซ้ำด้วยไฟล์เป้าต่างรุ่นกัน ห้ามผ่าน

เป้าเดิม A=60 B=40 (ภาค 100) · ส่งรวมภาคย้ายหีบ → TS: A=70 B=30
ทีม A โหลดขั้นที่ 1 ใหม่ (ไฟล์ 70) ทีม B ยังใช้ไฟล์ก่อนส่ง (40) → ยอดเป้ารวม 110 ผ่านทุกด่านเดิม
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from unittest.mock import patch

import pandas as pd
from fastapi import HTTPException

from backend.services import lakehouse as lh
from backend.services import sent_ledger

D = dict(SALESTYPE="S1", DIVISIONCODE="D1", AREACODE="10", PROVINCECODE="P1", WAREHOUSECODE="")


class TestBatchMixedSnapshot(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data", exist_ok=True)
        self._ep = patch(
            "backend.services.targetsun_endpoints.targetsun_endpoints_summary",
            return_value={"import_url": "U"},
        )
        self._ep.start()
        sent_ledger.record_send("SLA", 8, 2026, [dict(PRODUCTCODE="X", SALESMANCODE="EA", QUANTITYCASE=70, **D)],
                                send_batch_id="b1", import_url="U")
        sent_ledger.record_send("SLB", 8, 2026, [dict(PRODUCTCODE="X", SALESMANCODE="EB", QUANTITYCASE=30, **D)],
                                send_batch_id="b1", import_url="U")
        self.t_send = time.time()

    def tearDown(self):
        self._ep.stop()
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _write(self, sid: str, boxes: int, mtime: float) -> None:
        p = f"data/target_boxes_{sid}_2026_08.csv"
        pd.DataFrame([{"sku": "X", "supervisor_target_boxes": boxes}]).to_csv(p, index=False)
        os.utime(p, (mtime, mtime))

    def _metas(self, a: int, b: int) -> list[dict]:
        return [
            {"sup_id": "SLA", "target_year": 2026, "target_month": 8, "sku_totals": {"X": a},
             "send_batch_id": "b2", "full_send": True},
            {"sup_id": "SLB", "target_year": 2026, "target_month": 8, "sku_totals": {"X": b},
             "send_batch_id": "b2", "full_send": True},
        ]

    def test_reloaded_plus_pre_send_blocked(self):
        self._write("SLA", 70, self.t_send + 60)   # โหลดใหม่หลังส่ง
        self._write("SLB", 40, self.t_send - 600)  # ไฟล์ก่อนส่ง
        with self.assertRaises(HTTPException) as cm:
            lh.verify_send_batch(self._metas(75, 35))
        self.assertEqual(cm.exception.detail["code"], "send_batch_mixed_snapshot")
        self.assertEqual(cm.exception.detail["reloaded_sup_ids"], ["SLA"])
        self.assertEqual(cm.exception.detail["pre_send_sup_ids"], ["SLB"])

    def test_resend_with_all_pre_send_files_passes(self):
        # ส่งรวมภาคซ้ำหลังล้มกลางทาง โดยไม่โหลดใหม่ — ทางที่ข้อยกเว้นตั้งใจให้ผ่าน
        self._write("SLA", 60, self.t_send - 600)
        self._write("SLB", 40, self.t_send - 600)
        out = lh.verify_send_batch(self._metas(70, 30))
        self.assertTrue(out["verified"])

    def test_retry_after_partial_second_round_passes(self):
        """
        รอบ 1 ลงครบ → โหลดใหม่ทั้งคู่ (A=70 B=30) → รอบ 2 A ลง 65 แต่ B ล้ม → ส่งซ้ำโดยไม่โหลดใหม่ต้องผ่าน
        (เดิมเทียบเวลาของแต่ละทีม: B ถูกนับว่า "โหลดหลังส่ง" เพราะเทียบกับรอบ 1 ของตัวเอง → บล็อกผิด)
        """
        t2 = self.t_send + 1000
        with patch.object(sent_ledger.time, "time", return_value=t2):
            sent_ledger.record_send("SLA", 8, 2026, [dict(PRODUCTCODE="X", SALESMANCODE="EA", QUANTITYCASE=65, **D)],
                                    send_batch_id="b2", import_url="U")
        self._write("SLA", 70, self.t_send + 60)
        self._write("SLB", 30, self.t_send + 60)
        out = lh.verify_send_batch(self._metas(65, 35))
        self.assertTrue(out["verified"])

    def test_previous_round_incomplete_flagged(self):
        """รอบล่าสุด B ไม่ลง + A โหลดใหม่หลังรอบนั้น → บล็อก และบอกว่ารอบก่อนลงไม่ครบ (หน้าเว็บซ่อนปุ่มดึงใหม่)"""
        t2 = self.t_send + 1000
        with patch.object(sent_ledger.time, "time", return_value=t2):
            sent_ledger.record_send("SLA", 8, 2026, [dict(PRODUCTCODE="X", SALESMANCODE="EA", QUANTITYCASE=65, **D)],
                                    send_batch_id="b2", import_url="U")
        self._write("SLA", 65, t2 + 60)
        self._write("SLB", 30, self.t_send + 60)
        with self.assertRaises(HTTPException) as cm:
            lh.verify_send_batch(self._metas(65, 35))
        self.assertEqual(cm.exception.detail["code"], "send_batch_mixed_snapshot")
        self.assertFalse(cm.exception.detail["previous_send_complete"])

    def test_all_reloaded_passes(self):
        self._write("SLA", 70, self.t_send + 60)
        self._write("SLB", 30, self.t_send + 60)
        out = lh.verify_send_batch(self._metas(65, 35))
        self.assertTrue(out["verified"])


if __name__ == "__main__":
    unittest.main()
