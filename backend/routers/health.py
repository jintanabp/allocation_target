import os
import subprocess
from datetime import datetime, timezone

from fastapi import APIRouter

from .. import auth_entra
from ..core.constants import VALID_STRATEGIES, debug_endpoints_enabled

router = APIRouter(tags=["health"])


def _repo_root() -> str:
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))


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
        "files": {
            "target_boxes.csv": os.path.exists("data/target_boxes.csv"),
            "target_sun.csv": os.path.exists("data/target_sun.csv"),
        },
    }


@router.get("/health/build")
def health_build():
    return {"version": _git_short_hash()}

