"""
หน้าแอดมิน「กติกาบังคับคลัง」— เส้น API (CRUD/CAS/สิทธิ์)

โครงเทสเดียวกับ tests/test_alloc_rules_admin.py — เรียกฟังก์ชัน route ตรง ๆ (ไม่ผ่าน
FastAPI DI จริง) ส่วนการบังคับสิทธิ์ตรวจแบบ static-scan ว่าทุก route ผูก
require_capability("warehouse_pin_rules") ไว้จริง (แบบเดียวกับที่ alloc_rules ทำ)
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
from fastapi import HTTPException

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.routers import admin as admin_router  # noqa: E402
from backend.services import warehouse_pin_rules_store as store  # noqa: E402

ADMIN = {"email": "boss@spc.co.th", "role": "head_admin"}


def _read(rel: str) -> str:
    with open(os.path.join(REPO, rel), encoding="utf-8") as f:
        return f.read()


def _rule_body(section="702", skus=None, area="3", div="S", wh="G010"):
    return admin_router.WarehousePinRuleBody(
        section=section, skus=skus if skus is not None else ["SKU1"],
        areacode=area, divisioncode=div, warehouse_code=wh,
    )


class WarehousePinRulesApiTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_env = os.environ.get("WAREHOUSE_PIN_RULES_PATH")
        os.environ["WAREHOUSE_PIN_RULES_PATH"] = os.path.join(self._tmp.name, "rules.json")
        self._cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self._cwd)  # ต้องออกจาก tmp ก่อนลบ (Windows ลบโฟลเดอร์ที่ยืนอยู่ไม่ได้)
        if self._old_env is None:
            os.environ.pop("WAREHOUSE_PIN_RULES_PATH", None)
        else:
            os.environ["WAREHOUSE_PIN_RULES_PATH"] = self._old_env
        self._tmp.cleanup()

    def test_get_returns_empty_state_when_nothing_configured(self):
        out = admin_router.admin_get_warehouse_pin_rules(_admin=ADMIN)
        self.assertEqual(out["rules"], [])
        self.assertEqual(out["rev"], 0)

    def test_put_saves_and_audits(self):
        seen = {}
        orig = admin_router._audit_admin
        admin_router._audit_admin = lambda *a, **kw: seen.update({"args": a, "kw": kw})
        try:
            out = admin_router.admin_put_warehouse_pin_rules(
                admin_router.WarehousePinRulesBody(rules=[_rule_body()]), admin=ADMIN
            )
        finally:
            admin_router._audit_admin = orig
        self.assertTrue(out["ok"])
        self.assertEqual(len(out["rules"]), 1)
        self.assertEqual(seen["args"][1], "admin_warehouse_pin_rules_update")
        self.assertEqual(seen["kw"]["level"], "warn")
        self.assertIn("before", seen["kw"]["context"])
        self.assertEqual(store.read_state()["updated_by"], "boss@spc.co.th")

    def test_duplicate_rule_is_400_with_a_thai_reason(self):
        with self.assertRaises(HTTPException) as ctx:
            admin_router.admin_put_warehouse_pin_rules(
                admin_router.WarehousePinRulesBody(
                    rules=[_rule_body(wh="G010"), _rule_body(wh="R082")]
                ),
                admin=ADMIN,
            )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_stale_rev_is_409(self):
        admin_router.admin_put_warehouse_pin_rules(
            admin_router.WarehousePinRulesBody(rules=[_rule_body()], expected_rev=0), admin=ADMIN
        )
        with self.assertRaises(HTTPException) as ctx:
            admin_router.admin_put_warehouse_pin_rules(
                admin_router.WarehousePinRulesBody(
                    rules=[_rule_body(area="4")], expected_rev=0
                ),
                admin=ADMIN,
            )
        self.assertEqual(ctx.exception.status_code, 409)

    # ── รายการสินค้า/สถิติคลังอ่านจากไฟล์ขั้นที่ 1 ของทุกทีม (เลิกอ่าน Fabric TGA 21 ต.ค. 2026) ──

    def _in_tmp_data(self, files):
        """เขียนไฟล์ขั้นที่ 1 ปลอมลง <tmp>/data แล้ว chdir เข้าไป (โค้ดจริงอ่าน path สัมพัทธ์ data/)"""
        os.chdir(self._tmp.name)
        os.makedirs("data", exist_ok=True)
        for name, rows in files.items():
            pd.DataFrame(rows).to_csv(os.path.join("data", name), index=False)

    def test_combos_endpoint_reads_team_grain_files_not_fabric(self):
        self._in_tmp_data({
            "tga_lines_SLA_2026_11.csv": [
                {"emp_id": "E1", "sku": "SKU1", "qty": 6, "salestype": "S", "divisioncode": "B", "areacode": "3", "provincecode": "", "warehouse_code": "G010"},
                {"emp_id": "E2", "sku": "SKU1", "qty": 4, "salestype": "S", "divisioncode": "B", "areacode": "3", "provincecode": "", "warehouse_code": "G010"},
                {"emp_id": "E3", "sku": "SKU9", "qty": 9, "salestype": "S", "divisioncode": "B", "areacode": "3", "provincecode": "", "warehouse_code": "R082"},
            ],
            "tga_lines_SLB_2026_11.csv": [
                {"emp_id": "E4", "sku": "SKU2", "qty": 5, "salestype": "C", "divisioncode": "B", "areacode": "5", "provincecode": "", "warehouse_code": ""},
            ],
        })
        with patch.object(admin_router, "FabricDAXConnector", side_effect=AssertionError("ห้ามเรียก Fabric")):
            out = admin_router.admin_warehouse_pin_rule_combos(skus="SKU1,SKU2", year=2026, month=11, _admin=ADMIN)
        self.assertEqual(out["sku_count"], 2)
        got = {(c["areacode"], c["divisioncode"], c["warehouse_code"]): (c["qty"], c["emp_count"]) for c in out["combos"]}
        self.assertEqual(got, {("3", "B", "G010"): (10.0, 2), ("5", "B", ""): (5.0, 1)})
        self.assertEqual((out["source_year"], out["source_month"]), (2026, 11))

    def test_combos_falls_back_to_latest_month_that_has_the_skus(self):
        self._in_tmp_data({
            "tga_lines_SLA_2026_10.csv": [
                {"emp_id": "E1", "sku": "SKU1", "qty": 6, "salestype": "S", "divisioncode": "B", "areacode": "3", "provincecode": "", "warehouse_code": "G010"},
            ],
        })
        out = admin_router.admin_warehouse_pin_rule_combos(skus="SKU1", year=2026, month=11, _admin=ADMIN)
        self.assertEqual(len(out["combos"]), 1)
        self.assertEqual((out["source_year"], out["source_month"]), (2026, 10))
        self.assertIn("10/2026", out["hint"])

    def test_combos_fills_divisions_missing_this_month_from_earlier_months(self):
        """ต้นงวดมีแค่ทีม division E โหลดแล้ว — division B (ภาคกลาง) ต้องยังมีให้เลือกจากงวดก่อน
        และชุดที่มีทั้งสองงวดใช้ตัวเลขงวดใหม่ ไม่บวกข้ามงวด"""
        row = lambda e, q, div, wh: {"emp_id": e, "sku": "SKU1", "qty": q, "salestype": "S", "divisioncode": div, "areacode": "3", "provincecode": "", "warehouse_code": wh}
        self._in_tmp_data({
            "tga_lines_SLE_2026_11.csv": [row("E1", 7, "E", "R082")],
            "tga_lines_SLE_2026_10.csv": [row("E1", 99, "E", "R082")],
            "tga_lines_SLB_2026_10.csv": [row("B1", 5, "B", "G010")],
        })
        out = admin_router.admin_warehouse_pin_rule_combos(skus="SKU1", year=2026, month=11, _admin=ADMIN)
        got = {(c["divisioncode"], c["warehouse_code"]): c["qty"] for c in out["combos"]}
        self.assertEqual(got, {("E", "R082"): 7.0, ("B", "G010"): 5.0})
        self.assertIn("11/2026", out["hint"])
        self.assertIn("10/2026", out["hint"])

    def test_combos_endpoint_requires_at_least_one_sku(self):
        with self.assertRaises(HTTPException) as ctx:
            admin_router.admin_warehouse_pin_rule_combos(skus="  , ,", year=2026, month=10, _admin=ADMIN)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_catalog_lists_skus_with_section_from_every_team(self):
        tb = lambda sku, sec, boxes, name: {"sku": sku, "section": sec, "supervisor_target_boxes": boxes, "product_name_thai": name, "product_name_english": ""}
        self._in_tmp_data({
            "target_boxes_SLA_2026_11.csv": [tb("409011", "702", 10, "ก"), tb("SKU0", "702", 0, "ไม่มีเป้า")],
            "target_boxes_SLB_2026_11.csv": [tb("402107", "704", 5, "ข"), tb("409011", "702", 3, "ก")],
        })
        with patch.object(admin_router, "FabricDAXConnector", side_effect=AssertionError("ห้ามเรียก Fabric")):
            out = admin_router.admin_warehouse_pin_rule_catalog(year=2026, month=11, _admin=ADMIN)
        got = {r["sku"]: (r["section"], r["target_boxes"]) for r in out["skus"]}
        self.assertEqual(got, {"409011": ("702", 13.0), "402107": ("704", 5.0)})  # SKU ไม่มีเป้าไม่ขึ้น

    def test_catalog_falls_back_to_previous_month_and_says_so(self):
        self._in_tmp_data({
            "target_boxes_SLA_2026_10.csv": [{"sku": "409011", "section": "702", "supervisor_target_boxes": 10}],
        })
        out = admin_router.admin_warehouse_pin_rule_catalog(year=2026, month=11, _admin=ADMIN)
        self.assertEqual([r["sku"] for r in out["skus"]], ["409011"])
        self.assertEqual((out["source_year"], out["source_month"]), (2026, 10))
        self.assertIn("ยังไม่มีทีมไหนโหลด", out["hint"])

    def test_catalog_with_no_files_explains_what_to_do(self):
        self._in_tmp_data({})
        out = admin_router.admin_warehouse_pin_rule_catalog(year=2026, month=11, _admin=ADMIN)
        self.assertEqual(out["skus"], [])
        self.assertIn("ขั้นที่ 1", out["hint"])


class WarehousePinRulesWiringTest(unittest.TestCase):
    def setUp(self):
        self.src = _read("backend/routers/admin.py")

    def test_all_routes_are_head_admin_only(self):
        for marker in (
            '@router.get("/settings/warehouse-pin-rules")',
            '@router.put("/settings/warehouse-pin-rules")',
            '@router.get("/warehouse-pin-rules/combos")',
            '@router.get("/warehouse-pin-rules/catalog")',
        ):
            block = self.src.split(marker)[1].split("@router.")[0]
            self.assertIn('require_capability("warehouse_pin_rules")', block, marker)

    def test_capability_is_registered(self):
        caps = _read("backend/services/admin_capabilities.py")
        self.assertIn('"warehouse_pin_rules"', caps)
        self.assertIn('"tab": "warehousePinRules"', caps)

    def test_capability_is_granted_to_head_admin_in_the_tracked_config(self):
        """ถ้าไม่อยู่ในไฟล์นี้ head_admin จะไม่เห็นแท็บบน production เลย (ดูคอมเมนต์
        admin_capabilities.py เรื่อง DEFAULT_ROLE_CAPABILITIES เป็น fallback เท่านั้น)"""
        # อ่านเป็น JSON แล้วดูราย role — เดิมค้นสตริงทั้งไฟล์ ผ่านแม้ให้ผิด role (ผลตรวจ 1 ต.ค. 2026 ค)
        import json

        roles = json.loads(_read("config/admin_permissions.json"))["roles"]
        self.assertIn("warehouse_pin_rules", roles["head_admin"])
        for role in ("admin", "marketing"):
            with self.subTest(role=role):
                self.assertNotIn("warehouse_pin_rules", roles.get(role, []), "เฉพาะหัวหน้าแอดมิน")

    def test_the_frontend_tab_is_wired(self):
        app_js = _read("frontend/app.js")
        self.assertIn("/admin/settings/warehouse-pin-rules", app_js)
        self.assertIn("adminLoadWarehousePinRules", app_js)
        self.assertIn('data-tab="warehousePinRules"', _read("frontend/index.html"))
        self.assertIn('data-panel="warehousePinRules"', _read("frontend/index.html"))


if __name__ == "__main__":
    unittest.main()
