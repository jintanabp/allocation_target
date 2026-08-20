from fastapi import APIRouter, Depends, Query

from ..deps import ensure_own_supervisor_write, ensure_supervisor_allowed, require_authenticated_user
from ..schemas import OptimizeRequest
from ..services.optimize import run_optimization_service

router = APIRouter(tags=["optimize"])


@router.post("/optimize")
def run_optimization(
    req: OptimizeRequest,
    user: dict = Depends(require_authenticated_user),
    sup_id: str = Query(..., min_length=1),
    target_month: int = Query(..., ge=1, le=12),
    target_year: int = Query(..., ge=2020, le=2100),
):
    ensure_supervisor_allowed(user, sup_id)
    ensure_own_supervisor_write(user, sup_id)
    # โหมดรวมเป้าทั้งภาค: เป้าที่ใช้กระจายมาจากหลายทีม — ต้องตรวจสิทธิ์ "ทุกรหัส"
    # ไม่ใช่แค่รหัสที่ยิง request ไม่งั้นดึงเป้าของภาคอื่นมาเป็นฐานได้
    for peer in req.target_sup_ids:
        pid = str(peer or "").strip().upper()
        if pid and pid != sup_id.strip().upper():
            ensure_supervisor_allowed(user, pid)
    return run_optimization_service(
        req=req,
        sup_id=sup_id,
        target_month=target_month,
        target_year=target_year,
    )
