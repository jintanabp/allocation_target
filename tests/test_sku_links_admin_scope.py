"""
/admin/sku-links/preview และ /admin/sku-links/catalog?super_code= ต้องเช็คขอบเขตผู้ดูแล

เดิมรับ super_code อะไรก็ได้ — ผู้ดูแลภาคหนึ่งเปิดดูยอดขาย 3 เดือน/ปีก่อน หรือแคชเป้า
ของทีมภาคอื่นได้ (พบจากผลตรวจสอบระบบ 24 ก.ย. 2026) ตอนนี้ใช้กติกาเดียวกับ
/admin/supervisor-team: ผู้ดูแลเห็นแค่ทีมในภาคตัวเอง · dev/marketing ไม่จำกัด

เรียกฟังก์ชัน endpoint ตรง ๆ และ mock ตัวอ่าน Fabric/แคชให้ระเบิดถ้าถูกเรียก — ไม่ต่อ
ระบบจริง
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from fastapi import HTTPException  # noqa: E402

from backend.routers import admin as admin_router  # noqa: E402


def _admin_user(codes):
    role = sorted(admin_router.ADMIN_ROLES)[0]
    return {
        "email": "scoped-admin@example.test", "is_admin": False, "is_marketing": False,
        "role": role, "admin_scope": {"sl_codes": set(codes)},
    }


def _boom(*a, **k):
    raise AssertionError("ต้องไม่อ่านข้อมูลของทีมนอกขอบเขต")


class SkuLinkPreviewScopeTest(unittest.TestCase):
    def test_out_of_scope_team_is_forbidden_before_any_read(self):
        with patch.object(admin_router, "load_supervisor_team", side_effect=_boom), \
             patch.object(admin_router, "FabricDAXConnector", side_effect=_boom):
            with self.assertRaises(HTTPException) as ctx:
                admin_router.preview_sku_link(
                    super_code="SLOTHER", canonical_sku="100001", year=2026, month=9,
                    user=_admin_user({"SLMINE"}),
                )
        self.assertEqual(ctx.exception.status_code, 403)

    def test_in_scope_team_passes(self):
        with patch.object(admin_router, "load_supervisor_team", return_value={"employees": []}):
            out = admin_router.preview_sku_link(
                super_code="slmine", canonical_sku="100001", year=2026, month=9,
                user=_admin_user({"SLMINE"}),
            )
        self.assertEqual(out["supervisor_code"], "SLMINE")

    def test_marketing_is_not_limited(self):
        user = {"email": "m@example.test", "is_admin": False, "is_marketing": True,
                "role": "marketing", "admin_scope": None}
        with patch.object(admin_router, "load_supervisor_team", return_value={"employees": []}):
            out = admin_router.preview_sku_link(
                super_code="SLANY", canonical_sku="100001", year=2026, month=9, user=user,
            )
        self.assertEqual(out["employee_count"], 0)


class SkuLinkCatalogScopeTest(unittest.TestCase):
    def test_out_of_scope_team_cache_is_forbidden(self):
        with patch.object(admin_router, "read_cached_employee_payload", side_effect=_boom):
            with self.assertRaises(HTTPException) as ctx:
                admin_router.sku_link_catalog(
                    year=2026, month=9, super_code="SLOTHER", user=_admin_user({"SLMINE"}),
                )
        self.assertEqual(ctx.exception.status_code, 403)

    def test_in_scope_team_cache_is_served(self):
        payload = {"skus": [{"sku": "100001", "target_boxes": 5}]}
        with patch.object(admin_router, "read_cached_employee_payload", return_value=payload):
            out = admin_router.sku_link_catalog(
                year=2026, month=9, super_code="SLMINE", user=_admin_user({"SLMINE"}),
            )
        self.assertTrue(out["from_cache"])
        self.assertEqual(out["count"], 1)


if __name__ == "__main__":
    unittest.main()
