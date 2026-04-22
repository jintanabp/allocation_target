import logging
from typing import Annotated

from fastapi import Header, HTTPException

from . import auth_entra

logger = logging.getLogger("target_allocation")


def require_entra_member(
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    """ถ้าเปิด Entra auth — ต้องส่ง Bearer token และอยู่ในกลุ่ม AZURE_AUTH_ALLOWED_GROUP_ID"""
    if not auth_entra.auth_enabled():
        return {}
    if not authorization or not authorization.lower().startswith("bearer "):
        logger.info("Entra auth: missing bearer token")
        raise HTTPException(
            status_code=401,
            detail="กรุณาล็อกอินด้วย Microsoft (กดปุ่มล็อกอินก่อน)",
        )
    token = authorization[7:].strip()
    try:
        return auth_entra.verify_bearer_and_group(token)
    except PermissionError as e:
        logger.info("Entra auth: forbidden: %s", str(e))
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        logger.info("Entra auth: invalid token: %s", str(e))
        raise HTTPException(status_code=401, detail=str(e))

