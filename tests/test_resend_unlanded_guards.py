"""
「ส่งแถวที่ไม่ลง」ต้องผ่านด่านเดียวกับการส่งปกติ (ผลตรวจ 5 ต.ค. 2026 ข้อ 3)

รอบ 1 ต.ค. (ก4) เพิ่มแค่ "ต้องเป็นรอบล่าสุด + ปลายทางเดิม" · ที่ยังขาด:
  - คู่ที่ "ไม่ลง" มีแถวใน Target Sun คนละคลังอยู่แล้ว → ส่งซ้ำ = แถวที่สอง เป้าเบิ้ล (แบบ SL380)
  - รอบที่ส่งไม่สำเร็จ → ทุกแถวดูเหมือนไม่ลง ส่งซ้ำ = ส่งทั้งไฟล์ใหม่โดยไม่ผ่านด่าน
  - เป้าเปลี่ยนหลังส่ง → ไฟล์เก่าไม่เท่าเป้าใหม่ (ผิดกติกา เป้ารับเข้า = ส่งออก)

ทุก I/O ถูก mock — ไม่ยิงเน็ต ไม่แตะ data/ จริง
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from fastapi import HTTPException  # noqa: E402

import backend.services.targetsun_import as ti  # noqa: E402


def _row(wh: str, qty: int, emp: str = "E1", sku: str = "P1") -> dict:
    return dict(PRODUCTCODE=sku, SALESTYPE="1", DIVISIONCODE="D", SALESMANCODE=emp, AREACODE="A",
                PROVINCECODE="10", WAREHOUSECODE=wh, QUANTITYCASE=str(qty), EFFECTIVEDATE="2026-11-01",
                UPDATEDATE="", USERCODE="SL1")


def _key(wh: str, emp: str = "E1", sku: str = "P1") -> str:
    return f"{sku}|{emp}|1|D|A|10|{wh}"


class TestResendGuards(unittest.TestCase):
    def _run(self, rows, live_qty, *, status="ok", target=None):
        rec = {"sup_id": "SL1", "target_month": 11, "target_year": 2026, "rows": rows,
               "import_url": "https://x/import", "sent_at": 1, "token": "t", "send_status": status}
        posted: list = []

        def fake_post(content, fname, **kw):
            posted.append(kw.get("nrow"))
            return {"send_status": "ok", "targetsun": {"success": True}}

        live = {"qty_by_key": live_qty}
        with patch.object(ti, "load_sent_record", return_value=rec), \
             patch.object(ti, "_current_import_url", return_value="https://x/import"), \
             patch.object(ti, "_newer_send_exists", return_value=False), \
             patch("backend.services.targetsun_endpoints.targetsun_endpoints_summary",
                   return_value={"cross_env": "0"}), \
             patch("backend.services.lakehouse._sup_target_boxes_by_sku", return_value=target), \
             patch.object(ti, "_live_target_snapshot", return_value=live), \
             patch.object(ti, "_claim_team_send", return_value="k"), \
             patch.object(ti, "_release_team_send"), \
             patch.object(ti, "_post_targetsun_multipart", side_effect=fake_post), \
             patch.object(ti.sent_ledger, "record_send", return_value=True):
            out = ti.resend_unlanded_rows("SL1", "t")
        return out, posted

    def test_same_pair_in_another_warehouse_is_refused(self):
        with self.assertRaises(HTTPException) as cm:
            self._run([_row("W1", 10)], {_key("W2"): 10})
        self.assertEqual(cm.exception.status_code, 409)
        self.assertEqual(cm.exception.detail["code"], "send_warehouse_conflict")
        self.assertIn("เบิ้ล", cm.exception.detail["message"])

    def test_failed_round_is_refused(self):
        with self.assertRaises(HTTPException) as cm:
            self._run([_row("W1", 10)], {}, status="failed")
        self.assertIn("ไม่สำเร็จ", cm.exception.detail)

    def test_target_changed_after_send_is_refused(self):
        with self.assertRaises(HTTPException) as cm:
            self._run([_row("W1", 10)], {}, target={"P1": 12})
        self.assertEqual(cm.exception.detail["code"], "RESEND_TARGET_CHANGED")

    def test_plain_missing_row_is_still_resent(self):
        """แถวที่หายจริง (ไม่มีคู่นี้ในคลังอื่น) ยังส่งซ้ำได้ — ด่านใหม่ต้องไม่ปิดปุ่มทิ้ง"""
        rows = [_row("W1", 6), _row("W2", 4)]
        out, posted = self._run(rows, {_key("W1"): 6}, target={"P1": 10})
        self.assertEqual(posted, [1])
        self.assertEqual(out["resent_rows"], 1)

    def test_nothing_missing_posts_nothing(self):
        out, posted = self._run([_row("W1", 10)], {_key("W1"): 10}, target={"P1": 10})
        self.assertEqual(posted, [])
        self.assertEqual(out["resent_rows"], 0)


if __name__ == "__main__":
    unittest.main()
