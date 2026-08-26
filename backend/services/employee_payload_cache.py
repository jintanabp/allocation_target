"""In-memory-safe JSON cache สำหรับ load_employees_payload — ลดการยิง DAX ซ้ำในงวดเดียวกัน"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from ..core.atomic_io import atomic_write_json, read_locked
from ..core.paths import employee_payload_cache_path, safe_id

logger = logging.getLogger("target_allocation")

_META_KEYS = frozenset({"data_from_cache", "data_cached_at"})

# เวอร์ชันของ "วิธีคิดราคา" ที่ payload ก้อนนี้ถูกสร้างมา — ต้องบวกขึ้นทุกครั้งที่
# ตรรกะราคาเปลี่ยน แล้วก้อนที่เขียนด้วยตรรกะเก่าจะถูกทิ้งทันทีที่อ่าน
#
# ทำไมต้องมี: แคชนี้เก็บ "ราคาที่คิดเสร็จแล้ว" (skus[].price_per_box และ
# employees[].target_sun) ตอนแยกราคาตามหน่วยขาย แคชสินค้าถูกทิ้งด้วยการเช็ค schema
# ทันที แต่แคชก้อนนี้ซึ่งถือตัวเลขที่ผิดจริง ๆ กลับรอ TTL — หลัง deploy อีกหนึ่งชั่วโมง
# ทีมรถเงินสดจึงยังได้เป้าที่คิดด้วยราคาเครดิตอยู่
#   1 = ก่อนแยกราคาตามหน่วยขาย
#   2 = แยกราคาตามหน่วยขาย + ประทับ sales_unit ลง payload
PRICE_LOGIC_VERSION = 2


def employee_payload_cache_ttl_sec() -> int:
    """
    อายุ cache (วินาที). ค่าเริ่มต้น 3600 (1 ชั่วโมง).
    ตั้ง 0 หรือติดลบ = ปิดการอ่าน/เขียน cache JSON นี้ (ยังยิง DAX ทุกครั้ง).
    """
    raw = (os.environ.get("EMPLOYEE_PAYLOAD_CACHE_TTL_SEC") or "3600").strip()
    try:
        return int(raw)
    except ValueError:
        return 3600


def _cache_enabled() -> bool:
    return employee_payload_cache_ttl_sec() > 0


def _strip_meta(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if k not in _META_KEYS}


def read_cached_employee_payload(
    sup_id: str,
    target_month: int,
    target_year: int,
) -> dict[str, Any] | None:
    """คืน payload พร้อม data_from_cache / data_cached_at ถ้ายังไม่หมดอายุ"""
    if not _cache_enabled():
        return None

    path = employee_payload_cache_path(sup_id, target_month, target_year)
    if not os.path.isfile(path):
        return None

    try:
        # ต้องครอบ read_locked: บน Windows ถ้า reader ถือ handle ค้างตอน writer เรียก
        # os.replace ตัว writer จะพัง PermissionError (ดู backend/core/atomic_io.py)
        # โหลดรวมภาคยิงขนานกันแล้ว เคสนี้จึงเกิดได้จริง
        with read_locked(path), open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("payload cache read failed %s: %s", path, e)
        return None

    doc_version = int(doc.get("price_logic_version") or 0)
    if doc_version != PRICE_LOGIC_VERSION:
        logger.info(
            "payload cache ทิ้ง %s — สร้างด้วยตรรกะราคารุ่น %d (ปัจจุบัน %d)",
            path, doc_version, PRICE_LOGIC_VERSION,
        )
        return None

    cached_at_raw = str(doc.get("cached_at") or "").strip()
    if not cached_at_raw:
        return None

    try:
        cached_at = datetime.fromisoformat(cached_at_raw.replace("Z", "+00:00"))
        if cached_at.tzinfo is None:
            cached_at = cached_at.replace(tzinfo=timezone.utc)
    except ValueError:
        return None

    age_sec = (datetime.now(timezone.utc) - cached_at.astimezone(timezone.utc)).total_seconds()
    if age_sec > employee_payload_cache_ttl_sec():
        logger.info(
            "payload cache expired %s (age %.0fs > TTL %ds)",
            path,
            age_sec,
            employee_payload_cache_ttl_sec(),
        )
        return None

    payload = doc.get("payload")
    if not isinstance(payload, dict):
        return None

    out = dict(payload)
    out["data_from_cache"] = True
    out["data_cached_at"] = cached_at_raw
    logger.info(
        "payload cache hit %s (age %.0fs, %d emp, %d sku)",
        path,
        age_sec,
        len(out.get("employees") or []),
        len(out.get("skus") or []),
    )
    return out


def write_cached_employee_payload(
    sup_id: str,
    target_month: int,
    target_year: int,
    payload: dict[str, Any],
) -> None:
    if not _cache_enabled():
        return

    path = employee_payload_cache_path(sup_id, target_month, target_year)
    os.makedirs(os.path.dirname(path) or "data", exist_ok=True)
    cached_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    doc = {
        "cached_at": cached_at,
        "price_logic_version": PRICE_LOGIC_VERSION,
        "sup_id": str(sup_id).strip().upper(),
        "target_month": int(target_month),
        "target_year": int(target_year),
        "payload": _strip_meta(payload),
    }
    try:
        # ใช้ atomic_write_json แทน .tmp ชื่อตายตัว + os.replace ดิบ
        # ชื่อ .tmp ตายตัวทำให้สอง writer ของ (sup, งวด) เดียวกันชนกัน
        # และ os.replace ดิบบน Windows พังเป็น PermissionError เมื่อมี reader ถือ handle
        atomic_write_json(path, doc, ensure_ascii=False)
        logger.info("payload cache saved %s", path)
    except OSError as e:
        logger.warning("payload cache write failed %s: %s", path, e)


def invalidate_employee_payload_cache(
    sup_id: str | None = None,
    target_month: int | None = None,
    target_year: int | None = None,
) -> int:
    """
    ลบไฟล์ payload cache ที่ตรงเงื่อนไข (คืนจำนวนไฟล์ที่ลบ).
    ไม่ระบุ sup_id = ลบทุกซุปในงวดนั้น; ไม่ระบุงวด = ลบทุกงวดของซุปนั้น.
    """
    data_dir = "data"
    if not os.path.isdir(data_dir):
        return 0

    prefix = "payload_cache_"
    removed = 0
    sid_safe = None
    if sup_id is not None:
        sid_safe = safe_id(str(sup_id).strip().upper())
    for name in os.listdir(data_dir):
        if not name.startswith(prefix) or not name.endswith(".json"):
            continue
        if sid_safe is not None and not name.startswith(f"payload_cache_{sid_safe}_"):
            continue
        if target_year is not None and target_month is not None:
            suffix = f"_{int(target_year)}_{int(target_month):02d}.json"
            if not name.endswith(suffix):
                continue
        try:
            os.remove(os.path.join(data_dir, name))
            removed += 1
        except OSError as e:
            logger.warning("payload cache delete failed %s: %s", name, e)
    if removed:
        logger.info("payload cache invalidated: %d file(s)", removed)
    return removed
