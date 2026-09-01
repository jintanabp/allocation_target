"""
ดาวน์โหลด Excel ของ "ผลการกระจาย" ต้องได้เท่าที่เห็นบนจอ

ที่ผู้ใช้เจอ: เลือกเดือนแล้ว แต่กด Excel ได้ครบทุกงวด — ตัวกรองฝั่ง backend
ทำงานถูกมาตลอด สาเหตุจริงคือหน้าจอเปิดมาด้วย "ทุกเดือน/ทุกปี" และช่องค้นหา
บนตารางกรองแค่ฝั่งหน้าเว็บ ไฟล์ที่ได้จึงไม่เคยสนใจคำค้น

กติกาที่ล็อกไว้ที่นี่:
  1. คำค้นกรองด้วยฟิลด์ชุดเดียวกันและ **ลำดับเดียวกัน** กับที่ตารางใช้
  2. คำค้นต้องทำงาน **หลัง** กรองขอบเขตของแอดมินเสมอ — แคบลงได้ ขยายไม่ได้
  3. ชื่อไฟล์บอกงวดจริง ไม่มีคำว่า all_all ที่อ่านแล้วเหมือนบั๊กอีก
  4. ปุ่มดาวน์โหลดต้องไม่ยืม class ปุ่มโหลดใหม่ (ไอคอนหมุน) มาใช้
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

from backend.routers import admin  # noqa: E402


def _read(rel: str) -> str:
    with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
        return fh.read()


def _item(sup, name="", region="", division="", unit=""):
    return {
        "sup_id": sup, "full_name": name, "acc_region": region,
        "acc_division": division, "acc_unit": unit,
    }


class TestSearchFilter(unittest.TestCase):
    ITEMS = [
        _item("SL397", "สมชาย ใจดี", "เหนือ", "Div.B", "credit"),
        _item("SL460", "อารีย์ ทองดี", "ใต้", "Div.S", "van"),
        _item("SL509", "Somsak L.", "กลาง", "Div.B", "credit"),
    ]

    def test_empty_q_is_passthrough(self):
        for empty in (None, "", "   "):
            self.assertEqual(admin._filter_allocation_items_by_q(self.ITEMS, empty), self.ITEMS)

    def test_matches_every_field(self):
        for needle, want in (
            ("SL460", "SL460"), ("อารีย์", "SL460"), ("เหนือ", "SL397"),
            ("Div.S", "SL460"), ("van", "SL460"),
        ):
            got = admin._filter_allocation_items_by_q(self.ITEMS, needle)
            self.assertEqual([r["sup_id"] for r in got], [want], needle)

    def test_case_insensitive(self):
        got = admin._filter_allocation_items_by_q(self.ITEMS, "somsak")
        self.assertEqual([r["sup_id"] for r in got], ["SL509"])

    def test_needle_spanning_two_fields(self):
        """คำค้นคร่อมฟิลด์ได้ — ล็อกลำดับฟิลด์ให้ตรงกับ adminRenderAllocationsTable"""
        got = admin._filter_allocation_items_by_q(self.ITEMS, "SL397 สมชาย")
        self.assertEqual([r["sup_id"] for r in got], ["SL397"])

    def test_field_order_matches_frontend(self):
        """ฝั่งหน้าเว็บต่อสตริงเรียง sup → ชื่อ → ภาค → ดิวิชัน → หน่วย"""
        js = _read("frontend/app.js")
        parts = ["it.sup_id", "it.full_name", "it.acc_region", "it.acc_division", "it.acc_unit"]
        line = next(ln for ln in js.splitlines() if all(p in ln for p in parts))
        self.assertEqual([p for p in parts if p in line], sorted(parts, key=line.index))
        self.assertEqual(
            admin._alloc_search_haystack(self.ITEMS[0]),
            "SL397 สมชาย ใจดี เหนือ DIV.B CREDIT",
        )


class TestExportBasename(unittest.TestCase):
    def test_period_shapes(self):
        self.assertEqual(admin._alloc_export_basename(9, 2026), "allocation_report_2026-09")
        self.assertEqual(
            admin._alloc_export_basename(None, 2026), "allocation_report_2026-all-months"
        )
        self.assertEqual(admin._alloc_export_basename(None, None), "allocation_report_all-periods")

    def test_search_marks_the_file(self):
        self.assertEqual(
            admin._alloc_export_basename(9, 2026, "เหนือ"),
            "allocation_report_2026-09_filtered",
        )
        self.assertEqual(admin._alloc_export_basename(9, 2026, "  "), "allocation_report_2026-09")

    def test_never_all_all_again(self):
        for m, y in ((9, 2026), (None, 2026), (None, None)):
            self.assertNotIn("all_all", admin._alloc_export_basename(m, y))


class TestScopeBeatsSearch(unittest.TestCase):
    """แถวนอกขอบเขตที่ชื่อตรงคำค้น ต้องไม่หลุดเข้าไฟล์"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = self._tmp.name
        allocs = os.path.join(root, "allocations")
        os.makedirs(allocs)
        for sup in ("SL100", "SL200"):
            with open(os.path.join(allocs, f"{sup}_2026_09.json"), "w", encoding="utf-8") as fh:
                json.dump({
                    "sup_id": sup, "target_month": 9, "target_year": 2026,
                    "status": "optimized",
                    "allocations": [{"emp_id": "E1", "sku": "S1", "allocated_boxes": 5}],
                }, fh)
        ua = os.path.join(root, "user_access.json")
        with open(ua, "w", encoding="utf-8") as fh:
            json.dump([
                {"email": "a@x.com", "userpl": "SL100", "full_name": "ทีมเหนือหนึ่ง",
                 "login_kind": "supervisor_acc", "acc_region": "เหนือ", "acc_division": "Div.B"},
                {"email": "b@x.com", "userpl": "SL200", "full_name": "ทีมเหนือสอง",
                 "login_kind": "supervisor_acc", "acc_region": "เหนือ", "acc_division": "Div.B"},
            ], fh, ensure_ascii=False)
        self._env = {"ALLOCATIONS_DATA_DIR": allocs, "USER_ACCESS_JSON_PATH": ua}
        self._old = {k: os.environ.get(k) for k in self._env}
        os.environ.update(self._env)

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    @staticmethod
    def _rows(resp):
        return int(resp.headers["X-Export-Rows"])

    def test_dev_sees_both(self):
        resp = admin.admin_export_allocations_xlsx(
            admin={"role": "dev", "admin_scope": None},
            target_month=9, target_year=2026, q=None,
        )
        self.assertEqual(self._rows(resp), 2)

    def test_search_applied_after_scope(self):
        scoped = {"role": "admin", "admin_scope": {"sl_codes": {"SL100"}}}
        # คำค้น "เหนือ" ตรงทั้งสองทีม แต่ SL200 อยู่นอกขอบเขต ต้องเหลือแถวเดียว
        resp = admin.admin_export_allocations_xlsx(
            admin=scoped, target_month=9, target_year=2026, q="เหนือ",
        )
        self.assertEqual(self._rows(resp), 1)
        # ค้นทีมนอกขอบเขตตรง ๆ ต้องไม่ได้อะไรเลย ไม่ใช่หลุดมาหนึ่งแถว
        resp = admin.admin_export_allocations_xlsx(
            admin=scoped, target_month=9, target_year=2026, q="SL200",
        )
        self.assertEqual(self._rows(resp), 0)

    def test_period_filter_still_narrows(self):
        resp = admin.admin_export_allocations_xlsx(
            admin={"role": "dev", "admin_scope": None},
            target_month=7, target_year=2026, q=None,
        )
        self.assertEqual(self._rows(resp), 0)


class TestFrontendWiring(unittest.TestCase):
    """กันการถอยกลับไปอาการเดิม — ตรวจระดับซอร์ส เพราะไม่มีเทสต์ JS คุมส่วนนี้"""

    def test_panel_starts_on_current_period(self):
        js = _read("frontend/app.js")
        block = js[js.index("function adminInitAllocationsPanel()"):]
        block = block[:block.index("function _adminAllocPeriodChanged")]
        self.assertIn("_effectiveTargetPeriod()", block)

    def test_download_sends_the_search_word(self):
        js = _read("frontend/app.js")
        block = js[js.index("async function adminDownloadAllocationsXlsx()"):]
        block = block[:block.index("\n}\n") + 3]
        self.assertIn("_adminAllocFilterState()", block)
        self.assertIn("export-xlsx?", block)
        self.assertIn("st.params", block)

    def test_excel_buttons_dont_borrow_the_refresh_button(self):
        html = _read("frontend/index.html")
        for handler in ("adminDownloadAllocationsXlsx()", "adminDownloadUsageLogsXlsx()"):
            line = next(ln for ln in html.splitlines() if handler in ln)
            self.assertIn("admin-btn-export", line, handler)
            self.assertNotIn("admin-btn-refresh", line, handler)

    def test_export_icon_never_spins(self):
        css = _read("frontend/style.css")
        self.assertIn(".admin-btn-export {", css)
        hover = re.search(r"\.admin-btn-export:hover \.admin-btn-export__icon \{[^}]*\}", css)
        self.assertIsNotNone(hover)
        self.assertNotIn("rotate(", hover.group(0))


if __name__ == "__main__":
    unittest.main()
