"""
แอดมิน 2 คนแก้ config/user_access.json พร้อมกัน — การแก้ของใครต้องไม่หาย

ของเดิม: ทุก endpoint ทำ read_rows() → แก้ → write_rows() ซึ่งจับล็อกคนละรอบ คนที่
บันทึกทีหลังเขียนทับทั้งไฟล์ด้วยสำเนาที่อ่านไว้ก่อนการแก้ของอีกคน การแก้นั้นหายเงียบ ๆ
ตอนนี้ทุกจุดใช้ user_access_store.mutate_rows() — อ่าน/ตรวจ/เขียน รอบเดียวใต้ล็อกเดียว

วิธีทดสอบ: เรียกฟังก์ชัน endpoint จริงสองตัวในสอง thread แล้วใช้ threading.Event คุม
จังหวะให้คำขอแรก "ค้างอยู่ระหว่างอ่านกับเขียน" ตอนคำขอที่สองเข้ามา (ไม่ใช้ sleep เดา)
แล้วยืนยันว่า (1) คำขอที่สองต้องรอ ไม่แซงเขียน และ (2) ผลสุดท้ายมีการแก้ของทั้งสองคน

ไฟล์ทั้งหมดอยู่ใน temp — ปิดตัวที่เขียนไฟล์อื่น (sync ลำดับสิทธิ์/บันทึกการใช้งาน) ไว้
ไม่แตะ config/ หรือ data/ ของจริงเด็ดขาด
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from fastapi import HTTPException  # noqa: E402

from backend.routers import admin as admin_router  # noqa: E402
from backend.services import access_hierarchy as ah  # noqa: E402
from backend.services import user_access_store as uas  # noqa: E402

DEV = {"auth_disabled": True, "role": "dev", "admin_scope": None, "email": "dev@example.test"}
HEAD = {"role": "head_admin", "email": "head@example.test", "admin_scope": {"breadth": "all"}}

BASE_ROWS = [
    {"email": "a@example.test", "userpl": "SL001", "can_import_targetsun": False, "note": "a"},
    {"email": "b@example.test", "userpl": "SL002", "can_import_targetsun": False, "note": "b"},
    {"email": "c@example.test", "userpl": "SL003", "can_import_targetsun": False, "note": "c"},
]

WAIT = 5.0          # เพดานรอ — ให้ regression "ล้ม" ไม่ใช่ค้าง
PROVE_BLOCKED = 0.4  # คำขอที่สองต้องยังค้างอยู่หลังเวลานี้ (ถูกล็อกกันไว้จริง)


class _TmpStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "user_access.json")
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(BASE_ROWS, f)
        self._patches = [
            patch.dict(os.environ, {"USER_ACCESS_JSON_PATH": self.path}),
            # ตัวที่เขียนไฟล์อื่นนอก temp — ปิดไว้ ไม่เกี่ยวกับสิ่งที่ทดสอบ
            patch.object(admin_router, "_sync_access_hierarchy", lambda *a, **k: None),
            patch.object(admin_router, "_audit_admin", lambda *a, **k: None),
            patch.object(admin_router, "enrich_user_access_rows", lambda *a, **k: []),
            patch.object(admin_router, "invalidate_user_access_cache", lambda *a, **k: None),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()

    def rows(self):
        with open(self.path, encoding="utf-8") as f:
            return {(r["email"], r["userpl"]): r for r in json.load(f)}

    def run_race(self, first, second, hold_patch):
        """
        first() ค้างใน hold_patch (จังหวะระหว่างอ่านกับเขียน) → ปล่อย second() เข้าไป →
        ยืนยันว่า second ต้องรอ → ปล่อย first → ทั้งคู่ต้องจบโดยไม่ error
        """
        inside = threading.Event()
        release = threading.Event()
        errors: list[BaseException] = []

        def hold(*a, **k):
            if threading.current_thread().name == "first" and not inside.is_set():
                inside.set()
                release.wait(WAIT)

        def wrap(fn):
            def _run():
                try:
                    fn()
                except BaseException as e:  # noqa: BLE001 — เก็บไปรายงานใน thread หลัก
                    errors.append(e)
            return _run

        with hold_patch(hold):
            t1 = threading.Thread(target=wrap(first), name="first")
            t1.start()
            self.assertTrue(inside.wait(WAIT), "คำขอแรกไปไม่ถึงจุดที่ต้องค้าง")
            t2 = threading.Thread(target=wrap(second), name="second")
            t2.start()
            t2.join(PROVE_BLOCKED)
            second_was_blocked = t2.is_alive()
            release.set()
            t1.join(WAIT)
            t2.join(WAIT)
        self.assertFalse(t1.is_alive() or t2.is_alive(), "มีคำขอค้าง (deadlock?)")
        self.assertEqual(errors, [])
        self.assertTrue(
            second_was_blocked,
            "คำขอที่สองต้องรอคำขอแรกเขียนเสร็จก่อน — ไม่งั้นแปลว่าอ่าน/เขียนไม่ได้อยู่ใต้ล็อกเดียว",
        )


def _hold_in(name):
    """แทรกตัวค้างเข้าไปก่อนเรียกของจริง ณ ฟังก์ชันที่ถูกเรียกระหว่างอ่านกับเขียน"""
    real = getattr(admin_router, name)

    def factory(hold):
        def wrapped(*a, **k):
            hold()
            return real(*a, **k)
        return patch.object(admin_router, name, wrapped)

    return factory


def _hold_after_store_read(hold):
    """ค้างหลังอ่านไฟล์ (ใต้ล็อกของ mutate_rows) — ใช้กับ endpoint ที่ไม่มีจุดแทรกอื่นใต้ล็อก"""
    real = uas.read_rows_unlocked

    def wrapped(*a, **k):
        out = real(*a, **k)
        hold()
        return out

    return patch.object(uas, "read_rows_unlocked", wrapped)


class TwoAdminsAtOnceTest(_TmpStore):
    def test_update_while_another_admin_creates_keeps_both(self):
        self.run_race(
            lambda: admin_router.update_user_access(
                admin_router.UserAccessUpdateBody(email="a@example.test", userpl="SL001", note="แก้โดย A"),
                admin=DEV,
            ),
            lambda: admin_router.create_user_access(
                admin_router.UserAccessBody(email="new@example.test", userpl="SL009", note="เพิ่มโดย B"),
                admin=DEV,
            ),
            _hold_in("_patch_row_meta"),
        )
        rows = self.rows()
        self.assertEqual(rows[("a@example.test", "SL001")]["note"], "แก้โดย A")
        self.assertIn(("new@example.test", "SL009"), rows, "แถวที่แอดมินอีกคนเพิ่มหายไป")

    def test_two_updates_on_different_rows_keep_both(self):
        self.run_race(
            lambda: admin_router.update_user_access(
                admin_router.UserAccessUpdateBody(email="a@example.test", userpl="SL001", note="A1"),
                admin=DEV,
            ),
            lambda: admin_router.update_user_access(
                admin_router.UserAccessUpdateBody(email="b@example.test", userpl="SL002", note="B1"),
                admin=DEV,
            ),
            _hold_in("_patch_row_meta"),
        )
        rows = self.rows()
        self.assertEqual(rows[("a@example.test", "SL001")]["note"], "A1")
        self.assertEqual(rows[("b@example.test", "SL002")]["note"], "B1")

    def test_delete_while_another_admin_updates_keeps_the_update(self):
        self.run_race(
            lambda: admin_router.remove_user_access(
                admin_router.UserAccessDeleteBody(email="c@example.test", userpl="SL003"), admin=DEV,
            ),
            lambda: admin_router.update_user_access(
                admin_router.UserAccessUpdateBody(email="a@example.test", userpl="SL001", note="A2"),
                admin=DEV,
            ),
            _hold_in("ensure_row_in_admin_scope"),
        )
        rows = self.rows()
        self.assertNotIn(("c@example.test", "SL003"), rows)
        self.assertEqual(rows[("a@example.test", "SL001")]["note"], "A2")

    def test_targetsun_toggle_while_another_admin_creates_keeps_both(self):
        self.run_race(
            lambda: admin_router.set_targetsun_for_email(
                admin_router.TargetSunEmailBody(email="b@example.test", enabled=True), admin=DEV,
            ),
            lambda: admin_router.create_user_access(
                admin_router.UserAccessBody(email="new2@example.test", userpl="SL010"), admin=DEV,
            ),
            _hold_in("ensure_row_in_admin_scope"),
        )
        rows = self.rows()
        self.assertTrue(rows[("b@example.test", "SL002")]["can_import_targetsun"])
        self.assertIn(("new2@example.test", "SL010"), rows)

    def test_role_set_while_another_admin_updates_keeps_both(self):
        self.run_race(
            lambda: admin_router.set_user_role(
                admin_router.UserRoleBody(email="c@example.test", role="admin", admin_scope="all"),
                admin=HEAD,
            ),
            lambda: admin_router.update_user_access(
                admin_router.UserAccessUpdateBody(email="a@example.test", userpl="SL001", note="A3"),
                admin=DEV,
            ),
            _hold_in("ensure_row_in_admin_scope"),
        )
        rows = self.rows()
        self.assertEqual(rows[("c@example.test", "SL003")].get("role"), "admin")
        self.assertEqual(rows[("a@example.test", "SL001")]["note"], "A3")

    def test_hierarchy_rebuild_while_another_admin_updates_keeps_the_update(self):
        with patch.object(ah, "load_hierarchy_payload", lambda: {}), \
             patch.object(ah, "persist_hierarchy", lambda payload: "tmp"), \
             patch.object(ah, "build_hierarchy_payload", lambda rows=None: {"by_manager": {}}):
            real_enrich = ah.enrich_rows_with_visibility

            def factory(hold):
                def wrapped(rows, *a, **k):
                    hold()
                    return [dict(r) for r in rows]
                return patch.object(ah, "enrich_rows_with_visibility", wrapped)

            self.assertIsNotNone(real_enrich)
            self.run_race(
                lambda: admin_router.admin_rebuild_access_hierarchy(None, admin=DEV),
                lambda: admin_router.update_user_access(
                    admin_router.UserAccessUpdateBody(email="b@example.test", userpl="SL002", note="B4"),
                    admin=DEV,
                ),
                factory,
            )
        self.assertEqual(self.rows()[("b@example.test", "SL002")]["note"], "B4")


class ChecksRunAgainstLatestRowsTest(_TmpStore):
    def test_duplicate_created_by_another_admin_is_rejected_not_doubled(self):
        """B เพิ่มแถวเดียวกันระหว่างที่ A กำลังเพิ่ม — A ต้องได้ 409 ไม่ใช่มีสองแถว"""
        results: dict[str, object] = {}

        def create(tag):
            try:
                admin_router.create_user_access(
                    admin_router.UserAccessBody(email="dup@example.test", userpl="SL050", note=tag),
                    admin=DEV,
                )
                results[tag] = "ok"
            except HTTPException as e:
                results[tag] = e.status_code

        self.run_race(lambda: create("A"), lambda: create("B"), _hold_after_store_read)
        self.assertEqual(sorted(map(str, results.values())), ["409", "ok"])
        with open(self.path, encoding="utf-8") as f:
            dups = [r for r in json.load(f) if r["email"] == "dup@example.test"]
        self.assertEqual(len(dups), 1)

    def test_update_of_a_row_deleted_meanwhile_is_404_and_does_not_resurrect_it(self):
        """A ลบแถว c ระหว่างที่ B จะแก้แถว c — B ต้องได้ 404 และแถว c ต้องไม่ฟื้นกลับมา"""
        results: dict[str, object] = {}

        def update_c():
            try:
                admin_router.update_user_access(
                    admin_router.UserAccessUpdateBody(email="c@example.test", userpl="SL003", note="ฟื้น?"),
                    admin=DEV,
                )
                results["B"] = "ok"
            except HTTPException as e:
                results["B"] = e.status_code

        self.run_race(
            lambda: admin_router.remove_user_access(
                admin_router.UserAccessDeleteBody(email="c@example.test", userpl="SL003"), admin=DEV,
            ),
            update_c,
            _hold_in("ensure_row_in_admin_scope"),
        )
        self.assertEqual(results["B"], 404)
        self.assertNotIn(("c@example.test", "SL003"), self.rows())


class MutateRowsContractTest(_TmpStore):
    def test_error_inside_fn_writes_nothing(self):
        before = open(self.path, encoding="utf-8").read()

        def boom(rows):
            rows.append({"email": "x@example.test", "userpl": "SL999"})
            raise HTTPException(status_code=403, detail="นอกขอบเขต")

        with self.assertRaises(HTTPException):
            uas.mutate_rows(boom)
        self.assertEqual(open(self.path, encoding="utf-8").read(), before)

    def test_returning_none_writes_nothing(self):
        mtime = os.path.getmtime(self.path)
        out = uas.mutate_rows(lambda rows: None)
        self.assertEqual(len(out), 3)
        self.assertEqual(os.path.getmtime(self.path), mtime)

    def test_fn_gets_copies_not_the_callers_rows(self):
        seen = {}

        def fn(rows):
            rows[0]["note"] = "แก้ในสำเนา"
            seen["rows"] = rows
            return None     # ไม่เขียน — ของเดิมต้องไม่เปลี่ยน

        uas.mutate_rows(fn)
        self.assertNotEqual(self.rows()[("a@example.test", "SL001")]["note"], "แก้ในสำเนา")

    def test_nested_read_inside_fn_does_not_deadlock(self):
        """fn เรียกตัวช่วยที่อ่าน read_rows() ซ้ำใต้ล็อก — ต้องไม่ค้าง (ล็อกเป็น RLock)"""
        done = threading.Event()

        def run():
            uas.mutate_rows(lambda rows: (uas.read_rows(), rows)[1])
            done.set()

        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(WAIT)
        self.assertTrue(done.is_set(), "deadlock ใน mutate_rows")

    def test_legacy_helpers_ignore_a_stale_rows_argument(self):
        """delete_row/upsert_row รับ rows เก่ามา — ต้องไม่เอาไปเขียนทับของล่าสุด"""
        stale = uas.read_rows()
        uas.mutate_rows(lambda rows: rows + [{"email": "late@example.test", "userpl": "SL077"}])
        uas.delete_row(stale, "a@example.test", "SL001")
        uas.upsert_row(stale, email="b@example.test", userpl="SL002", note="upsert")
        rows = self.rows()
        self.assertIn(("late@example.test", "SL077"), rows, "แถวที่เพิ่มทีหลังหายเพราะใช้ rows เก่า")
        self.assertNotIn(("a@example.test", "SL001"), rows)
        self.assertEqual(rows[("b@example.test", "SL002")]["note"], "upsert")

    def test_many_concurrent_single_row_edits_all_land(self):
        """20 thread แก้ note คนละแถวพร้อมกัน — ต้องครบทั้ง 20"""
        uas.mutate_rows(lambda rows: rows + [
            {"email": f"u{i}@example.test", "userpl": f"SL{100 + i}", "note": ""} for i in range(20)
        ])
        barrier = threading.Barrier(20, timeout=WAIT)

        def edit(i):
            barrier.wait()
            uas.upsert_row(email=f"u{i}@example.test", userpl=f"SL{100 + i}", note=f"n{i}")

        ts = [threading.Thread(target=edit, args=(i,)) for i in range(20)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(WAIT)
        rows = self.rows()
        self.assertEqual(
            [rows[(f"u{i}@example.test", f"SL{100 + i}")]["note"] for i in range(20)],
            [f"n{i}" for i in range(20)],
        )


class NoEndpointWritesAStaleCopyTest(unittest.TestCase):
    """เฝ้าไว้: ห้ามมี endpoint ไหนกลับไปใช้ write_rows() / delete_row() กับแถวที่อ่านไว้ก่อน"""

    def test_admin_router_no_longer_calls_the_split_writers(self):
        import inspect
        import re

        src = inspect.getsource(admin_router)
        code = "\n".join(l.split("#", 1)[0] for l in src.splitlines())
        for name in ("write_rows(", "delete_row(", "set_email_targetsun_flag(", "upsert_row("):
            self.assertIsNone(
                re.search(rf"(?<![\w.]){re.escape(name)}", code),
                f"admin.py ยังเรียก {name} — ใช้ mutate_rows() แทน",
            )


if __name__ == "__main__":
    unittest.main()
