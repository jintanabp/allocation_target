"""
ผลตรวจ §5.1 (1-5) — แก้พร้อมกันแล้วของหาย / เขียนไฟล์ไม่ atomic

ท่าที่ใช้พิสูจน์ lost update: หน่วงตัวอ่านของ store ให้ช้าลง แล้วยิงสองคำขอพร้อมกัน
ถ้า read กับ write อยู่คนละรอบ lock ทั้งคู่จะอ่านได้รายการเดิม แล้วคนที่เขียนทีหลัง
ทับของอีกคนทิ้ง · ถ้าอยู่ใต้ lock เดียว คำขอที่สองต้องรอจนคำขอแรกเขียนเสร็จ ของจึงครบ

ทุกเทสต์เขียนลง tempdir เท่านั้น (ตั้ง env / patch path ของ store) ไม่แตะ config/ หรือ data/
"""

from __future__ import annotations

import inspect
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.core import atomic_io  # noqa: E402
from backend.routers import admin as admin_router  # noqa: E402
from backend.services import (  # noqa: E402
    access_hierarchy as ah,
    admin_permissions_store,
    admin_team,
    app_runtime_settings,
    emp_assignment_store,
    employees,
    managers as mgrs,
    no_target_store,
    sku_link_store,
    sl_link_store,
    user_access_store,
)

_DEV = {"auth_disabled": True, "role": "dev", "email": "tester@example.invalid"}
_READ_DELAY = 0.15


def _slow(fn):
    """ห่อตัวอ่านให้ช้าลง — ขยายช่วงเวลาที่ lost update จะเกิดได้"""

    def _w(*a, **kw):
        out = fn(*a, **kw)
        time.sleep(_READ_DELAY)
        return out

    return _w


def _run_parallel(*calls):
    """ยิงทุก call พร้อมกัน คืน exception ที่เกิด (ถ้ามี)"""
    barrier = threading.Barrier(len(calls))
    errors: list[BaseException] = []

    def _go(c):
        try:
            barrier.wait(timeout=5)
            c()
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=_go, args=(c,)) for c in calls]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=20)
    return errors


class _EnvTmp(unittest.TestCase):
    ENV: tuple[str, ...] = ()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = self._tmp.name
        self._old_env = {k: os.environ.get(k) for k in self.ENV}

    def tearDown(self):
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()


# ── §5.1-1 ผูกรหัส SKU / SL ────────────────────────────────────────────────


class TestSkuLinksLostUpdate(_EnvTmp):
    ENV = ("SKU_LINKS_JSON_PATH",)

    def setUp(self):
        super().setUp()
        self.path = os.path.join(self.d, "sku_links.json")
        os.environ["SKU_LINKS_JSON_PATH"] = self.path
        p = mock.patch.object(admin_router, "_audit_admin", lambda *a, **k: None)
        p.start()
        self.addCleanup(p.stop)

    def _slow_reads(self):
        p = mock.patch.object(
            sku_link_store, "read_links_unlocked", _slow(sku_link_store.read_links_unlocked)
        )
        p.start()
        self.addCleanup(p.stop)

    def test_two_admins_create_at_once_both_survive(self):
        self._slow_reads()
        errs = _run_parallel(
            lambda: admin_router.create_sku_link(admin_router.SkuLinkBody(canonical_sku="A1"), admin=_DEV),
            lambda: admin_router.create_sku_link(admin_router.SkuLinkBody(canonical_sku="B1"), admin=_DEV),
        )
        self.assertEqual(errs, [])
        got = {r["canonical_sku"] for r in sku_link_store.read_links()}
        self.assertEqual(got, {"A1", "B1"})

    def test_delete_and_create_at_once_both_apply(self):
        sku_link_store.write_links([{"canonical_sku": "OLD", "alias_skus": ["OLD"]}])
        self._slow_reads()
        errs = _run_parallel(
            lambda: admin_router.remove_sku_link(admin_router.SkuLinkDeleteBody(canonical_sku="OLD"), admin=_DEV),
            lambda: admin_router.create_sku_link(admin_router.SkuLinkBody(canonical_sku="NEW"), admin=_DEV),
        )
        self.assertEqual(errs, [])
        got = {r["canonical_sku"] for r in sku_link_store.read_links()}
        self.assertEqual(got, {"NEW"})

    def test_same_code_twice_one_gets_409(self):
        self._slow_reads()
        errs = _run_parallel(
            lambda: admin_router.create_sku_link(admin_router.SkuLinkBody(canonical_sku="A1", note="x"), admin=_DEV),
            lambda: admin_router.create_sku_link(admin_router.SkuLinkBody(canonical_sku="A1", note="y"), admin=_DEV),
        )
        self.assertEqual([getattr(e, "status_code", None) for e in errs], [409])

    def test_error_codes_unchanged_and_nothing_written(self):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as cm:
            admin_router.update_sku_link(admin_router.SkuLinkUpdateBody(canonical_sku="NOPE"), admin=_DEV)
        self.assertEqual(cm.exception.status_code, 404)
        with self.assertRaises(HTTPException) as cm:
            admin_router.remove_sku_link(admin_router.SkuLinkDeleteBody(canonical_sku="NOPE"), admin=_DEV)
        self.assertEqual(cm.exception.status_code, 404)
        self.assertFalse(os.path.exists(self.path), "fn ยกเลิกแล้วต้องไม่เขียนไฟล์")

        admin_router.create_sku_link(admin_router.SkuLinkBody(canonical_sku="A1"), admin=_DEV)
        admin_router.create_sku_link(admin_router.SkuLinkBody(canonical_sku="B1"), admin=_DEV)
        with self.assertRaises(HTTPException) as cm:
            admin_router.update_sku_link(
                admin_router.SkuLinkUpdateBody(canonical_sku="A1", new_canonical_sku="B1"), admin=_DEV
            )
        self.assertEqual(cm.exception.status_code, 409)
        out = admin_router.update_sku_link(
            admin_router.SkuLinkUpdateBody(canonical_sku="A1", new_canonical_sku="C1", note="n"), admin=_DEV
        )
        self.assertEqual(out["row"]["canonical_sku"], "C1")
        self.assertEqual(out["row"]["note"], "n")


class TestSlLinksLostUpdate(_EnvTmp):
    ENV = ("SL_LINKS_JSON_PATH",)

    def setUp(self):
        super().setUp()
        self.path = os.path.join(self.d, "sl_links.json")
        os.environ["SL_LINKS_JSON_PATH"] = self.path
        p = mock.patch.object(admin_router, "_audit_admin", lambda *a, **k: None)
        p.start()
        self.addCleanup(p.stop)
        p2 = mock.patch.object(
            sl_link_store, "read_links_unlocked", _slow(sl_link_store.read_links_unlocked)
        )
        p2.start()
        self.addCleanup(p2.stop)

    def test_two_admins_create_at_once_both_survive(self):
        errs = _run_parallel(
            lambda: admin_router.create_sl_link(admin_router.SlLinkBody(old_sl="SL100", new_sls=["SL101"]), admin=_DEV),
            lambda: admin_router.create_sl_link(admin_router.SlLinkBody(old_sl="SL200", new_sls=["SL201"]), admin=_DEV),
        )
        self.assertEqual(errs, [])
        got = {r["old_sl"] for r in sl_link_store.read_links()}
        self.assertEqual(got, {"SL100", "SL200"})

    def test_update_and_create_at_once_both_apply(self):
        sl_link_store.write_links([{"old_sl": "SL100", "new_sls": ["SL101"]}])
        errs = _run_parallel(
            lambda: admin_router.update_sl_link(
                admin_router.SlLinkUpdateBody(old_sl="SL100", new_sls=["SL102"], note="upd"), admin=_DEV
            ),
            lambda: admin_router.create_sl_link(admin_router.SlLinkBody(old_sl="SL300"), admin=_DEV),
        )
        self.assertEqual(errs, [])
        rows = {r["old_sl"]: r for r in sl_link_store.read_links()}
        self.assertEqual(set(rows), {"SL100", "SL300"})
        self.assertEqual(rows["SL100"]["new_sls"], ["SL102"])

    def test_delete_missing_is_404(self):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as cm:
            admin_router.remove_sl_link(admin_router.SlLinkDeleteBody(old_sl="SL999"), admin=_DEV)
        self.assertEqual(cm.exception.status_code, 404)

    def test_scoped_admin_delete_out_of_scope_is_403_and_keeps_row(self):
        from fastapi import HTTPException

        sl_link_store.write_links([{"old_sl": "SL100", "new_sls": ["SL101"]}])
        scoped = {"role": "admin", "email": "a@example.invalid", "admin_scope": {"sl_codes": {"SL100"}}}
        with self.assertRaises(HTTPException) as cm:
            admin_router.remove_sl_link(admin_router.SlLinkDeleteBody(old_sl="SL100"), admin=scoped)
        self.assertEqual(cm.exception.status_code, 403)
        self.assertEqual([r["old_sl"] for r in sl_link_store.read_links()], ["SL100"])


class TestLinkEndpointsUseMutate(unittest.TestCase):
    def test_admin_router_has_no_read_then_write(self):
        src = inspect.getsource(admin_router)
        for bad in ("write_links", "upsert_link(", "delete_link(", "write_sl_links", "upsert_sl_link", "delete_sl_link"):
            self.assertNotIn(bad, src, f"admin.py ยังเรียก {bad} แยกจากการอ่าน")
        for fn in (
            admin_router.create_sku_link, admin_router.update_sku_link, admin_router.remove_sku_link,
            admin_router.create_sl_link, admin_router.update_sl_link, admin_router.remove_sl_link,
        ):
            s = inspect.getsource(fn)
            self.assertIn("mutate_", s, fn.__name__)
            self.assertNotIn("read_links()", s, fn.__name__)
            self.assertNotIn("read_sl_links()", s, fn.__name__)


# ── §5.1-2 รายชื่อไม่ต้องตั้งเป้า ────────────────────────────────────────────


class TestNoTargetLostUpdate(_EnvTmp):
    ENV = ("NO_TARGET_EMPLOYEES_JSON_PATH",)

    def setUp(self):
        super().setUp()
        os.environ["NO_TARGET_EMPLOYEES_JSON_PATH"] = os.path.join(self.d, "no_target.json")

    def test_two_teams_save_at_once_both_survive(self):
        with mock.patch.object(no_target_store, "read_entries", _slow(no_target_store.read_entries)):
            errs = _run_parallel(
                lambda: no_target_store.set_for_supervisor("SL509", ["C444"]),
                lambda: no_target_store.set_for_supervisor("SL397", ["C445"]),
            )
        self.assertEqual(errs, [])
        got = {(r["super_code"], r["emp_id"]) for r in no_target_store.read_entries()}
        self.assertEqual(got, {("SL509", "C444"), ("SL397", "C445")})

    def test_lock_is_reentrant(self):
        self.assertTrue(hasattr(no_target_store._STORE_LOCK, "_is_owned"), "ต้องเป็น RLock")


# ── §5.1-3 emp_cache ─────────────────────────────────────────────────────────


class TestEmpCacheAtomic(_EnvTmp):
    def test_admin_team_writes_atomic_and_reads_locked(self):
        cache = os.path.join(self.d, "emp_cache_SL1_2026_10.csv")
        df = pd.DataFrame([{"emp_id": "E1", "emp_name": "n", "super_code": "SL1"}])
        writes, reads = [], []
        real_w, real_r = admin_team.atomic_write_csv, admin_team.read_locked

        def _w(path, frame, **kw):
            writes.append(path)
            return real_w(path, frame, **kw)

        def _r(path):
            reads.append(path)
            return real_r(path)

        with mock.patch.object(admin_team, "emp_cache_path", lambda *a: cache), \
             mock.patch.object(admin_team, "_fetch_from_fabric", lambda sc: (df.copy(), "")), \
             mock.patch.object(admin_team, "atomic_write_csv", _w), \
             mock.patch.object(admin_team, "read_locked", _r):
            out = admin_team.load_supervisor_team("SL1", target_year=2026, target_month=10, force_refresh=True)
            self.assertFalse(out["from_cache"])
            self.assertEqual(writes, [cache])
            out2 = admin_team.load_supervisor_team("SL1", target_year=2026, target_month=10)
            self.assertTrue(out2["from_cache"])
            self.assertEqual(reads, [cache])

    def test_no_plain_to_csv_on_emp_cache(self):
        src_team = inspect.getsource(admin_team.load_supervisor_team)
        self.assertNotIn("df_fabric.to_csv(", src_team)
        self.assertIn("atomic_write_csv(cache_path", src_team)
        src_emp = inspect.getsource(employees)
        self.assertNotIn("_raw_to_cache.to_csv(", src_emp)
        self.assertIn("with read_locked(cp):", src_emp)
        self.assertIn("with read_locked(emp_path):", src_emp)


# ── §5.1-4 store ที่เขียน temp + replace เอง ──────────────────────────────────


def _text_mode_bytes(text: str) -> bytes:
    """ไบต์ที่ open(..., "w") แบบ text mode เดิมเขียนออกมา (Windows = CRLF)"""
    return text.replace("\n", os.linesep).encode("utf-8")


class _FlakyReplace:
    """os.replace ที่พัง PermissionError ครั้งแรก (จำลอง antivirus ถือไฟล์) แล้วค่อยผ่าน"""

    def __init__(self, target: str):
        self.target = os.path.normcase(os.path.abspath(target))
        self.failed = 0
        self._real = os.replace

    def __call__(self, src, dst, *a, **kw):
        if os.path.normcase(os.path.abspath(dst)) == self.target and self.failed == 0:
            self.failed += 1
            raise PermissionError(5, "Access is denied (จำลอง)")
        return self._real(src, dst, *a, **kw)


class TestStoresAtomicWithRetry(_EnvTmp):
    ENV = (
        "SKU_LINKS_JSON_PATH", "SL_LINKS_JSON_PATH",
        "ADMIN_PERMISSIONS_JSON_PATH", "APP_RUNTIME_SETTINGS_PATH",
        "EMP_ASSIGNMENTS_JSON_PATH",
    )

    def test_no_hand_rolled_tempfile_replace(self):
        for mod in (sku_link_store, sl_link_store, admin_permissions_store, app_runtime_settings):
            src = inspect.getsource(mod)
            self.assertNotIn("tempfile.mkstemp", src, mod.__name__)
            self.assertNotIn("os.replace(", src, mod.__name__)

    def test_sku_links_retry_and_same_bytes(self):
        p = os.path.join(self.d, "sku_links.json")
        os.environ["SKU_LINKS_JSON_PATH"] = p
        flaky = _FlakyReplace(p)
        with mock.patch("os.replace", flaky):
            saved = sku_link_store.write_links([{"canonical_sku": "ก1", "alias_skus": ["ก1", "X"]}])
        self.assertEqual(flaky.failed, 1)
        want = json.dumps({"links": saved}, ensure_ascii=False, indent=2) + "\n"
        with open(p, "rb") as f:
            self.assertEqual(f.read(), _text_mode_bytes(want))

    def test_sl_links_retry_and_same_bytes(self):
        p = os.path.join(self.d, "sl_links.json")
        os.environ["SL_LINKS_JSON_PATH"] = p
        flaky = _FlakyReplace(p)
        with mock.patch("os.replace", flaky):
            saved = sl_link_store.write_links([{"old_sl": "SL1", "new_sls": ["SL2"], "note": "โน้ต"}])
        self.assertEqual(flaky.failed, 1)
        want = json.dumps({"links": saved}, ensure_ascii=False, indent=2) + "\n"
        with open(p, "rb") as f:
            self.assertEqual(f.read(), _text_mode_bytes(want))

    def test_admin_permissions_retry_and_same_bytes(self):
        p = os.path.join(self.d, "admin_permissions.json")
        os.environ["ADMIN_PERMISSIONS_JSON_PATH"] = p
        flaky = _FlakyReplace(p)
        roles = admin_permissions_store.default_roles()
        with mock.patch("os.replace", flaky):
            normalized = admin_permissions_store.write_roles(roles, "ผู้แก้", "2026-09-30")
        self.assertEqual(flaky.failed, 1)
        want = json.dumps(
            {"version": 1, "roles": normalized, "updated_by": "ผู้แก้", "updated_at": "2026-09-30"},
            ensure_ascii=False, indent=2,
        ) + "\n"
        with open(p, "rb") as f:
            self.assertEqual(f.read(), _text_mode_bytes(want))
        self.assertEqual(admin_permissions_store.read_roles(), normalized)

    def test_app_runtime_retry_and_same_bytes(self):
        p = os.path.join(self.d, "app_runtime.json")
        os.environ["APP_RUNTIME_SETTINGS_PATH"] = p
        flaky = _FlakyReplace(p)
        with mock.patch("os.replace", flaky):
            data = app_runtime_settings.set_target_read_source("fabric")
        self.assertEqual(flaky.failed, 1)
        want = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        with open(p, "rb") as f:
            self.assertEqual(f.read(), _text_mode_bytes(want))
        self.assertEqual(app_runtime_settings.read_settings()["target_read_source"], "fabric")

    def test_emp_assignment_reads_locked_and_concurrent_sets_survive(self):
        p = os.path.join(self.d, "emp_assignments.json")
        os.environ["EMP_ASSIGNMENTS_JSON_PATH"] = p
        self.assertIn("read_locked(path)", inspect.getsource(emp_assignment_store.read_rows))
        with mock.patch.object(emp_assignment_store, "read_rows", _slow(emp_assignment_store.read_rows)):
            errs = _run_parallel(
                lambda: emp_assignment_store.set_assignment("E1", "SL2", from_sup="SL1"),
                lambda: emp_assignment_store.set_assignment("E2", "SL3", from_sup="SL1"),
            )
        self.assertEqual(errs, [])
        got = {(r["emp_id"], r["to_sup"]) for r in emp_assignment_store.read_rows()}
        self.assertEqual(got, {("E1", "SL2"), ("E2", "SL3")})


# ── §5.1-5 rebuild ลำดับสิทธิ์ ───────────────────────────────────────────────


class TestHierarchyRebuildUnderUserAccessLock(_EnvTmp):
    def setUp(self):
        super().setUp()
        self._old_root = ah._repo_root
        ah._repo_root = lambda: self.d
        self._old_cache = mgrs.MANAGERS_CACHE_FILE
        self.calls: list[tuple[str, bool]] = []
        lock = user_access_store._STORE_LOCK

        def _build():
            self.calls.append(("build", lock._is_owned()))
            return {"by_manager": {}, "supervisors": [], "manager_codes": []}

        def _persist(payload):
            self.calls.append(("persist_hierarchy", lock._is_owned()))
            return "x"

        def _persist_mgr(payload):
            self.calls.append(("persist_managers", lock._is_owned()))

        for name, fn in (
            ("build_hierarchy_payload", _build),
            ("persist_hierarchy", _persist),
            ("persist_managers_payload", _persist_mgr),
        ):
            p = mock.patch.object(mgrs, name, fn)
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        ah._repo_root = self._old_root
        mgrs.MANAGERS_CACHE_FILE = self._old_cache
        super().tearDown()

    def test_rebuild_holds_user_access_lock(self):
        mgrs.MANAGERS_CACHE_FILE = os.path.join(self.d, "other", "managers_cache.json")
        mgrs.rebuild_managers_from_roster()
        self.assertEqual(
            self.calls,
            [("build", True), ("persist_hierarchy", True), ("persist_managers", True)],
        )

    def test_cache_not_written_twice_when_same_file(self):
        mgrs.MANAGERS_CACHE_FILE = os.path.join(self.d, "data", "managers_cache.json")
        mgrs.rebuild_managers_from_roster()
        self.assertEqual([c[0] for c in self.calls], ["build", "persist_hierarchy"])

    def test_rebuild_waits_for_user_access_mutation(self):
        """ระหว่างมีคนแก้รายชื่ออยู่ rebuild ต้องรอ — ไม่งั้นได้ลำดับสิทธิ์จากรายชื่อเก่า"""
        mgrs.MANAGERS_CACHE_FILE = os.path.join(self.d, "data", "managers_cache.json")
        lock = user_access_store._STORE_LOCK
        entered = threading.Event()
        release = threading.Event()

        def _hold():
            with lock:
                entered.set()
                release.wait(5)

        t = threading.Thread(target=_hold)
        t.start()
        entered.wait(5)
        r = threading.Thread(target=mgrs.rebuild_managers_from_roster)
        r.start()
        time.sleep(0.1)
        self.assertEqual(self.calls, [], "rebuild ต้องรอ lock ของ user_access")
        release.set()
        t.join(5)
        r.join(5)
        self.assertEqual([c[0] for c in self.calls], ["build", "persist_hierarchy"])


if __name__ == "__main__":
    unittest.main()
