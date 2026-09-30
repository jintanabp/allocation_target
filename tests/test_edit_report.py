"""
รายงานการแก้มือ (เฟส F1) + แผงตรวจ Target Sun รายคืน (เฟส F3 UI)

ตัวเลข: เทียบเฉพาะแถวที่มี engine_boxes และไม่ได้มาจาก Target Sun
ขอบเขต: ผู้ดูแลเห็นเฉพาะทีมในขอบเขต dev เห็นทุกทีม (ตัวเดียวกับ export-all)
เรียกฟังก์ชัน route ตรง ๆ ด้วยโฟลเดอร์ชั่วคราว — ไม่แตะ data/ จริง ไม่แตะเน็ต
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.routers import admin as admin_router  # noqa: E402
from backend.services import allocation_store, edit_report  # noqa: E402

DEV = {"role": "dev", "email": "dev@x", "admin_scope": None}


def _read(rel: str) -> str:
    with open(os.path.join(REPO, rel), encoding="utf-8") as f:
        return f.read()


def _row(emp, sku, got, eng, src="engine", **kw):
    return {"emp_id": emp, "sku": sku, "allocated_boxes": got, "engine_boxes": eng, "row_source": src, **kw}


class TestTeamMetrics(unittest.TestCase):
    def test_counts_edits_moves_and_zero_flips(self):
        snap = {"allocations": [
            _row("E1", "S1", 10, 10),                                 # ไม่แก้
            _row("E1", "S2", 0, 4, product_name_thai="น้ำ"),          # 4 → 0
            _row("E2", "S2", 4, 0, emp_name="สมชาย"),                 # 0 → 4
            _row("E2", "S1", 7, 5),                                   # +2
        ]}
        m = edit_report.team_metrics(snap)
        self.assertEqual(m["rows"], 4)
        self.assertEqual(m["edited_cells"], 3)
        self.assertEqual(m["edited_pct"], 75.0)
        self.assertEqual(m["boxes_moved"], 5.0)                      # (4+4+2)/2
        self.assertEqual(m["to_zero"], 1)
        self.assertEqual(m["from_zero"], 1)
        self.assertEqual(m["top_skus"][0]["code"], "S2")
        self.assertEqual(m["top_skus"][0]["boxes"], 8.0)
        self.assertEqual(m["top_skus"][0]["name"], "น้ำ")
        self.assertEqual(m["top_emps"][0]["code"], "E2")
        self.assertEqual(m["top_emps"][0]["name"], "สมชาย")

    def test_targetsun_rows_and_rows_without_engine_are_ignored(self):
        snap = {"allocations": [
            _row("E1", "S1", 9, 1, src="targetsun"),
            {"emp_id": "E1", "sku": "S2", "allocated_boxes": 5},       # ไม่มี engine_boxes
            _row("E1", "S3", 3, 3),
        ]}
        m = edit_report.team_metrics(snap)
        self.assertEqual(m["rows"], 1)
        self.assertEqual(m["edited_cells"], 0)
        self.assertEqual(m["boxes_moved"], 0.0)

    def test_team_without_usable_rows_gets_a_note(self):
        m = edit_report.team_metrics({"allocations": [_row("E1", "S1", 9, 1, src="targetsun")]})
        self.assertEqual(m["rows"], 0)
        self.assertIsNone(m["edited_pct"])
        self.assertTrue(m.get("note"))

    def test_top_lists_cap_at_five(self):
        snap = {"allocations": [_row(f"E{i}", f"S{i}", i + 1, 0) for i in range(8)]}
        m = edit_report.team_metrics(snap)
        self.assertEqual(len(m["top_skus"]), 5)
        self.assertEqual(m["top_skus"][0]["code"], "S7")


class TestEditReportEndpoint(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        # nightly_check อ่าน data/... แบบ path สัมพัทธ์ — ย้ายไปโฟลเดอร์ชั่วคราวกันอ่านไฟล์จริง
        os.chdir(self._tmp.name)
        os.makedirs("data/allocations")
        self._write("SLA", [_row("A1", "S1", 5, 10), _row("A2", "S1", 15, 10)])      # 100%
        self._write("SLB", [_row("B1", "S1", 5, 5), _row("B2", "S2", 3, 1)])         # 50%
        self._write("SLC", [_row("C1", "S1", 7, 2, src="targetsun")])                # ไม่มีแถวให้เทียบ
        self._write("SLA", [_row("A1", "S1", 1, 2)], month=11)                       # คนละงวด
        with open("data/nightly_check_state.json", "w", encoding="utf-8") as f:
            json.dump({"teams": {"SLB|2026-10": {"status": "ok", "changed": 2, "missing": 0, "extra": 1,
                                                 "boxes_changed": 6, "date": "2026-10-16"}}}, f)
        self._p = [
            patch.object(allocation_store, "allocations_dir", return_value=os.path.abspath("data/allocations")),
            patch.object(admin_router, "_supervisor_meta_index", return_value={}),
        ]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in reversed(self._p):
            p.stop()
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _write(self, sup, rows, month=10, year=2026):
        path = os.path.join("data/allocations", f"{sup}_{year}_{month:02d}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"sup_id": sup, "target_month": month, "target_year": year, "status": "draft",
                       "updated_by": f"{sup.lower()}@x", "allocations": rows}, f)

    def _call(self, admin):
        return admin_router.admin_allocations_edit_report(admin=admin, target_month=10, target_year=2026)

    def test_dev_sees_every_team_sorted_by_pct(self):
        out = self._call(DEV)
        self.assertEqual([t["sup_id"] for t in out["teams"]], ["SLA", "SLB", "SLC"])
        a, b, c = out["teams"]
        self.assertEqual(a["edited_pct"], 100.0)
        self.assertEqual(a["boxes_moved"], 5.0)
        self.assertEqual(a["updated_by"], "sla@x")
        self.assertEqual(b["edited_pct"], 50.0)
        self.assertEqual(c["rows"], 0)
        self.assertTrue(c["note"])
        self.assertEqual(out["totals"]["rows"], 4)
        self.assertEqual(out["totals"]["edited_cells"], 3)

    def test_nightly_result_is_attached_when_present(self):
        out = self._call(DEV)
        by = {t["sup_id"]: t for t in out["teams"]}
        self.assertEqual(by["SLB"]["nightly"]["changed"], 2)
        self.assertEqual(by["SLB"]["nightly"]["date"], "2026-10-16")
        self.assertIsNone(by["SLA"]["nightly"])
        self.assertFalse(out["nightly_enabled"])   # ไม่มีไฟล์ค่าตั้ง = ปิด

    def test_scoped_admin_sees_only_own_teams(self):
        admin = {"role": "admin", "email": "a@x", "admin_scope": {"sl_codes": {"SLB"}}}
        out = self._call(admin)
        self.assertEqual([t["sup_id"] for t in out["teams"]], ["SLB"])

    def test_admin_without_scope_sees_nothing(self):
        out = self._call({"role": "admin", "email": "a@x", "admin_scope": None})
        self.assertEqual(out["teams"], [])


class TestWiring(unittest.TestCase):
    def test_endpoint_uses_the_capability_gate(self):
        src = _read("backend/routers/admin.py")
        block = src.split('@router.get("/allocations/edit-report")')[1].split("@router.")[0]
        self.assertIn('require_capability("edit_report")', block)
        self.assertIn("_scoped_allocation_items", block)

    def test_capabilities_registered(self):
        from backend.services import admin_capabilities as caps

        self.assertEqual(caps.tab_for("edit_report"), "editReport")
        self.assertEqual(caps.tab_for("nightly_check"), "nightlyCheck")
        self.assertTrue(caps.can_grant("edit_report", "admin"))
        self.assertFalse(caps.is_grantable("nightly_check"))   # dev เท่านั้น

    def test_edit_report_granted_in_tracked_config(self):
        cfg = json.loads(_read("config/admin_permissions.json"))
        self.assertIn("edit_report", cfg["roles"]["head_admin"])
        self.assertIn("edit_report", cfg["roles"]["admin"])
        for role in cfg["roles"].values():
            self.assertNotIn("nightly_check", role)

    def test_frontend_tabs_exist_and_are_clickable(self):
        html = _read("frontend/index.html")
        app = _read("frontend/app.js")
        for tab in ("editReport", "nightlyCheck"):
            self.assertIn(f'data-tab="{tab}"', html)
            self.assertIn(f'data-panel="{tab}"', html)
            self.assertIn(f"adminSwitchTab('{tab}')", html)
            self.assertIn(f"{tab}: {{", app)
        self.assertIn('_adminActiveTab === "editReport") adminInitEditReportPanel()', app)
        self.assertIn('_adminActiveTab === "nightlyCheck") adminLoadNightlyCheck()', app)
        self.assertIn("/admin/allocations/edit-report", app)
        for path in ("/admin/nightly-check", "/admin/nightly-check/run", "/admin/nightly-check/team"):
            self.assertIn(path, app)
        # ปุ่มในแผงต้องเรียกฟังก์ชันที่มีจริง
        for fn in ("adminLoadEditReport", "adminSaveNightlyCheck", "adminRunNightlyCheckNow"):
            self.assertIn(f'onclick="{fn}()"', html)
            self.assertIn(f"function {fn}(", app)

    def test_run_now_asks_for_confirmation_first(self):
        app = _read("frontend/app.js")
        i = app.index("async function adminRunNightlyCheckNow(")
        body = app[i:app.index("\n}\n", i)]
        self.assertLess(body.index("_confirmDialog"), body.index("/admin/nightly-check/run"))
        self.assertIn("อ่านอย่างเดียว", body)


if __name__ == "__main__":
    unittest.main()
