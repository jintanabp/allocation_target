"""
หน้าแอดมิน「กติกาการเกลี่ย」 — เปิด/ปิดกติกา「ไม่เคยขาย = เป้า 0」ได้โดยไม่ต้อง deploy

สองข้อที่ต้องไม่พังเด็ดขาด:
  1. **ค่าที่แอดมินตั้งต้องอยู่ใต้ data/ ไม่ใช่ config/** — ไฟล์ใน config ที่ track ใน git
     ถูก `git pull` ทับตอน deploy ถ้าเก็บผิดที่ ทีมที่สั่งปิดกติกาไว้จะถูกเปิดคืนเงียบ ๆ
     แล้วเป้าของทั้งทีมเปลี่ยนโดยไม่มีใครรู้
  2. **ค่าที่ใช้ไม่ได้ต้องแจ้งกลับตอนกดบันทึก** ไม่ใช่เงียบ ๆ แล้วถอยไปใช้ค่าเริ่มต้น
     ไม่งั้นแอดมินเห็น「บันทึกแล้ว」ทั้งที่ค่าที่ระบบใช้จริงไม่ใช่ค่าที่เพิ่งพิมพ์
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

from fastapi import HTTPException

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.routers import admin as admin_router  # noqa: E402
from backend.services import alloc_rules_store as store  # noqa: E402

ADMIN = {"email": "boss@spc.co.th", "role": "head_admin"}


def _read(rel: str) -> str:
    with open(os.path.join(REPO, rel), encoding="utf-8") as f:
        return f.read()


class AllocRulesSettingsTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="allocrules_admin_")
        self._cfg = os.path.join(self._tmpdir, "allocation_rules.json")
        self._ov = os.path.join(self._tmpdir, "data", "alloc_rules.json")
        self._prev = (
            os.environ.get("ALLOC_RULES_JSON_PATH"),
            os.environ.get("ALLOC_RULES_OVERRIDE_PATH"),
        )
        os.environ["ALLOC_RULES_JSON_PATH"] = self._cfg
        os.environ["ALLOC_RULES_OVERRIDE_PATH"] = self._ov
        with open(self._cfg, "w", encoding="utf-8") as f:
            json.dump(
                {"_readme": "ค่าตั้งต้นจากโค้ด",
                 "never_sold_zero": {"enabled": True, "push_multiple": 5, "disabled_sups": []}},
                f,
            )

    def tearDown(self):
        for key, prev in zip(("ALLOC_RULES_JSON_PATH", "ALLOC_RULES_OVERRIDE_PATH"), self._prev):
            if prev is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    # ── ที่เก็บ ──────────────────────────────────────────────────────────

    def test_saving_never_touches_the_file_that_deploy_overwrites(self):
        """หัวใจของงานนี้ — เขียนลง data/ เท่านั้น ไฟล์ใน config/ ต้องเหมือนเดิมเป๊ะ"""
        before = _readfile(self._cfg)
        store.write_settings(enabled=False, push_multiple=5, disabled_sups=["SL531"], updated_by="boss@spc.co.th")
        self.assertEqual(_readfile(self._cfg), before, "ห้ามแตะ config/allocation_rules.json")
        self.assertTrue(os.path.isfile(self._ov), "ค่าที่ตั้งต้องไปอยู่ใต้ data/")

    def test_the_admin_value_wins_over_the_one_shipped_with_the_code(self):
        self.assertTrue(store.never_sold_zero_enabled("SL359"))
        store.write_settings(enabled=False, push_multiple=5, disabled_sups=[])
        self.assertFalse(store.never_sold_zero_enabled("SL359"))
        self.assertEqual(store.read_settings()["source"], "admin")

    def test_reset_brings_back_what_the_code_shipped(self):
        store.write_settings(enabled=False, push_multiple=9, disabled_sups=["SL531"])
        out = store.reset_settings()
        self.assertEqual(out["source"], "config")
        self.assertTrue(out["enabled"])
        self.assertEqual(out["push_multiple"], 5.0)
        self.assertFalse(os.path.isfile(self._ov))

    def test_reset_is_safe_when_nothing_was_ever_saved(self):
        self.assertEqual(store.reset_settings()["source"], "config")

    # ── ตรวจค่าก่อนเขียน ─────────────────────────────────────────────────

    def test_a_threshold_below_one_is_refused_loudly(self):
        with self.assertRaises(ValueError) as ctx:
            store.write_settings(enabled=True, push_multiple=0.5, disabled_sups=[])
        self.assertIn("ปิดกฎ", str(ctx.exception))
        self.assertFalse(os.path.isfile(self._ov), "ค่าที่ใช้ไม่ได้ต้องไม่ถูกเขียนลงไฟล์")

    def test_a_silly_high_threshold_is_refused_too(self):
        with self.assertRaises(ValueError):
            store.write_settings(enabled=True, push_multiple=1000, disabled_sups=[])

    def test_team_codes_are_tidied_up_before_saving(self):
        out = store.write_settings(
            enabled=True, push_multiple=5, disabled_sups=[" sl531 ", "SL531", "", "sl406"]
        )
        self.assertEqual(out["disabled_sups"], ["SL406", "SL531"])

    # ── สองคนแก้พร้อมกัน ─────────────────────────────────────────────────

    def test_the_second_admin_is_told_instead_of_overwriting_the_first(self):
        store.write_settings(enabled=True, push_multiple=5, disabled_sups=["SL531"], expected_rev=0)
        with self.assertRaises(store.AllocRulesConflict):
            store.write_settings(enabled=True, push_multiple=5, disabled_sups=["SL406"], expected_rev=0)

    def test_saving_without_a_rev_still_works(self):
        """ตัวเรียกที่ไม่ส่ง rev (เช่นสคริปต์) ต้องไม่โดนบล็อก"""
        store.write_settings(enabled=True, push_multiple=5, disabled_sups=[])
        out = store.write_settings(enabled=False, push_multiple=5, disabled_sups=[])
        self.assertEqual(out["rev"], 2)

    # ── รหัสทีมที่ผูกกันไว้ ───────────────────────────────────────────────

    def test_a_team_that_logs_in_with_an_old_code_is_still_covered(self):
        """SL524 ผูกไว้กับ SL508 = ทีมเดียวกัน ปิดรหัสหนึ่งต้องปิดอีกรหัสด้วย"""
        links = os.path.join(self._tmpdir, "sl_links.json")
        prev = os.environ.get("SL_LINKS_JSON_PATH")
        os.environ["SL_LINKS_JSON_PATH"] = links
        try:
            with open(links, "w", encoding="utf-8") as f:
                json.dump({"links": [{"old_sl": "SL508", "new_sls": ["SL524"]}]}, f)
            store.write_settings(enabled=True, push_multiple=5, disabled_sups=["SL508"])
            self.assertFalse(store.never_sold_zero_enabled("SL524"))
            self.assertTrue(store.never_sold_zero_enabled("SL359"))
        finally:
            if prev is None:
                os.environ.pop("SL_LINKS_JSON_PATH", None)
            else:
                os.environ["SL_LINKS_JSON_PATH"] = prev

    # ── เส้น API ─────────────────────────────────────────────────────────

    def test_the_screen_gets_everything_it_needs_to_draw_itself(self):
        data = admin_router.admin_get_alloc_rules(_admin=ADMIN)
        for key in ("enabled", "push_multiple", "disabled_sups", "source", "rev", "teams",
                    "default_push_multiple", "min_push_multiple", "max_push_multiple"):
            self.assertIn(key, data, key)

    def test_saving_through_the_api_records_who_did_it(self):
        seen = {}
        orig = admin_router._audit_admin
        admin_router._audit_admin = lambda *a, **kw: seen.update({"args": a, "kw": kw})
        try:
            out = admin_router.admin_put_alloc_rules(
                admin_router.AllocRulesBody(enabled=False, push_multiple=7, disabled_sups=["SL531"]),
                admin=ADMIN,
            )
        finally:
            admin_router._audit_admin = orig
        self.assertTrue(out["ok"])
        self.assertEqual(out["push_multiple"], 7.0)
        self.assertEqual(seen["args"][1], "admin_alloc_rules_update")
        self.assertEqual(seen["kw"]["level"], "warn")
        self.assertIn("before", seen["kw"]["context"])
        self.assertTrue(seen["kw"]["context"]["before"]["enabled"], "ต้องเก็บค่าก่อนแก้ไว้ด้วย")
        self.assertEqual(store.read_settings()["updated_by"], "boss@spc.co.th")

    def test_a_bad_threshold_from_the_api_is_400_with_a_thai_reason(self):
        with self.assertRaises(HTTPException) as ctx:
            admin_router.admin_put_alloc_rules(
                admin_router.AllocRulesBody.model_construct(
                    enabled=True, push_multiple=0.1, disabled_sups=[], expected_rev=None
                ),
                admin=ADMIN,
            )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("เท่า", ctx.exception.detail)

    def test_a_stale_screen_gets_409(self):
        admin_router.admin_put_alloc_rules(
            admin_router.AllocRulesBody(enabled=True, push_multiple=5, disabled_sups=[], expected_rev=0),
            admin=ADMIN,
        )
        with self.assertRaises(HTTPException) as ctx:
            admin_router.admin_put_alloc_rules(
                admin_router.AllocRulesBody(enabled=False, push_multiple=5, disabled_sups=[], expected_rev=0),
                admin=ADMIN,
            )
        self.assertEqual(ctx.exception.status_code, 409)


class AllocRulesWiringTest(unittest.TestCase):
    def setUp(self):
        self.src = _read("backend/routers/admin.py")

    def test_all_three_routes_are_head_admin_only(self):
        for marker in (
            '@router.get("/settings/alloc-rules")',
            '@router.put("/settings/alloc-rules")',
            '@router.post("/settings/alloc-rules/reset")',
        ):
            block = self.src.split(marker)[1].split("@router.")[0]
            self.assertIn('require_capability("alloc_rules")', block, marker)

    def test_the_team_list_does_not_trigger_a_hierarchy_rebuild(self):
        """
        list_supervisor_codes() เรียก load_hierarchy_payload() ซึ่ง rebuild ลำดับชั้นทั้งระบบได้
        หน้าตั้งค่าที่แค่เปิดดูต้องไม่ทำแบบนั้น — ใช้ allocating_teams() ที่อ่านไฟล์ล้วน
        """
        block = self.src.split("def _alloc_rules_payload(")[1].split("\n@router.")[0]
        self.assertIn("allocating_teams", block)
        self.assertNotIn("list_supervisor_codes", block)

    def test_the_frontend_tab_is_wired(self):
        app_js = _read("frontend/app.js")
        self.assertIn("/admin/settings/alloc-rules", app_js)
        self.assertIn("adminLoadAllocRules", app_js)
        self.assertIn('data-tab="allocRules"', _read("frontend/index.html"))

    def test_the_screen_says_what_the_switch_costs_before_it_is_pressed(self):
        html = _read("frontend/index.html")
        panel = html.split('data-panel="allocRules"')[1].split('id="adminPanel')[0]
        self.assertIn("3.4%", panel, "ต้องบอกขนาดผลกระทบที่วัดได้")
        self.assertIn("7.2%", panel)
        self.assertIn("รวมภาค", panel, "ต้องเตือนว่าปิดรายทีมไม่ครอบโหมดรวมภาค")


class RoundCheckForTheAllocationScreenTest(AllocRulesSettingsTest):
    """
    หน้าจอต้องรู้ **ก่อนกด** ว่ารอบรวมภาคนี้กติกาจะไม่ทำงาน เพราะมีทีมที่ถูกสั่งปิดอยู่ในชุด
    ถ้ารู้ตอนเลขออกมาแล้วคือสายไป — ซุปกระจายเสร็จแล้วถึงมาเห็นว่าเลขไม่เหมือนที่คาด
    """

    def test_a_normal_user_can_ask_about_the_teams_on_their_screen(self):
        store.write_settings(enabled=True, push_multiple=5, disabled_sups=["SL531"])
        out = admin_router.alloc_rules_round_check(
            body=admin_router.AllocRulesRoundBody(sup_ids=["SL509", "SL531"]),
            _user={"email": "sup@spc.co.th"},
        )
        self.assertFalse(out["enabled"])
        self.assertEqual(out["disabled_sups"], ["SL531"])

    def test_it_only_answers_about_the_codes_it_was_asked(self):
        """ห้ามกลายเป็นเส้นอ่านรายชื่อทีมที่ถูกปิดทั้งบริษัทให้ใครก็ได้"""
        store.write_settings(enabled=True, push_multiple=5, disabled_sups=["SL531", "SL406"])
        out = admin_router.alloc_rules_round_check(
            body=admin_router.AllocRulesRoundBody(sup_ids=["SL509", "SL531"]),
            _user={"email": "sup@spc.co.th"},
        )
        self.assertEqual(out["disabled_sups"], ["SL531"], "SL406 ไม่ได้ถูกถาม ห้ามหลุดออกไป")

    def test_the_read_route_is_still_admin_only(self):
        """เส้นอ่านค่าทั้งก้อนต้องไม่ถูก 'จัดระเบียบ' ให้เปิดตามเส้นใหม่นี้"""
        src = _read("backend/routers/admin.py")
        after = src.split('@router.get("/settings/alloc-rules")')[1]
        block = after.split("@router.")[0]
        self.assertIn('require_capability("alloc_rules")', block)

    def test_the_new_route_lives_under_admin_because_of_the_proxy(self):
        """prefix ใหม่ระดับรากได้ 404 เปล่าจาก proxy หน้า production — ดู feedback/submit"""
        src = _read("backend/routers/admin.py")
        self.assertIn('@router.post("/alloc-rules/round-check")', src)


class TheWholeRoundFollowsTheSwitchTest(unittest.TestCase):
    """
    บั๊กที่แก้รอบนี้: `/optimize` ถูกยิงครั้งเดียวต่อรอบรวมภาค โดยส่งรหัสทีมหลักมาตัวเดียว
    ถ้ายังดูสวิตช์ของทีมหลักอย่างเดียว ทีมที่แอดมินสั่งปิดจะยังโดนกติกา
    """

    @classmethod
    def setUpClass(cls):
        cls.py = _read("backend/services/optimize.py")
        cls.js = _read("frontend/app.js")

    def test_the_engine_asks_about_every_team_in_the_round(self):
        self.assertIn("never_sold_zero_round_state(hist_sup_ids)", self.py)
        self.assertNotIn("never_sold_zero_enabled(sup_id)", self.py)

    def test_the_engine_says_why_it_turned_the_rule_off(self):
        """ปิดเงียบไม่ได้ — ต้องส่งเหตุผลกลับไปให้หน้าจอพูดได้ว่าเพราะทีมไหน"""
        self.assertIn('"never_sold_off_reason": never_sold_off_reason', self.py)
        for reason in ("team_disabled", "system_off", "no_hist_12m"):
            self.assertIn(reason, self.py, reason)

    def test_both_frontend_paths_carry_the_reason(self):
        """ทีมเดียวกับรวมภาคเป็นคนละเส้นทาง — พลาดเส้นไหนเส้นนั้นเงียบ (บทเรียน R3)"""
        self.assertIn("S.neverSoldOffReasons = json.never_sold_off_reason", self.js)
        self.assertIn("S.neverSoldOffReasons = neverSoldOff;", self.js)

    def test_the_panel_actually_shows_it(self):
        """บทเรียน 8 ก.ย. — มีฟังก์ชันแต่ไม่มีใครเรียก = ฟีเจอร์ตายเงียบ"""
        i = self.js.index("function _neverSoldReviewLines(")
        self.assertIn("_neverSoldOffLines()", self.js[i : i + 400])

    def test_the_message_survives_an_empty_summary(self):
        """
        กติกาถูกปิด = สรุปว่างเสมอ ถ้ายัง return [] ตอนสรุปว่าง ข้อความจะไม่มีวันขึ้น
        (คำเตือนที่ขึ้นเฉพาะตอนไม่ต้องใช้ = คำเตือนที่ไม่มีอยู่จริง)
        """
        i = self.js.index("function _neverSoldReviewLines(")
        body = self.js[i : i + 400]
        self.assertIn("if (!items.length) return out;", body)

    def test_the_scope_modal_warns_before_the_button_is_pressed(self):
        i = self.js.index("async function openAllocScopeModal(")
        j = self.js.index("return new Promise((resolve)", i)
        block = self.js[i:j]
        self.assertIn("_allocRulesRoundCheck(supOrder)", block)
        self.assertIn("แยกตามทีม", block, "ต้องบอกทางออกที่กดได้จริงในโมดอลใบเดียวกัน")

    def test_a_broken_check_does_not_block_the_allocation(self):
        """เส้นถามล่มต้องเสียแค่คำเตือน ไม่ใช่กดกระจายไม่ได้"""
        i = self.js.index("async function _allocRulesRoundCheck(")
        self.assertIn("return null;", self.js[i : i + 700])


def _readfile(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


if __name__ == "__main__":
    unittest.main()
