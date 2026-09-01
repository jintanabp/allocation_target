"""
บันทึกการส่ง Target Sun ต้องตอบได้ว่า "งวดไหน ส่งให้ใครบ้าง"

สองช่องว่างที่ปิดที่นี่:
  1. _log_targetsun_send ไม่เคยส่ง target_month/target_year เข้า log เลย
     ทุกแถว send_targetsun จึงไม่มีงวดเป้าติดมา แล้วการกรองงวดในหน้าแอดมิน
     กลายเป็นกรองตามวันที่ของไฟล์ log (วันที่กด) ไม่ใช่งวดที่ส่ง
  2. รายชื่อพนักงานที่ส่งจริงถูกคำนวณอยู่แล้ว แต่หายไปพร้อม bundle ที่ถูกลบ
     ไม่มีที่ไหนตอบได้ว่างวดนี้ส่งเป้าให้พนักงานกี่คน

ข้อความ detail เดิม ("งวด YYYY-MM · …") ต้องคงรูปเดิมเป๊ะ — เป็น fallback
ของทุกแถวที่เขียนลงดิสก์ไปแล้วก่อนแก้

เทสต์นี้เรียกฟังก์ชันบันทึก log ตรง ๆ ไม่ผ่าน endpoint และไม่แตะเน็ตเลย
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.routers import lakehouse  # noqa: E402
from backend.schemas import LakehouseUploadRequest  # noqa: E402
from backend.services import targetsun_import as tsi  # noqa: E402
from backend.services import usage_log_store as uls  # noqa: E402

USER = {"email": "sup@x.com", "home_supervisor_codes": ["SL397"]}


def _req(month=9, year=2026, sup="SL397"):
    return LakehouseUploadRequest(sup_id=sup, target_month=month, target_year=year)


def _result(ok=True, emp_codes=("S402", "S420"), rows=12):
    return {
        "rows_sent": rows,
        "emp_codes": list(emp_codes),
        "targetsun": {"success": ok, "result": {"inserted": 5, "updated": 7, "skipped": 0}},
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

    def _rows(self):
        out = []
        for name in sorted(os.listdir(self._tmp.name)):
            with open(os.path.join(self._tmp.name, name), encoding="utf-8") as fh:
                out += [json.loads(ln) for ln in fh if ln.strip()]
        return out

    def _only(self):
        rows = self._rows()
        self.assertEqual(len(rows), 1, rows)
        return rows[0]


class TestSendLogRecordsPeriodAndPeople(_LogBase):
    def test_target_period_is_written(self):
        lakehouse._log_targetsun_send(USER, _req(9, 2026), _result())
        row = self._only()
        self.assertEqual(row["target_month"], 9)
        self.assertEqual(row["target_year"], 2026)

    def test_employee_ids_are_written(self):
        lakehouse._log_targetsun_send(USER, _req(), _result(emp_codes=("S402", "S420", "S454")))
        ctx = self._only()["context"]
        self.assertEqual(ctx["emp_count"], 3)
        self.assertEqual(ctx["emp_ids"], ["S402", "S420", "S454"])
        self.assertFalse(ctx["emp_ids_truncated"])

    def test_send_counts_are_written(self):
        lakehouse._log_targetsun_send(USER, _req(), _result(rows=12))
        ctx = self._only()["context"]
        self.assertEqual(ctx["rows_sent"], 12)
        self.assertEqual(ctx["inserted"], 5)
        self.assertEqual(ctx["updated"], 7)
        self.assertTrue(ctx["ok"])

    def test_long_team_is_truncated_with_a_flag(self):
        """ทีมที่ยาวเกินเพดานต้องบอกว่าถูกตัด รายงานจะได้ถอยไปใช้ค่าประมาณ"""
        many = [f"S{i:04d}" for i in range(lakehouse._MAX_LOGGED_EMP_IDS + 25)]
        lakehouse._log_targetsun_send(USER, _req(), _result(emp_codes=many))
        ctx = self._only()["context"]
        self.assertEqual(ctx["emp_count"], len(many))
        self.assertEqual(len(ctx["emp_ids"]), lakehouse._MAX_LOGGED_EMP_IDS)
        self.assertTrue(ctx["emp_ids_truncated"])

    def test_detail_string_keeps_its_old_shape(self):
        """แถวเก่าบนดิสก์ไม่มีฟิลด์งวด — รายงานต้องยังแกะจาก detail ได้ตลอดไป"""
        lakehouse._log_targetsun_send(USER, _req(9, 2026), _result())
        detail = self._only()["detail"]
        self.assertIsNotNone(re.search(r"งวด (\d{4})-(\d{2})", detail))
        self.assertTrue(detail.startswith("งวด 2026-09 · ส่ง 12 แถว"))

    def test_failed_send_still_records_period(self):
        lakehouse._log_targetsun_send(USER, _req(8, 2026), _result(ok=False))
        row = self._only()
        self.assertEqual(row["level"], "error")
        self.assertEqual(row["target_month"], 8)
        self.assertFalse(row["context"]["ok"])

    def test_readback_mismatch_is_flagged_as_error(self):
        res = _result()
        res["readback"] = {"checked": True, "ok": False, "diff_count": 2, "diff_boxes": -5}
        lakehouse._log_targetsun_send(USER, _req(), res)
        row = self._only()
        self.assertEqual(row["level"], "error")
        self.assertFalse(row["context"]["readback_ok"])


class TestLoggingNeverBreaksTheSend(_LogBase):
    """สัญญาเดิมของฟังก์ชันนี้: log พังยังไงก็ต้องไม่ทำให้การส่งพัง"""

    def test_swallows_every_broken_result(self):
        for bad in (None, {}, "boom", [], {"targetsun": "not-a-dict"}):
            lakehouse._log_targetsun_send(USER, _req(), bad)   # ต้องไม่ raise

    def test_swallows_missing_user(self):
        lakehouse._log_targetsun_send(None, _req(), _result())


class TestAttachReadbackCarriesEmpCodes(unittest.TestCase):
    """รายชื่อต้องรอดออกมาทั้งทางที่ส่งสำเร็จและทางที่ส่งไม่สำเร็จ"""

    def test_failed_send_path(self):
        out = tsi._attach_readback(
            {"targetsun": {"success": False}},
            sup_id="SL397", month=9, year=2026,
            sku_totals={}, emp_codes=["S402", " S420 ", ""],
        )
        self.assertEqual(out["emp_codes"], ["S402", "S420"])
        self.assertEqual(out["readback"]["reason"], "send_failed")

    def test_success_path(self):
        orig = tsi.verify_after_send
        tsi.verify_after_send = lambda *a, **k: {"checked": True, "ok": True}
        try:
            out = tsi._attach_readback(
                {"targetsun": {"success": True}},
                sup_id="SL397", month=9, year=2026,
                sku_totals={}, emp_codes=["S402"],
            )
        finally:
            tsi.verify_after_send = orig
        self.assertEqual(out["emp_codes"], ["S402"])
        self.assertTrue(out["readback"]["ok"])


class TestUsageLogStoreAcceptsIt(unittest.TestCase):
    def test_context_and_period_survive_a_round_trip(self):
        tmp = tempfile.TemporaryDirectory()
        old = os.environ.get("USAGE_LOGS_DIR")
        os.environ["USAGE_LOGS_DIR"] = tmp.name
        try:
            uls.append_log(
                action="send_targetsun", sup_id="SL397",
                target_month=9, target_year=2026,
                context={"emp_ids": ["S402"], "emp_count": 1},
            )
            rows = uls.read_logs(action="send_targetsun", scan_all=True)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["target_month"], 9)
            self.assertEqual(rows[0]["context"]["emp_ids"], ["S402"])
        finally:
            if old is None:
                os.environ.pop("USAGE_LOGS_DIR", None)
            else:
                os.environ["USAGE_LOGS_DIR"] = old
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
