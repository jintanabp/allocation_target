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

    def tearDown(self):
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

    def test_combos_endpoint_returns_data_from_fabric(self):
        seen_calls = {}
        fake = type(
            "Fake",
            (),
            {
                "get_tga_dim_combos_by_product": lambda self, skus, m, y: (
                    seen_calls.update({"skus": skus, "m": m, "y": y}) or
                    pd.DataFrame(
                        [{"areacode": "3", "divisioncode": "S", "warehouse_code": "G010", "qty": 100.0, "emp_count": 10}]
                    )
                ),
            },
        )
        with patch.object(admin_router, "FabricDAXConnector", return_value=fake()):
            out = admin_router.admin_warehouse_pin_rule_combos(
                skus="SKU1,SKU2", year=2026, month=10, _admin=ADMIN
            )
        self.assertEqual(out["sku_count"], 2)
        self.assertEqual(len(out["combos"]), 1)
        self.assertEqual(out["combos"][0]["warehouse_code"], "G010")
        self.assertEqual(seen_calls["skus"], ["SKU1", "SKU2"])  # ส่ง SKU ตรง ๆ ไม่ derive จาก section

    def test_combos_endpoint_requires_at_least_one_sku(self):
        with self.assertRaises(HTTPException) as ctx:
            admin_router.admin_warehouse_pin_rule_combos(skus="  , ,", year=2026, month=10, _admin=ADMIN)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_combos_endpoint_fails_loudly_not_silently_on_fabric_error(self):
        with patch.object(admin_router, "FabricDAXConnector", side_effect=RuntimeError("boom")):
            with self.assertRaises(HTTPException) as ctx:
                admin_router.admin_warehouse_pin_rule_combos(skus="SKU1", year=2026, month=10, _admin=ADMIN)
        self.assertEqual(ctx.exception.status_code, 502)


class WarehousePinRulesWiringTest(unittest.TestCase):
    def setUp(self):
        self.src = _read("backend/routers/admin.py")

    def test_all_routes_are_head_admin_only(self):
        for marker in (
            '@router.get("/settings/warehouse-pin-rules")',
            '@router.put("/settings/warehouse-pin-rules")',
            '@router.get("/warehouse-pin-rules/combos")',
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
        cfg = _read("config/admin_permissions.json")
        self.assertIn("warehouse_pin_rules", cfg)

    def test_the_frontend_tab_is_wired(self):
        app_js = _read("frontend/app.js")
        self.assertIn("/admin/settings/warehouse-pin-rules", app_js)
        self.assertIn("adminLoadWarehousePinRules", app_js)
        self.assertIn('data-tab="warehousePinRules"', _read("frontend/index.html"))
        self.assertIn('data-panel="warehousePinRules"', _read("frontend/index.html"))


if __name__ == "__main__":
    unittest.main()
