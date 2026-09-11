"""
ของเล็กสามข้อจากแผนหัวข้อ 11 กลุ่ม ค — ทำได้โดยไม่กระทบตัวเลขการกระจายเป้า

ค5 บรรทัด log ที่ขึ้นว่า "ส่งสำเร็จ" ทั้งที่ตัวเองถูกติดธง error
ค4 บันทึกว่ากติกา「ไม่เคยขาย = เป้า 0」แตะอะไรไปเท่าไรต่อทีมต่อรอบ
ค6 ของเล็กหน้าแอดมิน — คอลัมน์ว่างของผู้ที่แก้ไม่ได้ · คลาสตาย · คลาสที่ใช้ร่วมสองหน้า
"""

from __future__ import annotations

import os
import re
import sys
import unittest
from unittest import mock

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)


def _read(rel: str) -> str:
    with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
        return fh.read()


class SendLogTellsTheTruthTest(unittest.TestCase):
    """
    เดิมหัวบรรทัดเขียน "สำเร็จ" ทุกครั้งที่ปลายทางตอบ 200 แม้บรรทัดเดียวกันจะถูกติดธง
    error เพราะยอดลงจริงไม่ตรงไฟล์ — คนไล่ log เห็นคำนั้นแล้วข้ามไป ปัญหาเลยถูกซ่อน
    """

    @classmethod
    def setUpClass(cls):
        from backend.routers import lakehouse as lh_router

        cls.mod = lh_router

    def _log(self, *, success=True, readback=None, excluded=None):
        from backend.schemas import LakehouseUploadRequest

        req = LakehouseUploadRequest(
            sup_id="SL397", target_month=9, target_year=2026, allocations=[]
        )
        res = {
            "targetsun": {"success": success, "result": {}},
            "readback": readback or {},
            "rows_sent": 10,
        }
        if excluded:
            res["excluded_skus"] = excluded
        seen = {}
        with mock.patch.object(
            self.mod, "log_from_user",
            side_effect=lambda user, **kw: seen.update(kw),
        ):
            self.mod._log_targetsun_send({"email": "a@b.c"}, req, res)
        return seen

    def test_a_clean_send_still_reads_as_success(self):
        out = self._log()
        self.assertEqual(out["message"], "ส่งเข้า Target Sun สำเร็จ")
        self.assertEqual(out["level"], "info")

    def test_a_failed_send_says_so(self):
        out = self._log(success=False)
        self.assertIn("ไม่สำเร็จ", out["message"])
        self.assertEqual(out["level"], "error")

    def test_a_readback_mismatch_no_longer_hides_behind_the_word_success(self):
        out = self._log(readback={"checked": True, "ok": False, "diff_count": 3, "diff_boxes": -12})
        self.assertEqual(out["level"], "error", "ธงต้องยังเป็น error เหมือนเดิม")
        self.assertIn("ยอดลงจริงไม่ตรงไฟล์", out["message"],
                      "หัวบรรทัดต้องบอกเอง ไม่ใช่ซ่อนอยู่ท้าย detail")

    def test_it_keeps_the_word_success_so_old_rows_are_still_counted_right(self):
        """
        usage_summary ตัดสินแถวเก่าที่ไม่มี context.ok ด้วยข้อความ
        ("ไม่สำเร็จ" not in msg and "สำเร็จ" in msg) — ถ้าตัดคำนี้ทิ้ง
        การส่งที่ของลงปลายทางไปแล้วจะถูกนับเป็นล้มเหลว
        """
        from backend.services.usage_summary import _send_ok

        out = self._log(readback={"checked": True, "ok": False, "diff_count": 1, "diff_boxes": 1})
        self.assertTrue(_send_ok({"message": out["message"]}))

    def test_being_unable_to_check_is_not_reported_as_a_mismatch(self):
        """'ตรวจยอดหลังส่งไม่ได้' กับ 'ยอดไม่ตรง' คนละเรื่อง ห้ามเขียนรวมกัน"""
        out = self._log(readback={"checked": False, "reason": "ปลายทางไม่ตอบ"})
        self.assertIn("ตรวจยอดหลังส่งไม่ได้", out["message"])
        self.assertNotIn("ไม่ตรงไฟล์", out["message"])
        self.assertEqual(out["level"], "info", "ตรวจไม่ได้ไม่ใช่ความผิดพลาดของการส่ง")


class NeverSoldImpactIsRecordedTest(unittest.TestCase):
    """
    เปิดกติกาให้ทุกทีมพร้อมกัน = ไม่มีกลุ่มเปรียบเทียบ ตอบไม่ได้ว่า "ดีขึ้นไหม"
    สิ่งที่ยังเก็บได้คือ "แตะไปเท่าไร" ต่อทีมต่อรอบ เอาไปคู่กับสัดส่วนการแก้มืองวดหน้า
    """

    @classmethod
    def setUpClass(cls):
        cls.src = _read("backend/services/optimize.py")

    def test_the_numbers_go_back_to_the_screen(self):
        self.assertIn('"never_sold_impact": never_sold_impact', self.src)

    def test_it_counts_cells_and_the_size_of_what_it_touched(self):
        i = self.src.index("never_sold_impact = {")
        block = self.src[i : i + 500]
        for key in ("cells_zeroed", "skus_zeroed", "skus_evened", "boxes_in_touched_skus"):
            self.assertIn(key, block, key)

    def test_it_does_not_claim_to_know_how_many_boxes_moved(self):
        """
        รู้ไม่ได้ว่าถ้าไม่มีกติกาแล้วหีบจะไปอยู่ที่ใคร ถ้าไม่รันสองรอบ —
        ชื่อฟิลด์ต้องไม่ทำให้คนอ่านเข้าใจว่าเป็น "หีบที่ย้าย"
        """
        i = self.src.index("never_sold_impact = {")
        block = self.src[i : i + 500]
        self.assertNotIn("boxes_moved", block)

    def test_it_writes_a_log_line_with_the_team_and_period(self):
        i = self.src.index("never_sold_impact = {")
        block = self.src[i : i + 1400]
        self.assertIn("logger.info", block)
        self.assertIn("sup_id", block)


class AdminSmallFixesTest(unittest.TestCase):
    def test_the_dead_compact_class_is_gone_for_good(self):
        """
        คลาสนี้ไม่เคยมีผล (ประกาศก่อน .admin-table ที่ specificity เท่ากัน)
        ถ้าใครปลุกกลับมา 6 ตารางในหน้าแอดมินจะเหลือตัวอักษร 12px ทันที
        ซึ่งขัดกติกา "ผู้ใช้สูงวัย อ่านง่ายไว้ก่อน" — ทางที่ถูกคือลบทิ้ง
        """
        for rel in ("frontend/index.html", "frontend/app.js"):
            self.assertNotIn("admin-table--compact", _read(rel), rel)
        css = _read("frontend/style.css")
        self.assertNotIn(".admin-table--compact {", css)
        self.assertNotIn("#infoModal .admin-table--compact", css)

    def test_the_modal_table_keeps_its_width(self):
        """กฎเดิมเกาะชื่อคลาสที่เพิ่งลบ — ต้องย้ายมาเกาะตัวที่ยังอยู่ ไม่ใช่หายไปด้วย"""
        css = _read("frontend/style.css")
        self.assertIn("#infoModal .admin-table-wrap .admin-table { width: 100%; }", css)

    def test_read_only_viewers_do_not_see_an_empty_actions_column(self):
        js = _read("frontend/app.js")
        i = js.index("function adminRenderSlLinks(")
        block = js[i : i + 1400]
        self.assertIn("adminSlLinkActionsTh", block, "ต้องซ่อนหัวคอลัมน์ด้วย")
        self.assertIn("th.hidden = !canEdit", block)
        self.assertIn('canEdit ? `<td class="admin-td-actions"', block, "และไม่วาดช่องว่าง")
        self.assertIn('colspan="${cols}"', block, "แถวว่างต้องนับคอลัมน์ให้ตรง")

    def test_the_actions_header_can_actually_be_hidden(self):
        self.assertIn('id="adminSlLinkActionsTh"', _read("frontend/index.html"))

    def test_the_shared_status_colours_survive_a_future_table_rule(self):
        """
        .alloc-summary-status--* ใช้ร่วมกันทั้ง Dashboard และหน้าแอดมิน ·
        .admin-table td มี specificity สูงกว่าคลาสเปล่า ถ้าวันหนึ่งมีใครใส่ color ให้มัน
        สีสถานะในหน้าแอดมินจะตายเงียบ ๆ — ตัวเลือก td.… กันไว้
        """
        css = _read("frontend/style.css")
        for name in ("sent", "optimized", "draft", "none"):
            self.assertIn(f"td.alloc-summary-status--{name}", css, name)


if __name__ == "__main__":
    unittest.main()
