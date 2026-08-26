"""
Admin API — จัดการ user_access.json และการตั้งค่าระบบ

สองระดับสิทธิ์:
  - **dev** (`require_admin_user`) — ทำได้ทุกอย่างทั้งระบบ มาจาก ALLOCATION_ADMIN_EMAILS
    หรือแถวที่ตั้ง role=dev
  - **ผู้ดูแล** (`require_admin_scoped`) — จัดการผู้ใช้/ผูกรหัส/ดูผลการดำเนินงาน
    เฉพาะภาคของตัวเอง แตะการตั้งค่าระบบไม่ได้

route ที่มีผลทั้งระบบต้องใช้ require_admin_user เสมอ ส่วน route ที่ให้ผู้ดูแลใช้ได้
ต้องกรอง/ตรวจขอบเขตในตัว handler ด้วย — ผ่านด่านอย่างเดียวไม่พอ
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Literal

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..deps import (
    ensure_can_assign_role,
    ensure_row_in_admin_scope,
    ensure_sup_in_admin_scope,
    require_admin_or_marketing_team,
    require_admin_scoped,
    require_admin_user,
    require_authenticated_user,
    require_role_manager,
)
from ..services.access_control import (
    ADMIN_SCOPE_ALL,
    ADMIN_SCOPE_LABELS,
    ASSIGNABLE_ADMIN_SCOPES,
    ASSIGNABLE_ROLES,
    DEFAULT_ADMIN_SCOPE,
    ADMIN_ROLES,
    ROLE_ADMIN,
    ROLE_DEV,
    ROLE_HEAD_ADMIN,
    ROLE_REGION_ADMIN,
    enrich_user_access_rows,
    invalidate_user_access_cache,
    row_is_in_admin_scope,
    visible_supervisors_for_row_dict,
)
from ..services.usage_log_store import log_from_user
from ..services.user_access_store import (
    apply_inferred_access_fields,
    delete_row,
    normalized_email,
    normalize_userpl,
    read_rows,
    set_email_targetsun_flag,
    upsert_row,
    write_rows,
)
from ..services import emp_assignment_store, no_target_store
from ..services.admin_team import list_supervisor_codes, load_supervisor_team
from ..services.admin_inventory import build_data_inventory
from ..services.sku_link_store import (
    collapse_hist_to_canonical,
    delete_link,
    expand_skus_for_dax,
    find_link,
    normalize_sku,
    read_links,
    upsert_link,
    write_links,
)
from ..services.sl_link_store import (
    delete_link as delete_sl_link,
    find_link as find_sl_link,
    link_row_for_api,
    normalize_sl,
    read_links as read_sl_links,
    resolve_to_canonical,
    upsert_link as upsert_sl_link,
    write_links as write_sl_links,
)
from ..services.employee_payload_cache import (
    invalidate_employee_payload_cache,
    read_cached_employee_payload,
)
from ..services.fabric_cache import cache_status, invalidate_period_cache
from ..services.allocation_store import delete_snapshot, list_all_snapshots, read_snapshot
from ..services.target_baseline import (
    diff_against_baseline,
    read_baseline,
    restore_baseline_to_target_files,
)
from ..core.targets import load_target_csv_for
from ..services.usage_log_store import append_log, read_logs
from ..services.user_access_store import read_rows as read_user_access_rows
from ..fabric_dax_connector import FabricDAXConnector

logger = logging.getLogger("target_allocation")

router = APIRouter(prefix="/admin", tags=["admin"])


class UserAccessBody(BaseModel):
    email: str
    userpl: str
    can_import_targetsun: bool = False
    note: str = ""
    login_kind: str | None = None
    manager_level: str | None = None
    acc_region: str | None = None
    acc_division: str | None = None
    acc_unit: str | None = None
    acc_position: str | None = None
    acc_scope: str | None = None


class UserAccessUpdateBody(BaseModel):
    email: str
    userpl: str
    can_import_targetsun: bool | None = None
    note: str | None = None
    new_email: str | None = Field(default=None, description="เปลี่ยนอีเมล")
    new_userpl: str | None = Field(default=None, description="เปลี่ยนรหัส USERPL")
    login_kind: str | None = None
    manager_level: str | None = None
    acc_region: str | None = None
    acc_division: str | None = None
    acc_unit: str | None = None
    acc_position: str | None = None
    acc_scope: str | None = None


_META_PATCH_KEYS = (
    "login_kind",
    "manager_level",
    "acc_region",
    "acc_division",
    "acc_unit",
    "acc_position",
    "acc_scope",
)


def _sync_access_hierarchy(admin: dict, what: str) -> None:
    """
    อัปเดตลำดับสิทธิ์ให้เองทุกครั้งที่รายชื่อผู้ใช้เปลี่ยน

    เดิมเป็นปุ่มที่คนต้องจำไปกดเอง และเปิดให้เฉพาะ dev — แอดมินที่แก้ผู้ใช้ได้
    กลับกดไม่ได้ เจอแต่ 403 ผู้ใช้ใหม่จึงล็อกอินเข้ามาแล้วไม่มีทีมให้เลือก
    เพราะ access_hierarchy.json ยังไม่รู้จักเขา

    ปลอดภัยที่จะทำอัตโนมัติเพราะ build_hierarchy_payload คงทีมของผู้จัดการที่
    คำนวณกลับไม่ได้ไว้ให้แล้ว (keep_uncomputable_teams) จึงไม่มีทางตัดสิทธิ์ใคร
    ล้มก็ไม่เป็นไร งานหลัก (บันทึกผู้ใช้) สำเร็จไปแล้ว รอบหน้าค่อยซ่อมให้เอง
    """
    from ..services.managers import rebuild_managers_from_roster

    try:
        payload = rebuild_managers_from_roster()
        logger.info(
            "sync ลำดับสิทธิ์หลัง %s: ผจก. %d · ซุป %d",
            what,
            len(payload.get("manager_codes") or []),
            len(payload.get("supervisors") or []),
        )
    except Exception as e:
        logger.warning("sync ลำดับสิทธิ์หลัง %s ไม่สำเร็จ: %s", what, e)
        _audit_admin(
            admin, "admin_hierarchy_sync_failed",
            "อัปเดตลำดับสิทธิ์อัตโนมัติไม่สำเร็จ", f"{what}: {e}", level="warn",
        )


def _patch_row_meta(row: dict[str, Any], body: UserAccessUpdateBody) -> None:
    # "ดูได้" คิดจากฟิลด์พวกนี้ ถ้ามีค่าเก่าติดมากับแถว (ไฟล์ที่แก้มือ หรือของที่
    # import ไว้ก่อนหน้า) ต้องทิ้งไปพร้อมกับการแก้ ไม่งั้นมันจะไปทับผลคำนวณใหม่
    row.pop("visible_supervisor_codes", None)
    for key in _META_PATCH_KEYS:
        if getattr(body, key, None) is None:
            continue
        val = str(getattr(body, key) or "").strip()
        if key == "login_kind" and val == "standard":
            row.pop(key, None)
            row.pop("manager_level", None)
            continue
        if key == "manager_level" and val not in ("regional", "division"):
            row.pop(key, None)
            continue
        if val:
            row[key] = val
        else:
            row.pop(key, None)


def _scope_rows(admin: dict, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """dev เห็นทุกแถว ผู้ดูแลเห็นเฉพาะขอบเขตตัวเอง"""
    if admin.get("auth_disabled") or admin.get("role") == ROLE_DEV:
        return rows
    scope = admin.get("admin_scope") or {}
    return [r for r in rows if row_is_in_admin_scope(r, scope)]


def _audit_admin(
    admin: dict,
    action: str,
    message: str,
    detail: str = "",
    level: str = "info",
    *,
    sup_id: str = "",
    target_month: int | None = None,
    target_year: int | None = None,
    context: dict[str, Any] | None = None,
) -> None:
    """
    บันทึกทุกการแก้ไขฝั่งแอดมิน

    เดิมมีแค่การเปิดสิทธิ์ส่งแบบยกชุดที่ถูก log ทำให้ไม่มีหลักฐานว่าใครแก้สิทธิ์ใคร
    พอมีสองระดับสิทธิ์ยิ่งต้องตามรอยได้

    `sup_id` เคยถูกบังคับเป็นค่าว่างเสมอ ผลคือผู้ดูแลที่มีขอบเขตจะ **มองไม่เห็น audit
    ของตัวเอง** เพราะตัวกรองขอบเขตคัดแถวที่ไม่มีทีมออก — ตอนนี้ผู้เรียกที่รู้ว่าทำกับ
    ทีมไหนต้องส่งมา
    """
    try:
        log_from_user(
            admin,
            level=level,
            sup_id=sup_id,
            action=action,
            message=message,
            detail=f"[{admin.get('role') or '-'}] {detail}",
            target_month=target_month,
            target_year=target_year,
            context=context,
        )
    except Exception:  # log ต้องไม่ทำให้งานหลักพัง
        pass


class UserAccessDeleteBody(BaseModel):
    email: str
    userpl: str


class TargetSunEmailBody(BaseModel):
    email: str
    enabled: bool = True


@router.get("/user-access")
def list_user_access(admin: dict = Depends(require_admin_scoped)) -> dict[str, Any]:
    rows = _scope_rows(admin, enrich_user_access_rows())
    scope = admin.get("admin_scope") or {}
    return {
        "rows": rows,
        "count": len(rows),
        "role": admin.get("role"),
        "scope_regions": sorted(scope.get("regions") or []),
        "scope_divisions": sorted(scope.get("divisions") or []),
    }


@router.get("/user-access/preview-visible")
def preview_user_visible(
    userpl: str = Query(..., min_length=1),
    login_kind: str = Query("standard"),
    acc_region: str = Query(""),
    acc_division: str = Query(""),
    acc_unit: str = Query(""),
    manager_level: str = Query(""),
    _admin: dict = Depends(require_admin_scoped),
) -> dict[str, Any]:
    """Preview รหัส SL ที่ดูได้ — ใช้ในฟอร์มแอดมิน"""
    row = {
        "userpl": userpl.strip().upper(),
        "login_kind": (login_kind or "standard").strip(),
        "manager_level": (manager_level or "").strip(),
        "acc_region": (acc_region or "").strip(),
        "acc_division": (acc_division or "").strip(),
        "acc_unit": (acc_unit or "").strip(),
    }
    apply_inferred_access_fields(row)
    visible = visible_supervisors_for_row_dict(row)
    return {"visible_supervisors": visible}


@router.post("/user-access")
def create_user_access(
    body: UserAccessBody,
    admin: dict = Depends(require_admin_scoped),
) -> dict[str, Any]:
    em = normalized_email(body.email)
    upl = normalize_userpl(body.userpl)
    if "@" not in em or not upl:
        raise HTTPException(status_code=400, detail="อีเมลหรือ USERPL ไม่ถูกต้อง")
    rows = read_rows()
    if any(r["email"] == em and r["userpl"] == upl for r in rows):
        raise HTTPException(status_code=409, detail="มีแถวนี้อยู่แล้ว")
    new_row: dict[str, Any] = {
        "email": em,
        "userpl": upl,
        "can_import_targetsun": bool(body.can_import_targetsun),
        "note": str(body.note or "").strip(),
    }
    _patch_row_meta(new_row, body)
    # ผู้ดูแลสร้างคนนอกขอบเขตไม่ได้ — ตรวจ "ค่าที่จะบันทึก" ไม่ใช่แค่ตัวผู้เรียก
    ensure_row_in_admin_scope(admin, new_row)
    write_rows(rows + [new_row])
    invalidate_user_access_cache()
    _sync_access_hierarchy(admin, f"เพิ่มผู้ใช้ {em}")
    _audit_admin(
        admin, "admin_user_create", f"เพิ่มผู้ใช้ {em}",
        f"USERPL={upl} ภาค={new_row.get('acc_region') or '-'} div={new_row.get('acc_division') or '-'}",
    )
    enriched = enrich_user_access_rows()
    row = next((r for r in enriched if r["email"] == em and r["userpl"] == upl), None)
    return {"ok": True, "row": row}


@router.put("/user-access")
def update_user_access(
    body: UserAccessUpdateBody,
    admin: dict = Depends(require_admin_scoped),
) -> dict[str, Any]:
    em = normalized_email(body.email)
    upl = normalize_userpl(body.userpl)
    rows = read_rows()
    existing = next((r for r in rows if r["email"] == em and r["userpl"] == upl), None)
    if not existing:
        raise HTTPException(status_code=404, detail="ไม่พบแถว")
    ensure_row_in_admin_scope(admin, existing)

    new_em = normalized_email(body.new_email) if body.new_email else em
    new_upl = normalize_userpl(body.new_userpl) if body.new_userpl else upl
    if "@" not in new_em or not new_upl:
        raise HTTPException(status_code=400, detail="อีเมลหรือ USERPL ใหม่ไม่ถูกต้อง")

    if (new_em, new_upl) != (em, upl):
        if any(
            r["email"] == new_em and r["userpl"] == new_upl
            for r in rows
            if not (r["email"] == em and r["userpl"] == upl)
        ):
            raise HTTPException(status_code=409, detail="อีเมล + USERPL ใหม่ซ้ำกับแถวอื่น")

    updated_row = dict(existing)
    updated_row["email"] = new_em
    updated_row["userpl"] = new_upl
    if body.can_import_targetsun is not None:
        updated_row["can_import_targetsun"] = bool(body.can_import_targetsun)
    if body.note is not None:
        updated_row["note"] = str(body.note).strip()
    _patch_row_meta(updated_row, body)
    # ตรวจปลายทางด้วย ไม่งั้นย้ายคนออกนอกภาคตัวเองได้
    ensure_row_in_admin_scope(admin, updated_row)

    out = [
        updated_row if r["email"] == em and r["userpl"] == upl else r
        for r in rows
    ]
    write_rows(out)
    invalidate_user_access_cache()
    _sync_access_hierarchy(admin, f"แก้ผู้ใช้ {em}")
    _audit_admin(
        admin, "admin_user_update", f"แก้ผู้ใช้ {em}",
        f"USERPL={upl}→{new_upl} ภาค={existing.get('acc_region') or '-'}→{updated_row.get('acc_region') or '-'}",
    )
    enriched = enrich_user_access_rows()
    row = next((r for r in enriched if r["email"] == new_em and r["userpl"] == new_upl), None)
    return {"ok": True, "row": row}


@router.delete("/user-access")
def remove_user_access(
    body: UserAccessDeleteBody,
    admin: dict = Depends(require_admin_scoped),
) -> dict[str, Any]:
    em = normalized_email(body.email)
    upl = normalize_userpl(body.userpl)
    rows = read_rows()
    existing = next((r for r in rows if r["email"] == em and r["userpl"] == upl), None)
    if not existing:
        raise HTTPException(status_code=404, detail="ไม่พบแถว")
    ensure_row_in_admin_scope(admin, existing)
    try:
        delete_row(rows, em, upl)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    invalidate_user_access_cache()
    _sync_access_hierarchy(admin, f"ลบผู้ใช้ {em}")
    _audit_admin(
        admin, "admin_user_delete", f"ลบผู้ใช้ {em}",
        f"USERPL={upl} ภาค={existing.get('acc_region') or '-'}", level="warn",
    )
    return {"ok": True}


@router.put("/user-access/targetsun")
def set_targetsun_for_email(
    body: TargetSunEmailBody,
    admin: dict = Depends(require_admin_scoped),
) -> dict[str, Any]:
    em = normalized_email(body.email)
    # อีเมลหนึ่งมีได้หลายแถว — ทุกแถวต้องอยู่ในภาคที่ดูแล ไม่งั้นเปิดสิทธิ์ข้ามภาคได้
    target_rows = [r for r in read_rows() if normalized_email(r.get("email")) == em]
    if not target_rows:
        raise HTTPException(status_code=404, detail="ไม่พบอีเมลนี้")
    for r in target_rows:
        ensure_row_in_admin_scope(admin, r)
    try:
        set_email_targetsun_flag(em, body.enabled)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    invalidate_user_access_cache()
    _audit_admin(
        admin, "admin_targetsun_toggle",
        f"{'เปิด' if body.enabled else 'ปิด'}สิทธิ์ส่ง Target Sun ให้ {em}",
        f"{len(target_rows)} แถว", level="warn",
    )
    return {"ok": True, "email": em, "can_import_targetsun": body.enabled}


class UserRoleBody(BaseModel):
    """
    ตั้ง role ระบบให้ผู้ใช้ — dev เท่านั้นที่ทำได้

    admin_scope ใช้เฉพาะกับ role=admin ว่าดูแลผู้ใช้ได้กว้างแค่ไหน
    (all / division / division_region) — role อื่นไม่ต้องส่งมา

    acc_division/acc_region ใช้ตอนสร้าง "บัญชีแอดมินอย่างเดียว" (อีเมลที่ยังไม่มี
    ในระบบ) เพราะขอบเขตแบบ division/division_region คิดจากสองค่านี้ของแถวตัวเอง
    """

    email: str
    role: str = ""
    admin_scope: str = ""
    acc_division: str = ""
    acc_region: str = ""


@router.put("/user-access/role")
def set_user_role(
    body: UserRoleBody,
    admin: dict = Depends(require_role_manager),
) -> dict[str, Any]:
    """
    ตั้ง/ถอด role (dev | head_admin | admin) — **dev หรือหัวหน้าแอดมิน**

    ไม่อยู่ใน _META_PATCH_KEYS โดยตั้งใจ เพื่อไม่ให้ผู้ดูแลเลื่อนขั้นตัวเองผ่าน
    PUT /user-access ปกติ · หัวหน้าแอดมินถูกจำกัดอีกชั้นด้วย ensure_can_assign_role
    (มอบได้เฉพาะ 'admin' · แตะสิทธิ์ตัวเองไม่ได้) และต้องอยู่ในขอบเขตตัวเอง
    """
    em = normalized_email(body.email)
    role = str(body.role or "").strip().lower()
    if role and role not in ASSIGNABLE_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"role ต้องเป็น {' หรือ '.join(ASSIGNABLE_ROLES)} หรือค่าว่างเพื่อถอดสิทธิ์",
        )
    ensure_can_assign_role(admin, role, em)
    scope = str(body.admin_scope or "").strip().lower()
    if scope and scope not in ASSIGNABLE_ADMIN_SCOPES:
        raise HTTPException(
            status_code=400,
            detail=f"ขอบเขตต้องเป็น {' หรือ '.join(ASSIGNABLE_ADMIN_SCOPES)}",
        )
    if role not in ADMIN_ROLES:
        # dev ไม่มีขอบเขต — ล้างทิ้งเสมอ กันค่าค้างที่จะกลับมามีผลถ้าถูกตั้งเป็นผู้ดูแลอีก
        scope = ""
    elif role == ROLE_HEAD_ADMIN:
        # หัวหน้าแอดมินดูแลทั้งระบบเสมอ — ไม่มีขอบเขตให้จำกัด (ต่างจาก dev ที่ตรงอื่น)
        scope = ADMIN_SCOPE_ALL
    elif not scope:
        scope = DEFAULT_ADMIN_SCOPE

    # หัวหน้าแอดมินแตะได้เฉพาะคนในขอบเขตตัวเอง — ทั้งตอนมอบและตอนถอด
    if not (admin.get("auth_disabled") or admin.get("role") == ROLE_DEV):
        target_rows = [r for r in read_rows() if normalized_email(r.get("email")) == em]
        for r in target_rows:
            ensure_row_in_admin_scope(admin, r)
        if not target_rows and role:
            raise HTTPException(
                status_code=403,
                detail="หัวหน้าแอดมินเพิ่มสิทธิ์ให้อีเมลที่ยังไม่มีในระบบไม่ได้ — ให้ Dev เป็นคนเพิ่ม",
            )

    div = str(body.acc_division or "").strip()
    region = str(body.acc_region or "").strip()

    # ขอบเขตที่แคบกว่า "ทุกคนในระบบ" ต้องรู้ภาค/ดิวิชันของเจ้าตัว ไม่งั้นได้ขอบเขตว่าง
    # = ผู้ดูแลที่เข้าหน้าแอดมินแล้วเจอ 403 ทุก API ดูเหมือน "ไม่มีสิทธิ์อะไรเลย"
    # เคยเกิดจริงกับบัญชีผู้ดูแลที่ไม่มีตำแหน่งงาน (ไม่มีภาค/ดิวิชันให้อ้างอิง)
    # บล็อกตั้งแต่ตอนบันทึกดีกว่าปล่อยให้ไปตายตอนเจ้าตัวล็อกอิน
    if role in ADMIN_ROLES and scope != ADMIN_SCOPE_ALL:
        existing = [r for r in read_rows() if normalized_email(r.get("email")) == em]
        has_place = bool(div or region) or any(
            str(r.get("acc_region") or "").strip() or str(r.get("acc_division") or "").strip()
            for r in existing
        )
        if not has_place:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"บัญชีนี้ยังไม่มีภาค/Division จึงใช้ขอบเขต "
                    f"'{ADMIN_SCOPE_LABELS.get(scope, scope)}' ไม่ได้ (จะกลายเป็นผู้ดูแลที่ทำอะไรไม่ได้เลย) "
                    "— เลือกขอบเขต 'ทุกคนในระบบ' หรือระบุภาค/Division ให้บัญชีนี้ก่อน"
                ),
            )

    rows = read_rows()
    touched = 0
    dropped = 0
    out: list[dict[str, Any]] = []
    for r in rows:
        if normalized_email(r.get("email")) == em:
            r = dict(r)
            if role:
                r["role"] = role
            else:
                r.pop("role", None)
            if scope:
                r["admin_scope"] = scope
            else:
                r.pop("admin_scope", None)
            # เติมภาค/ดิวิชันได้เฉพาะแถวที่ไม่มีตำแหน่งงาน (บัญชีแอดมินอย่างเดียว)
            # แถวของ Supervisor/Manager ต้องแก้ที่หน้าผู้ใช้ กันเขียนทับภาคจริงของเขา
            if str(r.get("login_kind") or "standard").strip() == "standard":
                if div:
                    r["acc_division"] = div
                if region:
                    r["acc_region"] = region
            touched += 1
            # ถอดสิทธิ์จากบัญชีที่ไม่มีรหัส SL = ไม่เหลือเหตุผลให้มีแถวนี้อยู่
            # ลบทิ้งให้ชัด ๆ ดีกว่าปล่อยให้หายเงียบตอนอ่านไฟล์รอบหน้า
            if not role and not str(r.get("userpl") or "").strip():
                dropped += 1
                continue
        out.append(r)

    created = False
    if not touched:
        if not role:
            raise HTTPException(status_code=404, detail="ไม่พบอีเมลนี้")
        # อีเมลใหม่ = สร้างบัญชี "แอดมินอย่างเดียว" ไม่มีตำแหน่งงาน ไม่มีรหัส SL
        # จึงไม่เห็นข้อมูลทีมใด ๆ บนแดชบอร์ด — มีไว้ดูแลระบบเท่านั้น
        new_row: dict[str, Any] = {
            "email": em,
            "userpl": "",
            "can_import_targetsun": False,
            "note": "บัญชีผู้ดูแลระบบ (ไม่มีตำแหน่งงาน)",
            "login_kind": "standard",
            "role": role,
        }
        if scope:
            new_row["admin_scope"] = scope
        if div:
            new_row["acc_division"] = div
        if region:
            new_row["acc_region"] = region
        out.append(new_row)
        touched = 1
        created = True

    write_rows(out)
    invalidate_user_access_cache()
    # ตั้ง role สร้างแถวใหม่ได้ (บัญชีแอดมินอย่างเดียว) ลำดับสิทธิ์ต้องรู้จักด้วย
    _sync_access_hierarchy(admin, f"ตั้ง role ให้ {em}")
    scope_note = f" (ขอบเขต: {ADMIN_SCOPE_LABELS.get(scope, scope)})" if scope else ""
    what = "สร้างบัญชีแอดมินอย่างเดียว" if created else "ตั้ง role ของ"
    _audit_admin(
        admin, "admin_role_set",
        f"{what} {em} เป็น '{role or 'ผู้ใช้ทั่วไป'}'{scope_note}",
        f"{touched} แถว" + (f" · ลบแถวแอดมินอย่างเดียว {dropped} แถว" if dropped else ""),
        level="warn",
    )
    return {
        "ok": True, "email": em, "role": role,
        "admin_scope": scope, "rows_updated": touched,
        "created": created, "rows_removed": dropped,
    }


class TargetSunBulkBody(BaseModel):
    """
    เปิด/ปิดสิทธิ์ส่ง Target Sun หลายคนพร้อมกัน

    ต้องระบุ emails ที่ต้องการเสมอ (คือรายการที่แอดมินเห็นบนหน้าจอตอนนั้น)
    ยกเว้นตั้ง all_emails=true ซึ่งหมายถึง "ทุกคนในไฟล์" — ตั้งใจให้พิมพ์ยากกว่า
    เพราะเป็นการให้สิทธิ์ส่งข้อมูลจริงเข้า Target Sun
    """

    emails: list[str] = Field(default_factory=list)
    enabled: bool
    all_emails: bool = False


@router.put("/user-access/targetsun/bulk")
def set_targetsun_bulk(
    body: TargetSunBulkBody,
    admin: dict = Depends(require_admin_user),
) -> dict[str, Any]:
    from ..services.usage_log_store import log_from_user
    from ..services.user_access_store import set_targetsun_flag_bulk

    if not body.all_emails and not body.emails:
        raise HTTPException(400, detail="ต้องระบุ emails หรือตั้ง all_emails=true")

    rows, changed = set_targetsun_flag_bulk(
        body.emails, body.enabled, all_emails=body.all_emails
    )
    invalidate_user_access_cache()

    scope = "ทุกคนในระบบ" if body.all_emails else f"{len(body.emails)} อีเมลที่เลือก"
    log_from_user(
        admin,
        level="warn",
        sup_id="",
        action="admin_targetsun_bulk",
        message=f"{'เปิด' if body.enabled else 'ปิด'}สิทธิ์ส่ง Target Sun แบบยกชุด",
        detail=f"ขอบเขต: {scope} · เปลี่ยนจริง {changed} อีเมล",
    )
    return {
        "ok": True,
        "enabled": body.enabled,
        "changed": changed,
        "total_rows": len(rows),
    }


@router.get("/supervisor-codes")
def admin_supervisor_codes(user: dict = Depends(require_admin_or_marketing_team)) -> dict[str, Any]:
    codes = list_supervisor_codes()
    if user.get("role") in ADMIN_ROLES:
        allowed = {
            str(c).strip().upper()
            for c in ((user.get("admin_scope") or {}).get("sl_codes") or set())
        }
        codes = [c for c in codes if str(c.get("code") if isinstance(c, dict) else c).strip().upper() in allowed]
    return {"supervisors": codes, "count": len(codes)}


@router.get("/supervisor-team")
def admin_supervisor_team(
    super_code: str = Query(..., min_length=1),
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    force_refresh: int = Query(0, ge=0, le=1),
    user: dict = Depends(require_admin_or_marketing_team),
) -> dict[str, Any]:
    # เดิมรับ super_code อะไรก็ได้ — ผู้ดูแลต้องดูได้เฉพาะทีมในขอบเขตตัวเอง
    if user.get("role") in ADMIN_ROLES:
        ensure_sup_in_admin_scope(user, super_code)
    return load_supervisor_team(
        super_code,
        target_year=year,
        target_month=month,
        force_refresh=bool(force_refresh),
    )


@router.get("/data-inventory")
def admin_data_inventory(
    check_fabric: int = Query(1, ge=0, le=1),
    _admin: dict = Depends(require_admin_user),
) -> dict[str, Any]:
    return build_data_inventory(check_fabric=bool(check_fabric))


class SkuLinkBody(BaseModel):
    canonical_sku: str
    alias_skus: list[str] = Field(default_factory=list)
    product_name: str = ""
    note: str = ""


class SkuLinkUpdateBody(SkuLinkBody):
    new_canonical_sku: str | None = Field(default=None, description="เปลี่ยนรหัส canonical")


class SkuLinkDeleteBody(BaseModel):
    canonical_sku: str


def _sku_link_row_for_api(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "canonical_sku": row.get("canonical_sku"),
        "alias_skus": list(row.get("alias_skus") or []),
        "product_name": row.get("product_name") or "",
        "note": row.get("note") or "",
        "updated_by": row.get("updated_by") or "",
    }


@router.get("/sku-links")
def list_sku_links(_user: dict = Depends(require_admin_or_marketing_team)) -> dict[str, Any]:
    rows = [_sku_link_row_for_api(r) for r in read_links()]
    return {"links": rows, "count": len(rows)}


# ผูกรหัสสินค้าเป็นข้อมูลกลางของทั้งระบบ ไม่มีมิติภาคให้แบ่ง — เปิดให้ผู้ดูแล
# จัดการได้ตามที่ตกลง แต่ต้อง log ทุกครั้งเพราะกระทบทุกทีม
@router.post("/sku-links")
def create_sku_link(
    body: SkuLinkBody,
    admin: dict = Depends(require_admin_scoped),
) -> dict[str, Any]:
    canon = normalize_sku(body.canonical_sku)
    if not canon:
        raise HTTPException(status_code=400, detail="canonical_sku ว่าง")
    links = read_links()
    if find_link(canon, links):
        raise HTTPException(status_code=409, detail="มีกลุ่มผูกรหัสนี้อยู่แล้ว")
    email = str(admin.get("email") or admin.get("preferred_username") or "").strip()
    saved = upsert_link(
        links,
        canonical_sku=canon,
        alias_skus=body.alias_skus or [canon],
        product_name=body.product_name,
        note=body.note,
        updated_by=email,
    )
    row = find_link(canon, saved)
    _audit_admin(admin, "admin_sku_link_create", f"ผูกรหัสสินค้า {canon}", f"alias={len(body.alias_skus or [])}")
    return {"ok": True, "row": _sku_link_row_for_api(row or {})}


@router.put("/sku-links")
def update_sku_link(
    body: SkuLinkUpdateBody,
    admin: dict = Depends(require_admin_scoped),
) -> dict[str, Any]:
    canon = normalize_sku(body.canonical_sku)
    if not canon:
        raise HTTPException(status_code=400, detail="canonical_sku ว่าง")
    links = read_links()
    if not find_link(canon, links):
        raise HTTPException(status_code=404, detail="ไม่พบกลุ่มผูกรหัส")
    new_canon = normalize_sku(body.new_canonical_sku) if body.new_canonical_sku else canon
    if new_canon != canon and find_link(new_canon, links):
        raise HTTPException(status_code=409, detail="canonical_sku ใหม่ซ้ำกับกลุ่มอื่น")
    email = str(admin.get("email") or admin.get("preferred_username") or "").strip()
    out: list[dict[str, Any]] = []
    for row in links:
        if row["canonical_sku"] == canon:
            nr = dict(row)
            nr["canonical_sku"] = new_canon
            nr["alias_skus"] = body.alias_skus or nr.get("alias_skus") or [new_canon]
            nr["product_name"] = str(body.product_name or nr.get("product_name") or "").strip()
            nr["note"] = str(body.note if body.note is not None else nr.get("note") or "").strip()
            if email:
                nr["updated_by"] = email
            out.append(nr)
        else:
            out.append(dict(row))
    saved = write_links(out)
    row = find_link(new_canon, saved)
    _audit_admin(admin, "admin_sku_link_update", f"แก้ผูกรหัสสินค้า {canon}→{new_canon}")
    return {"ok": True, "row": _sku_link_row_for_api(row or {})}


@router.delete("/sku-links")
def remove_sku_link(
    body: SkuLinkDeleteBody,
    admin: dict = Depends(require_admin_scoped),
) -> dict[str, Any]:
    canon = normalize_sku(body.canonical_sku)
    try:
        delete_link(read_links(), canon)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    _audit_admin(admin, "admin_sku_link_delete", f"ลบผูกรหัสสินค้า {canon}", level="warn")
    return {"ok": True}


def _hist_totals_for_sku(df, sku: str) -> dict[str, float]:
    import pandas as pd

    if df is None or getattr(df, "empty", True):
        return {"hist_boxes": 0.0, "hist_amount": 0.0, "rows": 0.0}
    sub = df[df["sku"].astype(str).str.strip() == sku]
    boxes = float(pd.to_numeric(sub.get("hist_boxes", 0), errors="coerce").fillna(0).sum())
    amount = float(pd.to_numeric(sub.get("hist_amount", 0), errors="coerce").fillna(0).sum())
    return {"hist_boxes": boxes, "hist_amount": amount, "rows": float(len(sub))}


@router.get("/sku-links/preview")
def preview_sku_link(
    super_code: str = Query(..., min_length=1),
    canonical_sku: str = Query(..., min_length=1),
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    _user: dict = Depends(require_admin_or_marketing_team),
) -> dict[str, Any]:
    """ทดสอบยอดประวัติ 3M / LY หลังรวม alias"""
    canon = normalize_sku(canonical_sku)
    sup = super_code.strip().upper()
    team = load_supervisor_team(sup, target_year=year, target_month=month, force_refresh=False)
    emp_list = [
        str(e.get("emp_id") or "").strip()
        for e in (team.get("employees") or [])
        if str(e.get("emp_id") or "").strip()
    ]
    links = read_links()
    expanded = expand_skus_for_dax([canon], links)
    extra = [a for a in expanded if a != canon]

    before_3m = {"hist_boxes": 0.0, "hist_amount": 0.0, "rows": 0.0}
    after_3m = {"hist_boxes": 0.0, "hist_amount": 0.0, "rows": 0.0}
    before_ly = {"hist_boxes": 0.0, "hist_amount": 0.0, "rows": 0.0}
    after_ly = {"hist_boxes": 0.0, "hist_amount": 0.0, "rows": 0.0}
    fabric_error: str | None = None

    if emp_list:
        try:
            fabric = FabricDAXConnector()
            df_3m_canon = fabric.get_historical_sales(
                month, year, sku_list=[canon], emp_list=emp_list, n_months=3
            )
            df_3m_exp = fabric.get_historical_sales(
                month, year, sku_list=expanded, emp_list=emp_list, n_months=3
            )
            df_3m_merged = collapse_hist_to_canonical(df_3m_exp, links)
            before_3m = _hist_totals_for_sku(df_3m_canon, canon)
            after_3m = _hist_totals_for_sku(df_3m_merged, canon)

            df_ly_canon = fabric.get_same_month_prior_year_by_emp_sku(
                month, year, sku_list=[canon], emp_list=emp_list
            )
            df_ly_exp = fabric.get_same_month_prior_year_by_emp_sku(
                month, year, sku_list=expanded, emp_list=emp_list
            )
            df_ly_merged = collapse_hist_to_canonical(df_ly_exp, links)
            before_ly = _hist_totals_for_sku(df_ly_canon, canon)
            after_ly = _hist_totals_for_sku(df_ly_merged, canon)
        except Exception as e:
            fabric_error = str(e)
            logger.warning("sku-links preview fabric failed: %s", e)

    return {
        "supervisor_code": sup,
        "canonical_sku": canon,
        "alias_skus": expanded,
        "extra_aliases": extra,
        "employee_count": len(emp_list),
        "hist_3m": {"before_merge": before_3m, "after_merge": after_3m},
        "hist_ly_same_month": {"before_merge": before_ly, "after_merge": after_ly},
        "fabric_error": fabric_error,
        "refresh_hint": "หลังบันทึก link ให้โหลด Dashboard ใหม่ (refresh=true) เพื่อ rebuild hist cache",
    }


class SlLinkBody(BaseModel):
    old_sl: str = ""
    new_sls: list[str] = Field(default_factory=list)
    canonical_sl: str = ""
    alias_sls: list[str] = Field(default_factory=list)
    note: str = ""


class SlLinkUpdateBody(SlLinkBody):
    new_old_sl: str | None = Field(default=None, description="เปลี่ยนรหัสเก่า")


class SlLinkDeleteBody(BaseModel):
    old_sl: str = ""
    canonical_sl: str = ""


def _sl_body_old_new(body: SlLinkBody) -> tuple[str, list[str]]:
    old = normalize_sl(body.old_sl or body.canonical_sl)
    new_raw = list(body.new_sls or [])
    if not new_raw and body.alias_sls:
        old_a = normalize_sl(body.old_sl or body.canonical_sl)
        new_raw = [normalize_sl(x) for x in body.alias_sls if normalize_sl(x) != old_a]
    new_sls = []
    for x in new_raw:
        nx = normalize_sl(x)
        if nx and nx != old and nx not in new_sls:
            new_sls.append(nx)
    return old, new_sls


def _sl_link_row_for_api(row: dict[str, Any]) -> dict[str, Any]:
    return link_row_for_api(row)


@router.get("/sl-links")
def list_sl_links(_user: dict = Depends(require_admin_or_marketing_team)) -> dict[str, Any]:
    try:
        rows = [_sl_link_row_for_api(r) for r in read_sl_links()]
    except (ValueError, PermissionError) as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"links": rows, "count": len(rows)}


def _ensure_sl_link_in_scope(admin: dict, old: str, new_sls: list[str]) -> None:
    """
    ผูกรหัสมีผลกับสิทธิ์การมองเห็น — ผู้ดูแลต้องแตะได้เฉพาะรหัสในขอบเขตตัวเอง
    ต้องตรวจ **ทุกรหัสในกลุ่ม** ไม่ใช่แค่รหัสหลัก ไม่งั้นดึงทีมของภาคอื่นเข้ามาผูกได้
    """
    if admin.get("auth_disabled") or admin.get("role") == ROLE_DEV:
        return
    for code in [old, *(new_sls or [])]:
        if str(code or "").strip():
            ensure_sup_in_admin_scope(admin, code)


@router.post("/sl-links")
def create_sl_link(
    body: SlLinkBody,
    admin: dict = Depends(require_admin_scoped),
) -> dict[str, Any]:
    old, new_sls = _sl_body_old_new(body)
    if not old:
        raise HTTPException(status_code=400, detail="รหัสเก่า (old_sl) ว่าง")
    _ensure_sl_link_in_scope(admin, old, new_sls)
    links = read_sl_links()
    if find_sl_link(old, links):
        raise HTTPException(status_code=409, detail="มีกลุ่มผูกรหัสนี้อยู่แล้ว")
    email = str(admin.get("email") or admin.get("preferred_username") or "").strip()
    saved = upsert_sl_link(
        links,
        canonical_sl=old,
        alias_sls=[old, *new_sls],
        note=body.note,
        updated_by=email,
    )
    row = find_sl_link(old, saved)
    _audit_admin(admin, "admin_sl_link_create", f"ผูกรหัส SL {old}", f"→ {', '.join(new_sls) or '-'}")
    return {"ok": True, "row": _sl_link_row_for_api(row or {})}


@router.put("/sl-links")
def update_sl_link(
    body: SlLinkUpdateBody,
    admin: dict = Depends(require_admin_scoped),
) -> dict[str, Any]:
    old, new_sls = _sl_body_old_new(body)
    if not old:
        raise HTTPException(status_code=400, detail="รหัสเก่า (old_sl) ว่าง")
    _ensure_sl_link_in_scope(admin, old, new_sls)
    links = read_sl_links()
    if not find_sl_link(old, links):
        raise HTTPException(status_code=404, detail="ไม่พบกลุ่มผูกรหัส")
    new_old = normalize_sl(body.new_old_sl) if body.new_old_sl else old
    if new_old != old and find_sl_link(new_old, links):
        raise HTTPException(status_code=409, detail="รหัสเก่าใหม่ซ้ำกับกลุ่มอื่น")
    if new_old != old:
        _ensure_sl_link_in_scope(admin, new_old, [])
    email = str(admin.get("email") or admin.get("preferred_username") or "").strip()
    out: list[dict[str, Any]] = []
    for row in links:
        row_old = normalize_sl(row.get("old_sl") or row.get("canonical_sl"))
        if row_old == old:
            nr = dict(row)
            nr["old_sl"] = new_old
            nr["canonical_sl"] = new_old
            nr["new_sls"] = new_sls
            nr["alias_sls"] = [new_old, *new_sls]
            nr["note"] = str(body.note if body.note is not None else nr.get("note") or "").strip()
            if email:
                nr["updated_by"] = email
            out.append(nr)
        else:
            out.append(dict(row))
    saved = write_sl_links(out)
    row = find_sl_link(new_old, saved)
    _audit_admin(admin, "admin_sl_link_update", f"แก้ผูกรหัส SL {old}→{new_old}", f"→ {', '.join(new_sls) or '-'}")
    return {"ok": True, "row": _sl_link_row_for_api(row or {})}


@router.delete("/sl-links")
def remove_sl_link(
    body: SlLinkDeleteBody,
    admin: dict = Depends(require_admin_scoped),
) -> dict[str, Any]:
    old = normalize_sl(body.old_sl or body.canonical_sl)
    existing = find_sl_link(old, read_sl_links())
    if existing:
        _ensure_sl_link_in_scope(admin, old, list(existing.get("new_sls") or []))
    try:
        delete_sl_link(read_sl_links(), old)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    _audit_admin(admin, "admin_sl_link_delete", f"ลบผูกรหัส SL {old}", level="warn")
    return {"ok": True}


def _sku_rows_from_payload(payload: dict[str, Any], sku_links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from ..services.sku_link_store import alias_to_canonical_map, extra_aliases_for_canonical

    alias_map = alias_to_canonical_map(sku_links)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in payload.get("skus") or []:
        if not isinstance(raw, dict):
            continue
        sku = normalize_sku(raw.get("sku"))
        if not sku or sku in seen:
            continue
        seen.add(sku)
        canon = alias_map.get(sku, sku)
        extras = extra_aliases_for_canonical(canon, sku_links) if canon == sku else []
        out.append(
            {
                "sku": sku,
                "canonical_sku": canon,
                "product_name_thai": str(raw.get("product_name_thai") or "").strip(),
                "product_name_english": str(raw.get("product_name_english") or "").strip(),
                "brand": str(raw.get("brand") or "").strip(),
                "target_boxes": float(raw.get("target_boxes") or 0),
                "target_sun": float(raw.get("target_sun") or 0),
                "has_sku_link": canon != sku or bool(extras),
                "linked_aliases": extras,
            }
        )
    out.sort(key=lambda r: r["sku"])
    return out


@router.get("/sku-links/catalog")
def sku_link_catalog(
    year: int | None = Query(None, ge=2000, le=2100),
    month: int | None = Query(None, ge=1, le=12),
    super_code: str | None = Query(default=None, description="ไม่บังคับ — ใช้ cache ทีมเดียวถ้าระบุ"),
    _user: dict = Depends(require_admin_or_marketing_team),
) -> dict[str, Any]:
    """รายการสินค้าที่มีเป้าในงวดปัจจุบัน — เฉพาะ SKU ที่มีเป้าหีบ > 0 (ไม่แสดงรหัสเก่าที่ไม่มีเป้าในงวดนี้)"""
    from ..core.tga_period import expected_allocation_period_ce
    from ..services import targetsun_read

    if month is None or year is None:
        year, month = expected_allocation_period_ce()
    month = int(month)
    year = int(year)
    sku_links = read_links()
    read_src = targetsun_read.get_target_read_source()
    sup = str(super_code or "").strip().upper()
    skus: list[dict[str, Any]] = []
    hint = ""
    from_cache = False
    fabric_error: str | None = None

    if sup:
        payload = read_cached_employee_payload(sup, month, year)
        source_sup = sup
        if payload is None:
            canon_sl = resolve_to_canonical(sup)
            if canon_sl != sup:
                payload = read_cached_employee_payload(canon_sl, month, year)
                if payload is not None:
                    source_sup = canon_sl
        if payload is not None:
            from_cache = True
            skus = [r for r in _sku_rows_from_payload(payload, sku_links) if float(r.get("target_boxes") or 0) > 0]
            if skus:
                return {
                    "supervisor_code": sup,
                    "source_supervisor_code": source_sup,
                    "target_month": month,
                    "target_year": year,
                    "from_cache": True,
                    "from_fabric": False,
                    "count": len(skus),
                    "skus": skus,
                    "hint": f"จาก cache ทีม {source_sup}",
                }

    try:
        fabric = FabricDAXConnector()
        df_tgt = fabric.get_tga_period_sku_targets(month, year)
        src_label = "Target Sun" if read_src == "targetsun" else "Fabric TGA"
        if df_tgt is None or df_tgt.empty:
            hint = f"ไม่พบเป้าในงวด {month:02d}/{year} (แหล่ง {src_label}) — รอ HQ อัปเดตเป้า"
        else:
            sku_list = df_tgt["sku"].astype(str).str.strip().tolist()
            df_info = fabric.get_product_info(
                sku_list=sku_list, target_year=year, target_month=month
            )
            price_map: dict[str, float] = {}
            try:
                df_price = fabric.get_latest_price_per_box_by_sku(month, year, sku_list)
                if df_price is not None and not df_price.empty:
                    price_map = dict(
                        zip(
                            df_price["sku"].astype(str).str.strip(),
                            df_price["price_per_box"].astype(float),
                        )
                    )
            except Exception as e:
                logger.warning("sku catalog price fetch failed: %s", e)

            info_by_sku: dict[str, dict[str, Any]] = {}
            if df_info is not None and not df_info.empty:
                for _, r in df_info.iterrows():
                    k = str(r.get("sku") or r.get("ProductCode") or "").strip()
                    if k:
                        info_by_sku[k] = r.to_dict()

            alias_map = {}
            from ..services.sku_link_store import alias_to_canonical_map, extra_aliases_for_canonical

            alias_map = alias_to_canonical_map(sku_links)
            tgt_map = {
                str(r["sku"]).strip(): float(r["target_boxes"] or 0)
                for _, r in df_tgt.iterrows()
            }
            for sku, boxes in sorted(tgt_map.items()):
                if float(boxes or 0) <= 0:
                    continue
                info = info_by_sku.get(sku, {})
                canon = alias_map.get(sku, sku)
                extras = extra_aliases_for_canonical(canon, sku_links) if canon == sku else []
                skus.append(
                    {
                        "sku": sku,
                        "canonical_sku": canon,
                        "product_name_thai": str(
                            info.get("product_name_thai") or info.get("Product_NameThai") or ""
                        ).strip(),
                        "product_name_english": str(
                            info.get("product_name_english") or info.get("Product_NameEnglish") or ""
                        ).strip(),
                        "brand": str(info.get("brand") or info.get("Brand") or "").strip(),
                        "target_boxes": boxes,
                        "price_per_box": float(price_map.get(sku, 0) or 0),
                        "has_sku_link": canon != sku or bool(extras),
                        "linked_aliases": extras,
                    }
                )
            hint = f"งวดกระจายเป้า {month:02d}/{year} · แหล่ง {src_label} · {len(skus)} SKU มีเป้าหีบ > 0"
    except Exception as e:
        fabric_error = str(e)
        logger.warning("sku-links catalog fabric failed: %s", e)
        if not hint:
            hint = f"ดึงจาก Fabric ไม่สำเร็จ: {e}"

    return {
        "supervisor_code": sup or None,
        "source_supervisor_code": None,
        "target_month": month,
        "target_year": year,
        "from_cache": from_cache,
        "from_fabric": bool(skus) and not from_cache,
        "count": len(skus),
        "skus": skus,
        "hint": hint,
        "fabric_error": fabric_error,
    }


class UsageLogBody(BaseModel):
    level: str = "error"
    action: str = ""
    message: str = ""
    detail: str = ""
    sup_id: str = ""


def _supervisor_meta_index() -> dict[str, dict[str, str]]:
    """SL → {full_name, acc_division, acc_region, acc_unit} จาก user_access.json

    แถว supervisor_acc มาก่อน (ข้อมูลสังกัดครบกว่า) — แถวอื่นเติมเฉพาะรหัสที่ยังไม่มี
    """
    from ..services.user_access_store import real_userpl

    index: dict[str, dict[str, str]] = {}
    rows = sorted(
        read_rows(),
        key=lambda r: 0 if str(r.get("login_kind") or "") == "supervisor_acc" else 1,
    )
    for r in rows:
        code = real_userpl(r.get("userpl"))
        if not code or code in index:
            continue
        index[code] = {
            "full_name": str(r.get("full_name") or "").strip(),
            "acc_division": str(r.get("acc_division") or "").strip(),
            "acc_region": str(r.get("acc_region") or "").strip(),
            "acc_unit": str(r.get("acc_unit") or "").strip(),
        }
    return index


def _scoped_allocation_items(
    admin: dict,
    target_month: int | None,
    target_year: int | None,
) -> list[dict]:
    """snapshot ผลกระจาย กรองตามขอบเขตแอดมิน + เติมชื่อ/สังกัดของแต่ละ SL"""
    items = list_all_snapshots(
        month=target_month,
        year=target_year,
    )
    if admin.get("role") != ROLE_DEV and not admin.get("auth_disabled"):
        codes = {
            str(c).strip().upper()
            for c in ((admin.get("admin_scope") or {}).get("sl_codes") or set())
        }
        items = [it for it in items if str(it.get("sup_id") or "").strip().upper() in codes]
    meta = _supervisor_meta_index()
    for it in items:
        m = meta.get(str(it.get("sup_id") or "").strip().upper()) or {}
        it["full_name"] = m.get("full_name", "")
        it["acc_division"] = m.get("acc_division", "")
        it["acc_region"] = m.get("acc_region", "")
        it["acc_unit"] = m.get("acc_unit", "")
    return items


@router.get("/allocations")
def admin_list_allocations(
    admin: dict = Depends(require_admin_scoped),
    target_month: int | None = Query(None, ge=1, le=12),
    target_year: int | None = Query(None, ge=2020, le=2100),
):
    """รายการ snapshot ผลกระจาย — ผู้ดูแลเห็นเฉพาะทีมในขอบเขตตัวเอง"""
    items = _scoped_allocation_items(admin, target_month, target_year)
    return {"items": items, "count": len(items)}


@router.delete("/allocations")
def admin_delete_allocation(
    admin: dict = Depends(require_admin_user),
    sup_id: str = Query(..., min_length=1),
    target_month: int = Query(..., ge=1, le=12),
    target_year: int = Query(..., ge=2020, le=2100),
):
    """ลบ snapshot ผลกระจาย — ลบแล้วหายถาวร จึงต้องมีร่องรอยว่าเมื่อกี้มีอะไรอยู่"""
    sid = sup_id.strip().upper()
    prev = read_snapshot(sid, target_month, target_year)
    if not delete_snapshot(sid, target_month, target_year):
        raise HTTPException(status_code=404, detail="ไม่พบผลกระจายที่จะลบ")
    rows = (prev or {}).get("allocations") or []
    boxes = 0
    for a in rows:
        try:
            boxes += int(round(float((a or {}).get("allocated_boxes") or 0)))
        except (TypeError, ValueError):
            continue
    _audit_admin(
        admin,
        "admin_delete_allocation",
        f"ลบผลกระจาย {sid} งวด {target_month:02d}/{target_year}",
        f"ของเดิม {len(rows)} แถว ({boxes} หีบ) บันทึกโดย {(prev or {}).get('updated_by') or '—'}",
        level="warn",
        sup_id=sid,
        target_month=target_month,
        target_year=target_year,
        context={
            "rows_deleted": len(rows),
            "boxes_deleted": boxes,
            "version_deleted": (prev or {}).get("version"),
            "updated_by": (prev or {}).get("updated_by"),
            "updated_at": (prev or {}).get("updated_at"),
        },
    )
    return {"status": "ok", "sup_id": sid}


class NoTargetBody(BaseModel):
    super_code: str
    emp_ids: list[str] = Field(default_factory=list)
    notes: dict[str, str] = Field(default_factory=dict)
    names: dict[str, str] = Field(default_factory=dict)


@router.get("/no-target-employees")
def admin_list_no_target_employees(
    user: dict = Depends(require_admin_or_marketing_team),
    super_code: str | None = Query(None),
) -> dict[str, Any]:
    """
    รายชื่อพนักงานที่ไม่ต้องตั้งเป้า — ระบุ super_code เพื่อดูทีมเดียว

    ต่างจาก「ไม่นำไปกระจายเป้า」ที่ระบบอนุมานจากเป้าเงิน รายชื่อชุดนี้เป็นการ
    ตัดสินใจของคน จึงอยู่ถาวรจนกว่าจะปลด
    """
    rows = no_target_store.read_entries()
    if user.get("role") in ADMIN_ROLES:
        allowed = {
            str(c).strip().upper()
            for c in ((user.get("admin_scope") or {}).get("sl_codes") or set())
        }
        rows = [r for r in rows if r["super_code"] in allowed]
    if super_code:
        sup = no_target_store.norm_sup(super_code)
        if user.get("role") in ADMIN_ROLES:
            ensure_sup_in_admin_scope(user, sup)
        rows = [r for r in rows if r["super_code"] == sup]
    return {"employees": rows, "count": len(rows)}


@router.put("/no-target-employees")
def admin_set_no_target_employees(
    body: NoTargetBody,
    admin: dict = Depends(require_admin_scoped),
) -> dict[str, Any]:
    """
    แทนที่รายชื่อของทีมเดียวทั้งชุด — หน้าแอดมินส่งสถานะทั้งทีมมาทีเดียว

    การแทนที่ทั้งชุดตรงกับสิ่งที่ผู้ใช้เห็นบนจอ (ติ๊ก/ไม่ติ๊ก) และปลดคนที่เอาติ๊กออก
    ได้โดยไม่ต้องมีคำสั่งลบแยก · ทีมอื่นไม่ถูกแตะ
    """
    sup = no_target_store.norm_sup(body.super_code)
    if not sup:
        raise HTTPException(status_code=400, detail="ไม่ได้ระบุรหัสซุป")
    ensure_sup_in_admin_scope(admin, sup)
    before = sorted(no_target_store.no_target_emp_ids(sup))
    rows = no_target_store.set_for_supervisor(
        sup,
        body.emp_ids,
        updated_by=admin.get("email") or "",
        notes=body.notes,
        names=body.names,
    )
    after = sorted(no_target_store.no_target_emp_ids(sup))
    added = [e for e in after if e not in before]
    removed = [e for e in before if e not in after]
    if added or removed:
        _audit_admin(
            admin,
            "no_target_employees_set",
            f"ตั้งพนักงานที่ไม่ต้องตั้งเป้า {sup} — รวม {len(after)} คน",
            (
                (f"เพิ่ม: {', '.join(added)}" if added else "")
                + (" · " if added and removed else "")
                + (f"ปลด: {', '.join(removed)}" if removed else "")
            ),
            sup_id=sup,
            context={"before": before, "after": after, "added": added, "removed": removed},
        )
    return {
        "ok": True,
        "super_code": sup,
        "employees": [r for r in rows if r["super_code"] == sup],
        "added": added,
        "removed": removed,
    }


@router.get("/target-baseline")
def admin_get_target_baseline(
    admin: dict = Depends(require_admin_scoped),
    sup_id: str = Query(..., min_length=1),
    target_month: int = Query(..., ge=1, le=12),
    target_year: int = Query(..., ge=2020, le=2100),
) -> dict[str, Any]:
    """
    เป้าตั้งต้นของงวด (สำเนาชุดแรกที่ระบบดึงมา) — ผู้ดูแลทุกระดับดูได้

    ใช้ตอบคำถาม "เป้าเดิมเป็นเท่าไร" เมื่อเป้าปัจจุบันดูผิด · ตัวเทียบกับของปัจจุบัน
    อยู่ในคำตอบด้วย จะได้เห็นทันทีว่าต่างตรงไหนโดยไม่ต้องไล่เอง
    """
    sid = sup_id.strip().upper()
    ensure_sup_in_admin_scope(admin, sid)
    base = read_baseline(sid, target_month, target_year)
    if not base:
        raise HTTPException(
            status_code=404,
            detail=(
                "ยังไม่มีเป้าตั้งต้นของงวดนี้ — ระบบเก็บให้อัตโนมัติตอนเปิดงวดครั้งแรก "
                "(งวดที่เปิดไปก่อนที่ระบบนี้จะมี จึงยังไม่มีสำเนา)"
            ),
        )
    df_sku, df_sun = load_target_csv_for(sid, target_month, target_year)
    return {
        "baseline": base,
        "diff": diff_against_baseline(sid, target_month, target_year, df_sku, df_sun),
    }


@router.get("/target-baseline/export")
def admin_export_target_baseline(
    admin: dict = Depends(require_admin_scoped),
    sup_id: str = Query(..., min_length=1),
    target_month: int = Query(..., ge=1, le=12),
    target_year: int = Query(..., ge=2020, le=2100),
):
    """ดาวน์โหลดเป้าตั้งต้นเป็นไฟล์ JSON ไว้เก็บ/ส่งต่อ"""
    sid = sup_id.strip().upper()
    ensure_sup_in_admin_scope(admin, sid)
    base = read_baseline(sid, target_month, target_year)
    if not base:
        raise HTTPException(status_code=404, detail="ยังไม่มีเป้าตั้งต้นของงวดนี้")
    fname = f"target_baseline_{sid}_{target_year}_{target_month:02d}.json"
    return JSONResponse(
        content=base,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/target-baseline/restore")
def admin_restore_target_baseline(
    admin: dict = Depends(require_admin_user),
    sup_id: str = Query(..., min_length=1),
    target_month: int = Query(..., ge=1, le=12),
    target_year: int = Query(..., ge=2020, le=2100),
) -> dict[str, Any]:
    """
    เขียนเป้าตั้งต้นกลับทับไฟล์เป้าปัจจุบัน — **dev เท่านั้น**

    เป็นการทับข้อมูลที่คนอื่นอาจกำลังใช้อยู่ จึงไม่เปิดให้ผู้ดูแลระดับอื่น
    ไม่แตะผลกระจายที่บันทึกไว้ (snapshot) — คืนแค่ "เป้า" ให้กลับไปเป็นชุดแรก
    แล้วผู้ใช้กดกระจายใหม่เองตามต้องการ
    """
    sid = sup_id.strip().upper()
    result = restore_baseline_to_target_files(sid, target_month, target_year)
    _audit_admin(
        admin,
        "target_baseline_restore",
        f"กู้คืนเป้าตั้งต้น {sid} งวด {target_month:02d}/{target_year}",
        f"SKU {result['skus']} รายการ ({result['total_boxes']} หีบ) · พนักงาน {result['employees']} คน",
        level="warn",
        sup_id=sid,
        target_month=target_month,
        target_year=target_year,
        context={
            "skus": result["skus"],
            "employees": result["employees"],
            "total_boxes": result["total_boxes"],
            "captured_at": result.get("captured_at"),
        },
    )
    return {"ok": True, **result}


@router.get("/allocations/export")
def admin_export_allocation(
    admin: dict = Depends(require_admin_scoped),
    sup_id: str = Query(..., min_length=1),
    target_month: int = Query(..., ge=1, le=12),
    target_year: int = Query(..., ge=2020, le=2100),
):
    """ดาวน์โหลด snapshot JSON สำหรับสำรอง"""
    sid = sup_id.strip().upper()
    ensure_sup_in_admin_scope(admin, sid)
    snap = read_snapshot(sid, target_month, target_year)
    if not snap:
        raise HTTPException(status_code=404, detail="ไม่พบผลกระจาย")
    fname = f"allocation_{sid}_{target_year}_{target_month:02d}.json"
    return JSONResponse(
        content=snap,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


_ALLOC_STATUS_TH = {
    "optimized": "กระจายแล้ว",
    "draft": "แบบร่าง",
    "sent_targetsun": "ส่ง Target Sun แล้ว",
}


def _fmt_ts_th(ts: Any) -> str:
    """ISO timestamp → เวลาไทยอ่านง่าย (คืนค่าดิบถ้า parse ไม่ได้)"""
    s = str(ts or "").strip()
    if not s:
        return ""
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone(ZoneInfo("Asia/Bangkok"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return s


def _xlsx_response(rows: list[dict], columns: list[tuple[str, str]], basename: str) -> Response:
    """สร้าง .xlsx จาก rows ตามลำดับ columns (key, หัวตาราง) — ตอบกลับเป็นไฟล์แนบ"""
    import io

    import pandas as pd

    data = {header: [row.get(key, "") for row in rows] for key, header in columns}
    df = pd.DataFrame(data)
    buf = io.BytesIO()
    try:
        with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
            df.to_excel(writer, sheet_name="report", index=False)
    except ImportError:
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="report", index=False)
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{basename}.xlsx"',
            "X-Export-Rows": str(len(rows)),
        },
    )


@router.get("/allocations/export-xlsx")
def admin_export_allocations_xlsx(
    admin: dict = Depends(require_admin_scoped),
    target_month: int | None = Query(None, ge=1, le=12),
    target_year: int | None = Query(None, ge=2020, le=2100),
):
    """ตารางผลการกระจาย (ตามขอบเขต + filter งวด) เป็น Excel สำหรับรายงานการใช้งาน"""
    items = _scoped_allocation_items(admin, target_month, target_year)
    for it in items:
        it["period"] = f"{int(it.get('target_month') or 0):02d}/{it.get('target_year') or ''}"
        it["status_th"] = _ALLOC_STATUS_TH.get(
            str(it.get("status") or "").lower(), str(it.get("status") or "")
        )
        it["updated_at_th"] = _fmt_ts_th(it.get("updated_at"))
        it["sent_at_th"] = _fmt_ts_th(it.get("target_sun_sent_at"))
    cols = [
        ("sup_id", "รหัส SL"),
        ("full_name", "ชื่อ Supervisor"),
        ("acc_division", "Division"),
        ("acc_region", "ภาค"),
        ("acc_unit", "หน่วย"),
        ("period", "งวด"),
        ("status_th", "สถานะ"),
        ("allocation_rows", "จำนวนแถวผลกระจาย"),
        ("strategy", "วิธีกระจาย"),
        ("updated_at_th", "อัปเดตล่าสุด"),
        ("updated_by", "อัปเดตโดย"),
        ("sent_at_th", "ส่ง Target Sun เมื่อ"),
    ]
    m_part = f"{target_month:02d}" if target_month else "all"
    y_part = str(target_year) if target_year else "all"
    return _xlsx_response(items, cols, f"allocation_report_{y_part}_{m_part}")


@router.get("/user-access/export")
def admin_export_user_access(_admin: dict = Depends(require_admin_user)):
    """ดาวน์โหลด user_access.json สำหรับสำรอง"""
    rows = read_user_access_rows()
    fname = "user_access.json"
    return JSONResponse(
        content=rows,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


def _shrinking_manager_teams(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    """
    ผู้จัดการที่ทีมใต้สังกัดจะ "หดลง" ถ้าเขียนผลลัพธ์ใหม่ทับของเดิม

    เจอของจริง: กดปุ่มนี้ครั้งเดียว ผู้จัดการ 8 คนเหลือทีมจาก 12 → 1 เพราะแถวของเขา
    ใน user_access.json ไม่มี acc_division/acc_region ระบบจึงคำนวณทีมกลับไม่ได้
    ข้อมูลชุดเดิมมาจาก roster Excel ซึ่ง rebuild ในแอปสร้างขึ้นมาใหม่ไม่ได้
    """
    bo = (before or {}).get("by_manager") or {}
    bn = (after or {}).get("by_manager") or {}
    out: list[dict[str, Any]] = []
    for mgr, team in bo.items():
        was, now = len(team or []), len(bn.get(mgr) or [])
        if now < was:
            out.append({"manager_code": mgr, "before": was, "after": now})
    out.sort(key=lambda x: (x["after"] - x["before"], x["manager_code"]))
    return out


class RebuildHierarchyBody(BaseModel):
    """ยืนยันว่ารับทราบว่าจะมีผู้จัดการที่ทีมใต้สังกัดหดลง"""

    confirm_shrink: bool = False


@router.post("/access-hierarchy/rebuild")
def admin_rebuild_access_hierarchy(
    body: RebuildHierarchyBody | None = None,
    admin: dict = Depends(require_admin_user),
) -> dict[str, Any]:
    """
    Rebuild access_hierarchy.json จาก user_access.json
    (เทียบเท่า scripts/access/rebuild_access_hierarchy.py)

    บล็อกไว้ถ้าผลลัพธ์จะทำให้ผู้จัดการคนไหนเห็นทีมน้อยลง — เพราะนั่นคือการตัดสิทธิ์
    คนที่ยังทำงานอยู่ โดยที่หน้าจอเดิมไม่ได้บอกอะไรเลย ต้องไปเติม acc_division/
    acc_region ให้ครบก่อน หรือ import จาก roster Excel ใหม่
    """
    from ..services.access_hierarchy import (
        build_hierarchy_payload,
        enrich_rows_with_visibility,
        load_hierarchy_payload,
        persist_hierarchy,
    )
    from ..services.user_access_store import write_rows

    rows = read_user_access_rows()
    enriched = enrich_rows_with_visibility(rows)
    payload = build_hierarchy_payload(enriched)

    try:
        current = load_hierarchy_payload()
    except Exception:  # อ่านของเดิมไม่ได้ = เทียบไม่ได้ ปล่อยให้เขียนได้ตามปกติ
        current = {}
    shrink = _shrinking_manager_teams(current, payload)
    if shrink and not (body and body.confirm_shrink):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "hierarchy_rebuild_shrinks_teams",
                "message": (
                    f"ยังไม่ได้อัปเดต — จะทำให้ผู้จัดการ {len(shrink)} คนเห็นทีมใต้สังกัดน้อยลง"
                ),
                "hint_th": (
                    "มักเกิดเมื่อแถวของผู้จัดการไม่มี Division/ภาค ระบบจึงคำนวณทีมกลับไม่ได้ "
                    "ให้เติมข้อมูลให้ครบก่อน หรือนำเข้าจากไฟล์ roster ใหม่ "
                    "ถ้ายืนยันว่าตั้งใจ ให้ส่ง confirm_shrink"
                ),
                "shrinking": shrink[:20],
                "shrinking_count": len(shrink),
                "confirm_field": "confirm_shrink",
            },
        )

    write_rows(enriched)
    path = persist_hierarchy(payload)
    invalidate_user_access_cache()
    _audit_admin(
        admin, "admin_hierarchy_rebuild", "อัปเดตลำดับสิทธิ์",
        f"ผู้จัดการ {len(payload.get('manager_codes') or [])} · "
        f"ซุป {len(payload.get('supervisors') or [])}"
        + (f" · ยืนยันทีมหดลง {len(shrink)} คน" if shrink else ""),
        level="warn" if shrink else "info",
    )
    return {
        "ok": True,
        "path": path,
        "manager_count": len(payload.get("manager_codes") or []),
        "supervisor_count": len(payload.get("supervisors") or []),
        "shrinking_count": len(shrink),
    }


@router.get("/health/deep")
def admin_deep_health(
    _admin: dict = Depends(require_admin_user),
    target_month: int = Query(7, ge=1, le=12),
    target_year: int = Query(2026, ge=2020, le=2100),
) -> dict[str, Any]:
    """ทดสอบการเชื่อมต่อ Fabric + Target Sun read (timeout สั้น)"""
    from ..services import targetsun_read

    t0 = time.perf_counter()
    out: dict[str, Any] = {
        "target_month": target_month,
        "target_year": target_year,
        "fabric": {"ok": False, "ms": 0, "detail": ""},
        "targetsun_read": {"ok": False, "ms": 0, "detail": "", "enabled": targetsun_read.is_enabled()},
    }
    try:
        t1 = time.perf_counter()
        fabric = FabricDAXConnector()
        df = fabric.get_tga_period_sku_targets(target_month, target_year)
        out["fabric"]["ms"] = int((time.perf_counter() - t1) * 1000)
        nrow = int(len(df)) if df is not None else 0
        out["fabric"]["ok"] = nrow >= 0
        out["fabric"]["detail"] = f"TGA SKU rows: {nrow}"
    except Exception as e:
        out["fabric"]["ms"] = int((time.perf_counter() - t0) * 1000)
        out["fabric"]["detail"] = str(e)

    if targetsun_read.is_enabled():
        t2 = time.perf_counter()
        try:
            periods = targetsun_read.fetch_targetsun_periods_overview()
            out["targetsun_read"]["ms"] = int((time.perf_counter() - t2) * 1000)
            out["targetsun_read"]["ok"] = isinstance(periods, list)
            out["targetsun_read"]["detail"] = f"periods: {len(periods)}"
        except Exception as e:
            out["targetsun_read"]["ms"] = int((time.perf_counter() - t2) * 1000)
            out["targetsun_read"]["detail"] = str(e)
    else:
        out["targetsun_read"]["detail"] = "TARGETSUN_READ_ENABLED=0"

    out["total_ms"] = int((time.perf_counter() - t0) * 1000)
    return out


@router.get("/usage-logs")
def admin_get_usage_logs(
    admin: dict = Depends(require_admin_scoped),
    date: str | None = Query(None, description="YYYY-MM-DD (ถ้าไม่ส่งงวด)"),
    target_month: int | None = Query(None, ge=1, le=12),
    target_year: int | None = Query(None, ge=2020, le=2100),
    level: str | None = Query(None),
    limit: int = Query(500, ge=1, le=2000),
):
    scan_all = target_month is None and target_year is None and date is None
    items = read_logs(
        date=date,
        level=level,
        limit=limit,
        target_year=target_year,
        target_month=target_month,
        scan_all=scan_all,
    )
    items = _filter_usage_items_for_admin(admin, items)
    return {"items": items, "scan_all": scan_all}


def _filter_usage_items_for_admin(admin: dict, items: list[dict]) -> list[dict]:
    """ผู้ดูแลเห็นเฉพาะเหตุการณ์ของทีมในขอบเขตตัวเอง หรือของผู้ใช้ในขอบเขตนั้น"""
    if admin.get("role") not in ADMIN_ROLES:
        return items
    scope = admin.get("admin_scope") or {}
    codes = {str(c).strip().upper() for c in (scope.get("sl_codes") or set())}
    emails = {
        normalized_email(r.get("email"))
        for r in read_rows()
        if row_is_in_admin_scope(r, scope)
    }
    # ตัวเองต้องเห็นสิ่งที่ตัวเองทำเสมอ ไม่ว่าขอบเขตจะเป็นอะไร — การกระทำระดับระบบ
    # (แก้สิทธิ์ ผูกรหัส) ไม่ผูกกับทีมใดทีมหนึ่ง ถ้าคัดออกด้วยขอบเขต ผู้ดูแลจะกดบันทึก
    # แล้วมองไม่เห็นร่องรอยของตัวเอง แล้วเข้าใจว่าระบบไม่ได้เก็บ
    emails.add(normalized_email(admin.get("email")))
    emails.discard("")
    return [
        it
        for it in items
        if str(it.get("sup_id") or "").strip().upper() in codes
        or normalized_email(it.get("email")) in emails
    ]


@router.get("/usage-logs/export-xlsx")
def admin_export_usage_logs_xlsx(
    admin: dict = Depends(require_admin_scoped),
    date: str | None = Query(None, description="YYYY-MM-DD (ถ้าไม่ส่งงวด)"),
    target_month: int | None = Query(None, ge=1, le=12),
    target_year: int | None = Query(None, ge=2020, le=2100),
    level: str | None = Query(None),
    limit: int = Query(5000, ge=1, le=20000),
):
    """บันทึกการใช้งานเป็น Excel สำหรับรายงานผู้บริหาร — ขอบเขต/ตัวกรองชุดเดียวกับหน้าจอ"""
    import json as _json

    scan_all = target_month is None and target_year is None and date is None
    items = read_logs(
        date=date,
        level=level,
        limit=limit,
        target_year=target_year,
        target_month=target_month,
        scan_all=scan_all,
    )
    items = _filter_usage_items_for_admin(admin, items)
    for it in items:
        it["ts_th"] = _fmt_ts_th(it.get("ts"))
        d = it.get("detail")
        if isinstance(d, (dict, list)):
            it["detail_str"] = _json.dumps(d, ensure_ascii=False)
        else:
            it["detail_str"] = str(d or "")
        # งวดเป้าที่เหตุการณ์พูดถึง — คนละเรื่องกับ "เวลาที่เกิดเหตุ" ในคอลัมน์แรก
        it["period"] = (
            f"{int(it.get('target_month')):02d}/{it.get('target_year')}"
            if it.get("target_month") and it.get("target_year")
            else ""
        )
        ctx = it.get("context")
        it["context_str"] = (
            _json.dumps(ctx, ensure_ascii=False) if isinstance(ctx, (dict, list)) else ""
        )
    cols = [
        ("ts_th", "เวลา (ไทย)"),
        ("level", "ระดับ"),
        ("email", "ผู้ใช้"),
        ("role", "บทบาท"),
        ("sup_id", "ทีม (SL)"),
        ("period", "งวดเป้า"),
        ("action", "การกระทำ"),
        ("message", "ข้อความ"),
        ("detail_str", "รายละเอียด"),
        ("context_str", "ค่าที่บันทึกไว้"),
        ("request_id", "request_id"),
        ("entry_id", "entry_id"),
    ]
    m_part = f"{target_month:02d}" if target_month else "all"
    y_part = str(target_year) if target_year else "all"
    base = f"usage_logs_{date}" if date else f"usage_logs_{y_part}_{m_part}"
    return _xlsx_response(items, cols, base)


# หมายเหตุ: เดิมมี DELETE /usage-logs/{entry_id} และ POST /usage-logs/acknowledge สำหรับ
# ให้แอดมินกด "รับทราบ" แล้วลบรายการทิ้ง — ถอดออกแล้ว เพราะ log ต้องเป็นบันทึกการใช้งาน
# แบบ append-only ไว้ตาม monitor (ใครส่ง Target Sun เมื่อไหร่) การลบทำให้ตามย้อนหลังไม่ได้


@router.post("/usage-logs")
def admin_post_usage_log(
    body: UsageLogBody,
    user: dict = Depends(require_authenticated_user),
):
    email = str(user.get("email") or user.get("view_as_email") or "").strip()
    row = append_log(
        level=body.level,
        email=email,
        role="client",
        sup_id=body.sup_id,
        action=body.action,
        message=body.message,
        detail=body.detail,
    )
    return row


class EmpAssignmentBody(BaseModel):
    emp_id: str
    """รหัสทีมปลายทางที่จะไปเกลี่ยเป้าด้วย — ว่าง = ปลดการย้าย กลับไปทีมจริง"""
    to_sup: str = ""
    from_sup: str = ""
    emp_name: str = ""
    note: str = ""


def _sup_attrs() -> dict[str, dict[str, str]]:
    """ดิวิชัน / ภาค / หน่วยขาย ของแต่ละรหัสทีม จาก user_access (อ่านไฟล์ล้วน)"""
    from ..services.user_access_store import read_rows as read_access

    out: dict[str, dict[str, str]] = {}
    for r in read_access():
        code = str(r.get("userpl") or "").strip().upper()
        if not code or code in out:
            continue
        out[code] = {
            "division": str(r.get("acc_division") or "").strip(),
            "region": str(r.get("acc_region") or "").strip(),
            "unit": str(r.get("acc_unit") or "").strip(),
            "login_kind": str(r.get("login_kind") or "").strip(),
        }
    return out


def _employee_directory() -> list[dict]:
    """
    พนักงานทุกคนที่ระบบเคยเห็น พร้อมทีมที่สังกัดจริง — อ่านจากไฟล์แคชรายชื่อในเครื่อง

    ไม่ยิง Fabric เพราะหน้านี้ต้องเปิดได้แม้ตอน Fabric ล่ม (ซึ่งเป็นตอนที่คนอยาก
    เข้ามาดูว่าใครอยู่ทีมไหนพอดี) · แคชรายชื่อเก็บ "โครงสร้างจริง" ไว้เสมอ
    การย้ายไม่เคยถูกเขียนทับลงไป จึงยังบอกได้ว่าต้นทางคือใคร
    """
    import re

    # อ่านสองแหล่ง: แคชรายชื่อ (มีชื่อคน) และแคชแถวเป้า (ครอบคลุมกว่ามาก)
    #
    # ทีมที่เคยดึงเป้ามาแล้วแต่ยังไม่เคยเปิดหน้า Dashboard จะมีแต่ tga_lines
    # ไม่มี emp_cache — ถ้าอ่านแหล่งเดียว พนักงานของทีมเหล่านั้นหายไปจากหน้านี้
    # ทั้งที่เป็นกลุ่มที่ต้องมาย้ายบ่อยที่สุด (ของจริง: emp_cache 17 ไฟล์ / grain 90)
    pat = re.compile(r"^(?:emp_cache|tga_lines)_(.+)_(\d{4})_(\d{2})\.csv$")
    newest: dict[str, tuple[str, str]] = {}          # emp_id -> (stamp, sup)
    names: dict[str, str] = {}
    try:
        files = sorted(os.listdir("data"))
    except OSError:
        files = []
    for name in files:
        m = pat.match(name)
        if not m:
            continue
        sup, year, month = m.group(1).strip().upper(), m.group(2), m.group(3)
        stamp = f"{year}-{month}"
        try:
            df = pd.read_csv(os.path.join("data", name), dtype=str, keep_default_na=False)
        except Exception:
            continue
        if df.empty or "emp_id" not in df.columns:
            continue
        seen_here: set[str] = set()
        for _, row in df.iterrows():
            emp = str(row.get("emp_id") or "").strip().upper()
            if not emp or emp in seen_here:
                continue
            seen_here.add(emp)              # แคชแถวเป้ามีหลายแถวต่อคน
            nm = str(row.get("emp_name") or "").strip()
            if nm:
                names.setdefault(emp, nm)
            cur = newest.get(emp)
            if cur is None or stamp > cur[0]:
                newest[emp] = (stamp, sup)

    attrs = _sup_attrs()
    moves = {r["emp_id"]: r for r in emp_assignment_store.read_rows()}
    out: list[dict] = []
    for emp, (stamp, sup) in sorted(newest.items()):
        a = attrs.get(sup, {})
        mv = moves.get(emp)
        to_sup = mv["to_sup"] if mv else ""
        b = attrs.get(to_sup, {}) if to_sup else {}
        out.append({
            "emp_id": emp,
            "emp_name": names.get(emp, ""),
            "home_sup": sup,
            "home_division": a.get("division", ""),
            "home_region": a.get("region", ""),
            "home_unit": a.get("unit", ""),
            "seen_period": stamp,
            "to_sup": to_sup,
            "to_division": b.get("division", ""),
            "to_region": b.get("region", ""),
            "to_unit": b.get("unit", ""),
            "note": mv["note"] if mv else "",
        })
    return out


@router.get("/emp-assignments")
def admin_emp_assignments(_admin: dict = Depends(require_admin_user)):
    """รายชื่อพนักงาน + ทีมที่สังกัดจริง + ทีมที่ย้ายไปเกลี่ยเป้าด้วย (ถ้ามี)"""
    sups = _sup_attrs()
    return {
        "employees": _employee_directory(),
        "assignments": emp_assignment_store.read_rows(),
        "supervisors": [
            {"code": c, **v}
            for c, v in sorted(sups.items())
            if v.get("login_kind") in ("supervisor_acc", "manager_acc")
        ],
    }


@router.post("/emp-assignments")
def admin_set_emp_assignment(
    body: EmpAssignmentBody,
    admin: dict = Depends(require_admin_user),
):
    """
    ย้ายพนักงานไปให้ทีมอื่นเกลี่ยเป้า (หรือปลดการย้ายเมื่อ to_sup ว่าง)

    ล้างแคช payload ของทั้งทีมต้นทางและปลายทางทุกงวดที่มีอยู่ — ถ้าไม่ล้าง
    ทีมที่ยังหยิบของเก่าจะเห็นพนักงานคนนี้พร้อมกับอีกทีม แล้วเป้าถูกนับสองรอบ
    """
    emp = emp_assignment_store.norm_emp(body.emp_id)
    if not emp:
        raise HTTPException(400, detail="ต้องระบุรหัสพนักงาน")
    to_sup = emp_assignment_store.norm_sup(body.to_sup)
    prev = emp_assignment_store.assignment_for_emp(emp)
    from_sup = emp_assignment_store.norm_sup(body.from_sup) or (
        prev.get("from_sup") if prev else ""
    )
    if to_sup and to_sup == from_sup:
        raise HTTPException(400, detail="ทีมปลายทางเป็นทีมเดิมอยู่แล้ว")

    try:
        rows = emp_assignment_store.set_assignment(
            emp,
            to_sup,
            from_sup=from_sup,
            emp_name=body.emp_name,
            note=body.note,
            updated_by=str(admin.get("email") or ""),
        )
    except ValueError as e:
        raise HTTPException(400, detail=str(e)) from e

    touched = {c for c in (from_sup, to_sup, (prev or {}).get("to_sup")) if c}
    cleared = 0
    for sid in touched:
        try:
            cleared += invalidate_employee_payload_cache(sid, None, None)
        except Exception as e:
            logger.warning("ล้างแคช payload ของ %s หลังย้ายพนักงานไม่ได้: %s", sid, e)

    logger.info(
        "ย้ายพนักงาน %s → %s (เดิม %s) โดย %s · ล้างแคช %d ไฟล์",
        emp, to_sup or "(ปลดการย้าย)", from_sup or "-",
        admin.get("email"), cleared,
    )
    return {
        "status": "ok",
        "emp_id": emp,
        "to_sup": to_sup,
        "assignments": rows,
        "payload_cache_cleared": cleared,
    }


class CacheRefreshBody(BaseModel):
    layer: str = "all"
    month: int = Field(..., ge=1, le=12)
    year: int = Field(..., ge=2020, le=2100)
    sup_id: str | None = None


@router.get("/cache/status")
def admin_cache_status(
    _admin: dict = Depends(require_admin_user),
    target_month: int = Query(..., ge=1, le=12),
    target_year: int = Query(..., ge=2020, le=2100),
):
    from ..services import fabric_cache as fc
    from ..services.employee_payload_cache import employee_payload_cache_ttl_sec

    return {
        "layers": fc.cache_status(target_year, target_month),
        "payload_ttl_sec": employee_payload_cache_ttl_sec(),
        "fabric_ttl_sec": fc.fabric_static_cache_ttl_sec(),
    }


@router.post("/cache/invalidate")
def admin_cache_invalidate(
    body: CacheRefreshBody,
    _admin: dict = Depends(require_admin_user),
):
    from ..services import fabric_cache as fc

    removed_fabric = 0
    removed_payload = 0
    layer = str(body.layer or "all").strip().lower()
    if layer in ("product", "price", "tga_skus", "all", "fabric"):
        layers = {"product", "price", "tga_skus"} if layer in ("all", "fabric") else {layer}
        removed_fabric = fc.invalidate_period_cache(body.year, body.month, layers=layers)
    if layer in ("payload", "all"):
        removed_payload = invalidate_employee_payload_cache(
            body.sup_id,
            body.month,
            body.year,
        )
    return {
        "status": "ok",
        "removed_fabric_files": removed_fabric,
        "removed_payload_files": removed_payload,
    }


@router.post("/cache/refresh")
def admin_cache_refresh(
    body: CacheRefreshBody,
    _admin: dict = Depends(require_admin_user),
):
    """รีเฟรชแคช — layer=product|payload|all"""
    from ..services import fabric_cache as fc
    from ..services.employees import load_employees_payload

    layer = str(body.layer or "all").strip().lower()
    result: dict[str, Any] = {"status": "ok", "layer": layer}

    if layer in ("product", "all", "fabric"):
        # ห้ามลบก่อนดึงใหม่ — ถ้า Fabric ล่ม (เช่น capacity เต็ม) จะได้ "ไม่มีอะไรเลย"
        # แทนที่จะได้ของเดิม แล้วราคากลายเป็น 0 ทั้งระบบจนทุกทีมเปิดงวดไม่ได้
        # กด "รีเฟรช" แล้วแย่กว่าเดิมเป็นพฤติกรรมที่ไม่ควรมี — ดึงให้ได้ก่อนค่อยทับ
        sup = (body.sup_id or "").strip().upper()
        if sup:
            try:
                load_employees_payload(
                    sup_id=sup,
                    target_month=body.month,
                    target_year=body.year,
                    refresh=True,
                )
                result["warmed_sup"] = sup
            except Exception as e:
                logger.warning("warm product/price cache ไม่สำเร็จ: %s", e)
                result["warm_error"] = str(e)
                result["hint"] = (
                    "ดึงข้อมูลใหม่ไม่สำเร็จ — เก็บแคชเดิมไว้ให้ใช้งานต่อได้ "
                    "ลองใหม่อีกครั้งเมื่อ Fabric กลับมาปกติ"
                )
        else:
            result["hint"] = "ระบุ sup_id เพื่อ warm product/price cache จาก DAX"

    if layer in ("payload", "all"):
        sid = (body.sup_id or "").strip().upper()
        if sid:
            # เหตุผลเดียวกับสาขา product ข้างบน — ลบก่อนแล้วดึงใหม่ไม่ได้
            # แอดมินจะเหลือ "ไม่มีอะไรเลย" แล้วทีมนั้นเปิดงวดไม่ได้จนกว่า Fabric จะกลับมา
            try:
                load_employees_payload(
                    sup_id=sid,
                    target_month=body.month,
                    target_year=body.year,
                    refresh=True,
                )
                result["refreshed_payload_sup"] = sid
            except Exception as e:
                logger.warning("refresh payload ไม่สำเร็จ: %s", e)
                result["payload_error"] = str(e)
                result["hint"] = (
                    "ดึง payload ใหม่ไม่สำเร็จ — เก็บแคชเดิมไว้ให้ใช้งานต่อได้ "
                    "ลองใหม่อีกครั้งเมื่อ Fabric กลับมาปกติ"
                )
        else:
            # ไม่ระบุทีม = ล้างทั้งงวด ไม่มีอะไรให้ดึงคืนอยู่แล้ว
            invalidate_employee_payload_cache(None, body.month, body.year)

    result["cache_status"] = fc.cache_status(body.year, body.month)
    return result


class TargetReadSourceBody(BaseModel):
    source: Literal["targetsun", "fabric"]


@router.get("/settings/target-source")
def admin_get_target_read_source(
    _admin: dict = Depends(require_admin_user),
):
    from ..services import targetsun_read

    periods: list[dict] = []
    if targetsun_read.is_enabled():
        try:
            periods = targetsun_read.fetch_targetsun_periods_overview()
        except Exception:
            periods = []

    return {
        "source": targetsun_read.get_target_read_source(),
        "targetsun_read_enabled": targetsun_read.is_enabled(),
        "target_periods": periods,
        **admin_target_endpoints_payload(),
    }


def admin_target_endpoints_payload() -> dict:
    from ..services.targetsun_endpoints import (
        list_endpoint_presets,
        targetsun_endpoints_summary,
    )
    from ..services.app_runtime_settings import get_target_endpoint_config

    cfg = get_target_endpoint_config()
    summary = targetsun_endpoints_summary()
    return {
        "endpoint_preset": cfg.get("preset_stored") or "test",
        "endpoint_presets": list_endpoint_presets(),
        "effective_read_base": summary["read_base"],
        "effective_import_base": summary["import_base"],
        "effective_import_url": summary["import_url"],
        "read_host_label": summary["read_host_label"],
        "import_host_label": summary["import_host_label"],
        "cross_env": summary["cross_env"] == "1",
    }


class TargetEndpointPresetBody(BaseModel):
    preset: Literal["test", "uat", "prod", "code"]


@router.get("/settings/target-endpoints")
def admin_get_target_endpoints(
    _admin: dict = Depends(require_admin_user),
):
    return admin_target_endpoints_payload()


@router.put("/settings/target-endpoints")
def admin_set_target_endpoints(
    body: TargetEndpointPresetBody,
    _admin: dict = Depends(require_admin_user),
):
    from ..services.app_runtime_settings import set_target_endpoint_preset

    try:
        data = set_target_endpoint_preset(body.preset)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    payload_cache_cleared = 0
    if body.preset in ("test", "uat", "prod"):
        from ..services.employee_payload_cache import invalidate_employee_payload_cache

        payload_cache_cleared = invalidate_employee_payload_cache()
    return {
        "ok": True,
        "endpoint_preset": data.get("target_endpoint_preset"),
        "payload_cache_cleared": payload_cache_cleared,
        **admin_target_endpoints_payload(),
    }


@router.put("/settings/target-source")
def admin_set_target_read_source(
    body: TargetReadSourceBody,
    _admin: dict = Depends(require_admin_user),
):
    from ..services import targetsun_read
    from ..services.app_runtime_settings import set_target_read_source

    try:
        data = set_target_read_source(body.source)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    from ..services.employee_payload_cache import invalidate_employee_payload_cache

    removed = invalidate_employee_payload_cache()
    return {
        "ok": True,
        "source": data.get("target_read_source"),
        "targetsun_read_enabled": targetsun_read.is_enabled(),
        "payload_cache_cleared": removed,
    }
