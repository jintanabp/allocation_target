"""
สวิตช์กติกาการเกลี่ย — เปิดเป็นค่าเริ่มต้น ปิดรายทีมได้โดยไม่ต้อง deploy

กติกา "หน่วยไม่เคยขาย = เป้า 0" ตั้งใจเปิดให้ทุกทีม (ผู้ใช้เคาะ 10 ก.ย. 2026)
แต่มันทำให้ตัวเลขที่ซุปเห็นเปลี่ยนจริง — วัดจากงวด 09/2026 หีบย้ายที่ 3.4% ทั้งบริษัท
ถ้าทีมไหนมีปัญหาต้องปิดให้เขาได้ทันที ไม่ใช่รอ deploy รอบหน้า
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import alloc_rules_store as store  # noqa: E402


class AllocRulesStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="allocrules_")
        self._path = os.path.join(self._tmpdir, "allocation_rules.json")
        self._prev = os.environ.get("ALLOC_RULES_JSON_PATH")
        os.environ["ALLOC_RULES_JSON_PATH"] = self._path
        # ต้องชี้ไฟล์ override ไป tmp ด้วย ไม่งั้นค่าที่แอดมินตั้งไว้บนเครื่องจริง
        # จะทับค่าที่เทสเขียน แล้วเทสจะแดง/เขียวตามเครื่องที่รัน
        self._override = os.path.join(self._tmpdir, "alloc_rules.json")
        self._prev_ov = os.environ.get("ALLOC_RULES_OVERRIDE_PATH")
        os.environ["ALLOC_RULES_OVERRIDE_PATH"] = self._override

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("ALLOC_RULES_JSON_PATH", None)
        else:
            os.environ["ALLOC_RULES_JSON_PATH"] = self._prev
        if self._prev_ov is None:
            os.environ.pop("ALLOC_RULES_OVERRIDE_PATH", None)
        else:
            os.environ["ALLOC_RULES_OVERRIDE_PATH"] = self._prev_ov
        import shutil

        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _write(self, obj):
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(obj, f)

    def test_on_by_default_when_there_is_no_file(self):
        """ผู้ใช้เคาะว่าเปิดทุกทีม — ไม่มีไฟล์ตั้งค่าต้องแปลว่าเปิด ไม่ใช่ปิด"""
        self.assertTrue(store.never_sold_zero_enabled("SL509"))

    def test_can_be_turned_off_for_one_team_only(self):
        """ทีมที่มีปัญหาต้องปิดได้เดี่ยว ๆ โดยทีมอื่นไม่กระทบ"""
        self._write({"never_sold_zero": {"enabled": True, "disabled_sups": ["SL531"]}})
        self.assertFalse(store.never_sold_zero_enabled("SL531"))
        self.assertFalse(store.never_sold_zero_enabled(" sl531 "), "ต้องไม่แคร์ตัวพิมพ์/ช่องว่าง")
        self.assertTrue(store.never_sold_zero_enabled("SL509"))

    def test_can_be_turned_off_for_everyone(self):
        """สวิตช์ฉุกเฉิน — ถ้าออกมาผิดทั้งบริษัทต้องปิดได้ทีเดียว"""
        self._write({"never_sold_zero": {"enabled": False}})
        self.assertFalse(store.never_sold_zero_enabled("SL509"))

    def test_broken_file_falls_back_to_default_instead_of_blocking_work(self):
        """ไฟล์ตั้งค่าเสริมพังต้องไม่ทำให้ทั้งบริษัทกระจายเป้าไม่ได้"""
        with open(self._path, "w", encoding="utf-8") as f:
            f.write("{ ไม่ใช่ json")
        self.assertTrue(store.never_sold_zero_enabled("SL509"))
        self.assertEqual(store.push_multiple(), store.DEFAULT_PUSH_MULTIPLE)

    def test_push_multiple_is_configurable(self):
        """เกณฑ์สินค้าดันเป้าต้องปรับได้จาก config ไม่ต้องแก้โค้ด"""
        self._write({"never_sold_zero": {"push_multiple": 8}})
        self.assertEqual(store.push_multiple(), 8.0)

    def test_a_useless_threshold_is_ignored(self):
        """ต่ำกว่า 1 เท่ากับยกเว้นเกือบทุก SKU = กติกาไม่ทำงานเลยโดยไม่มีใครรู้"""
        self._write({"never_sold_zero": {"push_multiple": 0.2}})
        self.assertEqual(store.push_multiple(), store.DEFAULT_PUSH_MULTIPLE)
        self._write({"never_sold_zero": {"push_multiple": "ห้า"}})
        self.assertEqual(store.push_multiple(), store.DEFAULT_PUSH_MULTIPLE)

    def test_the_shipped_config_has_the_rule_on(self):
        """ไฟล์จริงในรีโปต้องตรงกับที่ตกลงไว้ — เปิดทุกทีม เกณฑ์ 5 เท่า"""
        os.environ.pop("ALLOC_RULES_JSON_PATH", None)
        try:
            with open(store.alloc_rules_json_path(), encoding="utf-8") as f:
                cfg = json.load(f)["never_sold_zero"]
            self.assertIs(cfg["enabled"], True)
            self.assertEqual(cfg["push_multiple"], 5)
            self.assertEqual(cfg["disabled_sups"], [])
        finally:
            os.environ["ALLOC_RULES_JSON_PATH"] = self._path


class RoundWithSeveralTeamsTest(AllocRulesStoreTest):
    """
    รอบรวมภาค/รวมหน่วยมีหลายทีมในการคำนวณเดียว แต่กติกาเป็นสวิตช์ตัวเดียวต่อรอบ

    ผู้ใช้เคาะ 11 ก.ย. 2026: **มีทีมไหนสักทีมถูกปิด = ปิดทั้งรอบ**
    ของเดิมดูสวิตช์ของทีมหลักทีมเดียว ทีมที่แอดมินสั่งปิดจึงยังโดนกติกาถ้าไม่ได้เป็นทีมหลัก
    = ปุ่มปิดในหน้าแอดมิน "กดแล้วไม่เกิดอะไร" ซึ่งเป็นอาการที่ยอมไม่ได้
    """

    def test_one_disabled_team_turns_the_rule_off_for_the_whole_round(self):
        self._write({"never_sold_zero": {"enabled": True, "disabled_sups": ["SL531"]}})
        st = store.never_sold_zero_round_state(["SL509", "SL531", "SL406"])
        self.assertFalse(st["enabled"])
        self.assertEqual(st["disabled_sups"], ["SL531"], "ต้องบอกได้ว่าเพราะทีมไหน")
        self.assertFalse(st["system_off"])

    def test_the_disabled_team_no_longer_depends_on_who_pressed_the_button(self):
        """หัวใจของบั๊ก — SL531 ถูกปิดไว้ ต้องปิดไม่ว่าใครเป็นทีมหลักของรอบ"""
        self._write({"never_sold_zero": {"enabled": True, "disabled_sups": ["SL531"]}})
        self.assertFalse(store.never_sold_zero_round_state(["SL531", "SL509"])["enabled"])
        self.assertFalse(store.never_sold_zero_round_state(["SL509", "SL531"])["enabled"])

    def test_a_round_without_any_disabled_team_still_runs_the_rule(self):
        self._write({"never_sold_zero": {"enabled": True, "disabled_sups": ["SL531"]}})
        st = store.never_sold_zero_round_state(["SL509", "SL406"])
        self.assertTrue(st["enabled"])
        self.assertEqual(st["disabled_sups"], [])

    def test_turning_it_off_system_wide_is_reported_separately(self):
        """หน้าจอต้องแยกได้ว่า 'ปิดทั้งระบบ' กับ 'ปิดเพราะทีมนี้' คนละข้อความ"""
        self._write({"never_sold_zero": {"enabled": False, "disabled_sups": []}})
        st = store.never_sold_zero_round_state(["SL509"])
        self.assertFalse(st["enabled"])
        self.assertTrue(st["system_off"])

    def test_it_reports_the_code_that_was_asked_about(self):
        """ผู้ใช้จำรหัสที่ตัวเองเห็นบนจอ ไม่ใช่รหัส canonical หลังแก้ alias"""
        links = os.path.join(self._tmpdir, "sl_links.json")
        prev = os.environ.get("SL_LINKS_JSON_PATH")
        os.environ["SL_LINKS_JSON_PATH"] = links
        try:
            with open(links, "w", encoding="utf-8") as f:
                json.dump({"links": [{"old_sl": "SL508", "new_sls": ["SL524"]}]}, f)
            self._write({"never_sold_zero": {"enabled": True, "disabled_sups": ["SL508"]}})
            st = store.never_sold_zero_round_state(["SL524", "SL359"])
            self.assertFalse(st["enabled"], "SL524 = SL508 ทีมเดียวกัน")
            self.assertEqual(st["disabled_sups"], ["SL524"])
        finally:
            if prev is None:
                os.environ.pop("SL_LINKS_JSON_PATH", None)
            else:
                os.environ["SL_LINKS_JSON_PATH"] = prev

    def test_an_empty_round_is_not_an_excuse_to_turn_the_rule_off(self):
        """ไม่มีรหัสส่งมา (ข้อมูลไม่ครบ) ต้องไม่แปลว่าปิด — ค่าเริ่มต้นคือเปิดเสมอ"""
        self._write({"never_sold_zero": {"enabled": True, "disabled_sups": ["SL531"]}})
        self.assertTrue(store.never_sold_zero_round_state([])["enabled"])
        self.assertTrue(store.never_sold_zero_round_state(["", "  "])["enabled"])

    def test_the_single_team_helper_agrees_with_the_round_version(self):
        """สองทางต้องไม่มีวันตอบคนละอย่าง — never_sold_zero_enabled เป็นทางลัดของตัวนี้"""
        self._write({"never_sold_zero": {"enabled": True, "disabled_sups": ["SL531"]}})
        for code in ("SL531", " sl531 ", "SL509"):
            self.assertEqual(
                store.never_sold_zero_enabled(code),
                store.never_sold_zero_round_state([code])["enabled"],
                code,
            )


if __name__ == "__main__":
    unittest.main()
