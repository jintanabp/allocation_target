"""
ผลตรวจระบบ 1 ต.ค. 2026 กลุ่ม ข (ผู้ใช้สั่งแก้ 1 ต.ค. 2026) — docs/system-review-2026-10-01.md

ออฟไลน์ทั้งหมด · ไม่มีการส่ง Target Sun (ส่วนที่อยู่บนเส้นทางส่งตรวจจากโค้ด หรือตรวจว่าหยุดก่อนส่ง)
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd
from fastapi import HTTPException

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend import fabric_dax_connector as fdc  # noqa: E402
from backend.services import nightly_check as nc  # noqa: E402
from backend.services import sent_ledger as sl  # noqa: E402
from backend.services import targetsun_import as ti  # noqa: E402
from backend.services import targetsun_read as tsr  # noqa: E402

TZ = ZoneInfo("Asia/Bangkok")


def _src(*parts):
    with open(os.path.join(REPO, *parts), encoding="utf-8") as f:
        return f.read()


APP = _src("frontend", "app.js")


def _fn(name, src=APP):
    m = re.search(rf"^(?:async )?function {re.escape(name)}\(", src, re.M)
    assert m, name
    return src[m.start():src.index("\n}\n", m.start()) + 3]


def _row(sku, emp, qty, wh=""):
    return {"PRODUCTCODE": sku, "SALESTYPE": "S", "DIVISIONCODE": "B", "SALESMANCODE": emp,
            "AREACODE": "10", "PROVINCECODE": "P1", "WAREHOUSECODE": wh, "QUANTITYCASE": qty}


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._t.name)
        os.makedirs("data", exist_ok=True)
        self._p = patch.object(nc, "_notify_dev")
        self._p.start()

    def tearDown(self):
        self._p.stop()
        os.chdir(self._cwd)
        self._t.cleanup()


# ── ข2 ปลายทางใน ledger ─────────────────────────────────────────────────


class TestLedgerDestination(_Tmp):
    UAT, PROD = "https://uat.x.test/import", "https://prod.x.test/import"

    def test_destination_change_starts_new_rows(self):
        sl.record_send("SLA", 10, 2026, [_row("A", "E1", 5)], send_status="ok", import_url=self.UAT)
        sl.record_send("SLA", 10, 2026, [_row("B", "E1", 3)], send_status="ok", import_url=self.PROD)
        led = sl.read_ledger("SLA", 10, 2026)
        self.assertEqual(led["import_url"], self.PROD)
        self.assertEqual(len(led["rows"]), 1, "แถวที่ส่ง UAT ต้องไม่ปนกับ Prod")
        self.assertEqual(len(led["sends"]), 2, "ประวัติการส่งยังอยู่ครบ")

    def test_nightly_skips_ledger_of_other_destination(self):
        sl.record_send("SLA", 10, 2026, [_row("A", "E1", 5)], send_status="ok", import_url=self.UAT)
        nc.write_settings(enabled=True)
        calls = []
        with patch("backend.services.targetsun_endpoints.targetsun_endpoints_summary",
                   return_value={"cross_env": "0", "import_url": self.PROD}):
            nc.run_once(now=datetime(2026, 9, 20, 2, 5, tzinfo=TZ),
                        fetch=lambda *a: calls.append(a) or {"rows": [], "complete": True},
                        sleep=lambda s: None)
        self.assertEqual(calls, [])
        self.assertEqual(nc.read_state()["teams"]["SLA|2026-10"]["reason"], "other_destination")


# ── ข4 ส่งบางส่วน = แถวยังไม่ยืนยัน ────────────────────────────────────────


class TestUnconfirmedRows(_Tmp):
    def test_partial_send_rows_flagged_and_compared_separately(self):
        sl.record_send("SLA", 10, 2026, [_row("A", "E1", 5)], send_status="partial")
        led = sl.read_ledger("SLA", 10, 2026)
        self.assertTrue(all(v.get("unconfirmed") for v in led["rows"].values()))
        diff = nc.compare(led["rows"], {})
        self.assertEqual((len(diff["missing"]), len(diff["unconfirmed"])), (0, 1))

    def test_ok_send_rows_are_confirmed(self):
        sl.record_send("SLA", 10, 2026, [_row("A", "E1", 5)], send_status="ok")
        led = sl.read_ledger("SLA", 10, 2026)
        diff = nc.compare(led["rows"], {})
        self.assertEqual((len(diff["missing"]), len(diff["unconfirmed"])), (1, 0))


# ── ข3 ด่านคลังซ้ำอ่านไม่ได้ = ไม่ส่ง ─────────────────────────────────────


class TestWarehouseCheckFailsClosed(unittest.TestCase):
    df = pd.DataFrame([_row("A", "E1", 5)])

    def test_read_expected_but_unavailable_blocks(self):
        with patch.object(ti, "_live_target_snapshot", return_value=None), \
             patch.object(ti, "_warehouse_check_expected", return_value=True):
            with self.assertRaises(HTTPException) as cm:
                ti._live_warehouse_conflicts("SLA", 10, 2026, self.df)
        self.assertEqual(cm.exception.status_code, 503)
        self.assertEqual(cm.exception.detail["code"], "warehouse_check_unavailable")

    def test_fabric_mode_is_not_blocked(self):
        with patch.object(ti, "_live_target_snapshot", return_value=None), \
             patch.object(ti, "_warehouse_check_expected", return_value=False):
            self.assertEqual(ti._live_warehouse_conflicts("SLA", 10, 2026, self.df), ([], None))

    def test_import_step_checks_before_posting(self):
        src = _src("backend", "services", "targetsun_import.py")
        i = src.index("if before_row_snapshot is None and file_qty and _warehouse_check_expected():")
        self.assertLess(i, src.index("out = _post_targetsun_multipart(\n                content,"))


# ── ข5 / ข10 จากโค้ด ───────────────────────────────────────────────────────


class TestFromSource(unittest.TestCase):
    def test_gateway_html_is_unknown_not_failed(self):
        src = _src("backend", "services", "targetsun_import.py")
        i = src.index("if int(r.status_code) in (502, 504):")
        self.assertIn("504,", src[i:i + 400])
        self.assertIn('"error_kind": "gateway_unknown"', src[i:i + 1200])

    def test_ly_and_prev_month_corrupt_files_are_not_swallowed(self):
        src = _src("backend", "services", "optimize.py")
        for label in ("hist LY same-month cache read failed", "hist prev-month cache read failed"):
            with self.subTest(label=label):
                i = src.index(label)
                self.assertIn("except HTTPException:\n        raise", src[i - 400:i])


# ── ข11 หน่วยขายจาก Fabric ────────────────────────────────────────────────


class TestSalesTypeFromFabric(unittest.TestCase):
    class _F:
        def get_dim_salesman_supervisor_index(self):
            return [{"super_code": "SLV", "sales_type": 1}, {"super_code": "SLC", "sales_type": 0}]

    def test_cash_team_found(self):
        self.assertEqual(tsr.sales_type_from_fabric_dim("slv", self._F()), "C")
        self.assertEqual(tsr.sales_type_from_fabric_dim("SLC", self._F()), "S")

    def test_unknown_or_broken_is_blank(self):
        self.assertEqual(tsr.sales_type_from_fabric_dim("SLX", self._F()), "")
        self.assertEqual(tsr.sales_type_from_fabric_dim("SLV", None), "")


# ── ข13 DAX escape ─────────────────────────────────────────────────────────


class TestDaxEscape(unittest.TestCase):
    def test_quote_is_doubled(self):
        self.assertEqual(fdc._dax_str('X"}, 1'), '"X""}, 1"')

    def test_product_query_escapes_alias(self):
        conn = fdc.FabricDAXConnector.__new__(fdc.FabricDAXConnector)
        seen = []
        conn._execute_dax = lambda q, debug=False: seen.append(q) or []
        conn.get_product_info(sku_list=['12"3'], target_year=2026, target_month=10)
        self.assertIn('"12""3"', seen[0])
        self.assertNotIn('{"12"3"}', seen[0])

    def test_no_raw_quoted_interpolation_left(self):
        src = _src("backend", "fabric_dax_connector.py")
        self.assertNotRegex(src, r"f'\"\{(x|e|s|str\(e\)|str\(s\))\}\"' for")


# ── ข15 / ข16 ────────────────────────────────────────────────────────────


class TestSlLinkAndNightlyManual(_Tmp):
    def test_sl_link_put_checks_existing_members(self):
        body = _fn("update_sl_link", _src("backend", "routers", "admin.py").replace("def update_sl_link(", "function update_sl_link("))
        self.assertIn('_ensure_sl_link_in_scope(admin, old, list(existing.get("new_sls") or []))', body)

    def test_run_now_does_not_consume_scheduled_run(self):
        nc.run_once(now=datetime(2026, 9, 20, 1, 30, tzinfo=TZ), force=True,
                    fetch=lambda *a: {"rows": [], "complete": True}, sleep=lambda s: None)
        st = nc.read_state()
        self.assertNotEqual(st.get("last_run_date"), "2026-09-20")
        self.assertEqual(st.get("last_manual_run_date"), "2026-09-20")
        s = {**nc.DEFAULTS, "enabled": True, "hour": 2}
        self.assertTrue(nc._due(s, st, datetime(2026, 9, 20, 2, 5, tzinfo=TZ)))


# ── ข1 ทางที่ 1: ส่งทั้งชุดซ้ำหลังล้มกลางทาง (ผู้ใช้เลือก 1 ต.ค. 2026) ─────────────


class TestResendWholeBatchAfterPartialFailure(_Tmp):
    """
    ตัวอย่างที่อธิบายผู้ใช้: เป้า A 60 / B 40 → กระจายรวมภาคได้ A 70 / B 30 → A ลง B ล้ม
    ส่งทั้งชุดซ้ำ: ทีม A ใน Target Sun ตอนนี้ 70 ≠ เป้าที่โหลดไว้ 60 — ต้องไม่ถูกบล็อกว่า「เป้าเปลี่ยน」
    เพราะ 70 คือของที่เราส่งเอง · ไม่มีการส่งจริงในเทสต์
    """
    URL = "https://uat.x.test/import"

    def _check(self, live, *, ledger_url=URL, current_url=URL, sent_qty=70):
        from backend.services import lakehouse as lh

        sl.record_send("SLA", 10, 2026, [_row("X", "E1", sent_qty)], send_status="ok", import_url=ledger_url)
        with patch.object(lh, "_sup_target_boxes_by_sku", return_value={"X": 60}),              patch("backend.services.targetsun_endpoints.targetsun_endpoints_summary",
                   return_value={"cross_env": "0", "import_url": current_url}):
            lh.assert_target_snapshot_is_fresh("SLA", 10, 2026, live_by_sku=live)

    def test_change_made_by_our_own_send_passes(self):
        self._check({"X": 70})

    def test_change_by_someone_else_still_blocks(self):
        with self.assertRaises(HTTPException) as cm:
            self._check({"X": 75})
        self.assertEqual(cm.exception.detail["code"], "send_target_stale")

    def test_ledger_of_other_destination_does_not_count(self):
        with self.assertRaises(HTTPException):
            self._check({"X": 70}, ledger_url="https://prod.x.test/import")

    def test_resending_same_file_creates_no_conflict(self):
        from backend.services.lakehouse import import_row_key_series, warehouse_conflicts

        df = pd.DataFrame([_row("X", "E1", 70), _row("X", "E2", 0, wh="W1")])
        keys = import_row_key_series(df).tolist()
        file_qty = dict(zip(keys, df["QUANTITYCASE"]))
        self.assertEqual(warehouse_conflicts(dict(file_qty), file_qty), [])

    def test_summary_tells_user_to_resend_whole_set(self):
        body = _fn("_showPartialSendSummaryModal")
        self.assertIn("กดส่งชุดเดิมทั้งหมดอีกครั้ง", body)
        self.assertNotIn("อย่ากดส่งทั้งชุดซ้ำ", body)
        self.assertNotIn("ห้ามส่งทีมเหล่านี้ซ้ำ", body)
        manual = _src("docs", "user-manual-th.md")
        self.assertNotIn("อย่ากดส่งทั้งชุดซ้ำ", manual)


# ── ข1 / ข6 / ข7 / ข8 / ข9 / ข17 หน้าเว็บ ───────────────────────────────────


class TestFrontend(unittest.TestCase):
    def test_incomplete_batch_logged_and_explained(self):
        self.assertIn('"send_batch_incomplete"', APP)
        self.assertIn("batchIncomplete", _fn("_showPartialSendSummaryModal"))

    def test_reload_failure_stops_partial_realloc(self):
        body = _fn("reloadThenReallocChanged")
        self.assertLess(body.index("if (!reloaded)"), body.index("await runReAllocationForSkus("))
        for name in ("refreshDashboardData", "refreshManagerDashboardData"):
            with self.subTest(fn=name):
                b = _fn(name)
                self.assertIn("return true;", b)
                self.assertNotRegex(b, r"\breturn;")

    def test_realloc_paths_filter_eligible(self):
        self.assertIn("_filterAllocationsEligibleOnly(allocs)", _fn("runReAllocationKeepEdits"))
        self.assertIn("_filterAllocationsEligibleOnly(part)", _fn("runReAllocationForSkus"))

    def test_recent_realloc_cleared_on_new_data(self):
        for name in ("loadData", "loadAggregateData", "loadSupervisorRegionAggregate"):
            with self.subTest(fn=name):
                self.assertIn("S.recentReallocSkus = [];", _fn(name))

    def test_rebalance_never_feeds_zeroed_pairs(self):
        self.assertIn("if (!weights.some((w) => w > 0)) return 0;", APP)

    def test_edit_round_keeps_its_team(self):
        self.assertIn("if (!_actionTally.sup_id) _actionTally.sup_id", _fn("_tally"))
        self.assertIn('keepalive: reason === "page_hidden"', _fn("_tallyFlush"))
        self.assertIn('_tallyFlush("context_switch");', _fn("loadData"))


if __name__ == "__main__":
    unittest.main()
