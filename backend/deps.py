import logging
import time
from typing import Annotated

from fastapi import Header, HTTPException

from . import auth_entra
from .services.access_control import (
    build_user_access_context,
    unrestricted_user_context,
    user_can_import_targetsun,
)

logger = logging.getLogger("target_allocation")


def require_authenticated_user(
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    """
    เมื่อเปิด Entra: ตรวจ Microsoft JWT แล้วผูกอีเมลกับ ACC_USER_CONTROL + trf supervisors
    ไม่ใช้ security group membership แล้ว
    """
    if not auth_entra.auth_enabled():
        return unrestricted_user_context()
    t0 = time.perf_counter()
    if not authorization or not authorization.lower().startswith("bearer "):
        logger.info("Entra auth: missing bearer token")
        raise HTTPException(
            status_code=401,
            detail="กรุณาล็อกอินด้วย Microsoft (กดปุ่มล็อกอินก่อน)",
        )
    token = authorization[7:].strip()
    try:
        ident = auth_entra.verify_microsoft_identity(token)
        ctx = build_user_access_context(ident["email"])
        elapsed = time.perf_counter() - t0
        if elapsed >= 0.3:
            logger.info("Entra auth timing: %.2fs", elapsed)
        return ctx
    except PermissionError as e:
        logger.info("Entra auth forbidden (ACC / role): %s", str(e))
        raise HTTPException(status_code=403, detail=str(e)) from None
    except ValueError as e:
        logger.info("Entra auth: invalid token: %s", str(e))
        raise HTTPException(status_code=401, detail=str(e)) from None


def ensure_targetsun_import_allowed(user: dict) -> None:
    """ส่งเข้า Target Sun — admin หรืออีเมลใน config/acc_local_test.json เท่านั้น"""
    if user_can_import_targetsun(user):
        return
    raise HTTPException(
        status_code=403,
        detail=(
            "บัญชีนี้ยังไม่มีสิทธิ์ส่งเข้า Target Sun "
            "(เฉพาะผู้ดูแลระบบและรายชื่อใน config/acc_local_test.json)"
        ),
    )


def ensure_supervisor_allowed(user: dict, sup_id: str) -> None:
    """เมื่อเปิด Auth — allow ตาม allowed_supervisor_codes; None = ไม่จำกัด (ผู้ดูแล ALLOCATION_ADMIN_EMAILS / ปิดการบังคับ auth)"""
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
