from fastapi import APIRouter, Depends

from ..deps import ensure_supervisor_allowed, require_authenticated_user
from ..schemas import LakehouseUploadRequest
from ..services.lakehouse import upload_allocations_to_lakehouse

router = APIRouter(tags=["lakehouse"])


@router.post("/lakehouse/upload")
def upload_to_lakehouse(
    req: LakehouseUploadRequest,
    user: dict = Depends(require_authenticated_user),
):
    ensure_supervisor_allowed(user, req.sup_id)
    return upload_allocations_to_lakehouse(req)
