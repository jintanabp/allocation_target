"""Admin API — จัดการ user_access.json (เฉพาะ ALLOCATION_ADMIN_EMAILS)"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..deps import require_admin_or_marketing_team, require_admin_user
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
    normalize_sl,
    read_links as read_sl_links,
    resolve_to_canonical,
    upsert_link as upsert_sl_link,
    write_links as write_sl_links,
)
from ..services.employee_payload_cache import read_cached_employee_payload
from ..fabric_dax_connector import FabricDAXConnector

logger = logging.getLogger("target_allocation")

router = APIRouter(prefix="/admin", tags=["admin"])


class UserAccessBody(BaseModel):
    email: str
    userpl: str
    can_import_targetsun: bool = False
    note: str = ""
    login_kind: str | None = None
    acc_region: str | None = None
    acc_division: str | None = None
    acc_unit: str | None = None
    acc_position: str | None = None


class UserAccessUpdateBody(BaseModel):
    email: str
    userpl: str
    can_import_targetsun: bool | None = None
    note: str | None = None
    new_email: str | None = Field(default=None, description="เปลี่ยนอีเมล")
    new_userpl: str | None = Field(default=None, description="เปลี่ยนรหัส USERPL")
    login_kind: str | None = None
    acc_region: str | None = None
    acc_division: str | None = None
    acc_unit: str | None = None
    acc_position: str | None = None


_META_PATCH_KEYS = (
    "login_kind",
    "acc_region",
    "acc_division",
    "acc_unit",
    "acc_position",
)


def _patch_row_meta(row: dict[str, Any], body: UserAccessUpdateBody) -> None:
    for key in _META_PATCH_KEYS:
        if getattr(body, key, None) is None:
            continue
        val = str(getattr(body, key) or "").strip()
        if key == "login_kind" and val == "standard":
            row.pop(key, None)
            continue
        if val:
            row[key] = val
        else:
            row.pop(key, None)


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
    acc_division: str = Query(""),
    acc_scope: str = Query(""),
    _admin: dict = Depends(require_admin_user),
) -> dict[str, Any]:
    """Preview รหัส SL ที่ดูได้ — ใช้ในฟอร์มแอดมิน"""
    row = {
        "userpl": userpl.strip().upper(),
        "login_kind": (login_kind or "standard").strip(),
        "acc_region": (acc_region or "").strip(),
        "acc_division": (acc_division or "").strip(),
        "acc_scope": (acc_scope or "").strip(),
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
    new_row: dict[str, Any] = {
        "email": em,
        "userpl": upl,
        "can_import_targetsun": bool(body.can_import_targetsun),
        "note": str(body.note or "").strip(),
    }
    _patch_row_meta(new_row, body)
    write_rows(rows + [new_row])
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
    _patch_row_meta(updated_row, body)

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


@router.get("/supervisor-codes")
def admin_supervisor_codes(_user: dict = Depends(require_admin_or_marketing_team)) -> dict[str, Any]:
    codes = list_supervisor_codes()
    return {"supervisors": codes, "count": len(codes)}


@router.get("/supervisor-team")
def admin_supervisor_team(
    super_code: str = Query(..., min_length=1),
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    force_refresh: int = Query(0, ge=0, le=1),
    _user: dict = Depends(require_admin_or_marketing_team),
) -> dict[str, Any]:
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


@router.post("/sku-links")
def create_sku_link(
    body: SkuLinkBody,
    admin: dict = Depends(require_admin_user),
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
    return {"ok": True, "row": _sku_link_row_for_api(row or {})}


@router.put("/sku-links")
def update_sku_link(
    body: SkuLinkUpdateBody,
    admin: dict = Depends(require_admin_user),
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
    return {"ok": True, "row": _sku_link_row_for_api(row or {})}


@router.delete("/sku-links")
def remove_sku_link(
    body: SkuLinkDeleteBody,
    _admin: dict = Depends(require_admin_user),
) -> dict[str, Any]:
    canon = normalize_sku(body.canonical_sku)
    try:
        delete_link(read_links(), canon)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
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
    canonical_sl: str
    alias_sls: list[str] = Field(default_factory=list)
    note: str = ""


class SlLinkUpdateBody(SlLinkBody):
    new_canonical_sl: str | None = Field(default=None, description="เปลี่ยนรหัส canonical")


class SlLinkDeleteBody(BaseModel):
    canonical_sl: str


def _sl_link_row_for_api(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "canonical_sl": row.get("canonical_sl"),
        "alias_sls": list(row.get("alias_sls") or []),
        "note": row.get("note") or "",
        "updated_by": row.get("updated_by") or "",
    }


@router.get("/sl-links")
def list_sl_links(_user: dict = Depends(require_admin_or_marketing_team)) -> dict[str, Any]:
    rows = [_sl_link_row_for_api(r) for r in read_sl_links()]
    return {"links": rows, "count": len(rows)}


@router.post("/sl-links")
def create_sl_link(
    body: SlLinkBody,
    admin: dict = Depends(require_admin_user),
) -> dict[str, Any]:
    canon = normalize_sl(body.canonical_sl)
    if not canon:
        raise HTTPException(status_code=400, detail="canonical_sl ว่าง")
    links = read_sl_links()
    if find_sl_link(canon, links):
        raise HTTPException(status_code=409, detail="มีกลุ่มผูกรหัสนี้อยู่แล้ว")
    email = str(admin.get("email") or admin.get("preferred_username") or "").strip()
    saved = upsert_sl_link(
        links,
        canonical_sl=canon,
        alias_sls=body.alias_sls or [canon],
        note=body.note,
        updated_by=email,
    )
    row = find_sl_link(canon, saved)
    return {"ok": True, "row": _sl_link_row_for_api(row or {})}


@router.put("/sl-links")
def update_sl_link(
    body: SlLinkUpdateBody,
    admin: dict = Depends(require_admin_user),
) -> dict[str, Any]:
    canon = normalize_sl(body.canonical_sl)
    if not canon:
        raise HTTPException(status_code=400, detail="canonical_sl ว่าง")
    links = read_sl_links()
    if not find_sl_link(canon, links):
        raise HTTPException(status_code=404, detail="ไม่พบกลุ่มผูกรหัส")
    new_canon = normalize_sl(body.new_canonical_sl) if body.new_canonical_sl else canon
    if new_canon != canon and find_sl_link(new_canon, links):
        raise HTTPException(status_code=409, detail="canonical_sl ใหม่ซ้ำกับกลุ่มอื่น")
    email = str(admin.get("email") or admin.get("preferred_username") or "").strip()
    out: list[dict[str, Any]] = []
    for row in links:
        if row["canonical_sl"] == canon:
            nr = dict(row)
            nr["canonical_sl"] = new_canon
            nr["alias_sls"] = body.alias_sls or nr.get("alias_sls") or [new_canon]
            nr["note"] = str(body.note if body.note is not None else nr.get("note") or "").strip()
            if email:
                nr["updated_by"] = email
            out.append(nr)
        else:
            out.append(dict(row))
    saved = write_sl_links(out)
    row = find_sl_link(new_canon, saved)
    return {"ok": True, "row": _sl_link_row_for_api(row or {})}


@router.delete("/sl-links")
def remove_sl_link(
    body: SlLinkDeleteBody,
    _admin: dict = Depends(require_admin_user),
) -> dict[str, Any]:
    canon = normalize_sl(body.canonical_sl)
    try:
        delete_sl_link(read_sl_links(), canon)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
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
    super_code: str = Query(..., min_length=1),
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    _user: dict = Depends(require_admin_or_marketing_team),
) -> dict[str, Any]:
    """รายการสินค้าในงวดจาก cache Dashboard (โหลดทันทีเมื่อเปิดแท็บ)"""
    sup = super_code.strip().upper()
    sku_links = read_links()
    payload = read_cached_employee_payload(sup, month, year)
    source_sup = sup
    if payload is None:
        canon_sl = resolve_to_canonical(sup)
        if canon_sl != sup:
            payload = read_cached_employee_payload(canon_sl, month, year)
            if payload is not None:
                source_sup = canon_sl
    from_cache = payload is not None
    skus = _sku_rows_from_payload(payload or {}, sku_links)
    hint = ""
    if not skus:
        hint = (
            "ยังไม่มี cache งวดนี้ — เปิด Dashboard ของ Supervisor นี้แล้วกดโหลดข้อมูล "
            "(หรือ refresh) ก่อน แล้วกลับมาดูรายการสินค้าอีกครั้ง"
        )
    return {
        "supervisor_code": sup,
        "source_supervisor_code": source_sup,
        "target_month": month,
        "target_year": year,
        "from_cache": from_cache,
        "count": len(skus),
        "skus": skus,
        "hint": hint,
    }
