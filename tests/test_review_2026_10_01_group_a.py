"""
ผลตรวจระบบ 1 ต.ค. 2026 กลุ่ม ก (ผู้ใช้สั่งแก้ 1 ต.ค. 2026) — docs/system-review-2026-10-01.md

ทุกเทสต์ออฟไลน์ ใช้โฟลเดอร์ชั่วคราว · ส่วนที่เกี่ยวกับการส่ง Target Sun ตรวจแค่ว่า "ปฏิเสธก่อนส่ง"
โดยดักตัวส่งให้ระเบิดถ้าถูกเรียก — ไม่มีการส่งจริงหรือจำลองการส่งสำเร็จ
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from fastapi import HTTPException

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend import OR_engine as eng  # noqa: E402
from backend.services import emp_assignment_store as eas  # noqa: E402
from backend.services import notification_store as ns  # noqa: E402
from backend.services import optimize as opt  # noqa: E402
from backend.services import sent_ledger as sl  # noqa: E402
from backend.services import targetsun_import as ti  # noqa: E402
from backend.services.employees import _build_sku_and_sun_from_tga, _price_or_zero  # noqa: E402


def _src(*parts):
    with open(os.path.join(REPO, *parts), encoding="utf-8") as f:
        return f.read()


APP = _src("frontend", "app.js")


def _fn(name):
    m = re.search(rf"^(?:async )?function {re.escape(name)}\(", APP, re.M)
    assert m, name
    return APP[m.start():APP.index("\n}\n", m.start()) + 3]


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._t.name)
        os.makedirs("data", exist_ok=True)

    def tearDown(self):
        os.chdir(self._cwd)
        self._t.cleanup()


def _ts_row(sku, emp, qty, wh=""):
    return {"PRODUCTCODE": sku, "SALESTYPE": "S", "DIVISIONCODE": "B", "SALESMANCODE": emp,
            "AREACODE": "10", "PROVINCECODE": "P1", "WAREHOUSECODE": wh, "QUANTITYCASE": qty}


# ── ก1 อ่านไม่ได้ = ห้ามเขียนทับ ─────────────────────────────────────────


class TestLedgerNotWipedOnBadRead(_Tmp):
    def test_unreadable_file_is_not_overwritten(self):
        self.assertTrue(sl.record_send("SLA", 10, 2026, [_ts_row("A", "E1", 5)], send_status="ok"))
        path = sl.ledger_path("SLA", 10, 2026)
        before = open(path, encoding="utf-8").read()
        real_open = open

        def flaky(p, *a, **k):
            if str(p) == path and "r" in (a[0] if a else k.get("mode", "r")):
                raise PermissionError("antivirus")
            return real_open(p, *a, **k)

        with patch("builtins.open", side_effect=flaky), patch("time.sleep"), \
             patch.object(sl, "_notify_dev_unrecorded") as notify:
            ok = sl.record_send("SLA", 10, 2026, [_ts_row("B", "E2", 3)], send_status="ok")
        self.assertFalse(ok)
        notify.assert_called_once()
        self.assertEqual(open(path, encoding="utf-8").read(), before, "ประวัติเดิมต้องอยู่ครบ")

    def test_corrupt_file_kept_aside_then_new_ledger(self):
        path = sl.ledger_path("SLA", 10, 2026)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{broken")
        self.assertTrue(sl.record_send("SLA", 10, 2026, [_ts_row("A", "E1", 5)], send_status="ok"))
        aside = [n for n in os.listdir(os.path.dirname(path)) if ".corrupt-" in n]
        self.assertEqual(len(aside), 1)
        self.assertEqual(len(sl.read_ledger("SLA", 10, 2026)["rows"]), 1)


class TestEmpAssignmentsNotWiped(_Tmp):
    def test_corrupt_file_blocks_write(self):
        p = os.path.join(self._t.name, "emp_assignments.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write('{"assignments": [ broken')
        with patch.dict(os.environ, {"EMP_ASSIGNMENTS_JSON_PATH": p}):
            self.assertEqual(eas.read_rows(), [], "อ่านทั่วไปยังไม่ล่ม")
            with self.assertRaises(ValueError):
                eas.set_assignment("E9", "SLB", from_sup="SLA")
        self.assertIn("broken", open(p, encoding="utf-8").read(), "ไฟล์เดิมต้องไม่ถูกทับ")


class TestNotificationsNotWiped(_Tmp):
    def _env(self):
        return patch.dict(os.environ, {"NOTIFICATIONS_DIR": self._t.name})

    def test_unreadable_box_create_returns_none_and_keeps_file(self):
        with self._env():
            ns.create(kind="x", title="t1", message="m", recipients=["a@x.co"])
            path = ns.notifications_json_path()
            before = open(path, encoding="utf-8").read()
            with patch.object(ns, "read_locked", side_effect=PermissionError("locked")):
                self.assertIsNone(ns.create(kind="x", title="t2", message="m", recipients=["a@x.co"]))
            self.assertEqual(open(path, encoding="utf-8").read(), before)

    def test_corrupt_box_kept_aside(self):
        with self._env():
            path = ns.notifications_json_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write("{nope")
            self.assertIsNotNone(ns.create(kind="x", title="t", message="m", recipients=["a@x.co"]))
            self.assertTrue(any(".corrupt-" in n for n in os.listdir(os.path.dirname(path))))


# ── ก2 ราคารถเงินสด NaN / ชั้นถอยเครดิต ─────────────────────────────────────


class TestCashPriceNaN(unittest.TestCase):
    def test_price_or_zero(self):
        self.assertEqual(_price_or_zero(float("nan")), 0.0)
        self.assertEqual(_price_or_zero(None), 0.0)
        self.assertEqual(_price_or_zero("x"), 0.0)
        self.assertEqual(_price_or_zero("12.5"), 12.5)

    def test_cash_team_with_nan_cash_price_falls_back_to_credit(self):
        df_tga = pd.DataFrame([{"emp_id": "E1", "sku": "B", "qty": 10}])
        df_prod = pd.DataFrame([{"sku": "B", "credit_unit_price": 100.0, "cash_unit_price": float("nan")}])
        df_sku, df_sun, _ = _build_sku_and_sun_from_tga(df_tga, df_prod, ["E1"], ["B"], sales_type="C")
        self.assertEqual(float(df_sku.loc[df_sku["sku"] == "B", "price_per_box"].iloc[0]), 100.0)
        self.assertEqual(float(df_sun.loc[df_sun["emp_id"] == "E1", "target_sun"].iloc[0]), 1000.0)

    def test_credit_only_result_is_flagged_and_not_cached(self):
        from backend.fabric_dax_connector import FabricDAXConnector

        conn = FabricDAXConnector.__new__(FabricDAXConnector)
        conn._execute_dax = lambda q, debug=False: (
            (_ for _ in ()).throw(RuntimeError("x")) if "CASHUNITPRICE" in q
            else [{"[ProductCode]": "B", "[CreditUnitPrice]": 100.0}]
        )
        df = conn.get_product_info(sku_list=["B"], target_year=2026, target_month=10)
        self.assertTrue(df.attrs.get("credit_only"))
        src = _src("backend", "services", "employees.py")
        self.assertIn("if not price_credit_only:\n                            fc.write_product_info_df", src)
        self.assertIn('"type": "cash_price_unavailable"', src)
        self.assertIn('w.type === "cash_price_unavailable"', APP)


# ── ก3 ล็อกในสินค้าเป้าน้อยกว่าจำนวนคน ─────────────────────────────────────


class TestLockOnSmallTargetSku(unittest.TestCase):
    def _run(self, strategy, locks, zero_hist_emp=None):
        emp = pd.DataFrame([{"emp_id": e, "yellow_target": 1000.0} for e in "ABCDE"])
        sku = pd.DataFrame([{"sku": "S1", "supervisor_target_boxes": 4, "price_per_box": 100.0},
                            {"sku": "S2", "supervisor_target_boxes": 50, "price_per_box": 80.0}])
        hist = pd.DataFrame([{"emp_id": e, "sku": "S1", "hist_boxes": 1} for e in "ABCDE"])
        return eng.allocate_boxes(emp, sku, hist, strategy=strategy, locked_edits=locks,
                                  tiered_allocation=False)

    def test_totals_met_and_locks_kept(self):
        locks = [{"emp_id": "A", "sku": "S1", "locked_boxes": 0}, {"emp_id": "B", "sku": "S1", "locked_boxes": 0}]
        for st in ("EVEN", "L3M", "PUSH", "LP"):
            with self.subTest(strategy=st):
                out = self._run(st, locks)
                tot = out.groupby("sku")["allocated_boxes"].sum().to_dict()
                self.assertEqual((tot["S1"], tot["S2"]), (4, 50))
                s1 = out[out["sku"] == "S1"].set_index("emp_id")["allocated_boxes"]
                self.assertEqual((int(s1.get("A", 0)), int(s1.get("B", 0))), (0, 0))
                self.assertFalse(out.attrs.get("optimization_fallback"))

    def test_without_locks_still_one_each(self):
        out = self._run("L3M", [])
        self.assertEqual(sorted(out[out["sku"] == "S1"]["allocated_boxes"].tolist(), reverse=True)[:4], [1, 1, 1, 1])


# ── ก4 / ก5 ส่งซ้ำแถวที่ไม่ลง + 504 (ตรวจว่าปฏิเสธก่อนส่ง — ไม่มีการส่ง) ──────────────


def _no_send(*a, **k):
    raise AssertionError("ห้ามส่ง Target Sun ในเทสต์")


class TestResendGuards(_Tmp):
    URL = "https://uat.example.test/import"

    def _rec(self, token, sent_at, url=URL):
        d = Path("data/ts_sent")
        d.mkdir(parents=True, exist_ok=True)
        rec = {"sup_id": "SLA", "target_month": 10, "target_year": 2026, "token": token,
               "sent_at": sent_at, "rows": [_ts_row("A", "E1", 5)], "import_url": url}
        (d / f"{token}.json").write_text(json.dumps(rec), encoding="utf-8")

    def _resend(self, token):
        summary = {"cross_env": "0", "import_url": self.URL}
        with patch.object(ti, "_SENT_DIR", Path("data/ts_sent")), \
             patch("backend.services.targetsun_endpoints.targetsun_endpoints_summary", return_value=summary), \
             patch.object(ti, "_post_targetsun_multipart", side_effect=_no_send), \
             patch.object(ti, "_live_target_snapshot", side_effect=_no_send):
            return ti.resend_unlanded_rows("SLA", token)

    def test_old_token_refused_when_newer_send_exists(self):
        self._rec("old", 1000.0)
        self._rec("new", 2000.0)
        with self.assertRaises(HTTPException) as cm:
            self._resend("old")
        self.assertEqual(cm.exception.status_code, 409)
        self.assertIn("รอบใหม่กว่า", cm.exception.detail)

    def test_other_destination_refused(self):
        self._rec("t", 1000.0, url="https://prod.example.test/import")
        with self.assertRaises(HTTPException) as cm:
            self._resend("t")
        self.assertIn("ปลายทางอื่น", cm.exception.detail)

    def test_record_without_destination_refused(self):
        self._rec("t", 1000.0, url="")
        with self.assertRaises(HTTPException):
            self._resend("t")

    def test_504_is_recorded_as_unknown(self):
        src = _src("backend", "services", "targetsun_import.py")
        i = src.index("if e.status_code != 504:")
        self.assertIn('_keep_sent_record(token, meta, "unknown")', src[i:i + 600])

    def test_resend_records_ledger(self):
        body = _src("backend", "services", "targetsun_import.py")
        i = body.index("def resend_unlanded_rows(")
        self.assertIn("sent_ledger.record_send(", body[i:i + 5000])


# ── ก6 / ก7 หน้าเว็บ ──────────────────────────────────────────────────────


class TestFrontendContextAndModal(unittest.TestCase):
    def test_switch_team_blocked_while_allocating(self):
        body = _fn("switchSupervisorContext")
        self.assertLess(body.index("if (_allocRunInFlight)"), body.index("_hasUnsaved"))

    def test_results_dropped_when_context_changed(self):
        for name in ("runOptimization", "runReAllocationKeepEdits", "runReAllocationForSkus"):
            with self.subTest(fn=name):
                body = _fn(name)
                self.assertIn("const _ctx = _allocContextKey();", body)
                self.assertIn("_allocContextChanged(_ctx)", body)
                self.assertLess(body.index("_allocContextChanged(_ctx)"), body.index("saveDraft(") if "saveDraft(" in body else len(body))

    def test_modal_removed_only_via_dismiss(self):
        self.assertNotRegex(APP, r'getElementById\("infoModal"\)\??\.remove\(\)')
        self.assertIn("_dismissInfoModal();", _fn("reloadThenReallocChanged"))
        self.assertIn("_dismissInfoModal();", _fn("jumpToResultCell"))
        body = _fn("_showInfoModal")
        self.assertTrue(body.lstrip().split("\n")[1].strip().startswith("_dismissInfoModal();"))
        self.assertIn("modal._dismiss = () =>", body)


# ── ก8 หลายกลยุทธ์ ─────────────────────────────────────────────────────────


class TestResolvedStrategies(unittest.TestCase):
    df = pd.DataFrame([{"sku": "1", "brand_name_thai": "X"}, {"sku": "2", "brand_name_thai": "Y"}])

    def test_all_brands_same_strategy_is_single(self):
        self.assertEqual(opt._resolved_strategies_by_sku(self.df, {"X": "EVEN", "Y": "EVEN"}, "L3M"), {"EVEN"})

    def test_unmapped_brand_uses_default(self):
        self.assertEqual(opt._resolved_strategies_by_sku(self.df, {"X": "EVEN"}, "L3M"), {"EVEN", "L3M"})

    def test_single_strategy_is_used_for_the_run(self):
        src = _src("backend", "services", "optimize.py")
        self.assertIn("strategy=single_strategy,", src)
        self.assertIn("len(_resolved_strategies) > 1", src)


# ── ก9 กระจายเฉพาะสินค้า ───────────────────────────────────────────────────


class TestPartialMerge(_Tmp):
    def test_rows_of_people_no_longer_in_round_dropped(self):
        pd.DataFrame([
            {"emp_id": "E1", "sku": "S1", "allocated_boxes": 3},
            {"emp_id": "X9", "sku": "S1", "allocated_boxes": 2},
            {"emp_id": "E1", "sku": "S2", "allocated_boxes": 1},
        ]).to_csv("data/result.csv", index=False)
        new = pd.DataFrame([{"emp_id": "E1", "sku": "S2", "allocated_boxes": 4}])
        out = opt._merge_partial_result("data/result.csv", new, ["S2"], allowed_emps={"E1"})
        self.assertEqual(sorted(zip(out["emp_id"], out["sku"])), [("E1", "S1"), ("E1", "S2")])

    def test_frontend_keeps_never_sold_keys_of_other_skus(self):
        body = _fn("runReAllocationForSkus")
        self.assertIn("prevZeroKeys.filter((k) => !changedSet.has(", body)
        self.assertIn("prevNeverSoldSummary", body)


if __name__ == "__main__":
    unittest.main()
