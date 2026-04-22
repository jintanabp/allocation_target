import json
import logging
import os
import time

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse

from ..deps import require_entra_member
from ..fabric_dax_connector import FabricDAXConnector
from ..services.managers import (
    MANAGERS_CACHE_FILE,
    persist_managers_payload,
    try_fetch_managers_from_fabric,
)

logger = logging.getLogger("target_allocation")

router = APIRouter(tags=["managers"])


@router.api_route("/manegers", methods=["GET", "HEAD"], include_in_schema=False)
def managers_common_typo():
    """พิมพ์ผิดบ่อย (manegers) — redirect ไป /managers"""
    return RedirectResponse(url="/managers", status_code=307)


@router.get("/managers")
def get_managers(_user: dict = Depends(require_entra_member)):
    os.makedirs("data", exist_ok=True)
    cache_path = MANAGERS_CACHE_FILE

    # ไม่ต้องรอ DAX ซ้ำถ้า cache ยังสด (เติมตอน startup หรือครั้งก่อนที่ดึงสำเร็จ)
    ttl = int(os.environ.get("MANAGERS_CACHE_TTL_SEC", "86400"))
    if ttl > 0 and os.path.exists(cache_path):
        try:
            age = time.time() - os.path.getmtime(cache_path)
            if age < ttl:
                with open(cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and data.get("rows"):
                    return data
        except Exception as e:
            logger.warning("managers cache fast read: %s", e)

    payload = try_fetch_managers_from_fabric()
    if payload:
        try:
            persist_managers_payload(payload)
        except Exception as e:
            logger.warning("managers cache write failed: %s", e)
        return payload

    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("rows"):
                return data
            if isinstance(data, list):
                return {
                    "managers": data,
                    "rows": [],
                    "by_manager": {},
                    "source": "cache_legacy",
                }
        except Exception as cache_err:
            logger.warning("managers cache corrupt: %s", cache_err)

    try:
        fabric = FabricDAXConnector()
        codes = fabric.get_all_super_codes()
        if codes:
            return {
                "managers": codes,
                "rows": [],
                "by_manager": {},
                "source": "dim_fallback",
            }
    except Exception as e:
        logger.warning("get_all_super_codes error: %s", e)

    logger.warning("ไม่มีรายชื่อ Supervisor/Manager — ตรวจสอบ trf_select_supervisor / Fabric")
    return {"managers": [], "rows": [], "by_manager": {}, "source": "empty"}

