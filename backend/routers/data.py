from fastapi import APIRouter, Depends, Query

from ..deps import ensure_supervisor_allowed, require_authenticated_user
from ..services.employees import load_employees_payload

router = APIRouter(tags=["data"])


@router.get("/data/employees")
def get_employees(
    user: dict = Depends(require_authenticated_user),
    sup_id: str = Query(..., description="SuperCode เช่น SL330"),
    target_month: int = Query(..., ge=1, le=12),
    target_year: int = Query(..., ge=2020, le=2100),
    regen_target: bool = Query(False, description="บังคับ regenerate dummy targets"),
):
    ensure_supervisor_allowed(user, sup_id)
    return load_employees_payload(
        sup_id=sup_id,
        target_month=target_month,
        target_year=target_year,
        regen_target=bool(regen_target),
    )
