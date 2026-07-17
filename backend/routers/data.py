from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..deps import (
    ensure_allocation_write_allowed,
    ensure_supervisor_allowed,
    require_authenticated_user,
)
from ..services.allocation_store import (
    SnapshotConflict,
    SnapshotPreconditionRequired,
    delete_snapshot,
    list_summaries,
    read_snapshot,
    write_snapshot,
)
from ..services.employees import (
    load_employees_bulk,
    load_employees_payload,
    load_live_targets_payload,
)
from ..services.access_control import resolve_summary_supervisor_codes
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


@router.get("/data/targets/live")
def get_live_targets(
    user: dict = Depends(require_authenticated_user),
    sup_id: str = Query(..., description="SuperCode เช่น SL330"),
    target_month: int = Query(..., ge=1, le=12),
    target_year: int = Query(..., ge=2020, le=2100),
    refresh: bool = Query(
        False,
        description="บังคับดึงจาก Target Sun ใหม่ (ข้าม cache สั้น)",
    ),
):
    """ดึงเป้าหีบล่าสุดจาก Target Sun Read API — ใช้ refresh ใน Step 3"""
    sid = sup_id.strip().upper()
    ensure_supervisor_allowed(user, sid)
    return load_live_targets_payload(
        sid,
        target_month,
        target_year,
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


@router.get("/data/employees/region-peers")
def get_employees_region_peers(
    user: dict = Depends(require_authenticated_user),
    sup_id: str = Query(..., description="รหัส Supervisor ที่ล็อกอิน (ทีมตัวเอง)"),
    target_month: int = Query(..., ge=1, le=12),
    target_year: int = Query(..., ge=2020, le=2100),
    refresh: bool = Query(
        False,
        description="บังคับดึงจาก Fabric ใหม่ (ข้าม payload cache)",
    ),
):
    """รวมข้อมูลทุกซุปในภาคเดียวกัน — สำหรับ supervisor_acc + region_peers (ดูอย่างเดียว)"""
    sid = sup_id.strip().upper()
    ensure_supervisor_allowed(user, sid)
    home = {str(x).strip().upper() for x in (user.get("home_supervisor_codes") or ())}
    if home and sid not in home:
        raise HTTPException(
            status_code=403,
            detail="โหลดรวมภาคได้เฉพาะจากรหัสทีมตัวเอง",
        )
    allowed = user.get("allowed_supervisor_codes")
    if not allowed:
        raise HTTPException(status_code=403, detail="ไม่มีสิทธิ์ดูภาคเดียวกัน")
    sup_ids = sorted({str(x).strip().upper() for x in allowed if str(x).strip()})
    if len(sup_ids) <= 1:
        raise HTTPException(
            status_code=400,
            detail="มีเพียงทีมเดียวในภาค — ใช้มุมมองรายคน",
        )
    label = f"รวมภาค ({sid})"
    return load_employees_bulk(
        sup_ids,
        target_month,
        target_year,
        aggregate_label=label,
        refresh=bool(refresh),
    )


class AllocationSnapshotBody(BaseModel):
    sup_id: str
    target_month: int = Field(..., ge=1, le=12)
    target_year: int = Field(..., ge=2020, le=2100)
    status: Literal["draft", "optimized", "sent_targetsun"] = "draft"
    allocations: list[dict[str, Any]] = Field(default_factory=list)
    yellow: dict[str, Any] = Field(default_factory=dict)
    yellow_locked: dict[str, Any] = Field(default_factory=dict)
    strategy: str = ""
    target_sun_sent_at: str | None = None
    # version ที่ client เห็นตอนโหลด — ไม่ส่งมา = เขียนทับแบบเดิม (tab เก่าจึงไม่พัง)
    # ใช้ field ใน body ไม่ใช่ header If-Match เพื่อเลี่ยงปัญหา preflight/proxy ตัด header
    if_match_version: int | None = None


@router.get("/data/allocations")
def get_allocation_snapshot(
    user: dict = Depends(require_authenticated_user),
    sup_id: str = Query(..., min_length=1),
    target_month: int = Query(..., ge=1, le=12),
    target_year: int = Query(..., ge=2020, le=2100),
):
    """โหลด snapshot ผลกระจายหีบล่าสุด — supervisor/manager/peer read-only"""
    sid = sup_id.strip().upper()
    ensure_supervisor_allowed(user, sid)
    snap = read_snapshot(sid, target_month, target_year)
    if not snap:
        raise HTTPException(status_code=404, detail="ยังไม่มีผลกระจายที่บันทึกบน server")
    return snap


@router.put("/data/allocations")
def put_allocation_snapshot(
    body: AllocationSnapshotBody,
    user: dict = Depends(require_authenticated_user),
):
    """บันทึก snapshot — supervisor ทีมตัวเอง / manager ที่มีสิทธิ"""
    from ..services.usage_log_store import log_from_user

    sid = body.sup_id.strip().upper()
    try:
        ensure_allocation_write_allowed(user, sid)
    except HTTPException as e:
        log_from_user(
            user,
            level="warn",
            sup_id=sid,
            action="save_allocation",
            message="บันทึกผลกระจายไม่ได้ — ไม่มีสิทธิ์",
            detail=str(e.detail),
        )
        raise
    email = str(user.get("email") or user.get("view_as_email") or "").strip()
    payload = body.model_dump()
    expected_version = payload.pop("if_match_version", None)
    payload["sup_id"] = sid
    payload["updated_by"] = email

    if expected_version is None and read_snapshot(sid, body.target_month, body.target_year):
        # สัญญาณสำหรับ rollout: ถ้าไม่มี log นี้แล้ว = ทุก client ส่ง version → เปิดบังคับได้
        log_from_user(
            user,
            level="warn",
            sup_id=sid,
            action="save_allocation_no_precondition",
            message="บันทึกทับโดยไม่ส่ง version (client เก่า)",
            detail=f"{body.target_year}-{body.target_month:02d}",
        )

    try:
        return write_snapshot(payload, expected_version=expected_version)
    except SnapshotConflict as e:
        log_from_user(
            user,
            level="warn",
            sup_id=sid,
            action="save_allocation_conflict",
            message="บันทึกไม่ได้ — มีคนอื่นบันทึกทับไปแล้ว",
            detail=f"version บนเซิร์ฟเวอร์={e.current.get('version')}",
        )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "snapshot_conflict",
                "message": "มีคนอื่นบันทึกผลกระจายนี้ไปแล้ว — โหลดใหม่ก่อนหรือเลือกเขียนทับ",
                "current": {
                    k: e.current.get(k)
                    for k in ("version", "updated_at", "updated_by", "status")
                },
            },
        ) from e
    except SnapshotPreconditionRequired as e:
        raise HTTPException(
            status_code=428,
            detail={
                "code": "precondition_required",
                "message": "หน้าเว็บเป็นเวอร์ชันเก่า — กรุณากด Ctrl+F5 รีเฟรชแล้วบันทึกใหม่",
                "current": {k: e.current.get(k) for k in ("version", "updated_at")},
            },
        ) from e
    except ValueError as e:
        log_from_user(
            user,
            level="error",
            sup_id=sid,
            action="save_allocation",
            message="บันทึกผลกระจายไม่สำเร็จ",
            detail=str(e),
        )
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete("/data/allocations")
def delete_allocation_snapshot(
    user: dict = Depends(require_authenticated_user),
    sup_id: str = Query(..., min_length=1),
    target_month: int = Query(..., ge=1, le=12),
    target_year: int = Query(..., ge=2020, le=2100),
):
    """ลบ snapshot — supervisor ทีมตัวเอง / manager ที่มีสิทธิ"""
    sid = sup_id.strip().upper()
    ensure_allocation_write_allowed(user, sid)
    if not delete_snapshot(sid, target_month, target_year):
        raise HTTPException(status_code=404, detail="ไม่พบผลกระจายที่จะลบ")
    return {"status": "ok", "sup_id": sid}


@router.get("/data/allocations/summary")
def get_allocations_summary(
    user: dict = Depends(require_authenticated_user),
    target_month: int = Query(..., ge=1, le=12),
    target_year: int = Query(..., ge=2020, le=2100),
    team: str = Query("", description="รหัส SL คั่นด้วย comma (แนะนำสำหรับแอดมิน)"),
):
    """สรุป snapshot ทุก SL ที่ user เข้าถึงได้ — สำหรับ manager / peer visibility"""
    sup_ids = resolve_summary_supervisor_codes(user, team)
    if not sup_ids:
        raise HTTPException(status_code=403, detail="ไม่มีสิทธิ์ดูสรุป")
    return {"items": list_summaries(sup_ids, target_month, target_year), "sup_ids": sup_ids}
