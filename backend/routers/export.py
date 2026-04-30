from fastapi import APIRouter, Depends, Query

from ..deps import ensure_supervisor_allowed, require_authenticated_user
from ..schemas import ExportRequest
from ..services.exporting import download_excel_response, export_excel_service

router = APIRouter(tags=["export"])


@router.post("/export/excel")
def export_excel(
    req: ExportRequest,
    user: dict = Depends(require_authenticated_user),
    sup_id: str = Query("SL330"),
):
    ensure_supervisor_allowed(user, sup_id)
    return export_excel_service(req=req, sup_id=sup_id)


@router.get("/download/excel")
def download_excel(
    user: dict = Depends(require_authenticated_user),
    sup_id: str = Query("SL330"),
    brand: str = Query("ALL"),
):
    ensure_supervisor_allowed(user, sup_id)
    return download_excel_response(sup_id=sup_id, brand=brand)
