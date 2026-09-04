"""
main.py — Target Allocation API (v3 — Production)
────────────────────────────────────────────────────────────────────
Uvicorn entrypoint: `uvicorn backend.main:app`
"""

import logging
import os
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import HTMLResponse
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


@app.get("/doc/{name}", response_class=HTMLResponse)
def read_doc(name: str):
    """อ่านเอกสารใน docs/ เป็นหน้า HTML แทนไฟล์ .md ดิบ

    /docs/<ชื่อ>.md ที่ mount ไว้ข้างล่างเสิร์ฟเป็น text/plain เบราว์เซอร์จึงโชว์
    '#' กับ '|' ดิบ ๆ · เส้นทางนี้แปลงเป็น HTML ให้อ่านได้ ตัวไฟล์ดิบยังโหลดได้เหมือนเดิม

    ชื่อไฟล์ต้องตรงกับ .md ที่มีอยู่จริงใน docs/ เท่านั้น (เทียบกับรายการที่ scan มา)
    จึงไม่มีทางหลุดออกไปนอกโฟลเดอร์ได้แม้จะใส่ ../ มา
    """
    from .services.markdown_view import render_markdown_page

    if not _DOCS_DIR.is_dir():
        raise HTTPException(status_code=404, detail="ไม่มีโฟลเดอร์เอกสารบนเครื่องนี้")
    stem = str(name or "").strip()
    if stem.lower().endswith(".md"):
        stem = stem[:-3]
    match = next((p for p in sorted(_DOCS_DIR.glob("*.md")) if p.stem == stem), None)
    if match is None:
        raise HTTPException(status_code=404, detail=f"ไม่พบเอกสาร '{stem}'")
    return HTMLResponse(render_markdown_page(match.read_text(encoding="utf-8"), match.stem))


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

