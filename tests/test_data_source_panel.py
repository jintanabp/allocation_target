"""
หน้า「แหล่งข้อมูลในระบบ」— สิ่งที่จัดระเบียบไปแล้วต้องไม่ถอยกลับ

วัดของเดิมไว้ก่อนแก้ (เปิดหน้าจริงในเซิร์ฟเวอร์แซนด์บ็อกซ์ วัด DOM):
  การ์ดสามใบรวม 2,133px · 22 ปุ่ม · ข้อความช่วยเหลือ 433 ตัวอักษรเป็นพรืด ·
  เวลาแบบ ISO ดิบ 9 จุด · หมวดย่อยเป็น <details> 5 อันเรียงซ้อน (พับอยู่ก็กินที่)
หลังแก้: การ์ดสามใบรวม 1,099px (ลด 48%) · การ์ดใบแรก 904 -> 405px · ISO เหลือ 0
"""

from __future__ import annotations

import os
import re
import sys
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)


def _read(rel: str) -> str:
    with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
        return fh.read()


APP = _read("frontend/app.js")
HTML = _read("frontend/index.html")
CSS = _read("frontend/style.css")


def _fn(name: str, span: int = 4000) -> str:
    i = APP.index(f"function {name}(")
    return APP[i : i + span]


class TestTimesAreReadable(unittest.TestCase):
    """เดิมโชว์ ISO ดิบ + ไมโครวินาที + UTC ทำให้อ่านผิดไป 7 ชั่วโมง"""

    def test_the_formatter_exists_and_uses_bangkok(self):
        body = _fn("_adminFmtTime", 800)
        self.assertIn('timeZone: "Asia/Bangkok"', body)
        self.assertIn('toLocaleString("th-TH"', body)

    def test_it_returns_the_raw_value_when_it_cannot_parse(self):
        """แปลงไม่ได้ต้องคืนค่าเดิม ไม่ใช่กลืนข้อมูลหาย"""
        body = _fn("_adminFmtTime", 800)
        self.assertIn("isNaN(d.getTime())", body)

    def test_every_inventory_timestamp_goes_through_it(self):
        body = _fn("_adminRenderInventory", 4000) + _fn("adminOpenInventoryDetail", 5000)
        for field in ("access_hierarchy_mtime", "managers_cache_mtime", "latest_mtime", "generated_at"):
            m = re.search(r"[\w.]*" + field, body)
            self.assertIsNotNone(m, field)
            self.assertIn(f"_adminFmtTime({m.group(0)})", body, f"{field} ยังไม่ผ่านตัวจัดรูปเวลา")


class TestSubsectionsOpenSeparately(unittest.TestCase):
    """เดิมเป็น <details> ห้าอันเรียงซ้อนกันในการ์ดเดียว — พับอยู่ก็กินที่เท่ากล่องเปล่า"""

    def test_the_inventory_no_longer_stacks_accordions(self):
        body = _fn("_adminRenderInventory", 4000)
        self.assertNotIn("<details", body, "กลับไปใช้ <details> ซ้อนกันอีกแล้ว")

    def test_it_renders_clickable_tiles(self):
        body = _fn("_adminRenderInventory", 4000)
        self.assertIn("admin-inv-tile", body)
        self.assertIn("adminOpenInventoryDetail(", body)

    def test_every_tile_kind_has_a_detail_view(self):
        render = _fn("_adminRenderInventory", 4000)
        detail = _fn("adminOpenInventoryDetail", 5000)
        # onclick ถูกประกอบจาก template literal — ชนิดจริงอยู่ในการเรียก tile("...")
        kinds = set(re.findall(r'\btile\("(\w+)"', render))
        self.assertGreaterEqual(len(kinds), 4, "ต้องมีแผ่นกดดูครบทุกหมวดที่เคยเป็น <details>")
        for k in kinds:
            self.assertIn(f'kind === "{k}"', detail, f"แผ่น {k} กดแล้วไม่มีอะไรขึ้น")

    def test_the_detail_view_reuses_the_shared_modal(self):
        """อย่าสร้างโมดัลเส้นที่สอง — ของกลางจัดการ Escape/คลิกพื้นหลังให้แล้ว"""
        self.assertIn("_showInfoModal({ title, bodyHtml: body })", _fn("adminOpenInventoryDetail", 5000))

    def test_wide_modals_only_apply_to_the_ones_with_tables(self):
        """ใบที่เป็นข้อความยืนยันสั้น ๆ ต้องแคบเหมือนเดิม"""
        self.assertIn("#infoModal .modal-card:has(.admin-table-wrap)", CSS)


class TestTheHeaderIsGrouped(unittest.TestCase):
    def test_buttons_are_split_into_labelled_groups(self):
        i = HTML.index('data-panel="data"')
        j = HTML.index('data-panel="', i + 10)
        panel = HTML[i:j]
        self.assertGreaterEqual(
            panel.count("admin-btn-group__label"), 2,
            "ปุ่มต้องถูกจับกลุ่มพร้อมป้ายกำกับ ไม่ใช่เรียงพรืดแถวเดียว",
        )

    def test_the_personal_data_warning_is_still_visible(self):
        """ย่อข้อความช่วยเหลือได้ แต่คำเตือนเรื่องอีเมลพนักงานห้ามหาย"""
        i = HTML.index('data-panel="data"')
        j = HTML.index('data-panel="', i + 10)
        panel = HTML[i:j]
        self.assertIn("admin-note-warn", panel)
        self.assertIn("อีเมลพนักงาน", panel)


class TestRepeatedErrorsCollapse(unittest.TestCase):
    """หกช่องพังด้วยเหตุเดียวกัน = เรื่องเดียว ไม่ใช่หกเรื่อง"""

    def test_a_shared_error_becomes_one_banner(self):
        body = _fn("adminRenderTargetPeriods", 2500)
        self.assertIn("sharedErr", body)
        self.assertIn("admin-target-periods__banner", body)

    def test_different_errors_still_show_per_card(self):
        body = _fn("adminRenderTargetPeriods", 2500)
        self.assertIn("p.error && !sharedErr", body)


class TestCssLivesAtTheEnd(unittest.TestCase):
    """กติกาของโปรเจกต์: กฎที่ประกาศก่อน .admin-* จะแพ้ลำดับเสมอ (เจอซ้ำหลายรอบ)"""

    def test_the_new_rules_come_after_the_base_admin_table_rule(self):
        base = CSS.index(".admin-table {")
        for sel in ("#adminView .admin-inv-tile {", "#adminView .admin-btn-group {"):
            self.assertGreater(CSS.index(sel), base, f"{sel} ต้องอยู่หลัง .admin-table")


if __name__ == "__main__":
    unittest.main()
