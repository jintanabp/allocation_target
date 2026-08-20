"""
พนักงานที่ไม่ต้องตั้งเป้า — กรณีพิเศษที่แอดมินกันออกจากการตั้งเป้า/กระจายหีบ

ต่างจาก "ไม่นำไปกระจายเป้า" ที่ระบบอนุมานจากเป้าเงิน: ชุดนี้เป็นการตัดสินใจของคน
จึงต้องอยู่ถาวรจนกว่าจะปลด และต้องกันได้จริงแม้หน้าเว็บรุ่นเก่าจะส่งเขามาให้กระจาย
"""

from __future__ import annotations

import inspect
import json
import os
import sys
import tempfile
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import no_target_store  # noqa: E402


class _TmpStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._path = os.path.join(self._tmp.name, "no_target_employees.json")
        self._prev = os.environ.get("NO_TARGET_EMPLOYEES_JSON_PATH")
        os.environ["NO_TARGET_EMPLOYEES_JSON_PATH"] = self._path

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("NO_TARGET_EMPLOYEES_JSON_PATH", None)
        else:
            os.environ["NO_TARGET_EMPLOYEES_JSON_PATH"] = self._prev
        self._tmp.cleanup()

    def _write(self, rows):
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump({"employees": rows}, f)


class TestStore(_TmpStore):
    def test_missing_file_is_empty_not_an_error(self):
        """ยังไม่เคยตั้งใคร = เรื่องปกติ ไม่ใช่ระบบพัง"""
        self.assertEqual(no_target_store.read_entries(), [])

    def test_corrupt_file_raises(self):
        """'อ่านไม่ออก' กับ 'ไม่มีใครถูกกัน' ต่างกันคนละเรื่อง — ห้ามกลืนเป็นลิสต์ว่าง"""
        with open(self._path, "w", encoding="utf-8") as f:
            f.write("{ ไม่ใช่ json")
        with self.assertRaises(PermissionError):
            no_target_store.read_entries()

    def test_safe_map_falls_open_on_corrupt_file(self):
        """ไฟล์ตั้งค่าเสริมพังต้องไม่ทำให้ซุปทั้งบริษัทเปิดหน้ากระจายหีบไม่ได้"""
        with open(self._path, "w", encoding="utf-8") as f:
            f.write("[[[")
        self.assertEqual(no_target_store.no_target_map_safe(), {})

    def test_normalizes_case_and_spaces(self):
        self._write([{"super_code": " sl509 ", "emp_id": " c444 "}])
        rows = no_target_store.read_entries()
        self.assertEqual(rows[0]["super_code"], "SL509")
        self.assertEqual(rows[0]["emp_id"], "C444")

    def test_drops_rows_without_both_keys(self):
        """คีย์เป็น (ทีม, พนักงาน) ขาดข้างใดข้างหนึ่งก็ระบุตัวไม่ได้"""
        self._write([
            {"super_code": "SL509"},
            {"emp_id": "C444"},
            {"super_code": "SL509", "emp_id": "C444"},
        ])
        self.assertEqual(len(no_target_store.read_entries()), 1)

    def test_same_emp_in_two_teams_stays_separate(self):
        """emp_id ซ้ำข้ามทีมได้ (I7) — กัน SL509 ต้องไม่พลอยกัน SL397 ไปด้วย"""
        self._write([{"super_code": "SL509", "emp_id": "C444"}])
        m = no_target_store.no_target_map()
        self.assertEqual(m["SL509"], {"C444"})
        self.assertNotIn("SL397", m)

    def test_set_for_supervisor_replaces_only_that_team(self):
        no_target_store.set_for_supervisor("SL509", ["C444", "C449"])
        no_target_store.set_for_supervisor("SL397", ["C445"])
        no_target_store.set_for_supervisor("SL509", ["C449"])
        m = no_target_store.no_target_map()
        self.assertEqual(m["SL509"], {"C449"}, "C444 ต้องถูกปลดเพราะไม่ได้ส่งมาในชุดใหม่")
        self.assertEqual(m["SL397"], {"C445"}, "ทีมอื่นห้ามถูกแตะ")

    def test_empty_list_clears_a_team(self):
        no_target_store.set_for_supervisor("SL509", ["C444"])
        no_target_store.set_for_supervisor("SL509", [])
        self.assertEqual(no_target_store.no_target_emp_ids("SL509"), set())

    def test_existing_rows_keep_their_original_timestamp(self):
        """ไม่งั้นทุกครั้งที่กดบันทึกทีม เวลาจะขยับทั้งชุด แล้วตามรอยไม่ได้ว่าใครถูกกันเมื่อไหร่"""
        no_target_store.set_for_supervisor("SL509", ["C444"], updated_by="a@x.com")
        first = {r["emp_id"]: r for r in no_target_store.read_entries()}["C444"]
        no_target_store.set_for_supervisor("SL509", ["C444", "C449"], updated_by="b@x.com")
        again = {r["emp_id"]: r for r in no_target_store.read_entries() if r["emp_id"] == "C444"}
        self.assertEqual(again["C444"]["updated_at"], first["updated_at"])
        self.assertEqual(again["C444"]["updated_by"], "a@x.com")

    def test_union_across_teams_for_the_fallback_path(self):
        no_target_store.set_for_supervisor("SL509", ["C444"])
        no_target_store.set_for_supervisor("SL397", ["C445"])
        self.assertEqual(
            no_target_store.no_target_emp_ids_for_sups(["SL509", "SL397"]),
            {"C444", "C445"},
        )
        self.assertEqual(no_target_store.no_target_emp_ids_for_sups(["SL460"]), set())


class TestOptimizeGuard(_TmpStore):
    """/optimize ไม่เคยกรองพนักงานเลย เชื่อรายชื่อจากหน้าเว็บล้วน"""

    def setUp(self):
        super().setUp()
        from backend.services import optimize

        self.optimize = optimize

    def _req(self, **kw):
        class _R:
            peer_sup_ids: list = []
            target_sup_ids: list = []

        r = _R()
        for k, v in kw.items():
            setattr(r, k, v)
        return r

    def _frame(self, rows):
        return pd.DataFrame(rows)

    def test_drops_the_blocked_employee(self):
        no_target_store.set_for_supervisor("SL509", ["C444"])
        df = self._frame([
            {"emp_id": "C444", "yellow_target": 500.0, "supervisor_code": "SL509"},
            {"emp_id": "C442", "yellow_target": 900.0, "supervisor_code": "SL509"},
        ])
        out, dropped = self.optimize._drop_no_target_employees(df, "SL509", self._req())
        self.assertEqual(dropped, ["C444"])
        self.assertEqual(out["emp_id"].tolist(), ["C442"])

    def test_does_nothing_when_nobody_is_blocked(self):
        df = self._frame([{"emp_id": "C442", "yellow_target": 900.0}])
        out, dropped = self.optimize._drop_no_target_employees(df, "SL509", self._req())
        self.assertEqual(dropped, [])
        self.assertEqual(len(out), 1)

    def test_uses_the_row_team_not_the_request_team(self):
        """โหมดรวมภาค: C444 ถูกกันที่ SL509 เท่านั้น คนชื่อเดียวกันของ SL397 ต้องรอด"""
        no_target_store.set_for_supervisor("SL509", ["C444"])
        df = self._frame([
            {"emp_id": "C444", "yellow_target": 500.0, "supervisor_code": "SL509"},
            {"emp_id": "C444", "yellow_target": 700.0, "supervisor_code": "SL397"},
        ])
        req = self._req(peer_sup_ids=["SL509", "SL397"])
        out, _ = self.optimize._drop_no_target_employees(df, "SL509", req)
        self.assertEqual(out["supervisor_code"].tolist(), ["SL397"])

    def test_old_frontend_without_team_falls_back_to_the_union(self):
        """หน้าเว็บรุ่นเก่าไม่ส่งทีมมา — กันเกินดีกว่ากันขาด เพราะกันขาด = หีบไปผิดคนบนระบบจริง"""
        no_target_store.set_for_supervisor("SL397", ["C445"])
        df = self._frame([
            {"emp_id": "C445", "yellow_target": 500.0},
            {"emp_id": "C442", "yellow_target": 900.0},
        ])
        req = self._req(target_sup_ids=["SL509", "SL397"])
        out, dropped = self.optimize._drop_no_target_employees(df, "SL509", req)
        self.assertEqual(dropped, ["C445"])
        self.assertEqual(out["emp_id"].tolist(), ["C442"])

    def test_union_only_covers_teams_in_this_request(self):
        """ทีมที่ไม่เกี่ยวกับคำขอนี้ต้องไม่ถูกลากมาด้วย"""
        no_target_store.set_for_supervisor("SL999", ["C442"])
        df = self._frame([{"emp_id": "C442", "yellow_target": 900.0}])
        out, dropped = self.optimize._drop_no_target_employees(df, "SL509", self._req())
        self.assertEqual(dropped, [])
        self.assertEqual(len(out), 1)

    def test_wh_split_rows_of_a_blocked_employee_all_go(self):
        """กันเป็นรายคน ไม่ใช่รายคลัง — เหลือคลังเดียวไว้ = ยังได้หีบอยู่ดี"""
        no_target_store.set_for_supervisor("SL509", ["C444"])
        df = self._frame([
            {"emp_id": "C444", "warehouse_code": "R408", "yellow_target": 300.0, "supervisor_code": "SL509"},
            {"emp_id": "C444", "warehouse_code": "R493", "yellow_target": 200.0, "supervisor_code": "SL509"},
            {"emp_id": "C442", "warehouse_code": "R408", "yellow_target": 900.0, "supervisor_code": "SL509"},
        ])
        out, _ = self.optimize._drop_no_target_employees(df, "SL509", self._req())
        self.assertEqual(out["emp_id"].tolist(), ["C442"])

    def test_corrupt_store_does_not_block_allocation(self):
        with open(self._path, "w", encoding="utf-8") as f:
            f.write("nope")
        df = self._frame([{"emp_id": "C442", "yellow_target": 900.0}])
        out, dropped = self.optimize._drop_no_target_employees(df, "SL509", self._req())
        self.assertEqual(dropped, [])
        self.assertEqual(len(out), 1)


class TestPayloadFlag(_TmpStore):
    def test_flag_reaches_the_payload_and_kills_eligibility(self):
        from backend.services.employees import _enrich_employee_allocation_flags

        no_target_store.set_for_supervisor("SL509", ["C444"])
        recs = [
            {"emp_id": "C444", "has_tga_rows": True, "target_sun": 500.0},
            {"emp_id": "C442", "has_tga_rows": True, "target_sun": 900.0},
        ]
        out = {r["emp_id"]: r for r in _enrich_employee_allocation_flags(recs, "SL509")}
        self.assertTrue(out["C444"]["no_target"])
        self.assertFalse(out["C444"]["allocation_eligible"])
        self.assertFalse(out["C444"]["include_in_allocation"])
        self.assertTrue(out["C444"]["view_only"])
        self.assertFalse(out["C442"]["no_target"])
        self.assertTrue(out["C442"]["allocation_eligible"])

    def test_row_team_wins_over_the_loading_team(self):
        """โหมดรวมภาคใส่หลายทีมในลิสต์เดียว — ต้องอ่านทีมจากแถว ไม่ใช่จาก sup_id ที่โหลด"""
        from backend.services.employees import _enrich_employee_allocation_flags

        no_target_store.set_for_supervisor("SL397", ["C445"])
        recs = [
            {"emp_id": "C445", "supervisor_code": "SL397", "has_tga_rows": True, "target_sun": 1.0},
            {"emp_id": "C445", "supervisor_code": "SL509", "has_tga_rows": True, "target_sun": 1.0},
        ]
        out = _enrich_employee_allocation_flags(recs, "SL509")
        self.assertTrue(out[0]["no_target"])
        self.assertFalse(out[1]["no_target"])

    def test_flag_is_always_present_even_when_nobody_is_blocked(self):
        """หน้าเว็บอ่าน e.no_target ตรง ๆ — ฟิลด์ขาดหายเป็นบางแถวทำให้ตรรกะฝั่งจอเดาไม่ถูก"""
        from backend.services.employees import _enrich_employee_allocation_flags

        out = _enrich_employee_allocation_flags(
            [{"emp_id": "C442", "has_tga_rows": True, "target_sun": 900.0}], "SL509"
        )
        self.assertIn("no_target", out[0])
        self.assertFalse(out[0]["no_target"])


class TestWiring(unittest.TestCase):
    def test_every_payload_path_shares_one_enrich_call(self):
        """
        cache hit / สร้างใหม่ / รวมภาค ต้องผ่าน _enrich_employee_allocation_flags ทั้งหมด

        ถ้าเส้นไหนหลุด ทีมนั้นจะได้ payload ที่ไม่มี no_target แล้วคนที่ถูกกันไว้
        จะกลับมาได้หีบเงียบ ๆ เฉพาะเส้นทางนั้น
        """
        from backend.services import employees

        src = inspect.getsource(employees)
        self.assertGreaterEqual(src.count("_enrich_employee_allocation_flags("), 4)

    def test_the_guard_runs_before_the_i8_snapshot(self):
        """
        ต้องตัดก่อน _requested_alloc_keys ไม่งั้นด่าน I8 จะเติมแถว 0 พาคนที่ถูกกันกลับเข้ามา

        I8 มีหน้าที่ "ส่งเข้ามากี่คนต้องได้กลับครบ" ถ้าถ่ายรูปตอนที่คนถูกกันยังอยู่ในเฟรม
        มันจะทำงานถูกต้องตามหน้าที่ตัวเอง แต่ผลคือคนที่แอดมินกันไว้โผล่ในตารางขั้นที่ 3
        """
        from backend.services import optimize

        src = inspect.getsource(optimize.run_optimization_service)
        i_guard = src.index("_drop_no_target_employees(")
        i_i8 = src.index("_requested_alloc_keys(")
        self.assertLess(i_guard, i_i8)

    def test_the_guard_runs_before_the_yellow_filter(self):
        from backend.services import optimize

        src = inspect.getsource(optimize.run_optimization_service)
        i_guard = src.index("_drop_no_target_employees(")
        i_filter = src.index('df_all_targets["yellow_target"] > 0')
        self.assertLess(i_guard, i_filter)

    def test_empty_after_the_guard_says_why(self):
        """ตัดจนเหลือ 0 คนต้องไม่ไปโผล่เป็น 'ทุกคนเป้า 0' ที่อ่านแล้วไม่รู้จะแก้ยังไง"""
        from backend.services import optimize

        src = inspect.getsource(optimize.run_optimization_service)
        i_guard = src.index("_drop_no_target_employees(")
        after = src[i_guard:i_guard + 900]
        self.assertIn("ไม่ต้องตั้งเป้า", after)

    def test_write_endpoint_is_scoped_and_audited(self):
        from backend.routers import admin as admin_router

        src = inspect.getsource(admin_router.admin_set_no_target_employees)
        self.assertIn("require_admin_scoped", src)
        self.assertIn("ensure_sup_in_admin_scope", src)
        self.assertIn("_audit_admin", src)

    def test_read_endpoint_is_open_to_marketing_but_scoped(self):
        from backend.routers import admin as admin_router

        src = inspect.getsource(admin_router.admin_list_no_target_employees)
        self.assertIn("require_admin_or_marketing_team", src)
        self.assertIn("admin_scope", src)


class TestFrontend(unittest.TestCase):
    """ตรวจจากซอร์ส app.js แบบเดียวกับเทสหน้าเว็บตัวอื่น (ไม่มี build step ให้รันจริง)"""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO, "frontend", "app.js"), encoding="utf-8") as f:
            cls.src = f.read()

    def test_both_flag_functions_honour_no_target(self):
        """
        _enrichEmployeeAllocFlags คำนวณ flag ใหม่จาก target_sun ล้วน

        ถ้าแก้แค่ _isAllocEligible การรีเฟรชเป้าสดจะเขียน allocation_eligible=true
        ทับลงไป แล้วคนที่ถูกกันไว้ก็หลุดกลับเข้ากระจายเงียบ ๆ
        """
        for fn in ("_enrichEmployeeAllocFlags", "_isAllocEligible"):
            i = self.src.index(f"function {fn}(")
            body = self.src[i:i + 900]
            self.assertIn("_isNoTargetEmp", body, f"{fn} ต้องเคารพ no_target")

    def test_step2_shows_the_row_instead_of_hiding_it(self):
        i = self.src.index("function _yellowRowHtml(")
        body = self.src[i:i + 400]
        self.assertIn("_yellowNoTargetRowHtml", body)
        self.assertLess(
            body.index("_isNoTargetEmp"),
            body.index("!_isAllocEligible(e)) return \"\""),
            "ต้องเช็ค no_target ก่อนสาขาที่คืนสตริงว่าง ไม่งั้นแถวถูกซ่อนไปก่อน",
        )

    def test_step2_loop_asks_for_the_no_target_rows(self):
        i = self.src.index("function renderYellowTable(")
        body = self.src[i:i + 3000]
        self.assertIn("withNoTarget: true", body)

    def test_no_target_row_has_no_editable_input(self):
        i = self.src.index("function _yellowNoTargetRowHtml(")
        body = self.src[i:self.src.index("function _yellowRowHtml(")]
        self.assertNotIn("cell-input", body)
        self.assertNotIn("onYellowChange", body)

    def test_payload_row_carries_the_team(self):
        i = self.src.index("function _yellowTargetPayloadRow(")
        body = self.src[i:i + 800]
        self.assertIn("supervisor_code", body)

    def test_step1_banner_separates_the_two_reasons(self):
        """'ระบบไม่พบเป้า' ควรไปตาม ส่วน 'แอดมินกันไว้' ถูกต้องแล้ว — ปนกันแล้วผู้ใช้ไล่ผิดเรื่อง"""
        i = self.src.index("const viewOnlyBanner = qs(")
        body = self.src[i:i + 2000]
        self.assertIn("_viewOnlyNotNoTarget", body)
        self.assertIn("_noTargetEmployees", body)


if __name__ == "__main__":
    unittest.main()
