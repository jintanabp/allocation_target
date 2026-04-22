import os

from fastapi import APIRouter

from .. import auth_entra
from ..core.constants import VALID_STRATEGIES, debug_endpoints_enabled

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    return {
        "status": "ok",
        "entra_auth_required": auth_entra.auth_enabled(),
        "managers_source": "GET /managers (ดึง SuperCode จาก Dim_Salesman ใน Fabric)",
        "valid_strategies": list(VALID_STRATEGIES),
        "debug_endpoints_enabled": debug_endpoints_enabled(),
        "files": {
            "target_boxes.csv": os.path.exists("data/target_boxes.csv"),
            "target_sun.csv": os.path.exists("data/target_sun.csv"),
        },
    }

