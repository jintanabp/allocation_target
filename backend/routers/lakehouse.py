from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from ..deps import (
    ensure_own_supervisor_write,
    ensure_supervisor_allowed,
    ensure_targetsun_import_allowed,
    require_authenticated_user,
)
from ..schemas import LakehouseUploadRequest
from ..services.lakehouse import export_allocations_excel, upload_allocations_to_lakehouse
from ..services.targetsun_import import (
    import_allocations_to_targetsun,
    import_prepared_targetsun,
    prepare_targetsun_import,
)
from ..services.usage_log_store import log_from_user

router = APIRouter(tags=["lakehouse"])


def _log_targetsun_send(user: dict, req: LakehouseUploadRequest, result: Any) -> None:
    """
    บันทึกทุกครั้งที่กดส่ง Target Sun — สำเร็จหรือไม่ก็ตาม
    นี่คือหลักฐานเดียวที่บอกได้ว่าทีมไหนส่งอะไรไปเมื่อไหร่ (snapshot เก็บแค่ครั้งล่าสุดต่องวด)
    """
    try:
        res = result if isinstance(result, dict) else {}
        ts = res.get("targetsun") or {}
        r = ts.get("result") or {}
        ok = ts.get("success") is not False
        log_from_user(
            user,
            level="info" if ok else "error",
            sup_id=req.sup_id,
            action="send_targetsun",
            message=("ส่งเข้า Target Sun สำเร็จ" if ok else "ส่งเข้า Target Sun ไม่สำเร็จ"),
            detail=(
                f"งวด {req.target_year}-{req.target_month:02d} · "
                f"ส่ง {res.get('rows_sent', 0)} แถว · "
                f"เพิ่ม {r.get('inserted', 0)} · แก้ {r.get('updated', 0)} · ข้าม {r.get('skipped', 0)}"
                + ("" if ok else f" · {ts.get('resultMsg') or ''}")
            ),
        )
    except Exception:  # log ต้องไม่ทำให้การส่งพัง
        pass


@router.post("/lakehouse/export-csv")
def export_lakehouse_csv(
    req: LakehouseUploadRequest,
    user: dict = Depends(require_authenticated_user),
):
    """ดาวน์โหลด Excel (.xlsx) รูปแบบ tga_target_salesman_next (รวม QUANTITYCASE=0)"""
    ensure_supervisor_allowed(user, req.sup_id)
    ensure_own_supervisor_write(user, req.sup_id)
    out = export_allocations_excel(req)
    return Response(
        content=out["content"],
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{out["filename"]}"',
            "X-Export-Rows": str(out["rows"]),
            "X-Export-Zero-Rows": str(out["zero_rows"]),
            "X-Export-Dropped-Missing-Dims": str(out.get("dropped_missing_dims", 0)),
        },
    )


@router.post("/lakehouse/upload")
def upload_to_lakehouse(
    req: LakehouseUploadRequest,
    user: dict = Depends(require_authenticated_user),
):
    ensure_supervisor_allowed(user, req.sup_id)
    ensure_own_supervisor_write(user, req.sup_id)
    return upload_allocations_to_lakehouse(req)


@router.post("/lakehouse/prepare-targetsun")
def prepare_targetsun_from_allocations(
    req: LakehouseUploadRequest,
    user: dict = Depends(require_authenticated_user),
):
    """ขั้นที่ 1: สร้าง Excel TGA เก็บชั่วคราว — คืน prepare_token สำหรับขั้นส่ง"""
    ensure_supervisor_allowed(user, req.sup_id)
    ensure_own_supervisor_write(user, req.sup_id)
    ensure_targetsun_import_allowed(user)
    return prepare_targetsun_import(req)


@router.post("/lakehouse/import-targetsun")
def import_targetsun_from_allocations(
    req: LakehouseUploadRequest,
    user: dict = Depends(require_authenticated_user),
):
    """
    ส่งเข้า importTargetSalesmanNextFromExcel
    - มี prepare_token: POST ไฟล์ที่เตรียมแล้ว (ขั้นที่ 2)
    - ไม่มี: สร้าง Excel + POST ในคำขอเดียว (เดิม)
    """
    ensure_supervisor_allowed(user, req.sup_id)
    ensure_own_supervisor_write(user, req.sup_id)
    ensure_targetsun_import_allowed(user)
    try:
        if (req.prepare_token or "").strip():
            result = import_prepared_targetsun(req)
        else:
            result = import_allocations_to_targetsun(req)
    except Exception as e:
        log_from_user(
            user,
            level="error",
            sup_id=req.sup_id,
            action="send_targetsun",
            message="ส่งเข้า Target Sun ไม่สำเร็จ",
            detail=f"งวด {req.target_year}-{req.target_month:02d} · {type(e).__name__}: {e}",
        )
        raise
    _log_targetsun_send(user, req, result)
    return result
