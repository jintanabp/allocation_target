"""
SKU ที่ถูกตัดออกทั้งตัวตอนส่ง ต้องมีร่องรอยในบันทึกการใช้งาน

ระบบตัด SKU ทั้งตัวเมื่อมีคู่พนักงาน×สินค้าที่เขียนลง Target Sun ไม่ได้และคู่นั้นมีหีบ > 0
เพื่อไม่ให้เป้าของ SKU นั้นกลายเป็นครึ่ง ๆ กลาง ๆ · ผลคือ Target Sun ยังถือเลขงวดก่อน
ของ SKU เหล่านั้น ไม่ตรงกับที่กระจายไว้

ตรวจบันทึกจริงย้อนหลัง 234 การส่ง (20 ก.ค. – 4 ก.ย. 2026) ไม่มีสักครั้งที่บันทึกเรื่องนี้
ผู้ใช้เห็นบนจอตอนนั้นแล้วก็หายไป ไม่มีทางรู้ว่าเกิดบ่อยแค่ไหนหรือกับ SKU ไหน

และด่านที่ตีกลับตอน "เตรียมไฟล์" ก็ไม่เคยถูกบันทึกเหมือนกัน — 2 ใน 3 ทีมที่รายงานว่า
ส่งไม่สำเร็จ ไม่มีบันทึกการกดส่งเลยสักครั้ง เพราะติดตั้งแต่ขั้นนั้น

เทสต์นี้เรียกฟังก์ชันบันทึกตรง ๆ ไม่ผ่าน endpoint และไม่แตะเน็ตเลย
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

from fastapi import HTTPException

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.routers import lakehouse  # noqa: E402
from backend.schemas import LakehouseUploadRequest  # noqa: E402

USER = {"email": "sup@x.com", "home_supervisor_codes": ["SL397"]}


def _req(month=9, year=2026, sup="SL397"):
    return LakehouseUploadRequest(sup_id=sup, target_month=month, target_year=year)


def _result(shortfall=None, zero_rows=0, ok=True):
    return {
        "rows_sent": 100,
        "zero_rows_sent": zero_rows,
        "rows_not_in_targetsun_count": 3,
        "shortfall": shortfall or [],
        "shortfall_boxes": sum(int(s.get("missing_boxes") or 0) for s in (shortfall or [])),
        "emp_codes": ["S402"],
        "targetsun": {"success": ok, "result": {"inserted": 1, "updated": 99, "skipped": 0}},
        "readback": {"checked": True, "ok": True},
    }


class _LogBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("USAGE_LOGS_DIR")
        os.environ["USAGE_LOGS_DIR"] = self._tmp.name

    def tearDown(self):
        if self._old is None:
            os.environ.pop("USAGE_LOGS_DIR", None)
        else:
            os.environ["USAGE_LOGS_DIR"] = self._old
        self._tmp.cleanup()

    def _only(self):
        rows = []
        for name in sorted(os.listdir(self._tmp.name)):
            with open(os.path.join(self._tmp.name, name), encoding="utf-8") as fh:
                rows += [json.loads(ln) for ln in fh if ln.strip()]
        self.assertEqual(len(rows), 1, rows)
        return rows[0]


class TestExcludedSkuSummary(unittest.TestCase):
    def test_counts_only_whole_sku_exclusions(self):
        out = lakehouse._excluded_sku_summary({
            "shortfall": [
                {"sku": "111336", "excluded_whole_sku": True, "excluded_boxes": 40},
                {"sku": "133975", "excluded_whole_sku": True, "excluded_boxes": 60},
                {"sku": "200774", "missing_boxes": 5},  # ขาดแต่ไม่ได้ตัดทั้งตัว
            ],
            "shortfall_boxes": 5,
        })
        self.assertEqual(out["excluded_sku_count"], 2)
        self.assertEqual(out["excluded_skus"], ["111336", "133975"])
        self.assertEqual(out["excluded_boxes"], 100)
        self.assertEqual(out["shortfall_sku_count"], 3)
        self.assertFalse(out["excluded_skus_truncated"])

    def test_no_shortfall_gives_zeroes(self):
        out = lakehouse._excluded_sku_summary({})
        self.assertEqual(out["excluded_sku_count"], 0)
        self.assertEqual(out["excluded_boxes"], 0)

    def test_long_list_is_truncated_but_count_is_exact(self):
        many = [
            {"sku": f"S{i:05d}", "excluded_whole_sku": True, "excluded_boxes": 1}
            for i in range(150)
        ]
        out = lakehouse._excluded_sku_summary({"shortfall": many})
        self.assertEqual(out["excluded_sku_count"], 150)
        self.assertEqual(len(out["excluded_skus"]), lakehouse._MAX_LOGGED_EXCLUDED_SKUS)
        self.assertTrue(out["excluded_skus_truncated"])
        self.assertEqual(out["excluded_boxes"], 150)


class TestSendLogRecordsExclusions(_LogBase):
    def test_excluded_sku_appears_in_detail_and_context(self):
        sf = [{"sku": "111336", "excluded_whole_sku": True, "excluded_boxes": 40}]
        lakehouse._log_targetsun_send(USER, _req(), _result(shortfall=sf, zero_rows=12))
        row = self._only()
        self.assertIn("ไม่ส่ง 1 SKU ทั้งตัว", row["detail"])
        self.assertIn("40 หีบ", row["detail"])
        self.assertEqual(row["context"]["excluded_sku_count"], 1)
        self.assertEqual(row["context"]["excluded_skus"], ["111336"])
        self.assertEqual(row["context"]["excluded_boxes"], 40)

    def test_zero_rows_sent_is_recorded(self):
        lakehouse._log_targetsun_send(USER, _req(), _result(zero_rows=38))
        row = self._only()
        self.assertIn("หีบ 0 ที่ส่งไปล้างเป้าเดิม 38 แถว", row["detail"])
        self.assertEqual(row["context"]["zero_rows_sent"], 38)

    def test_exclusion_raises_level_from_info_to_warn(self):
        sf = [{"sku": "111336", "excluded_whole_sku": True, "excluded_boxes": 40}]
        lakehouse._log_targetsun_send(USER, _req(), _result(shortfall=sf))
        self.assertEqual(self._only()["level"], "warn")

    def test_clean_send_stays_info_and_says_nothing_about_exclusions(self):
        lakehouse._log_targetsun_send(USER, _req(), _result())
        row = self._only()
        self.assertEqual(row["level"], "info")
        self.assertNotIn("ไม่ส่ง", row["detail"])
        self.assertEqual(row["context"]["excluded_sku_count"], 0)

    def test_failed_send_keeps_error_level_even_with_exclusions(self):
        sf = [{"sku": "111336", "excluded_whole_sku": True, "excluded_boxes": 40}]
        lakehouse._log_targetsun_send(USER, _req(), _result(shortfall=sf, ok=False))
        self.assertEqual(self._only()["level"], "error")

    def test_existing_detail_prefix_is_unchanged(self):
        """แถวเก่าบนดิสก์พึ่งรูป 'งวด YYYY-MM · ส่ง N แถว' เป็น fallback ของงวด"""
        lakehouse._log_targetsun_send(USER, _req(9, 2026), _result())
        self.assertTrue(self._only()["detail"].startswith("งวด 2026-09 · ส่ง 100 แถว · "))


class TestPrepareBlockedIsLogged(_LogBase):
    def test_shortfall_block_is_recorded(self):
        e = HTTPException(
            status_code=409,
            detail={
                "code": "send_target_shortfall",
                "message": "ยังไม่ได้ส่ง — มี 2 SKU ที่ส่งไม่ครบ",
                "excluded_skus": ["111336", "133975"],
                "excluded_sku_count": 2,
                "excluded_boxes": 100,
                "shortfall_boxes": 12,
            },
        )
        lakehouse._log_prepare_blocked(USER, _req(), e)
        row = self._only()
        self.assertEqual(row["action"], "prepare_targetsun_blocked")
        self.assertEqual(row["level"], "warn")
        self.assertEqual(row["target_month"], 9)
        self.assertEqual(row["target_year"], 2026)
        self.assertIn("send_target_shortfall", row["detail"])
        self.assertEqual(row["context"]["excluded_sku_count"], 2)
        self.assertEqual(row["context"]["excluded_boxes"], 100)
        self.assertEqual(row["context"]["status"], 409)

    def test_plain_http_error_still_logged_with_code(self):
        lakehouse._log_prepare_blocked(USER, _req(), HTTPException(status_code=403, detail="ไม่มีสิทธิ์"))
        row = self._only()
        self.assertEqual(row["context"]["code"], "http_403")
        self.assertEqual(row["context"]["excluded_sku_count"], 0)

    def test_logging_failure_never_breaks_the_request(self):
        original = lakehouse.log_from_user
        lakehouse.log_from_user = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full"))
        try:
            lakehouse._log_prepare_blocked(USER, _req(), HTTPException(status_code=409, detail={}))
        finally:
            lakehouse.log_from_user = original


if __name__ == "__main__":
    unittest.main()
