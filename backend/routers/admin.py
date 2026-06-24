"""Admin API — จัดการ user_access.json (เฉพาะ ALLOCATION_ADMIN_EMAILS)"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..deps import require_admin_user
from ..services.access_control import (
    enrich_user_access_rows,
    invalidate_user_access_cache,
    visible_supervisors_for_row_dict,
)
from ..services.user_access_store import (
    delete_row,
    normalized_email,
    normalize_userpl,
    read_rows,
    set_email_targetsun_flag,
    upsert_row,
    write_rows,
)

logger = logging.getLogger("target_allocation")

router = APIRouter(prefix="/admin", tags=["admin"])


class UserAccessBody(BaseModel):
    email: str
    userpl: str
    can_import_targetsun: bool = False
    note: str = ""


class UserAccessUpdateBody(BaseModel):
    email: str
    userpl: str
    can_import_targetsun: bool | None = None
    note: str | None = None
    new_email: str | None = Field(default=None, description="เปลี่ยนอีเมล")
    new_userpl: str | None = Field(default=None, description="เปลี่ยนรหัส USERPL")


class UserAccessDeleteBody(BaseModel):
    email: str
    userpl: str


class TargetSunEmailBody(BaseModel):
    email: str
    enabled: bool = True


@router.get("/user-access")
def list_user_access(_admin: dict = Depends(require_admin_user)) -> dict[str, Any]:
    rows = enrich_user_access_rows()
    return {"rows": rows, "count": len(rows)}


@router.get("/user-access/preview-visible")
def preview_user_visible(
    userpl: str = Query(..., min_length=1),
    login_kind: str = Query("standard"),
    acc_region: str = Query(""),
    _admin: dict = Depends(require_admin_user),
) -> dict[str, Any]:
    """Preview รหัส SL ที่ดูได้ — ใช้ในฟอร์มแอดมิน"""
    row = {
        "userpl": userpl.strip().upper(),
        "login_kind": (login_kind or "standard").strip(),
        "acc_region": (acc_region or "").strip(),
    }
    visible = visible_supervisors_for_row_dict(row)
    return {"visible_supervisors": visible}


@router.post("/user-access")
def create_user_access(
    body: UserAccessBody,
    _admin: dict = Depends(require_admin_user),
) -> dict[str, Any]:
    em = normalized_email(body.email)
    upl = normalize_userpl(body.userpl)
    if "@" not in em or not upl:
        raise HTTPException(status_code=400, detail="อีเมลหรือ USERPL ไม่ถูกต้อง")
    rows = read_rows()
    if any(r["email"] == em and r["userpl"] == upl for r in rows):
        raise HTTPException(status_code=409, detail="มีแถวนี้อยู่แล้ว")
    upsert_row(
        rows,
        email=em,
        userpl=upl,
        can_import_targetsun=body.can_import_targetsun,
        note=body.note,
    )
    invalidate_user_access_cache()
    enriched = enrich_user_access_rows()
    row = next((r for r in enriched if r["email"] == em and r["userpl"] == upl), None)
    return {"ok": True, "row": row}


@router.put("/user-access")
def update_user_access(
    body: UserAccessUpdateBody,
    _admin: dict = Depends(require_admin_user),
) -> dict[str, Any]:
    em = normalized_email(body.email)
    upl = normalize_userpl(body.userpl)
    rows = read_rows()
    existing = next((r for r in rows if r["email"] == em and r["userpl"] == upl), None)
    if not existing:
        raise HTTPException(status_code=404, detail="ไม่พบแถว")

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

    out = [
        updated_row if r["email"] == em and r["userpl"] == upl else r
        for r in rows
    ]
    write_rows(out)
    invalidate_user_access_cache()
    enriched = enrich_user_access_rows()
    row = next((r for r in enriched if r["email"] == new_em and r["userpl"] == new_upl), None)
    return {"ok": True, "row": row}


@router.delete("/user-access")
def remove_user_access(
    body: UserAccessDeleteBody,
    _admin: dict = Depends(require_admin_user),
) -> dict[str, Any]:
    em = normalized_email(body.email)
    upl = normalize_userpl(body.userpl)
    try:
        delete_row(read_rows(), em, upl)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    invalidate_user_access_cache()
    return {"ok": True}


@router.put("/user-access/targetsun")
def set_targetsun_for_email(
    body: TargetSunEmailBody,
    _admin: dict = Depends(require_admin_user),
) -> dict[str, Any]:
    em = normalized_email(body.email)
    try:
        set_email_targetsun_flag(em, body.enabled)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    invalidate_user_access_cache()
    return {"ok": True, "email": em, "can_import_targetsun": body.enabled}
