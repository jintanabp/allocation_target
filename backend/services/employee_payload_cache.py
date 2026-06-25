"""In-memory-safe JSON cache สำหรับ load_employees_payload — ลดการยิง DAX ซ้ำในงวดเดียวกัน"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from ..core.paths import employee_payload_cache_path, safe_id

logger = logging.getLogger("target_allocation")

_META_KEYS = frozenset({"data_from_cache", "data_cached_at"})


def employee_payload_cache_ttl_sec() -> int:
    """
    อายุ cache (วินาที). ค่าเริ่มต้น 900 (15 นาที).
    ตั้ง 0 หรือติดลบ = ปิดการอ่าน/เขียน cache JSON นี้ (ยังยิง DAX ทุกครั้ง).
    """
    raw = (os.environ.get("EMPLOYEE_PAYLOAD_CACHE_TTL_SEC") or "900").strip()
    try:
        return int(raw)
    except ValueError:
        return 900


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
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("payload cache read failed %s: %s", path, e)
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
        "sup_id": str(sup_id).strip().upper(),
        "target_month": int(target_month),
        "target_year": int(target_year),
        "payload": _strip_meta(payload),
    }
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
        os.replace(tmp, path)
        logger.info("payload cache saved %s", path)
    except OSError as e:
        logger.warning("payload cache write failed %s: %s", path, e)
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass


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
