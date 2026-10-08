"""
แยกแบรนด์เป็นกลุ่มสินค้า — ค่าที่ผู้ใช้เลือก (รายคน) + ชื่อกลุ่มสินค้าจาก Fabric

ผู้ใช้ขอ (8 ต.ค. 2026): ใครจะใช้ก็ได้ ทั้งทีมเดียวและโหมดรวมภาค จึงเก็บ "รายคน" (อีเมล) ไม่ใช่รายทีม
— คนเดียวกันเปิดทีมไหน/รวมภาคก็เห็นการตั้งค่าเดียวกัน และจำข้ามงวด
ค่าที่ผู้ใช้ตั้งเก็บใน data/ เท่านั้น ห้ามเก็บใน config/ (deploy แบบ git ทับค่าเงียบ ๆ)

การตั้งค่านี้เปลี่ยนแค่ "หน่วยกลุ่ม" ตอนจัดกลุ่ม/กรอง/เลือกวิธีกระจาย — ไม่แตะเป้าหีบ คลัง หรือแถวที่ส่ง Target Sun
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time

from ..core.alloc_groups import norm_section
from datetime import datetime, timezone
from typing import Any

from ..core.atomic_io import atomic_write_json

logger = logging.getLogger("target_allocation")

_LOCK = threading.RLock()
_MAX_BRANDS = 200
_MAX_BRAND_LEN = 120
_SECTION_NAMES_MAX_AGE_SEC = 7 * 86400
# ดึงไม่สำเร็จแล้วเว้นช่วงก่อนลองใหม่ — กันทุกคำขอไปรอ Fabric ซ้ำตอน Fabric ล่ม
_SECTION_RETRY_AFTER_SEC = 10 * 60
_section_refresh_lock = threading.Lock()
_section_refresh_state = {"running": False, "last_attempt": 0.0}


def _repo_root() -> str:
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))


def prefs_path() -> str:
    raw = (os.environ.get("ALLOC_GROUP_PREFS_JSON_PATH") or "").strip()
    if raw:
        return os.path.normpath(os.path.abspath(raw))
    return os.path.join(_repo_root(), "data", "alloc_group_prefs.json")


def _norm_email(email: Any) -> str:
    return str(email or "").strip().lower()


def _read_all() -> dict[str, Any]:
    path = prefs_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError) as e:
        # อ่านไม่ได้ = ถือว่าไม่ได้เลือกแยก (พฤติกรรมเดิม) — ไม่ทำให้หน้าเว็บเปิดไม่ได้
        logger.warning("อ่าน %s ไม่ได้ — ถือว่าไม่ได้เลือกแยกแบรนด์: %s", path, e)
        return {}
    users = doc.get("users") if isinstance(doc, dict) else None
    return users if isinstance(users, dict) else {}


def get_split_brands(email: Any) -> list[str]:
    key = _norm_email(email)
    with _LOCK:
        row = _read_all().get(key) or {}
    brands = row.get("split_brands") if isinstance(row, dict) else None
    if not isinstance(brands, list):
        return []
    return sorted({str(b).strip() for b in brands if str(b or "").strip()})


def set_split_brands(email: Any, brands: list[Any]) -> list[str]:
    key = _norm_email(email)
    clean = sorted({str(b).strip() for b in (brands or []) if str(b or "").strip()})
    if len(clean) > _MAX_BRANDS:
        raise ValueError(f"เลือกได้ไม่เกิน {_MAX_BRANDS} แบรนด์")
    if any(len(b) > _MAX_BRAND_LEN for b in clean):
        raise ValueError("ชื่อแบรนด์ยาวเกินกำหนด")
    with _LOCK:
        users = _read_all()
        if clean:
            users[key] = {
                "split_brands": clean,
                "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            }
        else:
            users.pop(key, None)
        atomic_write_json(prefs_path(), {"users": users}, indent=2)
    return clean


# ── ชื่อกลุ่มสินค้า ─────────────────────────────────────────────

def _section_names_path() -> str:
    from .fabric_cache import cache_dir

    return os.path.join(cache_dir(), "section_names.json")


def _read_section_cache() -> tuple[dict[str, str], float]:
    path = _section_names_path()
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        names = doc.get("names") if isinstance(doc, dict) else None
        if isinstance(names, dict):
            return {str(k): str(v) for k, v in names.items()}, os.path.getmtime(path)
    except (OSError, ValueError):
        pass
    return {}, 0.0


def refresh_section_names() -> dict[str, str]:
    """ดึงชื่อกลุ่มจาก Fabric แล้วเขียนแคช — ล้มเหลวคืน {} (ผู้เรียกใช้แคชเดิมต่อ)"""
    try:
        from ..fabric_dax_connector import FabricDAXConnector

        raw = FabricDAXConnector().get_section_names()
    except Exception as e:
        logger.warning("ดึงชื่อกลุ่มสินค้าจาก Fabric ไม่ได้ — ใช้แคชเดิม: %s", e)
        return {}
    fresh = {norm_section(k): str(v) for k, v in (raw or {}).items() if norm_section(k)}
    if fresh:
        try:
            atomic_write_json(_section_names_path(), {"names": fresh})
        except OSError as e:
            logger.warning("เขียนแคชชื่อกลุ่มสินค้าไม่ได้: %s", e)
    return fresh


def _refresh_in_background() -> None:
    def _run() -> None:
        try:
            refresh_section_names()
        finally:
            with _section_refresh_lock:
                _section_refresh_state["running"] = False

    with _section_refresh_lock:
        now = time.time()
        if _section_refresh_state["running"] or now - _section_refresh_state["last_attempt"] < _SECTION_RETRY_AFTER_SEC:
            return
        _section_refresh_state["running"] = True
        _section_refresh_state["last_attempt"] = now
    threading.Thread(target=_run, name="section-names-refresh", daemon=True).start()


def section_names() -> dict[str, str]:
    """
    รหัสกลุ่ม → ชื่อไทย · คืนจากแคชทันทีเสมอ (เก่าก็ใช้) ไม่รอ Fabric — ตรวจซ้ำ 8 ต.ค. 2026:
    เดิมคำขอนี้รอ Fabric ได้นานกว่าที่หน้าเว็บรอ (20 วิ) แล้วการแยกกลุ่มที่ผู้ใช้ตั้งไว้ปิดเงียบ ๆ
    แคชหาย/เกิน 7 วัน = ดึงใหม่เบื้องหลัง (ล้มเหลวเว้น 10 นาที) · ยังไม่มีชื่อ = หน้าเว็บแสดง "กลุ่ม <รหัส>"
    """
    cached, mtime = _read_section_cache()
    if not cached or time.time() - mtime >= _SECTION_NAMES_MAX_AGE_SEC:
        _refresh_in_background()
    return cached
