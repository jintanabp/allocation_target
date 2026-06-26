import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse

from ..deps import require_authenticated_user
from ..services.access_control import filter_managers_payload_for_user, user_can_import_targetsun
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
        if user.get("auth_disabled") or user.get("acc_admin_full_access"):
            out = dict(full)
        else:
            out = dict(filter_managers_payload_for_user(full, user))
        # แอดมิน / auth ปิด ได้ payload เต็มโดยไม่ผ่าน filter — ต้องสร้าง manager_views ที่นี่
        if not out.get("manager_views"):
            by_m = out.get("by_manager") or {}
            mgrs = out.get("manager_codes") or []
            out["manager_views"] = build_manager_views_map(by_m, list(mgrs))
        out["can_import_targetsun"] = user_can_import_targetsun(user)
        out["is_admin"] = bool(user.get("is_admin"))
        out["is_marketing"] = bool(user.get("is_marketing"))
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
