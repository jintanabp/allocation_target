"""ให้ทั้งชุดเทสเขียนแคช Manager ลงไฟล์ชั่วคราว ไม่ใช่ data/managers_cache.json ของโฟลเดอร์ที่รัน

load_full_managers_payload / persist_managers_payload เขียนแคชนี้ทุกครั้งที่อ่านลำดับชั้น
ซึ่งเทสหลายไฟล์เรียกทางอ้อม (สิทธิ์, บัญชีสาธิต, ดูแทน ...) — ไล่ patch ทีละเทสพลาดง่าย
run_tests.py จับได้แล้วคืน exit 1 ทั้งที่ทุกเทสผ่าน CI จึงแดง (QA 21 ต.ค. 2026)
เทสที่ตั้ง MANAGERS_CACHE_FILE เองยังทำได้ตามเดิม (เก็บค่าเก่าแล้วคืนให้เอง)

install() ถูกเรียกจาก run_tests.py, tests/__init__.py และ tests/conftest.py คู่กับ _netguard
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile

_TMP_DIR: str | None = None


def install() -> None:
    global _TMP_DIR
    if _TMP_DIR is not None:
        return
    from backend.services import managers

    _TMP_DIR = tempfile.mkdtemp(prefix="alloc-tests-")
    atexit.register(shutil.rmtree, _TMP_DIR, True)
    managers.MANAGERS_CACHE_FILE = os.path.join(_TMP_DIR, "managers_cache.json")
