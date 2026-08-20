"""
บันทึกการใช้งานต้องละเอียดพอที่จะ "กู้" ได้ ไม่ใช่แค่รู้ว่ามีอะไรเกิดขึ้น

คำถามที่ต้องตอบได้จาก log อย่างเดียว: ใครแตะเป้างวดไหน · ค่าก่อนเป็นเท่าไร · ตอนกี่โมง
(เวลาไทย) · ลบอะไรไปแล้วเมื่อกี้มีอะไรอยู่
"""

from __future__ import annotations

import inspect
import json
import os
import sys
import tempfile
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import usage_log_store  # noqa: E402


class _TmpLogs(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._prev = os.environ.get("USAGE_LOGS_DIR")
        os.environ["USAGE_LOGS_DIR"] = self._tmp.name

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("USAGE_LOGS_DIR", None)
        else:
            os.environ["USAGE_LOGS_DIR"] = self._prev
        self._tmp.cleanup()

    def _seed(self, date_str: str, rows: list[dict]):
        path = os.path.join(self._tmp.name, f"usage_{date_str}.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")


class TestPeriodFields(_TmpLogs):
    def test_period_is_recorded_when_given(self):
        """ไม่มีงวดใน log = ตามรอยไม่ได้ว่าใครแตะเป้างวดไหน"""
        row = usage_log_store.append_log(
            action="save_allocation_ok", target_month=9, target_year=2026
        )
        self.assertEqual(row["target_month"], 9)
        self.assertEqual(row["target_year"], 2026)

    def test_period_is_omitted_when_not_given(self):
        """เหตุการณ์ที่ไม่ผูกกับงวด (แก้สิทธิ์) ไม่ควรมีฟิลด์งวดปลอม ๆ ให้เข้าใจผิด"""
        row = usage_log_store.append_log(action="user_access_update")
        self.assertNotIn("target_month", row)
        self.assertNotIn("target_year", row)

    def test_context_is_stored_as_structured_data(self):
        """`detail` ไว้ให้คนอ่าน · `context` ไว้ให้เครื่องเทียบค่าก่อน/หลัง — คนละงาน"""
        row = usage_log_store.append_log(
            action="save_allocation_ok",
            detail="version 3 → 4",
            context={"boxes_before": 1548, "boxes_after": 1348},
        )
        self.assertEqual(row["context"]["boxes_before"], 1548)
        self.assertIsInstance(row["detail"], str)

    def test_unserializable_context_does_not_lose_the_whole_line(self):
        """json.dumps ล้มแล้วทั้งบรรทัดจะหาย — ยอมเก็บเป็นข้อความดีกว่าไม่มีร่องรอยเลย"""
        row = usage_log_store.append_log(action="x", context={"bad": {1, 2, 3}})
        self.assertIn("_unserializable", row["context"])
        found = usage_log_store.read_logs(scan_all=True, limit=10)
        self.assertTrue(any(r.get("action") == "x" for r in found))


class TestYearOnlyFilter(_TmpLogs):
    """เดิมระบุปีอย่างเดียวจะคืน 'เฉพาะวันนี้' เงียบ ๆ แอดมินเข้าใจว่าไม่มีเหตุการณ์"""

    def setUp(self):
        super().setUp()
        self._seed("2026-03-14", [{"ts": "2026-03-14T01:00:00Z", "action": "a", "level": "info"}])
        self._seed("2026-09-02", [{"ts": "2026-09-02T01:00:00Z", "action": "b", "level": "info"}])
        self._seed("2025-09-05", [{"ts": "2025-09-05T01:00:00Z", "action": "c", "level": "info"}])

    def test_year_only_returns_the_whole_year(self):
        acts = {r["action"] for r in usage_log_store.read_logs(target_year=2026, limit=0)}
        self.assertEqual(acts, {"a", "b"}, "ต้องได้ทุกเดือนของปีนั้น ไม่ใช่แค่วันนี้")

    def test_year_only_excludes_other_years(self):
        acts = {r["action"] for r in usage_log_store.read_logs(target_year=2025, limit=0)}
        self.assertEqual(acts, {"c"})

    def test_month_only_spans_every_year(self):
        acts = {r["action"] for r in usage_log_store.read_logs(target_month=9, limit=0)}
        self.assertEqual(acts, {"b", "c"})

    def test_year_and_month_together_still_narrow(self):
        acts = {r["action"] for r in usage_log_store.read_logs(target_year=2026, target_month=9, limit=0)}
        self.assertEqual(acts, {"b"})

    def test_missing_year_returns_nothing_not_everything(self):
        self.assertEqual(usage_log_store.read_logs(target_year=1999, limit=0), [])


class TestSaveAndDeleteAreLogged(unittest.TestCase):
    """เดิม log เฉพาะตอนล้มเหลว — จึงไม่มีทางรู้ว่าใครทับ/ลบผลกระจายของใคร"""

    def test_successful_save_is_logged_with_before_and_after(self):
        from backend.routers import data as data_router

        src = inspect.getsource(data_router.put_allocation_snapshot)
        self.assertIn("save_allocation_ok", src)
        self.assertIn("version_before", src)
        self.assertIn("boxes_before", src)
        self.assertIn("target_month=body.target_month", src)

    def test_save_reads_the_previous_snapshot_before_writing(self):
        """ต้องอ่านของเดิม 'ก่อน' เขียน ไม่งั้นค่าก่อน/หลังจะเป็นค่าเดียวกันทั้งคู่"""
        src = inspect.getsource(
            __import__("backend.routers.data", fromlist=["x"]).put_allocation_snapshot
        )
        i_prev = src.index("prev = read_snapshot(")
        i_write = src.index("write_snapshot(payload")
        self.assertLess(i_prev, i_write)

    def test_user_delete_is_logged(self):
        from backend.routers import data as data_router

        src = inspect.getsource(data_router.delete_allocation_snapshot)
        self.assertIn("delete_allocation", src)
        self.assertIn("rows_deleted", src)
        self.assertIn('level="warn"', src)

    def test_admin_delete_is_logged(self):
        """ลบแล้วหายถาวร — ไม่มี log = ไม่มีอะไรเหลือให้บอกว่าเมื่อกี้มีอะไรอยู่"""
        from backend.routers import admin as admin_router

        src = inspect.getsource(admin_router.admin_delete_allocation)
        self.assertIn("admin_delete_allocation", src)
        self.assertIn("boxes_deleted", src)

    def test_both_delete_paths_read_the_snapshot_before_deleting(self):
        from backend.routers import admin as admin_router
        from backend.routers import data as data_router

        for fn in (admin_router.admin_delete_allocation, data_router.delete_allocation_snapshot):
            src = inspect.getsource(fn)
            self.assertLess(
                src.index("read_snapshot("),
                src.index("delete_snapshot("),
                f"{fn.__name__} ต้องอ่านของเดิมก่อนลบ",
            )


class TestAuditScope(unittest.TestCase):
    def test_audit_can_carry_a_team(self):
        """เดิมบังคับ sup_id='' ทุกครั้ง ผู้ดูแลที่มีขอบเขตจึงมองไม่เห็น audit ของตัวเอง"""
        from backend.routers import admin as admin_router

        sig = inspect.signature(admin_router._audit_admin)
        for name in ("sup_id", "target_month", "target_year", "context"):
            self.assertIn(name, sig.parameters)

    def test_team_scoped_audits_pass_their_team(self):
        from backend.routers import admin as admin_router

        for fn in (
            admin_router.admin_restore_target_baseline,
            admin_router.admin_set_no_target_employees,
            admin_router.admin_delete_allocation,
        ):
            self.assertIn("sup_id=", inspect.getsource(fn), fn.__name__)

    def test_admin_always_sees_their_own_actions(self):
        from backend.routers import admin as admin_router

        src = inspect.getsource(admin_router._filter_usage_items_for_admin)
        self.assertIn('emails.add(normalized_email(admin.get("email")))', src)


class TestExportColumns(unittest.TestCase):
    def test_excel_carries_period_context_and_ids(self):
        from backend.routers import admin as admin_router

        src = inspect.getsource(admin_router.admin_export_usage_logs_xlsx)
        for col in ('"period"', '"context_str"', '"request_id"', '"entry_id"'):
            self.assertIn(col, src)


class TestFrontendTime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO, "frontend", "app.js"), encoding="utf-8") as f:
            cls.src = f.read()

    def test_screen_converts_to_bangkok_like_the_excel_does(self):
        """
        เดิมหน้าจอตัด T/Z ทิ้งแล้วโชว์ UTC ดิบ ส่วน Excel แปลงเป็นเวลาไทย

        คนที่เทียบสองที่จึงเห็นเวลาต่างกัน 7 ชั่วโมง — ตอนไล่ว่า "ใครแก้ตอนกี่โมง"
        นั่นคือคนละคำตอบกันเลย
        """
        i = self.src.index("function _fmtLogTimeBangkok(")
        body = self.src[i:i + 900]
        self.assertIn("Asia/Bangkok", body)

        i2 = self.src.index("async function adminLoadUsageLogs(")
        rows = self.src[i2:i2 + 3000]
        self.assertIn("_fmtLogTimeBangkok(r.ts)", rows)

    def test_detail_box_shows_role_and_request_id(self):
        i = self.src.index("function _adminLogDetailText(")
        body = self.src[i:i + 1500]
        for need in ("r.role", "request_id", "r.context", "target_month"):
            self.assertIn(need, body)

    def test_period_is_visible_in_the_table_not_only_in_excel(self):
        i = self.src.index("async function adminLoadUsageLogs(")
        body = self.src[i:i + 3000]
        self.assertIn("log-period", body)


if __name__ == "__main__":
    unittest.main()
