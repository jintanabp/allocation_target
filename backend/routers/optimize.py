from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import ensure_own_supervisor_write, ensure_supervisor_allowed, require_authenticated_user
from ..schemas import OptimizeRequest
from ..services.error_explain import explain_http
from ..services.optimize import run_optimization_service
from ..services.usage_log_store import log_from_user

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
    # โหมดรวมทั้งหน่วย/ภาค: peer_sup_ids กำหนดว่าจะอ่านประวัติขายจากทีมไหนบ้าง
    # (คนละตัวกับ target_sup_ids ที่กำหนดเป้า) ต้องตรวจสิทธิ์ทุกรหัสเหมือนกัน ไม่งั้น
    # ดึงประวัติขายของทีมที่ไม่มีสิทธิ์เห็นมาปนในเป้าตัวเองได้
    for peer in req.peer_sup_ids:
        pid = str(peer or "").strip().upper()
        if pid and pid != sup_id.strip().upper():
            ensure_supervisor_allowed(user, pid)
    try:
        return run_optimization_service(
            req=req,
            sup_id=sup_id,
            target_month=target_month,
            target_year=target_year,
        )
    except HTTPException as e:
        # เดิมกด「เริ่มคำนวณ」แล้วล้มไม่มีบันทึกเลย แอดมินไม่รู้ว่าซุปติดอะไร (ผู้ใช้ขอ 29 ก.ย. 2026)
        try:
            d = e.detail if isinstance(e.detail, dict) else {}
            log_from_user(
                user,
                level="error" if e.status_code >= 409 else "warn",
                sup_id=sup_id,
                action="optimize_failed",
                message="กระจายหีบไม่สำเร็จ — " + str(d.get("message") or e.detail)[:200],
                detail=f"งวด {target_year}-{target_month:02d} · HTTP {e.status_code}"
                + (f" · รวมภาค {len(req.target_sup_ids)} ทีม" if req.target_sup_ids else ""),
                target_month=target_month,
                target_year=target_year,
                context={"ok": False, "status": e.status_code, "explain": explain_http(e)},
            )
        except Exception:
            pass
        raise
