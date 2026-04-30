import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from ..core.constants import debug_endpoints_enabled
from ..deps import ensure_supervisor_allowed, require_authenticated_user
from ..fabric_dax_connector import FabricDAXConnector

logger = logging.getLogger("target_allocation")

router = APIRouter(tags=["debug"])


@router.get("/debug/fabric")
def debug_fabric(
    user: dict = Depends(require_authenticated_user),
    sup_id: str = Query("SL330"),
):
    """
    ดึงข้อมูล debug จาก Fabric:
    - SuperCode ทั้งหมดที่มีใน Dim_Salesman
    - พนักงานที่ SuperCode นี้ดูแล (ถ้าเจอ)
    เปิด: http://localhost:8000/debug/fabric?sup_id=SL330
    Production: ตั้ง ENABLE_DEBUG_ENDPOINTS=1 เท่านั้น
    """
    if not debug_endpoints_enabled():
        raise HTTPException(404, detail="ไม่พบ endpoint")
    ensure_supervisor_allowed(user, sup_id)
    try:
        fabric = FabricDAXConnector()

        dax_all = """
EVALUATE
SUMMARIZECOLUMNS(
    'Dim_Salesman'[SuperCode],
    "cnt", COUNTROWS('Dim_Salesman')
)
ORDER BY 'Dim_Salesman'[SuperCode]
"""
        rows_all = fabric._execute_dax(dax_all, debug=True)
        all_super_codes = []
        for r in rows_all:
            sc = fabric._get(r, "[SuperCode]", "Dim_Salesman[SuperCode]", default="")
            cnt = fabric._get(r, "[cnt]", default=0)
            all_super_codes.append({"super_code": repr(str(sc)), "count": cnt})

        df_emp = fabric.get_employees_by_manager(sup_id)

        return {
            "query_super_code": sup_id,
            "query_super_code_repr": repr(sup_id),
            "employees_found": len(df_emp),
            "employees": df_emp.to_dict(orient="records") if not df_emp.empty else [],
            "all_super_codes_in_db": all_super_codes,
        }
    except Exception as e:
        logger.error("debug_fabric error: %s", e)
        raise HTTPException(500, detail=str(e))

