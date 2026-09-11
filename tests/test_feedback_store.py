"""
กล่องข้อเสนอแนะจากผู้ใช้ — ข้อความหายไม่ได้ และไฟล์พังต้องไม่ทำให้ใครทำงานไม่ได้

ช่องทางนี้เป็นทางเดียวที่ซุปบอกเราได้ตอนเจอปัญหาจริง (ก่อนหน้านี้มีแต่แบบสำรวจ
ที่ทำนอกแอปปีละครั้ง) ถ้าข้อความหายเงียบ ๆ จะไม่มีใครรู้เลยว่าหาย
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import feedback_store as store  # noqa: E402


class FeedbackStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="feedback_")
        self._prev = os.environ.get("FEEDBACK_DIR")
        os.environ["FEEDBACK_DIR"] = self._tmpdir

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("FEEDBACK_DIR", None)
        else:
            os.environ["FEEDBACK_DIR"] = self._prev
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _add(self, **kw):
        kw.setdefault("email", "sup@spc.co.th")
        kw.setdefault("message", "ตารางโหลดช้ามากตอนเช้า")
        return store.append_entry(**kw)

    # ── รูปร่างของรายการ ────────────────────────────────────────────────

    def test_a_submitted_message_comes_back_with_everything_we_need_to_reply(self):
        row = self._add(category="problem", sup_id="sl359", target_month=10, target_year=2026)
        self.assertTrue(row["id"])
        self.assertEqual(row["status"], "new")
        self.assertEqual(row["sup_id"], "SL359", "รหัสทีมต้องเก็บเป็นตัวพิมพ์ใหญ่เสมอ")
        self.assertEqual(row["email"], "sup@spc.co.th")
        self.assertEqual(row["rev"], 0)
        self.assertEqual(store.read_items()[0]["id"], row["id"])

    def test_an_unknown_category_becomes_a_problem_report_not_a_crash(self):
        """หน้าเว็บรุ่นเก่าส่งค่าที่เราเลิกใช้แล้ว ต้องไม่ทำให้ข้อความหาย"""
        row = self._add(category="ระเบิด")
        self.assertEqual(row["category"], "problem")

    def test_newest_first_and_filters_work(self):
        self._add(category="problem", message="ปัญหา ก")
        self._add(category="request", message="ขอปุ่มใหม่")
        items = store.read_items()
        self.assertEqual(items[0]["message"], "ขอปุ่มใหม่")
        self.assertEqual([r["message"] for r in store.read_items(category="problem")], ["ปัญหา ก"])
        self.assertEqual(store.counts_by_status()["new"], 2)

    # ── เพดานและการกันยิงรัว ────────────────────────────────────────────

    def test_a_very_long_message_is_trimmed_not_rejected(self):
        row = self._add(message="ก" * (store.MAX_MESSAGE_CHARS + 500))
        self.assertEqual(len(row["message"]), store.MAX_MESSAGE_CHARS)

    def test_same_person_spamming_is_stopped_but_other_people_are_not(self):
        for _ in range(store.RATE_LIMIT_PER_EMAIL):
            self._add()
        with self.assertRaises(store.FeedbackRateLimited):
            self._add()
        other = self._add(email="another@spc.co.th")
        self.assertTrue(other["id"], "คนอื่นต้องยังส่งได้")

    def test_old_messages_do_not_count_towards_the_rate_limit(self):
        doc = {"version": 1, "items": []}
        old_ts = (datetime.now(timezone.utc) - timedelta(seconds=store.RATE_LIMIT_WINDOW_SEC + 60))
        for i in range(store.RATE_LIMIT_PER_EMAIL):
            doc["items"].append({
                "id": f"old{i}", "ts": old_ts.isoformat().replace("+00:00", "Z"),
                "email": "sup@spc.co.th", "status": "new", "message": "เก่า", "rev": 0,
            })
        os.makedirs(self._tmpdir, exist_ok=True)
        with open(store.feedback_json_path(), "w", encoding="utf-8") as f:
            json.dump(doc, f)
        self.assertTrue(self._add()["id"])

    def test_trimming_drops_handled_messages_before_unread_ones(self):
        """เต็มแล้วต้องตัดของที่จัดการแล้วก่อน — ข้อความที่ยังไม่มีใครอ่านห้ามหายก่อน"""
        items = []
        for i in range(store.MAX_RECORDS):
            items.append({
                "id": f"done{i}", "ts": f"2026-01-01T00:{i % 60:02d}:00Z",
                "email": "x@spc.co.th", "status": "done", "message": "เก่า", "rev": 0,
            })
        items[0]["status"] = "new"
        items[0]["id"] = "keep-me"
        os.makedirs(self._tmpdir, exist_ok=True)
        with open(store.feedback_json_path(), "w", encoding="utf-8") as f:
            json.dump({"version": 1, "items": items}, f)

        self._add(email="fresh@spc.co.th", message="ข้อความใหม่ล่าสุด")

        ids = {r["id"] for r in store.read_doc()["items"]}
        self.assertIn("keep-me", ids, "รายการที่ยังไม่ได้อ่านต้องไม่ถูกตัดทิ้งก่อน")
        self.assertLessEqual(len(ids), store.MAX_RECORDS)

    # ── สถานะ ───────────────────────────────────────────────────────────

    def test_status_can_move_forward_and_back(self):
        row = self._add()
        done = store.set_status(row["id"], status="done", handled_by="boss@spc.co.th")
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["handled_by"], "boss@spc.co.th")
        self.assertEqual(done["rev"], 1)
        back = store.set_status(row["id"], status="new")
        self.assertEqual(back["status"], "new", "กดผิดแล้วต้องถอยกลับได้")

    def test_two_admins_on_the_same_item_do_not_overwrite_each_other(self):
        row = self._add()
        store.set_status(row["id"], status="read", expected_rev=0)
        with self.assertRaises(store.FeedbackConflict):
            store.set_status(row["id"], status="done", expected_rev=0)

    def test_two_admins_on_different_items_never_conflict(self):
        a = self._add(message="ข้อความ ก")
        b = self._add(email="b@spc.co.th", message="ข้อความ ข")
        store.set_status(a["id"], status="read", expected_rev=0)
        store.set_status(b["id"], status="done", expected_rev=0)
        self.assertEqual(store.counts_by_status()["read"], 1)
        self.assertEqual(store.counts_by_status()["done"], 1)

    def test_unknown_id_is_a_clear_error_not_a_silent_no_op(self):
        with self.assertRaises(ValueError):
            store.set_status("ไม่มีจริง", status="done")

    # ── ความทนทาน ───────────────────────────────────────────────────────

    def test_a_broken_file_does_not_stop_anyone_from_working(self):
        os.makedirs(self._tmpdir, exist_ok=True)
        with open(store.feedback_json_path(), "w", encoding="utf-8") as f:
            f.write("{ นี่ไม่ใช่ json")
        self.assertEqual(store.read_items(), [])
        self.assertTrue(self._add()["id"], "ไฟล์พังแล้วต้องยังส่งข้อความใหม่ได้")

    def test_messages_sent_at_the_same_time_are_all_kept(self):
        errors: list[Exception] = []

        def send(i: int):
            try:
                store.append_entry(email=f"u{i}@spc.co.th", message=f"ข้อความที่ {i}")
            except Exception as e:  # pragma: no cover - จะ fail ที่ assert ข้างล่าง
                errors.append(e)

        threads = [threading.Thread(target=send, args=(i,)) for i in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(store.read_doc()["items"]), 12, "ส่งพร้อมกันแล้วต้องไม่มีข้อความหาย")


if __name__ == "__main__":
    unittest.main()
