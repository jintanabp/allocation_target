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


MANAGER_LEVELS = frozenset({"regional", "division"})
NONE_SENTINEL = "none"


def _clean_none_sentinel(value: Any) -> Any:
    if isinstance(value, str) and value.strip().lower() == NONE_SENTINEL:
        return ""
    return value


def _clean_row_sentinels(row: dict[str, Any]) -> dict[str, Any]:
    return {k: _clean_none_sentinel(v) for k, v in row.items()}


def _opt_canonical_str(row: dict[str, Any], key: str, *, required: bool = False) -> str:
    raw = row.get(key)
    if raw is None:
        return "" if required else NONE_SENTINEL
    s = str(raw).strip()
    if not s or s.lower() == NONE_SENTINEL:
        return "" if required else NONE_SENTINEL
    return s


def canonicalize_user_access_row(row: dict[str, Any]) -> dict[str, Any]:
    """จัดรูปแบบแถวให้ฟิลด์ครบ — ค่าว่างใช้ 'none' (ยกเว้น note)"""
    work = _clean_row_sentinels(dict(row))
    em = normalized_email(work.get("email"))
    upl = normalize_userpl(work.get("userpl"))
    if not em or "@" not in em:
        raise ValueError("email/userpl ไม่ถูกต้อง")
    # ปกติต้องมีรหัส SL — ยกเว้นบัญชี "แอดมินอย่างเดียว" ที่ไม่มีตำแหน่งงาน
    # จึงไม่มีรหัสขาย มีไว้ดูแลระบบเท่านั้น (แถวยังมีความหมายเพราะมี role)
    if not upl and not str(work.get("role") or "").strip():
        raise ValueError("email/userpl ไม่ถูกต้อง")
    work["email"] = em
    work["userpl"] = upl
    apply_inferred_access_fields(work)

    lk = str(work.get("login_kind") or "standard").strip() or "standard"
    ml = str(work.get("manager_level") or "").strip().lower()
    if lk != "manager_acc" or ml not in MANAGER_LEVELS:
        ml_out = NONE_SENTINEL
    else:
        ml_out = ml

    unit_raw = str(work.get("acc_unit") or "").strip().lower()
    if lk == "supervisor_acc" and unit_raw in ("van", "credit"):
        unit_out = unit_raw
    else:
        unit_out = NONE_SENTINEL

    region_raw = str(work.get("acc_region") or "").strip()
    if lk == "manager_acc" and ml_out == "division":
        region_out = NONE_SENTINEL
    elif region_raw:
        region_out = region_raw
    else:
        region_out = NONE_SENTINEL

    if lk in ("manager_acc", "supervisor_acc"):
        acc_type = str(work.get("acc_type") or "NON").strip() or "NON"
        acc_joblevel = str(work.get("acc_joblevel") or "1").strip() or "1"
    else:
        acc_type = _opt_canonical_str(work, "acc_type")
        acc_joblevel = _opt_canonical_str(work, "acc_joblevel")

    vis = work.get("visible_supervisor_codes")
    if not isinstance(vis, list):
        vis = []
    vis_out = sorted({str(x).strip().upper() for x in vis if str(x).strip()})

    scope = str(work.get("acc_scope") or "").strip()
    if not scope:
        scope = NONE_SENTINEL

    return {
        "email": em,
        "userpl": upl or NONE_SENTINEL,
        "can_import_targetsun": bool(work.get("can_import_targetsun")),
        "note": str(work.get("note") or "").strip(),
        "full_name": _opt_canonical_str(work, "full_name"),
        "acc_region": region_out,
        "acc_type": acc_type,
        "acc_joblevel": acc_joblevel,
        "login_kind": lk,
        "manager_level": ml_out,
        "acc_division": (
            str(work.get("acc_division") or "").strip()
            if str(work.get("acc_division") or "").strip()
            else NONE_SENTINEL
        ),
        "acc_unit": unit_out,
        "acc_position": _opt_canonical_str(work, "acc_position"),
        "acc_scope": scope,
        # role ระบบ (dev / admin รายภาค) — ไม่มี = ผู้ใช้ทั่วไป
        # ต้องคงไว้ตอน canonicalize ไม่งั้นการเขียนไฟล์ครั้งถัดไปจะลบสิทธิ์ทิ้งเงียบ ๆ
        "role": _opt_canonical_str(work, "role"),
        # ขอบเขตของ role admin — แก้ผู้ใช้คนไหนได้บ้าง (all/division/division_region)
        "admin_scope": _opt_canonical_str(work, "admin_scope"),
        "visible_supervisor_codes": vis_out,
    }


def canonicalize_user_access_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        canon = canonicalize_user_access_row(row)
        key = (canon["email"], canon["userpl"])
        if key in seen:
            continue
        seen.add(key)
        out.append(canon)
    out.sort(key=lambda r: (r["email"], r["userpl"]))
    return out


def _apply_login_kind_manager_level(row: dict[str, Any]) -> None:
    """รวม legacy login_kind เป็นตำแหน่ง + ระดับ Manager"""
    lk = str(row.get("login_kind") or "standard").strip().lower()
    ml = str(row.get("manager_level") or "").strip().lower()
    if ml == NONE_SENTINEL:
        ml = ""
    if lk == "regional_manager":
        row["login_kind"] = "manager_acc"
        row["manager_level"] = "regional"
    elif lk == "district_manager":
        row["login_kind"] = "manager_acc"
        row["manager_level"] = "division"
    elif lk == "manager_acc":
        if ml in MANAGER_LEVELS:
            row["manager_level"] = ml
        else:
            row.pop("manager_level", None)
    else:
        row.pop("manager_level", None)


def _infer_manager_level(row: dict[str, Any]) -> None:
    """เติม manager_level จาก division/ภาค เมื่อแอดมินไม่ได้ระบุ"""
    if str(row.get("login_kind") or "") != "manager_acc":
        return
    ml = str(row.get("manager_level") or "").strip().lower()
    if ml in MANAGER_LEVELS:
        return
    div = str(row.get("acc_division") or "").strip()
    region = str(row.get("acc_region") or "").strip()
    if div == "Div.S" and not region:
        row["manager_level"] = "division"
    elif div in ("Div.E", "Div.S") and not region:
        row["manager_level"] = "division"
    elif region:
        row["manager_level"] = "regional"


def _apply_inferred_acc_scope(row: dict[str, Any]) -> None:
    """ขอบเขตดู — อนุมานจากตำแหน่ง (ไม่ให้แอดมินเลือกเอง)"""
    lk = str(row.get("login_kind") or "standard").strip().lower()
    if lk in ("marketing", "standard"):
        row.pop("acc_scope", None)
        return
    if lk == "manager_acc":
        row["acc_scope"] = "all"
        return
    if lk == "supervisor_acc":
        unit = str(row.get("acc_unit") or "").strip().lower()
        if unit == "credit":
            row["acc_scope"] = "credit"
        elif unit == "van":
            row["acc_scope"] = "van"
        else:
            row["acc_scope"] = "region_peers"
        return
    row.pop("acc_scope", None)


def apply_inferred_access_fields(row: dict[str, Any]) -> None:
    """เติม manager_level + acc_scope ตามกฎสิทธิมาตรฐาน"""
    _apply_login_kind_manager_level(row)
    _infer_manager_level(row)
    _apply_inferred_acc_scope(row)


def _normalize_row(row: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    row = _clean_row_sentinels(row)
    em = normalized_email(row.get("email") or row.get("EMAIL"))
    upl = normalize_userpl(row.get("userpl") if row.get("userpl") is not None else row.get("USERPL"))
    if not em or "@" not in em:
        return None
    # แถวไม่มีรหัส SL ทิ้งเหมือนเดิม เว้นแต่เป็นบัญชีแอดมินอย่างเดียว (มี role)
    if not upl and not str(row.get("role") or "").strip():
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
        "manager_level",
        "acc_division",
        "acc_unit",
        "acc_position",
        "acc_scope",
        "role",
        "admin_scope",
    ):
        val = row.get(key)
        if val is not None and str(val).strip():
            out[key] = str(val).strip()
    apply_inferred_access_fields(out)
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


def _write_rows_unlocked(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    เขียนไฟล์โดย **ไม่จับ _STORE_LOCK** — ผู้เรียกต้องถืออยู่แล้ว

    _STORE_LOCK เป็น threading.Lock ธรรมดา ไม่ใช่ RLock
    เรียก write_rows() ซ้อนอยู่ในบล็อกที่ถือ lock อยู่จะ deadlock ทันที
    ตัวที่ต้องทำ read-modify-write ครบรอบใต้ lock เดียวจึงต้องใช้ตัวนี้
    """
    normalized = _dedupe_rows(rows)
    path = user_access_json_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    payload = json.dumps(normalized, ensure_ascii=False, indent=2) + "\n"
    dir_name = os.path.dirname(path) or "."
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


def write_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    with _STORE_LOCK:
        return _write_rows_unlocked(rows)


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


def set_targetsun_flag_bulk(
    emails: list[str] | None,
    enabled: bool,
    *,
    all_emails: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    """
    ตั้ง can_import_targetsun ให้หลายอีเมลในคราวเดียว — คืน (rows, จำนวนอีเมลที่เปลี่ยนจริง)

    ทำ read-modify-write รอบเดียวใต้ lock เดียว
    ถ้าวนเรียก set_email_targetsun_flag ทีละคน 200 อีเมล = อ่าน+เขียนไฟล์ 200 รอบ
    ทั้งช้าและเปิดช่องให้ admin อีกคนเขียนแทรกกลางทาง (ดู docs/CONCURRENCY.md)

    all_emails=True = ทุกแถวในไฟล์ · ไม่งั้นใช้เฉพาะอีเมลใน emails
    """
    want = {normalized_email(e) for e in (emails or []) if normalized_email(e)}
    if not all_emails and not want:
        return read_rows(), 0

    with _STORE_LOCK:
        rows = read_rows_unlocked()
        out: list[dict[str, Any]] = []
        touched: set[str] = set()
        for r in rows:
            nr = dict(r)
            em = normalized_email(nr.get("email"))
            if (all_emails and "@" in em) or (em in want):
                if bool(nr.get("can_import_targetsun")) != bool(enabled):
                    touched.add(em)
                nr["can_import_targetsun"] = bool(enabled)
            out.append(nr)
        saved = _write_rows_unlocked(out)
    return saved, len(touched)


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
