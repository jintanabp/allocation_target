import logging
import time
from typing import Annotated

from fastapi import Header, HTTPException

from . import auth_entra
from .services.access_control import (
    ADMIN_ROLES,
    ROLE_ADMIN,
    ROLE_DEV,
    ROLE_HEAD_ADMIN,
    ROLE_REGION_ADMIN,
    admin_role_for_email,
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
from .services.admin_permissions_store import capabilities_for_role
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


def _viewed_privileged_context(
    view_as: str,
    acting_email: str | None,
    *,
    allow_marketing: bool = False,
) -> dict:
    """
    สิทธิ์ของ "บัญชีที่กำลังดูแบบ" สำหรับ route ฝั่งแอดมิน — ใช้เมื่อคนกดเป็น dev เท่านั้น

    โหมดดูสิทธิ์ต้องเหมือนจริง: dev กดดูบัญชีผู้ดูแลแล้วต้องเห็นหน้าแอดมิน
    แบบเดียวกับที่บัญชีนั้นเห็น (แท็บ/ขอบเขต/ข้อมูลถูกกรองตามภาคของบัญชีนั้น)
    ไม่ใช่การยกสิทธิ์ — คนกดเป็น dev ซึ่งมีสิทธิ์เต็มอยู่แล้ว มีแต่ "แคบลง" เท่านั้น
    บัญชีที่ไม่มีสิทธิ์แอดมินได้ 403 แบบเดียวกับที่เจ้าตัวจริงจะเจอ
    """
    role = role_for_email(view_as)
    if role == ROLE_DEV:
        return {
            "email": view_as, "is_admin": True, "is_marketing": False,
            "role": ROLE_DEV, "admin_scope": None,
            "view_as_email": view_as, "acting_admin_email": acting_email,
        }
    if role in ADMIN_ROLES:
        scope = admin_scope_for_email(view_as)
        if not admin_scope_is_usable(scope):
            raise HTTPException(
                status_code=403,
                detail=(
                    "บัญชีที่กำลังดูแบบยังไม่ได้ระบุภาค/ดิวิชันตามขอบเขตที่ตั้งไว้ — "
                    "เจ้าตัวจริงล็อกอินมาก็จะเจอ 403 แบบเดียวกัน (เติมข้อมูลให้ก่อน)"
                ),
            )
        return {
            "email": view_as, "is_admin": False, "is_marketing": False,
            "role": role, "admin_scope": scope,
            "view_as_email": view_as, "acting_admin_email": acting_email,
        }
    if allow_marketing and is_marketing_email(view_as):
        return {
            "email": view_as, "is_admin": False, "is_marketing": True,
            "role": "marketing", "admin_scope": None,
            "view_as_email": view_as, "acting_admin_email": acting_email,
        }
    raise HTTPException(
        status_code=403,
        detail="บัญชีที่กำลังดูแบบไม่มีสิทธิ์แอดมิน — เจ้าตัวจริงก็เข้าส่วนนี้ไม่ได้เช่นกัน",
    )


def require_authenticated_user(
    authorization: Annotated[str | None, Header()] = None,
    x_view_as_email: Annotated[str | None, Header(alias="X-View-As-Email")] = None,
) -> dict:
    """
    เมื่อเปิด Entra: ตรวจ Microsoft JWT แล้วผูกอีเมลกับ user_access.json + trf supervisors
    แอดมินส่ง X-View-As-Email เพื่อทดสอบมุมมองผู้ใช้ (JWT ยังเป็นตัวแอดมิน)
    """
    if not auth_entra.auth_enabled():
        # โหมดปิด auth = เครื่อง dev — ให้ view-as ใช้งานได้เหมือนโหมดจริง
        view_as_local = normalized_email(x_view_as_email) if x_view_as_email else ""
        if not view_as_local:
            return unrestricted_user_context()
        try:
            ctx = build_user_access_context(view_as_local, allow_admin_bypass=False)
        except (PermissionError, ValueError) as e:
            raise HTTPException(status_code=403, detail=str(e)) from None
        ctx["is_admin"] = False
        ctx["view_as_email"] = view_as_local
        ctx["acting_admin_email"] = None
        ctx["acc_admin_full_access"] = False
        return ctx

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
    x_view_as_email: Annotated[str | None, Header(alias="X-View-As-Email")] = None,
) -> dict:
    """
    role dev เท่านั้น — ทำได้ทุกอย่างทั้งระบบ

    ใช้กับของที่มีผลทั้งระบบ: ตั้งค่าปลายทาง/แหล่งข้อมูล, rebuild ลำดับชั้น,
    ล้าง cache, ลบผลกระจาย, export รายชื่อทั้งไฟล์, เปิดสิทธิ์ส่งแบบยกชุด

    ระหว่าง "ดูสิทธิ์แบบผู้ใช้อื่น": จำลองตามจริง — บัญชีที่ดูไม่ใช่ dev ก็ต้อง 403
    เหมือนที่เจ้าตัวจริงจะเจอ (กันจอ dev-only หลุดเข้าไปในการจำลองผู้ดูแล)
    """
    view_as = normalized_email(x_view_as_email) if x_view_as_email else ""
    if not auth_entra.auth_enabled():
        if view_as and role_for_email(view_as) != ROLE_DEV:
            raise HTTPException(
                status_code=403,
                detail="กำลังดูสิทธิ์แบบผู้ใช้อื่น — ส่วนนี้เฉพาะ dev (บัญชีที่ดูไม่ใช่ dev)",
            )
        return {
            "auth_disabled": True, "email": None, "is_admin": True,
            "is_marketing": False, "role": ROLE_DEV, "admin_scope": None,
        }
    ident = _identity_from_bearer(authorization)
    email = normalized_email(ident.get("email"))
    if not is_allocation_admin_email(email):
        raise HTTPException(
            status_code=403,
            detail="เฉพาะผู้ดูแลระบบ (dev) เท่านั้น — ผู้ดูแลระดับอื่นไม่มีสิทธิ์ส่วนนี้",
        )
    if view_as and role_for_email(view_as) != ROLE_DEV:
        raise HTTPException(
            status_code=403,
            detail="กำลังดูสิทธิ์แบบผู้ใช้อื่น — ส่วนนี้เฉพาะ dev (บัญชีที่ดูไม่ใช่ dev)",
        )
    return {
        "email": email, "is_admin": True, "is_marketing": False,
        "role": ROLE_DEV, "admin_scope": None,
    }


def require_admin_scoped(
    authorization: Annotated[str | None, Header()] = None,
    x_view_as_email: Annotated[str | None, Header(alias="X-View-As-Email")] = None,
) -> dict:
    """
    dev หรือผู้ดูแล (หัวหน้าแอดมิน/แอดมิน) — คืนขอบเขตมาด้วยเสมอ

    dev ได้ admin_scope = None (ไม่จำกัด) ส่วนผู้ดูแลได้ "เซ็ตจริง" เสมอ
    ผู้เรียกต้องกรอง/ตรวจด้วย ensure_row_in_admin_scope หรือ admin_scope["sl_codes"]
    ไม่ใช่แค่ผ่านด่านนี้แล้วถือว่าทำได้ทุกแถว

    dev ที่กำลัง "ดูสิทธิ์แบบ" บัญชีผู้ดูแล จะได้ขอบเขตของบัญชีนั้นแทน —
    การจำลองมีแต่แคบลง (dev เต็มอยู่แล้ว) และคนที่ไม่ใช่ dev ใช้ view-as ไม่ได้
    """
    view_as = normalized_email(x_view_as_email) if x_view_as_email else ""
    if not auth_entra.auth_enabled():
        if view_as:
            return _viewed_privileged_context(view_as, None)
        return {
            "auth_disabled": True, "email": None, "is_admin": True,
            "is_marketing": False, "role": ROLE_DEV, "admin_scope": None,
        }
    ident = _identity_from_bearer(authorization)
    email = normalized_email(ident.get("email"))
    if is_allocation_admin_email(email):
        if view_as:
            return _viewed_privileged_context(view_as, email)
        return {
            "email": email, "is_admin": True, "is_marketing": False,
            "role": ROLE_DEV, "admin_scope": None,
        }
    if view_as:
        raise HTTPException(status_code=403, detail="ไม่มีสิทธิ์ใช้โหมดดูแบบผู้ใช้อื่น")
    admin_role = admin_role_for_email(email)
    if admin_role:
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
            "role": admin_role, "admin_scope": scope,
        }
    raise HTTPException(status_code=403, detail="ไม่มีสิทธิ์เข้าถึงหน้านี้")


def require_role_manager(
    authorization: Annotated[str | None, Header()] = None,
    x_view_as_email: Annotated[str | None, Header(alias="X-View-As-Email")] = None,
) -> dict:
    """
    ผู้ที่ "ตั้ง/ถอดสิทธิ์คนอื่น" ได้ — dev หรือหัวหน้าแอดมิน

    หัวหน้าแอดมินได้ context ที่มีขอบเขตติดมาเสมอ ตัว handler ต้องบังคับเพิ่มอีกชั้นว่า
    มอบได้เฉพาะ role `admin` และเฉพาะแถวในขอบเขตตัวเอง (ดู admin.set_user_role)
    """
    ctx = require_admin_scoped(authorization=authorization, x_view_as_email=x_view_as_email)
    if ctx.get("role") in (ROLE_DEV, ROLE_HEAD_ADMIN) or ctx.get("auth_disabled"):
        return ctx
    raise HTTPException(
        status_code=403,
        detail="เฉพาะ Dev และหัวหน้าแอดมินเท่านั้นที่ตั้งสิทธิ์ผู้ดูแลได้",
    )


def ensure_can_assign_role(actor: dict, target_role: str, target_email: str) -> None:
    """
    หัวหน้าแอดมินมอบได้เฉพาะ role `admin` และห้ามแตะสิทธิ์ของตัวเอง

    กันสองอย่างที่ต้องกันเสมอ: เลื่อนขั้นตัวเอง/พวกพ้องเป็น dev หรือหัวหน้าแอดมิน
    และถอดสิทธิ์ตัวเองจนไม่มีใครดูแลต่อ (dev เท่านั้นที่จัดการระดับบนได้)
    """
    if actor.get("auth_disabled") or actor.get("role") == ROLE_DEV:
        return
    role = str(target_role or "").strip().lower()
    if role and role != ROLE_ADMIN:
        raise HTTPException(
            status_code=403,
            detail="หัวหน้าแอดมินมอบได้เฉพาะสิทธิ์ 'แอดมิน' — ระดับ Dev/หัวหน้าแอดมินต้องให้ Dev เป็นคนตั้ง",
        )
    actor_email = normalized_email(actor.get("email"))
    if actor_email and actor_email == normalized_email(target_email):
        raise HTTPException(
            status_code=403,
            detail="แก้สิทธิ์ของตัวเองไม่ได้ — ให้ Dev เป็นคนแก้",
        )


def ensure_row_in_admin_scope(user: dict, row: dict | None) -> None:
    """
    แถวผู้ใช้แถวนี้อยู่ในภาคที่คนนี้ดูแลไหม — ใช้ทั้งตอนอ่านแถวเดิมและตอนตรวจค่าที่ส่งมา

    ต้องตรวจ **ทั้งสองฝั่ง**: แถวเป้าหมายเดิม และค่าใหม่ที่จะบันทึก
    ไม่งั้นผู้ดูแลจะย้ายคนออกนอกขอบเขตตัวเอง (หรือดึงคนนอกขอบเขตเข้ามา) ได้
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
    x_view_as_email: Annotated[str | None, Header(alias="X-View-As-Email")] = None,
) -> dict:
    """dev, ผู้ดูแล หรือ Marketing (ทีมพนักงาน) — view-as จำลองตามบัญชีที่ดู"""
    view_as = normalized_email(x_view_as_email) if x_view_as_email else ""
    if not auth_entra.auth_enabled():
        if view_as:
            return _viewed_privileged_context(view_as, None, allow_marketing=True)
        return {
            "auth_disabled": True, "email": None, "is_admin": True,
            "is_marketing": True, "role": ROLE_DEV, "admin_scope": None,
        }
    ident = _identity_from_bearer(authorization)
    email = normalized_email(ident.get("email"))
    if is_allocation_admin_email(email):
        if view_as:
            return _viewed_privileged_context(view_as, email, allow_marketing=True)
        return {
            "email": email, "is_admin": True, "is_marketing": False,
            "role": ROLE_DEV, "admin_scope": None,
        }
    if view_as:
        raise HTTPException(status_code=403, detail="ไม่มีสิทธิ์ใช้โหมดดูแบบผู้ใช้อื่น")
    admin_role = admin_role_for_email(email)
    if admin_role:
        return {
            "email": email, "is_admin": False, "is_marketing": False,
            "role": admin_role, "admin_scope": admin_scope_for_email(email),
        }
    if is_marketing_email(email):
        return {
            "email": email, "is_admin": False, "is_marketing": True,
            "role": "marketing", "admin_scope": None,
        }
    raise HTTPException(status_code=403, detail="ไม่มีสิทธิ์เข้าถึงหน้านี้")


def require_capability(capability: str):
    """
    ด่านที่อ่านสิทธิ์จาก config/admin_permissions.json แทนการฮาร์ดโค้ดต่อ endpoint

    ต่างจาก require_admin_user/require_admin_scoped ตรงที่ "ใครเข้าได้" ย้ายไปอยู่ในไฟล์
    ที่ dev แก้ได้จากหน้าแอดมิน — โค้ดบอกแค่ว่า endpoint นี้คู่กับสิทธิ์ตัวไหน

    ยังคงกติกาเดิมสองข้อไว้:
      - dev ผ่านเสมอ (capabilities_for_role คืนทุกสิทธิ์ให้ dev)
      - โหมด "ดูสิทธิ์แบบผู้ใช้อื่น" จำลองตามจริง — บัญชีที่กำลังดูไม่มีสิทธิ์ก็ต้อง 403
        เหมือนที่เจ้าตัวจะเจอ ไม่งั้นจอที่ควรถูกกันจะหลุดเข้าไปในการจำลอง

    หมายเหตุ: ผ่านด่านนี้แล้วยังต้องกรองข้อมูลตามขอบเขตเองเหมือนเดิม
    (ensure_row_in_admin_scope / ensure_sup_in_admin_scope) — ยกเว้น capability
    ที่ตั้งใจให้ข้ามขอบเขต เช่น emp_moves ซึ่งย้ายข้ามทีมได้ตามที่ตกลงไว้
    """

    def _dep(
        authorization: Annotated[str | None, Header()] = None,
        x_view_as_email: Annotated[str | None, Header(alias="X-View-As-Email")] = None,
    ) -> dict:
        ctx = require_admin_or_marketing_team(
            authorization=authorization, x_view_as_email=x_view_as_email
        )
        role = ctx.get("role") or ""
        if capability in capabilities_for_role(role):
            return ctx
        raise HTTPException(
            status_code=403,
            detail=f"บทบาทนี้ไม่มีสิทธิ์ '{capability}' — ให้ dev เปิดให้ในหน้า「สิทธิ์หน้าแอดมิน」",
        )

    return _dep


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
