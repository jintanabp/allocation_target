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
            age = time.time() - os.path.getmtime(cache_path)
            if age < ttl:
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
    """เรียกหลัง import/repair user_access — rebuild hierarchy + cache"""
    payload = build_hierarchy_payload()
    persist_hierarchy(payload)
    persist_managers_payload(payload)
    return payload
