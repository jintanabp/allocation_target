import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse

from ..core.tga_period import expected_allocation_period_ce
from ..deps import require_authenticated_user
from ..services.access_control import (
    ADMIN_SCOPE_LABELS,
    DEFAULT_ADMIN_SCOPE,
    ROLE_REGION_ADMIN,
    admin_scope_for_email,
    filter_managers_payload_for_user,
    role_for_email,
    user_can_import_targetsun,
)
from ..services import demo_data
from ..services.manager_views import build_manager_views_map
from ..services.managers import load_full_managers_payload

logger = logging.getLogger("target_allocation")

router = APIRouter(tags=["managers"])


@router.api_route("/manegers", methods=["GET", "HEAD"], include_in_schema=False)
def managers_common_typo():
    """พิมพ์ผิดบ่อย (manegers) — redirect ไป /managers"""
    return RedirectResponse(url="/managers", status_code=307)


@router.get("/managers")
def get_managers(user: dict = Depends(require_authenticated_user)):
    try:
        os.makedirs("data", exist_ok=True)
        full = load_full_managers_payload()
        # ทีมสาธิตไม่ได้อยู่ใน roster จริง — ใส่เข้าไปเฉพาะคนที่มีสิทธิ์เห็นมัน
        # (บัญชีสาธิตเอง หรือ dev ที่กำลังกด "ดูแบบนี้") ผู้ใช้จริงต้องไม่เห็น
        allowed_codes = user.get("allowed_supervisor_codes")
        sees_demo = demo_data.is_demo_email(user.get("view_as_email") or user.get("email")) or (
            allowed_codes is not None and demo_data.DEMO_SUP_ID in set(allowed_codes)
        )
        if sees_demo:
            full = demo_data.inject_into_managers_payload(full)
        if user.get("auth_disabled") or user.get("acc_admin_full_access"):
            out = dict(full)
        else:
            out = dict(filter_managers_payload_for_user(full, user))
        # แอดมิน / auth ปิด ได้ payload เต็มโดยไม่ผ่าน filter — ต้องสร้าง manager_views ที่นี่
        if not out.get("manager_views"):
            by_m = out.get("by_manager") or {}
            mgrs = out.get("manager_codes") or []
            out["manager_views"] = build_manager_views_map(by_m, list(mgrs), mgrs)
        # งวดที่ต้องทำ คิดที่ server (Asia/Bangkok) — เบราว์เซอร์คิดเองด้วย
        # timezone ของเครื่องผู้ใช้ ซึ่งอาจข้ามวัน/ข้ามเดือนไม่ตรงกับ backend
        _ey, _em = expected_allocation_period_ce()
        out["expected_period"] = {"month": _em, "year": _ey}
        out["can_import_targetsun"] = user_can_import_targetsun(user)
        out["is_admin"] = bool(user.get("is_admin"))
        out["is_marketing"] = bool(user.get("is_marketing"))
        # role จริงของบัญชี — หน้าเว็บใช้ตัดสินว่าจะโชว์แท็บแอดมินไหนบ้าง
        # is_admin คงไว้เพื่อความเข้ากันได้ (= role dev) แต่ตัวที่ควรใช้ต่อไปคือ role
        if user.get("view_as_email"):
            out["role"] = "user"   # โหมดดูแบบผู้ใช้อื่น = ไม่มีสิทธิ์แอดมินติดไปด้วย
        else:
            out["role"] = role_for_email(user.get("email"))
        if out["role"] == ROLE_REGION_ADMIN:
            scope = admin_scope_for_email(user.get("email"))
            out["admin_regions"] = sorted(scope.get("regions") or [])
            out["admin_divisions"] = sorted(scope.get("divisions") or [])
            # ขอบเขต "แก้ผู้ใช้คนไหนได้" ที่ dev ตั้งไว้ — หน้าเว็บเอาไปขึ้นบอกให้ชัด
            breadth = str(scope.get("breadth") or DEFAULT_ADMIN_SCOPE)
            out["admin_scope"] = breadth
            out["admin_scope_label"] = ADMIN_SCOPE_LABELS.get(breadth, breadth)
        from ..services import targetsun_read

        out["targetsun_read_enabled"] = targetsun_read.is_enabled()
        out["target_read_source"] = targetsun_read.get_target_read_source()
        if user.get("view_as_email"):
            out["view_as_email"] = user["view_as_email"]
            out["acting_admin_email"] = user.get("acting_admin_email")
        return out
    except Exception as e:
        logger.exception("GET /managers failed for %s", user.get("email") or user.get("view_as_email"))
        raise HTTPException(
            status_code=503,
            detail=f"โหลดรายการ Supervisor/Manager ไม่สำเร็จ — {e}",
        ) from e
