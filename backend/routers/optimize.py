from fastapi import APIRouter, Depends, Query

from ..deps import require_entra_member
from ..schemas import OptimizeRequest
from ..services.optimize import run_optimization_service

router = APIRouter(tags=["optimize"])


@router.post("/optimize")
def run_optimization(
    req: OptimizeRequest,
    _user: dict = Depends(require_entra_member),
    sup_id: str = Query("SL330"),
    target_month: int = Query(..., ge=1, le=12),
    target_year: int = Query(..., ge=2020, le=2100),
):
    return run_optimization_service(
        req=req,
        sup_id=sup_id,
        target_month=target_month,
        target_year=target_year,
    )

