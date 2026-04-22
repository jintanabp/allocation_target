from fastapi import APIRouter, Depends, Query

from ..deps import require_entra_member
from ..schemas import ExportRequest
from ..services.exporting import download_excel_response, export_excel_service

router = APIRouter(tags=["export"])


@router.post("/export/excel")
def export_excel(
    req: ExportRequest,
    _user: dict = Depends(require_entra_member),
    sup_id: str = Query("SL330"),
):
    return export_excel_service(req=req, sup_id=sup_id)


@router.get("/download/excel")
def download_excel(
    _user: dict = Depends(require_entra_member),
    sup_id: str = Query("SL330"),
    brand: str = Query("ALL"),
):
    return download_excel_response(sup_id=sup_id, brand=brand)

