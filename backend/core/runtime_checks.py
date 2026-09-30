"""
ตรวจสภาพแวดล้อมตอนเปิดแอป — log อย่างเดียว ไม่ทำให้แอปเปิดไม่ขึ้น (IT เป็นเจ้าของ deploy)

1. path สองระบบ (ผลตรวจ §5.2): core/paths.py ใช้ "data/..." ที่อิงโฟลเดอร์ที่รันโปรแกรม
   ส่วน store อื่น (ผลกระจาย กติกา แจ้งเตือน ฯลฯ) ใช้ data/ ของ repo · ถ้ารันจากโฟลเดอร์อื่น
   ไฟล์จะแยกไปอยู่สองที่ รวม data/baselines/ ที่สร้างใหม่ไม่ได้ — ตรวจแล้วเตือน ไม่ย้าย path
   (ย้ายเองจะทำให้ไฟล์ที่อยู่อีกที่หายไปจากมุมมองของแอป)
2. หลายโปรเซสใช้ข้อมูลชุดเดียวกัน (§5.2): ล็อกทุกตัวในแอปเป็นล็อกระดับโปรเซส เดิมตรวจแค่
   WEB_CONCURRENCY จึงจับ `uvicorn --workers 2` หรือเปิด server สองตัวไม่ได้ — ตอนนี้แต่ละ
   โปรเซสล็อกไฟล์ data/.app_process.lock ไว้ตลอดอายุ ล็อกไม่ได้ = มีอีกโปรเซสใช้อยู่
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("target_allocation")

_LOCK_FILE_NAME = ".app_process.lock"
_lock_fh = None  # ถือไว้ตลอดอายุโปรเซส — ปิดเมื่อไหร่ล็อกหลุด
_status: dict[str, Any] = {"single_process": None, "data_dir": None}


def _repo_root() -> str:
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))


def data_dir_status() -> dict[str, Any]:
    cwd_data = os.path.normcase(os.path.abspath("data"))
    repo_data = os.path.normcase(os.path.join(_repo_root(), "data"))
    return {"cwd_data": os.path.abspath("data"), "repo_data": os.path.join(_repo_root(), "data"),
            "same": cwd_data == repo_data}


def _try_lock(fh) -> bool:
    try:
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def acquire_single_process_lock(data_dir: str = "data") -> bool | None:
    """True = เป็นโปรเซสเดียว · False = มีโปรเซสอื่นถือล็อกอยู่ · None = ตรวจไม่ได้"""
    global _lock_fh
    if _lock_fh is not None:
        return True
    try:
        os.makedirs(data_dir, exist_ok=True)
        fh = open(os.path.join(data_dir, _LOCK_FILE_NAME), "a+")
    except OSError as e:
        logger.warning("ตรวจจำนวนโปรเซสไม่ได้: %s", e)
        return None
    if _try_lock(fh):
        _lock_fh = fh
        return True
    fh.close()
    return False


def run_startup_checks() -> dict[str, Any]:
    dd = data_dir_status()
    _status["data_dir"] = dd
    if not dd["same"]:
        logger.error(
            "โฟลเดอร์ data/ มีสองที่: ที่รันโปรแกรม %s กับของ repo %s — แคช/เป้าตั้งต้นกับผลกระจาย/กติกา "
            "จะแยกกันอยู่ ให้รันแอปจากโฟลเดอร์ repo (ผลตรวจ §5.2)",
            dd["cwd_data"], dd["repo_data"],
        )
    single = acquire_single_process_lock()
    _status["single_process"] = single
    if single is False:
        logger.error(
            "มีอีกโปรเซสใช้โฟลเดอร์ data/ ชุดเดียวกันอยู่ (เช่น uvicorn --workers > 1 หรือเปิด server สองตัว) "
            "— ล็อกในแอปกันได้แค่ในโปรเซสเดียว ผลกระจาย/กติกาอาจหายแบบไม่มี error ดู docs/CONCURRENCY.md",
        )
    return dict(_status)


def runtime_status() -> dict[str, Any]:
    return dict(_status)
