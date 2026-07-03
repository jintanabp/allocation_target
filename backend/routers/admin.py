"""Admin API — จัดการ user_access.json (เฉพาะ ALLOCATION_ADMIN_EMAILS)"""

from __future__ import annotations

import logging
import time
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..deps import require_admin_or_marketing_team, require_admin_user, require_authenticated_user
from ..services.access_control import (
    enrich_user_access_rows,
    invalidate_user_access_cache,
    visible_supervisors_for_row_dict,
)
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
from ..services.usage_log_store import append_log, acknowledge_logs, read_logs
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


def _patch_row_meta(row: dict[str, Any], body: UserAccessUpdateBody) -> None:
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
    acc_unit: str = Query(""),
    manager_level: str = Query(""),
    _admin: dict = Depends(require_admin_user),
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


@router.post("/sl-links")
def create_sl_link(
    body: SlLinkBody,
    admin: dict = Depends(require_admin_user),
) -> dict[str, Any]:
    old, new_sls = _sl_body_old_new(body)
    if not old:
        raise HTTPException(status_code=400, detail="รหัสเก่า (old_sl) ว่าง")
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
    return {"ok": True, "row": _sl_link_row_for_api(row or {})}


@router.put("/sl-links")
def update_sl_link(
    body: SlLinkUpdateBody,
    admin: dict = Depends(require_admin_user),
) -> dict[str, Any]:
    old, new_sls = _sl_body_old_new(body)
    if not old:
        raise HTTPException(status_code=400, detail="รหัสเก่า (old_sl) ว่าง")
    links = read_sl_links()
    if not find_sl_link(old, links):
        raise HTTPException(status_code=404, detail="ไม่พบกลุ่มผูกรหัส")
    new_old = normalize_sl(body.new_old_sl) if body.new_old_sl else old
    if new_old != old and find_sl_link(new_old, links):
        raise HTTPException(status_code=409, detail="รหัสเก่าใหม่ซ้ำกับกลุ่มอื่น")
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
    return {"ok": True, "row": _sl_link_row_for_api(row or {})}


@router.delete("/sl-links")
def remove_sl_link(
    body: SlLinkDeleteBody,
    _admin: dict = Depends(require_admin_user),
) -> dict[str, Any]:
    old = normalize_sl(body.old_sl or body.canonical_sl)
    try:
        delete_sl_link(read_sl_links(), old)
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
            df_info = fabric.get_product_info(sku_list=sku_list)
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


@router.get("/allocations")
def admin_list_allocations(
    _admin: dict = Depends(require_admin_user),
    target_month: int | None = Query(None, ge=1, le=12),
    target_year: int | None = Query(None, ge=2020, le=2100),
):
    """รายการ snapshot ผลกระจายทั้งหมด"""
    items = list_all_snapshots(
        month=target_month,
        year=target_year,
    )
    return {"items": items, "count": len(items)}


@router.delete("/allocations")
def admin_delete_allocation(
    _admin: dict = Depends(require_admin_user),
    sup_id: str = Query(..., min_length=1),
    target_month: int = Query(..., ge=1, le=12),
    target_year: int = Query(..., ge=2020, le=2100),
):
    sid = sup_id.strip().upper()
    if not delete_snapshot(sid, target_month, target_year):
        raise HTTPException(status_code=404, detail="ไม่พบผลกระจายที่จะลบ")
    return {"status": "ok", "sup_id": sid}


@router.get("/allocations/export")
def admin_export_allocation(
    _admin: dict = Depends(require_admin_user),
    sup_id: str = Query(..., min_length=1),
    target_month: int = Query(..., ge=1, le=12),
    target_year: int = Query(..., ge=2020, le=2100),
):
    """ดาวน์โหลด snapshot JSON สำหรับสำรอง"""
    sid = sup_id.strip().upper()
    snap = read_snapshot(sid, target_month, target_year)
    if not snap:
        raise HTTPException(status_code=404, detail="ไม่พบผลกระจาย")
    fname = f"allocation_{sid}_{target_year}_{target_month:02d}.json"
    return JSONResponse(
        content=snap,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.get("/user-access/export")
def admin_export_user_access(_admin: dict = Depends(require_admin_user)):
    """ดาวน์โหลด user_access.json สำหรับสำรอง"""
    rows = read_user_access_rows()
    fname = "user_access.json"
    return JSONResponse(
        content=rows,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/access-hierarchy/rebuild")
def admin_rebuild_access_hierarchy(_admin: dict = Depends(require_admin_user)) -> dict[str, Any]:
    """Rebuild access_hierarchy.json จาก user_access.json (เทียบเท่า scripts/access/rebuild_access_hierarchy.py)"""
    from ..services.access_hierarchy import (
        build_hierarchy_payload,
        enrich_rows_with_visibility,
        persist_hierarchy,
    )
    from ..services.user_access_store import write_rows

    rows = read_user_access_rows()
    enriched = enrich_rows_with_visibility(rows)
    write_rows(enriched)
    payload = build_hierarchy_payload(enriched)
    path = persist_hierarchy(payload)
    invalidate_user_access_cache()
    return {
        "ok": True,
        "path": path,
        "manager_count": len(payload.get("manager_codes") or []),
        "supervisor_count": len(payload.get("supervisors") or []),
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
    _admin: dict = Depends(require_admin_user),
    date: str | None = Query(None, description="YYYY-MM-DD (ถ้าไม่ส่งงวด)"),
    target_month: int | None = Query(None, ge=1, le=12),
    target_year: int | None = Query(None, ge=2020, le=2100),
    level: str | None = Query(None),
    limit: int = Query(500, ge=1, le=2000),
):
    scan_all = target_month is None and target_year is None and date is None
    return {
        "items": read_logs(
            date=date,
            level=level,
            limit=limit,
            target_year=target_year,
            target_month=target_month,
            scan_all=scan_all,
        ),
        "scan_all": scan_all,
    }


class UsageLogAckBody(BaseModel):
    entry_ids: list[str] = Field(default_factory=list)


def _acknowledge_usage_log_ids(entry_ids: list[str]) -> dict[str, Any]:
    removed = acknowledge_logs(entry_ids)
    if removed <= 0:
        raise HTTPException(status_code=404, detail="ไม่พบรายการที่จะลบ")
    return {"ok": True, "removed": removed}


@router.delete("/usage-logs/{entry_id}")
def admin_delete_usage_log(
    entry_id: str,
    _admin: dict = Depends(require_admin_user),
):
    """รับทราบ/แก้ไขแล้ว — ลบรายการออกจากบันทึก"""
    return _acknowledge_usage_log_ids([entry_id])


@router.post("/usage-logs/acknowledge")
def admin_acknowledge_usage_logs(
    body: UsageLogAckBody,
    _admin: dict = Depends(require_admin_user),
):
    """รับทราบ/แก้ไขแล้ว — ลบรายการออกจากบันทึก (legacy)"""
    return _acknowledge_usage_log_ids(body.entry_ids)


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
        fc.invalidate_period_cache(body.year, body.month, layers={"product", "price"})
        sup = (body.sup_id or "").strip().upper()
        if sup:
            load_employees_payload(
                sup_id=sup,
                target_month=body.month,
                target_year=body.year,
                refresh=True,
            )
            result["warmed_sup"] = sup
        else:
            result["hint"] = "ระบุ sup_id เพื่อ warm product/price cache จาก DAX"

    if layer in ("payload", "all"):
        sid = body.sup_id
        invalidate_employee_payload_cache(sid, body.month, body.year)
        if sid:
            load_employees_payload(
                sup_id=sid.strip().upper(),
                target_month=body.month,
                target_year=body.year,
                refresh=True,
            )
            result["refreshed_payload_sup"] = sid.strip().upper()

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
