from fastapi import APIRouter
from fastapi.responses import Response

router = APIRouter(include_in_schema=False)


@router.get("/favicon.ico", include_in_schema=False)
def favicon_placeholder():
    """ลด 404 ใน log — เบราว์เซอร์ขอ /favicon.ico อัตโนมัติ"""
    return Response(status_code=204)

