import json
import logging
import os
import threading
import time

from ..core.atomic_io import atomic_write_json, read_locked
from .access_hierarchy import load_hierarchy_payload, persist_hierarchy, build_hierarchy_payload

logger = logging.getLogger("target_allocation")

MANAGERS_CACHE_FILE = "data/managers_cache.json"

# ไฟล์นี้ถูกอ่านทุก request ที่ผ่าน auth (access_control.build_user_access_context)
# เดิมเขียนด้วย open(...,"w") ตรง ๆ ไม่มี lock → reader อ่านได้ไฟล์ครึ่งใบตอนมีคน login พร้อมกัน
_CACHE_LOCK = threading.Lock()


def _hierarchy_mtime() -> float:
    """เวลาแก้ไขล่าสุดของไฟล์ต้นทาง (0 = ไม่มีไฟล์ ให้แคชใช้ต่อได้ตามเดิม)"""
    try:
        from .access_hierarchy import access_hierarchy_json_path

        return os.path.getmtime(access_hierarchy_json_path())
    except OSError:
        return 0.0


def load_full_managers_payload() -> dict:
    """
    โหลด hierarchy จาก config/access_hierarchy.json (หรือ rebuild จาก user_access)
    ใช้โดย GET /managers และ access_control
    """
    os.makedirs("data", exist_ok=True)
    cache_path = MANAGERS_CACHE_FILE
    ttl = int(os.environ.get("MANAGERS_CACHE_TTL_SEC", "86400"))
    if ttl > 0 and os.path.exists(cache_path):
        try:
            cache_mtime = os.path.getmtime(cache_path)
            age = time.time() - cache_mtime
            # แคชต้องไม่เก่ากว่าไฟล์ต้นทาง — ไม่งั้น deploy ที่แก้ลำดับชั้นแล้ว
            # หน้าจอยังเหมือนเดิมได้อีกเป็นวัน โดยไม่มีอะไรบอกว่าทำไม
            if age < ttl and cache_mtime >= _hierarchy_mtime():
                # ต้องถือ lock เดียวกับตอนเขียน ไม่งั้นบน Windows การ replace จะพัง
                # เพราะ reader ถือ handle ค้าง (ดู docs/CONCURRENCY.md)
                with read_locked(cache_path), open(cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and data.get("by_manager") is not None:
                    return data
        except Exception as e:
            logger.warning("managers cache fast read: %s", e)

    payload = load_hierarchy_payload()
    try:
        persist_managers_payload(payload)
    except Exception as e:
        logger.warning("managers cache write failed: %s", e)
    return payload


def persist_managers_payload(payload: dict) -> None:
    os.makedirs("data", exist_ok=True)
    with _CACHE_LOCK:
        atomic_write_json(MANAGERS_CACHE_FILE, payload)


def warm_managers_cache_at_startup() -> None:
    """Preload hierarchy ตอน startup — อ่านจาก access_hierarchy / rebuild จาก roster"""
    try:
        payload = load_full_managers_payload()
        logger.info(
            "managers cache warmed at startup: %d managers, %d supervisors (excel_roster)",
            len(payload.get("manager_codes") or []),
            len(payload.get("supervisors") or []),
        )
    except Exception as e:
        logger.warning("managers cache warm at startup failed: %s", e)


def rebuild_managers_from_roster() -> dict:
    """
    เรียกหลัง import/repair user_access — rebuild hierarchy + cache

    ผลตรวจ §5.1-5: ทำทั้งหมดใต้ lock ของ user_access (RLock — build อ่าน read_rows ซ้ำได้)
    เดิมคำนวณนอก lock แอดมินสองคนแก้ผู้ใช้พร้อมกัน รอบที่อ่านรายชื่อเก่ากว่า
    เขียนทีหลังได้ แล้วลำดับสิทธิ์ถอยไปไม่รู้จักผู้ใช้ที่เพิ่งเพิ่ม · พอถือ lock เดียวกับ
    ตัวแก้รายชื่อ รอบที่เขียนทีหลังสุดจะอ่านรายชื่อล่าสุดเสมอ

    แคช managers_cache.json: persist_hierarchy เขียนให้แล้ว (atomic) — เขียนซ้ำที่นี่เฉพาะ
    ตอน MANAGERS_CACHE_FILE (path สัมพัทธ์กับ cwd) ชี้ไปคนละไฟล์กับของ persist_hierarchy
    ไม่งั้นไฟล์เดียวถูกเขียนสองรอบใต้ lock คนละตัว
    """
    from .access_hierarchy import _repo_root as _hier_repo_root
    from .user_access_store import _STORE_LOCK as _USER_ACCESS_LOCK

    with _USER_ACCESS_LOCK:
        payload = build_hierarchy_payload()
        persist_hierarchy(payload)
        hier_cache = os.path.join(_hier_repo_root(), "data", "managers_cache.json")
        if os.path.normcase(os.path.abspath(MANAGERS_CACHE_FILE)) != os.path.normcase(
            os.path.abspath(hier_cache)
        ):
            persist_managers_payload(payload)
    return payload
