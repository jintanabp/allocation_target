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
from ..services.usage_log_store import read_logs

router = APIRouter(tags=["data"])


@router.get("/data/send-history")
def get_send_history(
    user: dict = Depends(require_authenticated_user),
    sup_id: str = Query(..., min_length=1),
    target_month: int | None = Query(None, ge=1, le=12),
    target_year: int | None = Query(None, ge=2020, le=2100),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """
    ประวัติการส่งเข้า Target Sun ของทีมนี้

    เดิมผลการส่งอยู่ในข้อความแจ้งเตือนที่หายไปใน 5 วินาที ไม่มีที่ให้เปิดดูย้อนหลังเลย
    ทั้งที่ server บันทึกไว้ครบใน usage log อยู่แล้ว — ตรงนี้แค่เปิดให้อ่าน
    """
    ensure_supervisor_allowed(user, sup_id)
    items = read_logs(
        limit=limit,
        target_year=target_year,
        target_month=target_month,
        scan_all=(target_month is None or target_year is None),
        action="send_targetsun",
        sup_id=sup_id,
    )
    return {
        "items": [
            {
                "ts": it.get("ts"),
                "level": it.get("level"),
                "email": it.get("email"),
                "message": it.get("message"),
                "detail": it.get("detail"),
            }
            for it in items
        ],
        "count": len(items),
        "sup_id": str(sup_id or "").strip().upper(),
    }


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
        # ตรงกับ _managerAggregateWritable() ฝั่งหน้าเว็บ — รวมภาคแก้/กระจายได้
        # ส่วนรวมทั้ง division เป็นมุมมองดูอย่างเดียว จึงต้องไม่ไปซ่อมไฟล์ของทีมอื่น
        can_write=(view == "region"),
    )


@router.get("/data/targets/drift")
def get_target_drift(
    user: dict = Depends(require_authenticated_user),
    sup_ids: str = Query(..., description="รหัสทีม คั่นด้วยจุลภาค"),
    target_month: int = Query(..., ge=1, le=12),
    target_year: int = Query(..., ge=2020, le=2100),
):
    """
    เป้าใน Target Sun เปลี่ยนไปจากตอนโหลดขั้นที่ 1 หรือยัง (อ่านอย่างเดียว)

    หน้ารวมภาคเรียกตอนเปิดหน้าและตอนผู้ใช้กดปุ่มตรวจเอง — ไม่ยิงเป็นรอบอัตโนมัติ
    เพราะแต่ละครั้งต้องอ่าน Target Sun ทีละทีม (ภาคหนึ่งมีได้ถึงสิบกว่าทีม)
    """
    from ..services.lakehouse import target_drift_for_sups

    ids = [x.strip().upper() for x in (sup_ids or "").split(",") if x.strip()]
    if not ids:
        raise HTTPException(status_code=400, detail="ต้องระบุรหัสทีมอย่างน้อยหนึ่งรหัส")
    if len(ids) > 40:
        raise HTTPException(status_code=400, detail="ระบุรหัสทีมได้ไม่เกิน 40 รหัสต่อครั้ง")
    for sid in ids:
        ensure_supervisor_allowed(user, sid)
    return target_drift_for_sups(ids, target_month, target_year)


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
    """รวมข้อมูลทุกซุปในภาคเดียวกัน — สำหรับ supervisor_acc + region_peers (แก้/กระจายได้)"""
    sid = sup_id.strip().upper()
    ensure_supervisor_allowed(user, sid)
    home = {str(x).strip().upper() for x in (user.get("home_supervisor_codes") or ())}
    if home and sid not in home:
        raise HTTPException(
            status_code=403,
            detail="โหลดรวมภาคได้เฉพาะจากรหัสทีมตัวเอง",
        )
    allowed = user.get("allowed_supervisor_codes")
    if allowed is None:
        # None = "ไม่จำกัดขอบเขต" (dev / ALLOCATION_ADMIN_EMAILS) ไม่ใช่ "ไม่มีสิทธิ์"
        # แต่มุมมองรวมภาคต้องรู้ว่า "ภาคไหน" ถึงจะรวมได้ ซึ่งบัญชีที่ไม่จำกัดขอบเขต
        # ตอบคำถามนั้นไม่ได้ · ทางที่ใช้ได้และมีเทสคุมอยู่แล้วคือโหมด "ดูในมุมของผู้ใช้"
        # (X-View-As-Email) ซึ่งจะได้ home/peer ของซุปคนนั้นมาจริง ๆ
        raise HTTPException(
            status_code=403,
            detail=(
                "บัญชีนี้ไม่ได้ผูกกับภาคใดภาคหนึ่ง จึงรวมภาคให้ไม่ได้ — "
                "ใช้โหมด “ดูในมุมของผู้ใช้” แล้วเลือกซุปในภาคที่ต้องการ "
                "มุมมองรวมภาคจะใช้งานได้ตามสิทธิ์ของซุปคนนั้น"
            ),
        )
    if not allowed:
        # เซ็ตว่าง = บัญชีแอดมินอย่างเดียว (ไม่มีทีมของตัวเอง) — คนละเรื่องกับข้างบน
        raise HTTPException(
            status_code=403,
            detail=(
                "บัญชีนี้ยังไม่มีทีมในขอบเขต จึงรวมภาคให้ไม่ได้ — "
                "ใช้โหมด “ดูในมุมของผู้ใช้” แล้วเลือกซุปในภาคที่ต้องการ"
            ),
        )
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
        # peer ในภาคเดียวกันแก้เป้า/กระจายได้ (_supervisorRegionAggregateView)
        can_write=True,
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
            target_month=body.target_month,
            target_year=body.target_year,
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
            target_month=body.target_month,
            target_year=body.target_year,
        )

    prev = read_snapshot(sid, body.target_month, body.target_year)
    try:
        saved = write_snapshot(payload, expected_version=expected_version)
    except SnapshotConflict as e:
        log_from_user(
            user,
            level="warn",
            sup_id=sid,
            action="save_allocation_conflict",
            message="บันทึกไม่ได้ — มีคนอื่นบันทึกทับไปแล้ว",
            detail=f"version บนเซิร์ฟเวอร์={e.current.get('version')}",
            target_month=body.target_month,
            target_year=body.target_year,
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
            target_month=body.target_month,
            target_year=body.target_year,
        )
        raise HTTPException(status_code=400, detail=str(e)) from e

    # เดิม log เฉพาะตอน "ล้มเหลว" — จึงไม่มีทางรู้เลยว่าใครทับผลกระจายของใครเมื่อไหร่
    # ซึ่งเป็นคำถามแรกที่ถูกถามทุกครั้งที่ตัวเลขเปลี่ยนโดยไม่มีใครยอมรับ
    _rows = len(payload.get("allocations") or [])
    _boxes = _sum_boxes(payload.get("allocations"))
    log_from_user(
        user,
        sup_id=sid,
        action="save_allocation_ok",
        message=f"บันทึกผลกระจาย {_rows} แถว ({_boxes} หีบ)",
        detail=(
            f"version {(prev or {}).get('version', '—')} → {saved.get('version')}"
            + (f" · ทับของ {prev.get('updated_by')}" if prev and prev.get("updated_by") else "")
        ),
        target_month=body.target_month,
        target_year=body.target_year,
        context={
            "rows": _rows,
            "boxes": _boxes,
            "version_before": (prev or {}).get("version"),
            "version_after": saved.get("version"),
            "boxes_before": _sum_boxes((prev or {}).get("allocations")),
            "updated_by_before": (prev or {}).get("updated_by"),
            "strategy": payload.get("strategy"),
        },
    )
    return saved


def _sum_boxes(allocations) -> int:
    """ยอดหีบรวมของ snapshot — ตัวเลขเดียวที่บอกได้เร็วที่สุดว่า 'หายไปเท่าไร'"""
    total = 0
    for a in allocations or []:
        try:
            total += int(round(float((a or {}).get("allocated_boxes") or 0)))
        except (TypeError, ValueError):
            continue
    return total


@router.delete("/data/allocations")
def delete_allocation_snapshot(
    user: dict = Depends(require_authenticated_user),
    sup_id: str = Query(..., min_length=1),
    target_month: int = Query(..., ge=1, le=12),
    target_year: int = Query(..., ge=2020, le=2100),
):
    """ลบ snapshot — supervisor ทีมตัวเอง / manager ที่มีสิทธิ"""
    from ..services.usage_log_store import log_from_user

    sid = sup_id.strip().upper()
    ensure_allocation_write_allowed(user, sid)
    # อ่านของเดิมก่อนลบ — ลบแล้วไม่มีอะไรเหลือให้บอกว่าเมื่อกี้มีอะไรอยู่
    prev = read_snapshot(sid, target_month, target_year)
    if not delete_snapshot(sid, target_month, target_year):
        raise HTTPException(status_code=404, detail="ไม่พบผลกระจายที่จะลบ")
    log_from_user(
        user,
        level="warn",
        sup_id=sid,
        action="delete_allocation",
        message=f"ลบผลกระจาย {sid} งวด {target_month:02d}/{target_year}",
        detail=(
            f"ของเดิม {len((prev or {}).get('allocations') or [])} แถว "
            f"({_sum_boxes((prev or {}).get('allocations'))} หีบ) "
            f"บันทึกโดย {(prev or {}).get('updated_by') or '—'}"
        ),
        target_month=target_month,
        target_year=target_year,
        context={
            "rows_deleted": len((prev or {}).get("allocations") or []),
            "boxes_deleted": _sum_boxes((prev or {}).get("allocations")),
            "version_deleted": (prev or {}).get("version"),
            "updated_by": (prev or {}).get("updated_by"),
            "updated_at": (prev or {}).get("updated_at"),
        },
    )
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
