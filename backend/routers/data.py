from fastapi import APIRouter, Depends, Query

from ..deps import require_entra_member
from ..services.employees import load_employees_payload

router = APIRouter(tags=["data"])


@router.get("/data/employees")
def get_employees(
    _user: dict = Depends(require_entra_member),
    sup_id: str = Query(..., description="SuperCode เช่น SL330"),
    target_month: int = Query(..., ge=1, le=12),
    target_year: int = Query(..., ge=2020, le=2100),
    regen_target: bool = Query(False, description="บังคับ regenerate dummy targets"),
):
    return load_employees_payload(
        sup_id=sup_id,
        target_month=target_month,
        target_year=target_year,
        regen_target=bool(regen_target),
    )

