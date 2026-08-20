"""บันทึกการใช้งาน / error สำหรับ Admin"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("target_allocation")

_LOCK = threading.Lock()


def _repo_root() -> str:
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))


def logs_dir() -> str:
    raw = (os.environ.get("USAGE_LOGS_DIR") or "").strip()
    if raw:
        return os.path.normpath(os.path.abspath(raw))
    return os.path.join(_repo_root(), "data", "logs")


def _log_path_for_date(date_str: str) -> str:
    return os.path.join(logs_dir(), f"usage_{date_str}.jsonl")


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def entry_id(row: dict[str, Any]) -> str:
    rid = str(row.get("request_id") or row.get("entry_id") or "").strip()
    if rid:
        return rid
    raw = "|".join(
        str(row.get(k) or "")
        for k in ("ts", "email", "action", "message")
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def append_log(
    *,
    level: str = "info",
    email: str = "",
    role: str = "",
    sup_id: str = "",
    action: str = "",
    message: str = "",
    detail: str = "",
    request_id: str | None = None,
    target_month: int | None = None,
    target_year: int | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    เขียนหนึ่งบรรทัดลง usage_YYYY-MM-DD.jsonl

    `target_month`/`target_year` = **งวดเป้าที่เหตุการณ์นี้พูดถึง** ไม่ใช่วันที่เกิดเหตุ
    (เดิมไม่มีเลย เวลาเป้างวดหนึ่งเพี้ยนจึงตามรอยไม่ได้ว่าใครแตะงวดไหน)

    `context` = ค่าก่อน/หลังแบบมีโครงสร้าง ไว้เทียบด้วยเครื่องได้ ต่างจาก `detail`
    ที่เป็นข้อความสำหรับคนอ่าน — เก็บทั้งคู่เพราะใช้คนละงาน
    """
    row: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "level": str(level or "info").strip().lower(),
        "email": str(email or "").strip(),
        "role": str(role or "").strip(),
        "sup_id": str(sup_id or "").strip().upper(),
        "action": str(action or "").strip(),
        "message": str(message or "").strip(),
        "detail": str(detail or "").strip(),
        "request_id": request_id or str(uuid.uuid4())[:12],
    }
    if target_month is not None:
        row["target_month"] = int(target_month)
    if target_year is not None:
        row["target_year"] = int(target_year)
    if context:
        # ต้อง serialize ได้เสมอ ไม่งั้นทั้งบรรทัดหายไปตอน json.dumps ล้ม
        try:
            json.dumps(context, ensure_ascii=False)
            row["context"] = context
        except (TypeError, ValueError):
            row["context"] = {"_unserializable": str(context)[:500]}
    row["entry_id"] = entry_id(row)
    os.makedirs(logs_dir(), exist_ok=True)
    path = _log_path_for_date(_today_str())
    line = json.dumps(row, ensure_ascii=False)
    with _LOCK:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    return row


def _infer_role(user: dict[str, Any] | None) -> str:
    if not user:
        return ""
    if user.get("is_admin"):
        return "admin"
    if user.get("userpls_manager_pick"):
        return "manager"
    if user.get("home_supervisor_codes"):
        return "supervisor"
    return "user"


def log_from_user(
    user: dict[str, Any] | None,
    *,
    level: str = "info",
    sup_id: str = "",
    action: str = "",
    message: str = "",
    detail: str = "",
    target_month: int | None = None,
    target_year: int | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    email = str((user or {}).get("email") or (user or {}).get("view_as_email") or "").strip()
    return append_log(
        level=level,
        email=email,
        role=_infer_role(user),
        sup_id=sup_id,
        action=action,
        message=message,
        detail=detail,
        target_month=target_month,
        target_year=target_year,
        context=context,
    )


def _list_log_paths(
    *,
    date: str | None = None,
    target_year: int | None = None,
    target_month: int | None = None,
    scan_all: bool = False,
) -> list[str]:
    # ปี/เดือน (อย่างใดอย่างหนึ่งหรือทั้งคู่) — คัดจากชื่อไฟล์ที่มีอยู่จริง
    # ระบุปีหรือเดือนอย่างเดียว — เดิมตกมาบรรทัดสุดท้ายแล้วคืน "เฉพาะวันนี้" เงียบ ๆ
    # แอดมินเห็นว่างเปล่าแล้วเข้าใจว่าไม่มีเหตุการณ์ ทั้งที่มีอยู่เต็มไปหมด
    if target_year is not None or target_month is not None:
        d = logs_dir()
        if not os.path.isdir(d):
            return []
        paths = []
        for fn in os.listdir(d):
            if not (fn.startswith("usage_") and fn.endswith(".jsonl")):
                continue
            stamp = fn[len("usage_"):-len(".jsonl")]        # YYYY-MM-DD
            parts = stamp.split("-")
            if len(parts) != 3:
                continue
            if target_year is not None and parts[0] != f"{int(target_year)}":
                continue
            if target_month is not None and parts[1] != f"{int(target_month):02d}":
                continue
            paths.append(os.path.join(d, fn))
        paths.sort(reverse=True)
        return paths
    if scan_all or (date is None and target_year is None and target_month is None):
        d = logs_dir()
        if not os.path.isdir(d):
            return []
        paths = [
            os.path.join(d, fn)
            for fn in os.listdir(d)
            if fn.startswith("usage_") and fn.endswith(".jsonl")
        ]
        paths.sort(reverse=True)
        return paths
    return [_log_path_for_date(date or _today_str())]


def read_logs(
    date: str | None = None,
    level: str | None = None,
    limit: int = 200,
    *,
    target_year: int | None = None,
    target_month: int | None = None,
    scan_all: bool = False,
    action: str | None = None,
    sup_id: str | None = None,
) -> list[dict[str, Any]]:
    want_level = str(level or "").strip().lower()
    want_action = str(action or "").strip().lower()
    want_sup = str(sup_id or "").strip().upper()
    paths = _list_log_paths(
        date=date,
        target_year=target_year,
        target_month=target_month,
        scan_all=scan_all,
    )

    out: list[dict[str, Any]] = []
    for path in paths:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if want_level and str(row.get("level") or "").lower() != want_level:
                        continue
                    if want_action and str(row.get("action") or "").lower() != want_action:
                        continue
                    if want_sup and str(row.get("sup_id") or "").strip().upper() != want_sup:
                        continue
                    if not isinstance(row, dict):
                        continue
                    row = dict(row)
                    row["entry_id"] = entry_id(row)
                    out.append(row)
        except OSError as e:
            logger.warning("usage log read %s: %s", path, e)
    out.sort(key=lambda r: str(r.get("ts") or ""), reverse=True)
    if limit > 0 and len(out) > limit:
        out = out[:limit]
    return out


