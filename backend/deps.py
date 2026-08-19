import logging
import time
from typing import Annotated

from fastapi import Header, HTTPException

from . import auth_entra
from .services.access_control import (
    ROLE_DEV,
    ROLE_REGION_ADMIN,
    admin_scope_for_email,
    admin_scope_is_usable,
    build_user_access_context,
    is_allocation_admin_email,
    is_marketing_email,
    is_region_admin_email,
    normalized_email,
    role_for_email,
    row_is_in_admin_scope,
    unrestricted_user_context,
    user_can_import_targetsun,
)
from .services.sl_link_store import expand_sl_codes, resolve_to_canonical

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
        # โหมดทดสอบ: สิทธิ์ตามผู้ใช้ที่จำลองเท่านั้น — ไม่คงสิทธิ์แอดมิน
        ctx["is_admin"] = is_admin and not view_as
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
    """
    role dev เท่านั้น — ทำได้ทุกอย่างทั้งระบบ ไม่รับ view-as (ใช้ JWT จริง)

    ใช้กับของที่มีผลทั้งระบบ: ตั้งค่าปลายทาง/แหล่งข้อมูล, rebuild ลำดับชั้น,
    ล้าง cache, ลบผลกระจาย, export รายชื่อทั้งไฟล์, เปิดสิทธิ์ส่งแบบยกชุด
    """
    if not auth_entra.auth_enabled():
        return {
            "auth_disabled": True, "email": None, "is_admin": True,
            "is_marketing": False, "role": ROLE_DEV, "admin_scope": None,
        }
    ident = _identity_from_bearer(authorization)
    email = normalized_email(ident.get("email"))
    if not is_allocation_admin_email(email):
        raise HTTPException(
            status_code=403,
            detail="เฉพาะผู้ดูแลระบบ (dev) เท่านั้น — แอดมินรายภาคไม่มีสิทธิ์ส่วนนี้",
        )
    return {
        "email": email, "is_admin": True, "is_marketing": False,
        "role": ROLE_DEV, "admin_scope": None,
    }


def require_admin_scoped(
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    """
    dev หรือแอดมินรายภาค — คืนขอบเขตมาด้วยเสมอ

    dev ได้ admin_scope = None (ไม่จำกัด) ส่วนแอดมินรายภาคได้ "เซ็ตจริง" เสมอ
    ผู้เรียกต้องกรอง/ตรวจด้วย ensure_row_in_admin_scope หรือ admin_scope["sl_codes"]
    ไม่ใช่แค่ผ่านด่านนี้แล้วถือว่าทำได้ทุกแถว
    """
    if not auth_entra.auth_enabled():
        return {
            "auth_disabled": True, "email": None, "is_admin": True,
            "is_marketing": False, "role": ROLE_DEV, "admin_scope": None,
        }
    ident = _identity_from_bearer(authorization)
    email = normalized_email(ident.get("email"))
    if is_allocation_admin_email(email):
        return {
            "email": email, "is_admin": True, "is_marketing": False,
            "role": ROLE_DEV, "admin_scope": None,
        }
    if is_region_admin_email(email):
        scope = admin_scope_for_email(email)
        if not admin_scope_is_usable(scope):
            # ขอบเขตที่ตั้งไว้ต้องมีข้อมูลรองรับ (ภาค/ดิวิชันของตัวเอง)
            # ไม่งั้น = ไม่มีอะไรให้ดูแล ต้องไม่กลายเป็น "เห็นทั้งระบบ"
            raise HTTPException(
                status_code=403,
                detail=(
                    "บัญชีแอดมินนี้ยังไม่ได้ระบุภาค/ดิวิชันตามขอบเขตที่ตั้งไว้ — "
                    "ให้ผู้ดูแลระบบเติมข้อมูล หรือเปลี่ยนขอบเขตเป็น 'ทุกคนในระบบ'"
                ),
            )
        return {
            "email": email, "is_admin": False, "is_marketing": False,
            "role": ROLE_REGION_ADMIN, "admin_scope": scope,
        }
    raise HTTPException(status_code=403, detail="ไม่มีสิทธิ์เข้าถึงหน้านี้")


def ensure_row_in_admin_scope(user: dict, row: dict | None) -> None:
    """
    แถวผู้ใช้แถวนี้อยู่ในภาคที่คนนี้ดูแลไหม — ใช้ทั้งตอนอ่านแถวเดิมและตอนตรวจค่าที่ส่งมา

    ต้องตรวจ **ทั้งสองฝั่ง**: แถวเป้าหมายเดิม และค่าใหม่ที่จะบันทึก
    ไม่งั้นแอดมินภาคจะย้ายคนออกนอกภาคตัวเอง (หรือดึงคนของภาคอื่นเข้ามา) ได้
    """
    if user.get("auth_disabled") or user.get("role") == ROLE_DEV:
        return
    scope = user.get("admin_scope") or {}
    if row is not None and row_is_in_admin_scope(row, scope):
        return
    raise HTTPException(
        status_code=403,
        detail="แถวนี้อยู่นอกภาคที่บัญชีนี้ดูแล",
    )


def ensure_sup_in_admin_scope(user: dict, sup_id: str) -> None:
    """รหัส Supervisor นี้อยู่ในภาคที่คนนี้ดูแลไหม"""
    if user.get("auth_disabled") or user.get("role") == ROLE_DEV:
        return
    scope = user.get("admin_scope") or {}
    codes = {str(c).strip().upper() for c in (scope.get("sl_codes") or set())}
    if str(sup_id or "").strip().upper() in codes:
        return
    raise HTTPException(
        status_code=403,
        detail="รหัส Supervisor นี้อยู่นอกภาคที่บัญชีนี้ดูแล",
    )


def require_admin_or_marketing_team(
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    """dev, แอดมินรายภาค หรือ Marketing (ทีมพนักงาน)"""
    if not auth_entra.auth_enabled():
        return {
            "auth_disabled": True, "email": None, "is_admin": True,
            "is_marketing": True, "role": ROLE_DEV, "admin_scope": None,
        }
    ident = _identity_from_bearer(authorization)
    email = normalized_email(ident.get("email"))
    if is_allocation_admin_email(email):
        return {
            "email": email, "is_admin": True, "is_marketing": False,
            "role": ROLE_DEV, "admin_scope": None,
        }
    if is_region_admin_email(email):
        return {
            "email": email, "is_admin": False, "is_marketing": False,
            "role": ROLE_REGION_ADMIN, "admin_scope": admin_scope_for_email(email),
        }
    if is_marketing_email(email):
        return {
            "email": email, "is_admin": False, "is_marketing": True,
            "role": "marketing", "admin_scope": None,
        }
    raise HTTPException(status_code=403, detail="ไม่มีสิทธิ์เข้าถึงหน้านี้")


def ensure_demo_team_not_sent(sup_id) -> None:
    """
    ทีมสาธิตส่งเข้า Target Sun ไม่ได้เด็ดขาด

    ชั้นแรกคือ can_import_targetsun=false ในไฟล์ แต่ dev มีสิทธิ์ส่งอยู่แล้ว
    ถ้า dev กด "ดูแบบนี้" เป็นบัญชีสาธิตแล้วเผลอกดส่ง ข้อมูลสมมติจะเข้าระบบจริง
    — ด่านนี้ปิดตาย ไม่มีปุ่มยืนยันข้าม เพราะไม่มีเหตุผลใดที่ควรส่งข้อมูลปลอม
    """
    from .services.demo_data import is_demo_supervisor

    if is_demo_supervisor(sup_id):
        raise HTTPException(
            status_code=403,
            detail=(
                f"'{sup_id}' เป็นทีมสาธิต (ข้อมูลสมมติ) — ส่งเข้า Target Sun ไม่ได้ "
                "ใช้สำหรับสาธิตหน้าจอเท่านั้น"
            ),
        )


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
    allowed_set = {str(x).strip().upper() for x in allowed}
    if sid in allowed_set:
        return
    if sid in expand_sl_codes(allowed_set):
        return
    if resolve_to_canonical(sid) in allowed_set:
        return
    raise HTTPException(
        status_code=403,
        detail="บัญชีนี้ไม่มีสิทธิ์เข้าถึงรหัส Supervisor นี้",
    )


def ensure_own_supervisor_write(user: dict, sup_id: str) -> None:
    """กระจายหีบ/ส่งผลได้เมื่อมีสิทธิ์เข้าถึงรหัสนั้น (รวม peer ในกลุ่มเดียวกัน)"""
    ensure_allocation_write_allowed(user, sup_id)


def ensure_allocation_write_allowed(user: dict, sup_id: str) -> None:
    """
    บันทึก snapshot / กระจาย / ส่ง Target Sun ได้เมื่อ sup_id อยู่ใน allowed
    (supervisor peer ใน division+ภาค+หน่วยเดียวกัน และ manager ที่ home ว่าง)
    """
    ensure_supervisor_allowed(user, sup_id)
    if user.get("auth_disabled") or user.get("acc_admin_full_access"):
        return
    # ensure_supervisor_allowed ผ่านแล้ว = อยู่ใน allowed / admin / auth ปิด
    # ไม่ล็อกที่ home อีก — peer ในกลุ่มเดียวกันเขียนได้


require_entra_member = require_authenticated_user
