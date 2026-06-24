import logging
import time
from typing import Annotated

from fastapi import Header, HTTPException

from . import auth_entra
from .services.access_control import (
    build_user_access_context,
    is_allocation_admin_email,
    normalized_email,
    unrestricted_user_context,
    user_can_import_targetsun,
)

logger = logging.getLogger("target_allocation")


def _identity_from_bearer(authorization: str | None) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        logger.info("Entra auth: missing bearer token")
        raise HTTPException(
            status_code=401,
            detail="กรุณาล็อกอินด้วย Microsoft (กดปุ่มล็อกอินก่อน)",
        )
    token = authorization[7:].strip()
    try:
        ident = auth_entra.verify_microsoft_identity(token)
    except ValueError as e:
        logger.info("Entra auth: invalid token: %s", str(e))
        raise HTTPException(status_code=401, detail=str(e)) from None
    return ident


def require_authenticated_user(
    authorization: Annotated[str | None, Header()] = None,
    x_view_as_email: Annotated[str | None, Header(alias="X-View-As-Email")] = None,
) -> dict:
    """
    เมื่อเปิด Entra: ตรวจ Microsoft JWT แล้วผูกอีเมลกับ user_access.json + trf supervisors
    แอดมินส่ง X-View-As-Email เพื่อทดสอบมุมมองผู้ใช้ (JWT ยังเป็นตัวแอดมิน)
    """
    if not auth_entra.auth_enabled():
        return unrestricted_user_context()

    t0 = time.perf_counter()
    ident = _identity_from_bearer(authorization)
    actual_email = normalized_email(ident.get("email"))
    is_admin = is_allocation_admin_email(actual_email)

    view_as = normalized_email(x_view_as_email) if x_view_as_email else ""
    if view_as and not is_admin:
        raise HTTPException(status_code=403, detail="ไม่มีสิทธิ์ใช้โหมดดูแบบผู้ใช้อื่น")

    effective_email = view_as if (view_as and is_admin) else actual_email
    allow_admin_bypass = not bool(view_as)

    try:
        ctx = build_user_access_context(effective_email, allow_admin_bypass=allow_admin_bypass)
        ctx["is_admin"] = is_admin
        if view_as and is_admin:
            ctx["view_as_email"] = view_as
            ctx["acting_admin_email"] = actual_email
            ctx["acc_admin_full_access"] = False
        elapsed = time.perf_counter() - t0
        if elapsed >= 0.3:
            logger.info("Entra auth timing: %.2fs", elapsed)
        return ctx
    except PermissionError as e:
        logger.info("Entra auth forbidden (ACC / role): %s", str(e))
        raise HTTPException(status_code=403, detail=str(e)) from None
    except ValueError as e:
        logger.info("Entra auth: %s", str(e))
        raise HTTPException(status_code=401, detail=str(e)) from None


def require_admin_user(
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    """แอดมินเท่านั้น — ไม่รับ view-as (ใช้ JWT จริง)"""
    if not auth_entra.auth_enabled():
        return {"auth_disabled": True, "email": None, "is_admin": True}
    ident = _identity_from_bearer(authorization)
    email = normalized_email(ident.get("email"))
    if not is_allocation_admin_email(email):
        raise HTTPException(status_code=403, detail="เฉพาะผู้ดูแลระบบเท่านั้น")
    return {"email": email, "is_admin": True}


def ensure_targetsun_import_allowed(user: dict) -> None:
    if user_can_import_targetsun(user):
        return
    raise HTTPException(
        status_code=403,
        detail=(
            "บัญชีนี้ยังไม่มีสิทธิ์ส่งเข้า Target Sun "
            "(เฉพาะผู้ดูแลระบบและอีเมลที่ตั้ง can_import_targetsun ใน user_access.json)"
        ),
    )


def ensure_supervisor_allowed(user: dict, sup_id: str) -> None:
    if user.get("auth_disabled"):
        return
    allowed = user.get("allowed_supervisor_codes")
    if allowed is None:
        return
    sid = (sup_id or "").strip().upper()
    if sid not in allowed:
        raise HTTPException(
            status_code=403,
            detail="บัญชีนี้ไม่มีสิทธิ์เข้าถึงรหัส Supervisor นี้",
        )


require_entra_member = require_authenticated_user
