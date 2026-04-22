from fastapi import APIRouter

from .. import auth_entra

router = APIRouter(tags=["auth"])


@router.get("/auth/config")
def auth_public_config():
    """ค่าสาธารณะสำหรับ MSAL (ไม่มี secret)"""
    return auth_entra.spa_config_payload()

