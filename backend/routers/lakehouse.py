from fastapi import APIRouter, Depends

from ..deps import require_entra_member
from ..schemas import LakehouseUploadRequest
from ..services.lakehouse import upload_allocations_to_lakehouse

router = APIRouter(tags=["lakehouse"])


@router.post("/lakehouse/upload")
def upload_to_lakehouse(
    req: LakehouseUploadRequest,
    _user: dict = Depends(require_entra_member),
):
    return upload_allocations_to_lakehouse(req)

