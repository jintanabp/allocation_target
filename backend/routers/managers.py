import logging
import os

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse

from ..deps import require_authenticated_user
from ..services.access_control import filter_managers_payload_for_user
from ..services.managers import load_full_managers_payload

logger = logging.getLogger("target_allocation")

router = APIRouter(tags=["managers"])


@router.api_route("/manegers", methods=["GET", "HEAD"], include_in_schema=False)
def managers_common_typo():
    """พิมพ์ผิดบ่อย (manegers) — redirect ไป /managers"""
    return RedirectResponse(url="/managers", status_code=307)


@router.get("/managers")
def get_managers(user: dict = Depends(require_authenticated_user)):
    os.makedirs("data", exist_ok=True)
    full = load_full_managers_payload()
    if user.get("auth_disabled"):
        return full
    return filter_managers_payload_for_user(full, user)
