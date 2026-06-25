from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import ensure_supervisor_allowed, require_authenticated_user
from ..services.employees import load_employees_bulk, load_employees_payload
from ..services.manager_views import resolve_aggregate_supervisor_codes

router = APIRouter(tags=["data"])


@router.get("/data/employees")
def get_employees(
    user: dict = Depends(require_authenticated_user),
    sup_id: str = Query(..., description="SuperCode เช่น SL330"),
    target_month: int = Query(..., ge=1, le=12),
    target_year: int = Query(..., ge=2020, le=2100),
    regen_target: bool = Query(False, description="บังคับ regenerate dummy targets"),
    refresh: bool = Query(
        False,
        description="บังคับดึงจาก Fabric ใหม่ (ข้าม payload cache)",
    ),
):
    ensure_supervisor_allowed(user, sup_id)
    return load_employees_payload(
        sup_id=sup_id,
        target_month=target_month,
        target_year=target_year,
        regen_target=bool(regen_target),
        refresh=bool(refresh),
    )


@router.get("/data/employees/aggregate")
def get_employees_aggregate(
    user: dict = Depends(require_authenticated_user),
    manager_code: str = Query(..., min_length=1, description="รหัส Manager ที่ล็อกอิน"),
    view: Literal["all", "region"] = Query(..., description="all=รวมทั้งหมด, region=รวมภาค"),
    region: str = Query("", description="ภาค (เมื่อ view=region)"),
    team: str = Query("", description="รายการ SL ในทีม คั่นด้วย comma"),
    target_month: int = Query(..., ge=1, le=12),
    target_year: int = Query(..., ge=2020, le=2100),
    refresh: bool = Query(
        False,
        description="บังคับดึงจาก Fabric ใหม่ (ข้าม payload cache)",
    ),
):
    mgr = manager_code.strip().upper()
    ensure_supervisor_allowed(user, mgr)
    team_codes = [x.strip().upper() for x in (team or "").split(",") if x.strip()]
    if not team_codes:
        allowed = user.get("allowed_supervisor_codes") or set()
        team_codes = sorted(str(x).strip().upper() for x in allowed if x)

    try:
        sup_ids = resolve_aggregate_supervisor_codes(mgr, team_codes, view, region or None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if not sup_ids:
        raise HTTPException(status_code=404, detail="ไม่มี Supervisor ในขอบเขตที่เลือก")

    for sid in sup_ids:
        ensure_supervisor_allowed(user, sid)

    if view == "all":
        label = f"รวมทั้งหมด ({mgr})"
    else:
        reg_label = (region or "").strip() or "ทั้งภาค"
        label = f"รวม{reg_label} ({mgr})"

    return load_employees_bulk(
        sup_ids,
        target_month,
        target_year,
        aggregate_label=label,
        refresh=bool(refresh),
    )
