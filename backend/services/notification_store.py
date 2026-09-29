"""
กล่องแจ้งเตือนในแอป — ส่งถึงคนที่ต้องรู้เรื่องเมื่อการส่ง Target Sun ผิดปกติ

ที่มา (ผู้ใช้ตัดสิน 29 ก.ย. 2026): นับแถวใน Target Sun ก่อนและหลังส่ง ต้องได้
"หลังส่ง = ก่อนส่ง + คู่ใหม่ในไฟล์" ต่างแม้แถวเดียว หรือตรวจไม่ได้ ต้องแจ้ง
  - คนที่กดส่ง
  - เจ้าของ SL ของทีมนั้น (ส่งรวมภาค = เจ้าของทุก SL ในรอบ)
  - dev ทุกคน
  - แอดมินที่ขอบเขตครอบทีมนั้น

ทำไมเป็นกล่องในแอป ไม่ใช่อีเมล: ระบบส่งอีเมลไม่ได้ (ต้องขอ Mail.Send จาก IT และ
ตั้งค่าบน server) ผู้ใช้เลือกกล่องในแอปเอง

หนึ่งเหตุการณ์ = หนึ่งรายการ มีรายชื่อผู้รับ และจดว่าใครกดรับทราบแล้ว
(ไม่แตกเป็นรายการต่อคน — คนหนึ่งรับทราบแล้วอีกคนยังต้องเห็นอยู่)

ทำไมอยู่ใต้ data/ ไม่ใช่ config/: ไฟล์ใน config/ ถูก git pull ทับตอน deploy
และมีอีเมลคนอยู่ข้างใน ไม่ควรขึ้น git
ไฟล์พังต้องไม่ทำให้ใครทำงานไม่ได้ — อ่านไม่ออกให้ถือว่าว่าง แล้ว log error ไว้
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from ..core.atomic_io import atomic_write_json, read_locked
from .user_access_store import normalized_email

logger = logging.getLogger("target_allocation")

_STORE_LOCK = threading.RLock()

#: เก็บกี่รายการ — เกินแล้วตัดของที่ทุกคนรับทราบแล้วและเก่าที่สุดก่อน
MAX_RECORDS = 3000


def _repo_root() -> str:
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))


def notifications_json_path() -> str:
    # ตัวแปรนี้มีไว้ให้เทสต์ชี้ไปโฟลเดอร์ชั่วคราว — บน server ไม่ต้องตั้ง
    raw = (os.environ.get("NOTIFICATIONS_DIR") or "").strip()
    base = os.path.normpath(os.path.abspath(raw)) if raw else os.path.join(_repo_root(), "data", "notifications")
    return os.path.join(base, "notifications.json")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_unlocked() -> list[dict[str, Any]]:
    path = notifications_json_path()
    if not os.path.isfile(path):
        return []
    try:
        with read_locked(path):
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        logger.error("อ่านกล่องแจ้งเตือนไม่ได้ (%s) — ถือว่าว่าง", e)
        return []
    items = data.get("items") if isinstance(data, dict) else None
    return [x for x in (items or []) if isinstance(x, dict)]


def _write_unlocked(items: list[dict[str, Any]]) -> None:
    if len(items) > MAX_RECORDS:
        # ตัดของที่ทุกคนรับทราบแล้วก่อน (เก่าสุดก่อน) ยังเกินค่อยตัดของเก่าจริง ๆ
        done = [i for i, x in enumerate(items) if set(x.get("recipients") or []) <= set(x.get("acked_by") or {})]
        drop = set(done[: len(items) - MAX_RECORDS])
        items = [x for i, x in enumerate(items) if i not in drop]
        items = items[-MAX_RECORDS:]
    atomic_write_json(notifications_json_path(), {"items": items}, indent=1)


def create(
    *,
    kind: str,
    title: str,
    message: str,
    recipients: Iterable[str],
    sup_id: str = "",
    context: dict[str, Any] | None = None,
    created_by: str = "",
) -> dict[str, Any] | None:
    """เพิ่มหนึ่งรายการ — ไม่มีผู้รับเลยก็ไม่เก็บ (คืน None)"""
    rcpt = sorted({normalized_email(e) for e in recipients if "@" in normalized_email(e)})
    if not rcpt:
        return None
    item = {
        "id": uuid.uuid4().hex,
        "kind": str(kind),
        "title": str(title)[:200],
        "message": str(message)[:2000],
        "sup_id": str(sup_id or "").strip().upper(),
        "context": dict(context or {}),
        "recipients": rcpt,
        "acked_by": {},
        "created_by": normalized_email(created_by),
        "created_at": _now_iso(),
    }
    with _STORE_LOCK:
        items = _read_unlocked()
        items.append(item)
        _write_unlocked(items)
    return item


def list_for(email: str, *, include_acked: bool = False, limit: int = 100) -> list[dict[str, Any]]:
    """รายการของคนนี้ ใหม่สุดก่อน — ค่าเริ่มต้นเฉพาะที่ยังไม่ได้กดรับทราบ"""
    ne = normalized_email(email)
    if not ne:
        return []
    out = []
    for x in reversed(_read_unlocked()):
        if ne not in (x.get("recipients") or []):
            continue
        acked = ne in (x.get("acked_by") or {})
        if acked and not include_acked:
            continue
        view = {k: v for k, v in x.items() if k not in ("recipients", "acked_by")}
        view["acked"] = acked
        out.append(view)
        if len(out) >= limit:
            break
    return out


def unread_count(email: str) -> int:
    ne = normalized_email(email)
    if not ne:
        return 0
    return sum(
        1
        for x in _read_unlocked()
        if ne in (x.get("recipients") or []) and ne not in (x.get("acked_by") or {})
    )


def acknowledge(email: str, item_id: str) -> bool:
    """กดรับทราบ — ได้เฉพาะคนที่อยู่ในรายชื่อผู้รับ คืน False ถ้าไม่พบ/ไม่ใช่ผู้รับ"""
    ne = normalized_email(email)
    with _STORE_LOCK:
        items = _read_unlocked()
        for x in items:
            if x.get("id") != item_id:
                continue
            if ne not in (x.get("recipients") or []):
                return False
            acked = dict(x.get("acked_by") or {})
            acked.setdefault(ne, _now_iso())
            x["acked_by"] = acked
            _write_unlocked(items)
            return True
    return False
