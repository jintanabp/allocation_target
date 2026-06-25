"""
เก็บสิทธิ์ EMAIL + USERPL ในไฟล์ JSON บน server (แทน ACC_USER_CONTROL / acc_extra_user บน Fabric)
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from typing import Any

logger = logging.getLogger("target_allocation")

_STORE_LOCK = threading.Lock()


def _repo_root() -> str:
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))


def user_access_json_path() -> str:
    raw = (os.environ.get("USER_ACCESS_JSON_PATH") or "").strip()
    if raw:
        return os.path.normpath(os.path.abspath(raw))
    return os.path.join(_repo_root(), "config", "user_access.json")


def normalized_email(s: str | None) -> str:
    return (s or "").strip().lower()


def normalize_userpl(s: str | None) -> str:
    return (s or "").strip().upper()


def _normalize_row(row: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    em = normalized_email(row.get("email") or row.get("EMAIL"))
    upl = normalize_userpl(row.get("userpl") if row.get("userpl") is not None else row.get("USERPL"))
    if not em or "@" not in em or not upl:
        return None
    note = str(row.get("note") or "").strip()
    ts = row.get("can_import_targetsun")
    if isinstance(ts, str):
        can_ts = ts.strip().lower() in ("1", "true", "yes")
    else:
        can_ts = bool(ts)
    out: dict[str, Any] = {
        "email": em,
        "userpl": upl,
        "can_import_targetsun": can_ts,
        "note": note,
    }
    for key in (
        "full_name",
        "acc_region",
        "acc_type",
        "acc_joblevel",
        "login_kind",
        "acc_division",
        "acc_unit",
        "acc_position",
        "acc_scope",
    ):
        val = row.get(key)
        if val is not None and str(val).strip():
            out[key] = str(val).strip()
    vis = row.get("visible_supervisor_codes")
    if isinstance(vis, list) and vis:
        out["visible_supervisor_codes"] = [str(x).strip().upper() for x in vis if x]
    return out


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        nr = _normalize_row(row)
        if not nr:
            continue
        key = (nr["email"], nr["userpl"])
        if key in seen:
            continue
        seen.add(key)
        out.append(nr)
    out.sort(key=lambda r: (r["email"], r["userpl"]))
    return out


def read_rows_unlocked() -> list[dict[str, Any]]:
    path = user_access_json_path()
    if not os.path.isfile(path):
        logger.warning("user_access JSON ไม่พบ: %s", path)
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.error("อ่าน user_access JSON ไม่ได้ %s: %s", path, e)
        raise PermissionError(
            f"ไม่สามารถโหลดตารางสิทธิ์ผู้ใช้ ({path})"
        ) from e
    if not isinstance(data, list):
        raise PermissionError(f"รูปแบบ user_access JSON ไม่ถูกต้อง (ต้องเป็น array): {path}")
    return _dedupe_rows(data)


def read_rows() -> list[dict[str, Any]]:
    with _STORE_LOCK:
        return read_rows_unlocked()


def write_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = _dedupe_rows(rows)
    path = user_access_json_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    payload = json.dumps(normalized, ensure_ascii=False, indent=2) + "\n"
    dir_name = os.path.dirname(path) or "."
    with _STORE_LOCK:
        fd, tmp = tempfile.mkstemp(prefix=".user_access_", suffix=".json", dir=dir_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    logger.info("บันทึก user_access %d แถว → %s", len(normalized), path)
    return normalized


def row_key(email: str, userpl: str) -> tuple[str, str]:
    return normalized_email(email), normalize_userpl(userpl)


def find_row(rows: list[dict[str, Any]], email: str, userpl: str) -> dict[str, Any] | None:
    k = row_key(email, userpl)
    for r in rows:
        if (r.get("email"), r.get("userpl")) == k:
            return r
    return None


def upsert_row(
    rows: list[dict[str, Any]],
    *,
    email: str,
    userpl: str,
    can_import_targetsun: bool | None = None,
    note: str | None = None,
) -> list[dict[str, Any]]:
    k = row_key(email, userpl)
    out: list[dict[str, Any]] = []
    found = False
    for r in rows:
        if (r.get("email"), r.get("userpl")) == k:
            found = True
            nr = dict(r)
            if can_import_targetsun is not None:
                nr["can_import_targetsun"] = bool(can_import_targetsun)
            if note is not None:
                nr["note"] = str(note).strip()
            out.append(nr)
        else:
            out.append(r)
    if not found:
        out.append(
            {
                "email": k[0],
                "userpl": k[1],
                "can_import_targetsun": bool(can_import_targetsun),
                "note": str(note or "").strip(),
            }
        )
    return write_rows(out)


def delete_row(rows: list[dict[str, Any]], email: str, userpl: str) -> list[dict[str, Any]]:
    k = row_key(email, userpl)
    out = [r for r in rows if (r.get("email"), r.get("userpl")) != k]
    if len(out) == len(rows):
        raise ValueError("ไม่พบแถวที่จะลบ")
    return write_rows(out)


def emails_with_targetsun(rows: list[dict[str, Any]] | None = None) -> set[str]:
    data = rows if rows is not None else read_rows()
    out: set[str] = set()
    for r in data:
        if r.get("can_import_targetsun"):
            em = normalized_email(r.get("email"))
            if "@" in em:
                out.add(em)
    return out


def set_email_targetsun_flag(email: str, enabled: bool) -> list[dict[str, Any]]:
    """ตั้ง can_import_targetsun ให้ทุกแถวของอีเมลนี้"""
    em = normalized_email(email)
    rows = read_rows()
    changed = False
    out: list[dict[str, Any]] = []
    for r in rows:
        nr = dict(r)
        if normalized_email(nr.get("email")) == em:
            nr["can_import_targetsun"] = bool(enabled)
            changed = True
        out.append(nr)
    if not changed:
        raise ValueError("ไม่พบอีเมลในรายการ")
    return write_rows(out)
