"""
เปิด/ปิดสิทธิ์ส่ง Target Sun แบบยกชุด

สิทธิ์นี้ตัดสินว่าใครส่งข้อมูลจริงเข้า Target Sun ได้ การเปลี่ยนยกชุดจึงต้อง:
  - เปลี่ยนเฉพาะอีเมลที่ระบุ ไม่ไปโดนคนอื่น
  - นับ "เปลี่ยนจริง" ให้ถูก (คนที่สถานะตรงอยู่แล้วไม่นับ) เพราะตัวเลขนี้ไปโชว์ในคำยืนยัน
  - ทำ read-modify-write รอบเดียวใต้ lock เดียว ไม่ใช่วนเรียกทีละคน
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import user_access_store as st  # noqa: E402


def _rows():
    return [
        {"email": "a@x.com", "userpl": "SL330", "can_import_targetsun": False},
        {"email": "b@x.com", "userpl": "SL384", "can_import_targetsun": False},
        {"email": "c@x.com", "userpl": "SL397", "can_import_targetsun": True},
        # อีเมลเดียวหลาย userpl — ต้องโดนทุกแถวของอีเมลนั้น
        {"email": "a@x.com", "userpl": "SL460", "can_import_targetsun": False},
    ]


class TestTargetSunBulk(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "user_access.json")
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(_rows(), f, ensure_ascii=False)
        self._prev = os.environ.get("USER_ACCESS_JSON_PATH")
        os.environ["USER_ACCESS_JSON_PATH"] = self.path

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("USER_ACCESS_JSON_PATH", None)
        else:
            os.environ["USER_ACCESS_JSON_PATH"] = self._prev
        self._tmp.cleanup()

    def _state(self, rows):
        out = {}
        for r in rows:
            out.setdefault(st.normalized_email(r.get("email")), set()).add(
                bool(r.get("can_import_targetsun"))
            )
        return out

    def test_enables_only_listed_emails(self):
        rows, changed = st.set_targetsun_flag_bulk(["a@x.com"], True)
        self.assertEqual(changed, 1)
        s = self._state(rows)
        self.assertEqual(s["a@x.com"], {True}, "ต้องเปิดครบทุกแถวของอีเมลนั้น")
        self.assertEqual(s["b@x.com"], {False}, "อีเมลที่ไม่ได้ระบุต้องไม่โดน")
        self.assertEqual(s["c@x.com"], {True})

    def test_changed_count_ignores_already_correct(self):
        _rows_out, changed = st.set_targetsun_flag_bulk(["c@x.com"], True)
        self.assertEqual(changed, 0, "c เปิดอยู่แล้ว ต้องไม่นับว่าเปลี่ยน")

    def test_all_emails_flag(self):
        rows, changed = st.set_targetsun_flag_bulk(None, True, all_emails=True)
        self.assertEqual(changed, 2, "a และ b เท่านั้นที่เปลี่ยน (c เปิดอยู่แล้ว)")
        for v in self._state(rows).values():
            self.assertEqual(v, {True})

        rows, changed = st.set_targetsun_flag_bulk(None, False, all_emails=True)
        self.assertEqual(changed, 3)
        for v in self._state(rows).values():
            self.assertEqual(v, {False})

    def test_empty_request_is_noop(self):
        rows, changed = st.set_targetsun_flag_bulk([], True)
        self.assertEqual(changed, 0)
        self.assertEqual(self._state(rows)["a@x.com"], {False}, "ต้องไม่แตะอะไรเลย")

    def test_no_deadlock_under_lock(self):
        """
        set_targetsun_flag_bulk ทำ read+write ใต้ _STORE_LOCK เดียว
        _STORE_LOCK เป็น Lock ธรรมดา ถ้าเผลอเรียก write_rows() (ซึ่งจับ lock ซ้ำ)
        จะค้างตาย — เทสต์นี้จับได้ด้วย timeout
        """
        done = threading.Event()

        def run():
            st.set_targetsun_flag_bulk(["a@x.com", "b@x.com"], True)
            done.set()

        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(timeout=10)
        self.assertTrue(done.is_set(), "เกิด deadlock ใน set_targetsun_flag_bulk")

    def test_lock_released_for_next_caller(self):
        st.set_targetsun_flag_bulk(["a@x.com"], True)
        # ถ้า lock ไม่ถูกปล่อย การอ่านครั้งถัดไปจะค้าง
        rows = st.read_rows()
        self.assertEqual(self._state(rows)["a@x.com"], {True})


if __name__ == "__main__":
    unittest.main()
