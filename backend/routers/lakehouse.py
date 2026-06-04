from fastapi import APIRouter, Depends
from fastapi.responses import Response

from ..deps import (
    ensure_supervisor_allowed,
    ensure_targetsun_import_allowed,
    require_authenticated_user,
)
from ..schemas import LakehouseUploadRequest
from ..services.lakehouse import export_allocations_excel, upload_allocations_to_lakehouse
from ..services.targetsun_import import import_allocations_to_targetsun

router = APIRouter(tags=["lakehouse"])


@router.post("/lakehouse/export-csv")
def export_lakehouse_csv(
    req: LakehouseUploadRequest,
    user: dict = Depends(require_authenticated_user),
):
    """ดาวน์โหลด Excel (.xlsx) รูปแบบ tga_target_salesman_next (รวม QUANTITYCASE=0)"""
    ensure_supervisor_allowed(user, req.sup_id)
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
    return upload_allocations_to_lakehouse(req)


@router.post("/lakehouse/import-targetsun")
def import_targetsun_from_allocations(
    req: LakehouseUploadRequest,
    user: dict = Depends(require_authenticated_user),
):
    """
    สร้าง Excel รูปแบบ tga_target_salesman_next แล้ว POST ไปบริการ importTargetSalesmanNextFromExcel
    (Oracle UAT/Prod ตามที่ service ของ SPC config ไว้ — ค่าเริ่มต้นชี้ UAT)
    """
    ensure_supervisor_allowed(user, req.sup_id)
    ensure_targetsun_import_allowed(user)
    return import_allocations_to_targetsun(req)
