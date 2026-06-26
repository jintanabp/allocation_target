"""
สิทธิ์ผู้ใช้หลังล็อกอิน Microsoft: อิงอีเมลใน config/user_access.json
ลำดับชั้น Manager→Supervisor จาก Excel roster (access_hierarchy.json)
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from . import managers as managers_svc
from .access_hierarchy import (
    compute_visible_supervisors_for_row,
    parse_hierarchy_metadata,
)
from .manager_views import build_manager_view_options, build_manager_views_map
from .user_access_store import (
    emails_with_targetsun,
    normalized_email,
    read_rows,
)

logger = logging.getLogger("target_allocation")

_ACC_LOCK = threading.Lock()
_ACC_ROWS_CACHE: list[dict] | None = None
_ACC_CACHE_AT: float = 0.0


def _acc_cache_ttl_sec() -> int:
    raw = os.environ.get("USER_ACCESS_CACHE_TTL_SEC", "").strip()
    if raw:
        return int(raw)
    return int(os.environ.get("ACC_USER_CONTROL_CACHE_TTL_SEC", "300"))


def invalidate_user_access_cache() -> None:
    global _ACC_ROWS_CACHE, _ACC_CACHE_AT
    with _ACC_LOCK:
        _ACC_ROWS_CACHE = None
        _ACC_CACHE_AT = 0.0


def parse_allocation_admin_emails() -> set[str]:
    """
    อีเมลที่กำหนดใน ALLOCATION_ADMIN_EMAILS — เห็นรหัส Supervisor/Manager ทั้งหมด
    และเข้าหน้าแอดมินจัดการสิทธิ์
    """
    raw = (os.environ.get("ALLOCATION_ADMIN_EMAILS") or "").strip()
    if not raw:
        return set()
    out: set[str] = set()
    for chunk in raw.replace(";", ",").replace("\n", ",").split(","):
        ne = normalized_email(chunk)
        if "@" in ne:
            out.add(ne)
    return out


def is_allocation_admin_email(email: str | None) -> bool:
    return normalized_email(email) in parse_allocation_admin_emails()


def is_marketing_email(email: str | None) -> bool:
    """อีเมลที่มี login_kind=marketing ใน user_access.json — เข้าแอดมินแท็บทีมพนักงานเท่านั้น"""
    ne = normalized_email(email)
    if not ne:
        return False
    try:
        for r in read_rows():
            if normalized_email(r.get("email")) != ne:
                continue
            if str(r.get("login_kind") or "").strip().lower() == "marketing":
                return True
    except Exception:
        return False
    return False


def user_can_import_targetsun(user: dict[str, Any]) -> bool:
    """ส่งเข้า Target Sun ได้: ปิด auth / admin / อีเมลที่ตั้ง can_import_targetsun ใน user_access.json"""
    if user.get("auth_disabled"):
        return True
    if user.get("acc_admin_full_access"):
        return True
    email = normalized_email(user.get("email"))
    if not email:
        return False
    if user.get("view_as_email"):
        return email in emails_with_targetsun()
    if email in parse_allocation_admin_emails():
        return True
    return email in emails_with_targetsun()


def _rows_for_email(acc_rows: list[dict], email: str) -> list[dict]:
    ne = normalized_email(email)
    return [r for r in acc_rows if normalized_email(r.get("email")) == ne]


def _unique_userpls_for_email(acc_rows: list[dict], email: str) -> set[str]:
    return {str(r["userpl"]).strip().upper() for r in _rows_for_email(acc_rows, email) if r.get("userpl")}


def _normalized_by_manager(raw_bm: dict[str, Any] | None) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for k, vlist in (raw_bm or {}).items():
        ku = str(k).strip().upper()
        out[ku] = [str(x).strip().upper() for x in (vlist or [])]
    return out


# Backward compat alias
parse_trf_managers_metadata = parse_hierarchy_metadata


def classify_userpls_picks(
    userpls: set[str],
    supervisors: set[str],
    manager_codes: set[str],
) -> tuple[set[str], set[str]]:
    sup_pick: set[str] = set()
    mgr_pick: set[str] = set()
    for u in userpls:
        if u in supervisors:
            sup_pick.add(u)
        if u in manager_codes:
            mgr_pick.add(u)
    return sup_pick, mgr_pick


def _full_rows_by_email_userpl() -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for r in read_rows():
        key = (normalized_email(r.get("email")), str(r.get("userpl") or "").strip().upper())
        out[key] = r
    return out


def _visible_codes_for_row(row: dict[str, Any], all_rows: list[dict[str, Any]]) -> set[str]:
    upl = str(row.get("userpl") or "").strip().upper()
    precomputed = row.get("visible_supervisor_codes")
    if isinstance(precomputed, list) and precomputed:
        vis = {str(x).strip().upper() for x in precomputed if x}
    else:
        vis = set(compute_visible_supervisors_for_row(row, all_rows=all_rows))
    if upl:
        vis.add(upl)
    return vis


def role_label_for_meta(
    meta: dict[str, Any] | None,
    upl: str,
    supervisors: set[str],
    manager_codes: set[str],
) -> str:
    login_kind = str((meta or {}).get("login_kind") or "standard")
    upl = upl.strip().upper()

    if login_kind == "marketing":
        return "marketing"

    if login_kind == "manager_acc":
        if upl in manager_codes:
            return "manager"
        return "manager_acc"
    if login_kind == "supervisor_acc":
        if upl in manager_codes:
            return "manager"
        if upl in supervisors:
            return "supervisor"
        return "supervisor_acc"

    if upl in manager_codes:
        return "manager"
    if upl in supervisors:
        return "supervisor"
    if login_kind == "supervisor_acc":
        return "supervisor_acc"
    return "unknown"


def role_label_for_userpls(
    userpls: set[str],
    supervisors: set[str],
    manager_codes: set[str],
) -> str:
    codes = {str(u).strip().upper() for u in userpls if u}
    if any(u in manager_codes for u in codes):
        return "manager"
    if any(u in supervisors for u in codes):
        return "supervisor"
    if userpls:
        return "unknown"
    return "none"


def compute_allowed_supervisor_codes(
    email: str,
    acc_rows: list[dict],
    mdata: dict[str, Any] | None = None,
) -> set[str]:
    userpls = _unique_userpls_for_email(acc_rows, email)
    meta_map = _full_rows_by_email_userpl()
    full_rows = read_rows()
    ne = normalized_email(email)

    allowed: set[str] = set()
    for upl in userpls:
        meta = meta_map.get((ne, upl))
        if meta:
            allowed.update(_visible_codes_for_row(meta, full_rows))
        elif upl:
            allowed.add(upl)
            logger.info("USERPL=%s ใช้รหัสตัวเองเป็น fallback", upl)

    return allowed


def load_acc_rows() -> list[dict]:
    """โหลด EMAIL+USERPL จาก config/user_access.json (แคชใน process)"""
    global _ACC_ROWS_CACHE, _ACC_CACHE_AT
    ttl = _acc_cache_ttl_sec()
    now = time.time()
    with _ACC_LOCK:
        if (
            _ACC_ROWS_CACHE is not None
            and ttl > 0
            and (now - _ACC_CACHE_AT) < ttl
        ):
            return list(_ACC_ROWS_CACHE)

    try:
        rows = read_rows()
    except PermissionError:
        raise
    except Exception as e:
        logger.error("user_access JSON fetch failed: %s", e)
        raise PermissionError(
            "ไม่สามารถโหลดตารางสิทธิ์ผู้ใช้ (user_access.json)"
        ) from e

    slim = [{"email": r["email"], "userpl": r["userpl"]} for r in rows]

    with _ACC_LOCK:
        _ACC_ROWS_CACHE = slim
        _ACC_CACHE_AT = time.time()
    return list(slim)


def unrestricted_user_context() -> dict[str, Any]:
    return {
        "auth_disabled": True,
        "email": None,
        "allowed_supervisor_codes": None,
        "can_import_targetsun": True,
        "is_admin": False,
    }


def build_user_access_context(email: str, *, allow_admin_bypass: bool = True) -> dict[str, Any]:
    ne = normalized_email(email)
    if not ne:
        raise ValueError("ไม่มีอีเมลสำหรับตรวจสิทธิ์")

    if allow_admin_bypass and ne in parse_allocation_admin_emails():
        logger.info(
            "สิทธิ์ผู้ดูแล (ALLOCATION_ADMIN_EMAILS): เข้าถึงทุกรหัส — ไม่อิง user_access.json",
        )
        return {
            "auth_disabled": False,
            "email": ne,
            "acc_admin_full_access": True,
            "allowed_supervisor_codes": None,
            "userpls_supervisor_pick": set(),
            "userpls_manager_pick": set(),
            "can_import_targetsun": True,
            "is_admin": True,
        }

    full_rows = read_rows()
    if any(
        str(r.get("login_kind") or "").strip().lower() == "marketing"
        for r in full_rows
        if normalized_email(r.get("email")) == ne
    ):
        logger.info("สิทธิ์ Marketing: เข้าแอดมินแท็บทีมพนักงานเท่านั้น — %s", ne)
        return {
            "auth_disabled": False,
            "email": ne,
            "is_admin": False,
            "is_marketing": True,
            "allowed_supervisor_codes": set(),
            "userpls_supervisor_pick": set(),
            "userpls_manager_pick": set(),
            "can_import_targetsun": False,
        }

    acc_rows = load_acc_rows()
    user_rows = _rows_for_email(acc_rows, ne)
    if not user_rows:
        raise PermissionError(
            "ไม่พบสิทธิ์การใช้งาน — "
            "อีเมลของคุณยังไม่อยู่ในรายชื่อ user_access.json"
        )

    mdata = managers_svc.load_full_managers_payload()
    supervisors, manager_codes, _ = parse_hierarchy_metadata(mdata)
    userpls = _unique_userpls_for_email(acc_rows, ne)
    meta_map = _full_rows_by_email_userpl()

    sup_pick, mgr_pick = classify_userpls_picks(userpls, supervisors, manager_codes)
    for upl in userpls:
        meta = meta_map.get((ne, upl))
        lk = str((meta or {}).get("login_kind") or "")
        if lk == "manager_acc":
            mgr_pick.add(upl)
        elif lk == "supervisor_acc":
            if upl in supervisors:
                sup_pick.add(upl)
            if upl in manager_codes:
                mgr_pick.add(upl)
            if upl not in supervisors and upl not in manager_codes:
                sup_pick.add(upl)
        elif upl in supervisors:
            sup_pick.add(upl)
        elif upl in manager_codes:
            mgr_pick.add(upl)

    allowed = compute_allowed_supervisor_codes(ne, acc_rows, mdata)

    if not allowed:
        raise PermissionError(
            "บัญชีนี้ไม่มีรหัส Supervisor/Manager ที่ใช้งานได้ — "
            "ตรวจสอบ USERPL ใน user_access.json ให้ตรงกับรหัสในระบบ"
        )

    ctx = {
        "auth_disabled": False,
        "email": ne,
        "allowed_supervisor_codes": allowed,
        "userpls_supervisor_pick": sup_pick,
        "userpls_manager_pick": mgr_pick,
        "is_admin": False,
    }
    ctx["can_import_targetsun"] = user_can_import_targetsun(ctx)
    return ctx


def enrich_user_access_rows(rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """เพิ่ม role + visible_supervisors สำหรับหน้าแอดมิน"""
    source = rows if rows is not None else read_rows()
    mdata = managers_svc.load_full_managers_payload()
    supervisors, manager_codes, by_m = parse_hierarchy_metadata(mdata)

    out: list[dict[str, Any]] = []
    for r in source:
        em = normalized_email(r.get("email"))
        upl = str(r.get("userpl") or "").strip().upper()
        role = role_label_for_meta(r, upl, supervisors, manager_codes)
        visible = visible_supervisors_for_row_dict(r, supervisors, manager_codes, by_m)
        out.append(
            {
                "email": em,
                "userpl": upl,
                "full_name": str(r.get("full_name") or ""),
                "can_import_targetsun": bool(r.get("can_import_targetsun")),
                "note": str(r.get("note") or ""),
                "acc_region": str(r.get("acc_region") or ""),
                "acc_division": str(r.get("acc_division") or ""),
                "acc_unit": str(r.get("acc_unit") or ""),
                "acc_position": str(r.get("acc_position") or ""),
                "acc_scope": str(r.get("acc_scope") or ""),
                "login_kind": str(r.get("login_kind") or "standard"),
                "role": role,
                "visible_supervisors": visible,
            }
        )
    return out


def visible_supervisors_for_row_dict(
    row: dict[str, Any],
    supervisors: set[str] | None = None,
    manager_codes: set[str] | None = None,
    by_m: dict[str, list[str]] | None = None,
    region_teams: dict[str, list[str]] | None = None,
    division_index: dict[tuple[str, str], set[str]] | None = None,
) -> list[str]:
    """คำนวณรหัส SL ที่ผู้ใช้ดูได้ (สำหรับแอดมิน / preview)"""
    _ = supervisors, manager_codes, by_m, region_teams, division_index
    full_rows = read_rows()
    vis = _visible_codes_for_row(row, full_rows)
    upl = str(row.get("userpl") or "").strip().upper()
    if upl:
        vis.add(upl)
    return sorted(vis)


def filter_managers_payload_for_user(full: dict, user: dict[str, Any]) -> dict:
    if user.get("auth_disabled") or user.get("acc_admin_full_access"):
        return full
    if user.get("is_marketing"):
        return {
            "rows": [],
            "supervisors": [],
            "manager_codes": [],
            "by_manager": {},
            "manager_views": {},
        }

    hierarchy_supervisors, hierarchy_manager_codes, raw_bm_from_meta = parse_hierarchy_metadata(full)
    sup_pick = {str(x).strip().upper() for x in (user.get("userpls_supervisor_pick") or ())}
    mgr_pick = {str(x).strip().upper() for x in (user.get("userpls_manager_pick") or ())}

    raw_bm_src = full.get("by_manager") or raw_bm_from_meta or {}
    raw_bm = _normalized_by_manager(raw_bm_src)

    rows_f: list[dict] = []
    bad_dep = {"NONE", "0", "(BLANK)"}

    def _bad_dep(dep: str) -> bool:
        u = dep.strip().upper()
        return not u or u in bad_dep

    for row in full.get("rows") or []:
        sc = str(row.get("supervisor_code") or "").strip().upper()
        dep = str(row.get("depend_on") or row.get("manager_code") or "").strip().upper()
        if sc in sup_pick:
            rows_f.append(row)
            continue
        if not _bad_dep(dep) and dep in mgr_pick:
            rows_f.append(row)

    if not rows_f and (sup_pick or mgr_pick):
        for s in sorted(sup_pick):
            rows_f.append({"supervisor_code": s, "depend_on": "NONE"})
        for m in sorted(mgr_pick):
            for s in raw_bm.get(m, []):
                rows_f.append({"supervisor_code": s, "depend_on": m})

    allowed_codes = user.get("allowed_supervisor_codes")
    if allowed_codes:
        allowed_set = {str(x).strip().upper() for x in allowed_codes}
        if not rows_f:
            for sc in sorted(allowed_set):
                rows_f.append({"supervisor_code": sc, "depend_on": "NONE"})
                sup_pick.add(sc)
        else:
            rows_f = [
                row
                for row in rows_f
                if str(row.get("supervisor_code") or "").strip().upper() in allowed_set
            ]

    by_manager_f: dict[str, list[str]] = {}
    allowed_set: set[str] = set()
    if allowed_codes:
        allowed_set = {str(x).strip().upper() for x in allowed_codes}
    for m in sorted(mgr_pick):
        team = sorted(raw_bm.get(m, []))
        if team:
            by_manager_f[m] = [s for s in team if not allowed_set or s in allowed_set]
        elif allowed_set:
            by_manager_f[m] = sorted(allowed_set)
        if m in hierarchy_supervisors and m not in by_manager_f.get(m, []):
            by_manager_f.setdefault(m, [])
            by_manager_f[m].append(m)
            by_manager_f[m] = sorted(by_manager_f[m])

    pick_labels: list[str] = []
    for c in sorted(sup_pick):
        pick_labels.append(f"{c} (Supervisor)")
    for c in sorted(mgr_pick):
        pick_labels.append(f"{c} (Manager)")

    manager_views = build_manager_views_map(by_manager_f, sorted(mgr_pick))

    out = dict(full)
    out["supervisors"] = sorted(sup_pick)
    out["manager_codes"] = sorted(mgr_pick)
    out["by_manager"] = by_manager_f
    out["rows"] = rows_f
    out["managers"] = pick_labels
    out["manager_views"] = manager_views
    out["filtered_by_acc"] = True
    out["filtered_by_userpl_only"] = True
    return out
