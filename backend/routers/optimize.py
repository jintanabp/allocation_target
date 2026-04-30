from fastapi import APIRouter, Depends, Query

from ..deps import ensure_supervisor_allowed, require_authenticated_user
from ..schemas import OptimizeRequest
from ..services.optimize import run_optimization_service

router = APIRouter(tags=["optimize"])


@router.post("/optimize")
def run_optimization(
    req: OptimizeRequest,
    user: dict = Depends(require_authenticated_user),
    sup_id: str = Query("SL330"),
    target_month: int = Query(..., ge=1, le=12),
    target_year: int = Query(..., ge=2020, le=2100),
):
    ensure_supervisor_allowed(user, sup_id)
    return run_optimization_service(
        req=req,
        sup_id=sup_id,
        target_month=target_month,
        target_year=target_year,
    )
