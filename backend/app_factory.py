import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import auth_entra
from .core.caches import cleanup_export_artifacts_keep_latest_per_sup, cleanup_old_caches
from .routers import admin as admin_router
from .routers import auth as auth_router
from .routers import data as data_router
from .routers import debug as debug_router
from .routers import export as export_router
from .routers import favicon as favicon_router
from .routers import health as health_router
from .routers import lakehouse as lakehouse_router
from .routers import managers as managers_router
from .routers import optimize as optimize_router
from .services.access_control import parse_allocation_admin_emails
from .services.fabric_cache import seed_cache_from_repo
from .services.managers import warm_managers_cache_at_startup

logger = logging.getLogger("target_allocation")


def _warn_if_multi_worker() -> None:
    """
    store ทั้งหมดกันการแก้ทับกันด้วย threading.Lock ซึ่งเป็น lock ระดับโปรเซส
    หลาย worker = lock ไร้ผล = บั๊กที่แก้ไปแล้วกลับมาแบบเงียบ ๆ (ดู docs/CONCURRENCY.md)
    log อย่างเดียว ไม่ fail startup — IT เป็นเจ้าของ deploy
    """
    raw = (os.environ.get("WEB_CONCURRENCY") or "").strip()
    if not raw:
        return
    try:
        n = int(raw)
    except ValueError:
        return
    if n > 1:
        logger.error(
            "WEB_CONCURRENCY=%d (>1) — ไฟล์ใน data/ ไม่ปลอดภัยเมื่อรันหลายโปรเซส "
            "ผลกระจายอาจหายหรือคำนวณผิดแบบไม่มี error; ดู docs/CONCURRENCY.md",
            n,
        )


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app_: FastAPI):
        os.makedirs("data", exist_ok=True)
        # เติมแคชตั้งต้นก่อนอย่างอื่น — ถ้า Fabric ดึงไม่ได้และเครื่องนี้ยังไม่เคย
        # ดึงงวดนั้นสำเร็จ ราคาจะเป็น 0 ทั้งระบบแล้วทุกทีมเปิดงวดไม่ได้
        # เขียนเฉพาะไฟล์ที่ยังไม่มี ของที่ดึงสดมาได้จึงไม่ถูกแตะ
        try:
            n_seed = seed_cache_from_repo()
            if n_seed:
                logger.info("เติมแคชตั้งต้นจาก seed/cache: %d ไฟล์", n_seed)
        except Exception as e:
            logger.warning("เติมแคชตั้งต้นไม่สำเร็จ: %s", e)
        _warn_if_multi_worker()
        cleanup_old_caches(max_age_days=7)
        cleanup_export_artifacts_keep_latest_per_sup(keep_n=1)
        warm_managers_cache_at_startup()
        yield

    app = FastAPI(title="Target Allocation API", version="3.0", lifespan=lifespan)

    if auth_entra.auth_enabled():
        n_admin = len(parse_allocation_admin_emails())
        logger.info(
            "Entra login เปิด — สิทธิจาก user_access.json; "
            "ALLOCATION_ADMIN_EMAILS=%d entry สำหรับแอดมิน",
            n_admin,
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth_router.router)
    app.include_router(admin_router.router)
    app.include_router(favicon_router.router)
    app.include_router(managers_router.router)
    app.include_router(data_router.router)
    app.include_router(optimize_router.router)
    app.include_router(export_router.router)
    app.include_router(lakehouse_router.router)
    app.include_router(health_router.router)
    app.include_router(debug_router.router)

    return app

