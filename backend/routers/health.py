import os
import subprocess
from datetime import datetime, timezone

from fastapi import APIRouter

from .. import auth_entra
from ..core.constants import VALID_STRATEGIES, debug_endpoints_enabled
from ..core.runtime_checks import runtime_status

router = APIRouter(tags=["health"])


def _repo_root() -> str:
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _count_data_files(prefix: str) -> int:
    try:
        return sum(1 for f in os.listdir("data") if f.startswith(prefix))
    except OSError:
        return 0


def _git_short_hash() -> str:
    env = (os.environ.get("BUILD_VERSION") or os.environ.get("GIT_COMMIT") or "").strip()
    if env:
        return env[:12]
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_repo_root(),
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        return out.decode().strip()[:12] or "unknown"
    except Exception:
        return "dev"


def _public_runtime_status() -> dict:
    st = runtime_status()
    dd = st.get("data_dir")
    if isinstance(dd, dict):
        st["data_dir"] = {"same": dd.get("same")}
    return st


@router.get("/health")
def health():
    return {
        "status": "ok",
        "entra_auth_required": auth_entra.auth_enabled(),
        "managers_source": "GET /managers (ดึง SuperCode จาก Dim_Salesman ใน Fabric)",
        "valid_strategies": list(VALID_STRATEGIES),
        "debug_endpoints_enabled": debug_endpoints_enabled(),
        "build": {
            "version": _git_short_hash(),
            "built_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        },
        # ตรวจตอนเปิดแอป (ผลตรวจ §5.2): data_dir.same=false = data/ แยกสองที่ ·
        # single_process=false = มีหลายโปรเซสใช้ข้อมูลชุดเดียวกัน (ล็อกในแอปใช้ไม่ได้)
        # /health ไม่ต้องล็อกอิน — ไม่ส่ง path เต็มของ server (ผลตรวจ 7 ต.ค. 2026 ค) · path เต็มอยู่ใน log ตอนสตาร์ท
        "runtime": _public_runtime_status(),
        "files": {
            # ไฟล์เป้าแยกราย sup แล้ว — รายงานเป็นจำนวนไฟล์ ส่วน legacy_* คือไฟล์ global เก่าที่ยังค้าง
            "target_boxes_files": _count_data_files("target_boxes_"),
            "target_sun_files": _count_data_files("target_sun_"),
            "legacy_target_boxes.csv": os.path.exists("data/target_boxes.csv"),
            "legacy_target_sun.csv": os.path.exists("data/target_sun.csv"),
        },
    }


@router.get("/health/build")
def health_build():
    return {"version": _git_short_hash()}

