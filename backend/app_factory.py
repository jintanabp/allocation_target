import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from . import auth_entra
from .core.caches import cleanup_export_artifacts_keep_latest_per_sup, cleanup_old_caches
from .core.runtime_checks import run_startup_checks
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
from .services.nightly_check import start_scheduler as start_nightly_scheduler

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


def _warn_if_ssl_verification_off() -> None:
    """
    TARGETSUN_*_VERIFY_SSL=0 ปิดการยืนยันใบรับรอง — ใช้ได้เฉพาะเครือข่ายทดสอบ (ผลตรวจ §2.9)
    log อย่างเดียว ไม่ fail startup (IT เป็นเจ้าของ .env บน server)
    """
    off = [
        name
        for name in ("TARGETSUN_IMPORT_VERIFY_SSL", "TARGETSUN_READ_VERIFY_SSL")
        if (os.environ.get(name) or "1").strip().lower() in ("0", "false", "no", "off")
    ]
    if off:
        logger.error(
            "ปิดการยืนยันใบรับรอง HTTPS ของ Target Sun อยู่ (%s) — ใช้ได้เฉพาะเครือข่ายทดสอบ",
            ", ".join(off),
        )


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app_: FastAPI):
        # ล็อกอินต้องไม่ถูกปิดเองเงียบ ๆ เมื่อ config ไม่ครบ — ไม่ครบ = ไม่สตาร์ท (§1.4)
        auth_entra.check_auth_config_at_startup()
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
        run_startup_checks()
        _warn_if_ssl_verification_off()
        cleanup_old_caches(max_age_days=7)
        cleanup_export_artifacts_keep_latest_per_sup(keep_n=1)
        warm_managers_cache_at_startup()
        # ตรวจ Target Sun รายคืน (F3) — thread ตื่นทุก 5 นาที ทำงานเฉพาะเมื่อแอดมินเปิดไว้ (ค่าตั้งต้นปิด)
        start_nightly_scheduler()
        yield

    app = FastAPI(title="Target Allocation API", version="3.0", lifespan=lifespan)

    if auth_entra.auth_enabled():
        n_admin = len(parse_allocation_admin_emails())
        logger.info(
            "Entra login เปิด — สิทธิจาก user_access.json; "
            "ALLOCATION_ADMIN_EMAILS=%d entry สำหรับแอดมิน",
            n_admin,
        )

    @app.middleware("http")
    async def _local_only_when_login_off(request, call_next):
        # ล็อกอินปิด = ทุกคนเป็น dev → รับเฉพาะคำขอจากเครื่องนี้โดยตรง (7.9)
        # server ที่ตั้ง Entra ครบ (auth_enabled) ไม่ผ่านเงื่อนไขนี้เลย — ไม่กระทบ ไม่ต้องแก้ .env
        if not auth_entra.auth_enabled() and not auth_entra.request_allowed_without_login(
            request.client.host if request.client else None, request.headers
        ):
            logger.error(
                "ปฏิเสธคำขอจาก %s: ล็อกอินปิดอยู่ ใช้ได้เฉพาะจากเครื่องนี้โดยตรง (ไม่ผ่าน proxy)",
                request.client.host if request.client else "?",
            )
            return JSONResponse(
                status_code=403,
                content={
                    "detail": "ระบบนี้ปิดการล็อกอินอยู่ (โหมดเครื่องพัฒนา) — เปิดได้เฉพาะจากเครื่อง server เอง "
                    "ถ้าเห็นข้อความนี้บน server จริง แจ้ง dev ให้ตั้งค่าล็อกอิน Entra"
                },
            )
        return await call_next(request)

    @app.exception_handler(Exception)
    async def _unhandled_error_to_usage_log(request, exc):
        """
        error ที่ไม่ได้ตั้งใจ (500) ต้องไปถึงหน้า log ของแอดมิน ไม่ใช่แค่ data/app.log ที่ไม่มีหน้าดู
        (ผลตรวจ 7 ต.ค. 2026 ง) · ผู้ใช้เห็นข้อความไทย + รหัสอ้างอิงไว้แจ้ง dev · ไม่ส่ง traceback กลับ
        """
        import uuid as _uuid

        ref = _uuid.uuid4().hex[:8]
        logger.exception("unhandled error ref=%s %s %s", ref, request.method, request.url.path)
        try:
            from .services.usage_log_store import append_log

            qp = request.query_params

            def _int(v):
                try:
                    return int(v) if v not in (None, "") else None
                except (TypeError, ValueError):
                    return None

            append_log(
                level="error",
                sup_id=str(qp.get("sup_id") or "").strip().upper(),
                action="server_error",
                message=f"ระบบขัดข้องโดยไม่คาดคิด ({type(exc).__name__}) ที่ {request.method} {request.url.path}",
                detail=f"ref={ref} · {str(exc)[:300]}",
                target_month=_int(qp.get("target_month")),
                target_year=_int(qp.get("target_year")),
                context={"ref": ref, "path": request.url.path, "method": request.method,
                         "error_type": type(exc).__name__},
            )
        except Exception:
            logger.exception("เขียน log ของ error ref=%s ไม่สำเร็จ", ref)
        return JSONResponse(
            status_code=500,
            content={"detail": f"ระบบขัดข้องชั่วคราว กรุณาลองใหม่อีกครั้ง — ถ้ายังไม่ได้ แจ้งผู้ดูแลระบบพร้อมรหัสอ้างอิง {ref}"},
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

