"""
main.py — Target Allocation API (v3 — Production)
────────────────────────────────────────────────────────────────────
Uvicorn entrypoint: `uvicorn backend.main:app`
"""

import logging
import os
from pathlib import Path

from fastapi.staticfiles import StaticFiles
from starlette.staticfiles import StaticFiles as StarletteStaticFiles
from starlette.types import Scope

from .app_factory import create_app
from .load_env import load_project_dotenv


load_project_dotenv()

# ── Logging ──────────────────────────────────────────────
os.makedirs("data", exist_ok=True) 
try:
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    # If reconfigure is not supported (some embedded runtimes), keep default encoding.
    pass
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("data/app.log", encoding="utf-8"),
    ],
)


app = create_app()


class _NoCacheFrontendStaticFiles(StarletteStaticFiles):
    """ห้าเบราว์เซอร์ cache JS/CSS เก่าหลัง Run_Local — มักทำให้ Step 2 ยังโชว์คนไม่มีเป้า"""

    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        if path.endswith((".js", ".css", ".html")) or path in ("", "index.html"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        return response


_DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"
if _DOCS_DIR.is_dir():
    app.mount("/docs", StaticFiles(directory=str(_DOCS_DIR)), name="docs")

# เสิร์ฟ frontend จาก http://127.0.0.1:8000/ (ลงท้าย — ให้ API routes ถูกจับก่อน)
_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if _FRONTEND_DIR.is_dir():
    app.mount(
        "/",
        _NoCacheFrontendStaticFiles(directory=str(_FRONTEND_DIR), html=True),
        name="frontend",
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)

