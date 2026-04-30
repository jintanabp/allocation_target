import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import auth_entra
from .core.caches import cleanup_old_caches
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
from .services.managers import warm_managers_cache_at_startup

logger = logging.getLogger("target_allocation")


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app_: FastAPI):
        os.makedirs("data", exist_ok=True)
        cleanup_old_caches(max_age_days=7)
        warm_managers_cache_at_startup()
        yield

    app = FastAPI(title="Target Allocation API", version="3.0", lifespan=lifespan)

    if auth_entra.auth_enabled():
        n_admin = len(parse_allocation_admin_emails())
        logger.info(
            "Entra login เปิด — สิทธิทั่วไปจาก ACC_USER_CONTROL; "
            "ALLOCATION_ADMIN_EMAILS=%d entry สำหรับเข้าถึงทุกรหัส",
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
    app.include_router(favicon_router.router)
    app.include_router(managers_router.router)
    app.include_router(data_router.router)
    app.include_router(optimize_router.router)
    app.include_router(export_router.router)
    app.include_router(lakehouse_router.router)
    app.include_router(health_router.router)
    app.include_router(debug_router.router)

    return app

