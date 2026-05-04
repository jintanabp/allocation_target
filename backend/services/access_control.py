"""
สิทธิ์ผู้ใช้หลังล็อกอิน Microsoft: อิงอีเมลใน ACC_USER_CONTROL[EMAIL] แล้วผูก USERPL
กับรหัส Supervisor / Manager จาก trf_select_supervisor (เหมือนหน้า login)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any

from ..fabric_dax_connector import FabricDAXConnector
from . import managers as managers_svc

logger = logging.getLogger("target_allocation")

_ACC_LOCK = threading.Lock()
_ACC_ROWS_CACHE: list[dict] | None = None
_ACC_CACHE_AT: float = 0.0

_EXTRA_LOCK = threading.Lock()
_EXTRA_ROWS_CACHE: list[dict] | None = None
_EXTRA_CACHE_AT: float = 0.0


def _acc_cache_ttl_sec() -> int:
    return int(os.environ.get("ACC_USER_CONTROL_CACHE_TTL_SEC", "300"))


def _extra_cache_ttl_sec() -> int:
    raw = os.environ.get("EXTRA_USER_ACCESS_CACHE_TTL_SEC", "").strip()
    return int(raw) if raw else _acc_cache_ttl_sec()


def _extra_disabled() -> bool:
    """ปิดการดึงตาราง acc_extra_user เมื่ออยากประกันไม่ให้ยิง DAX ฟีเจอร์นี้ (ออปชัน)."""
    flag = os.environ.get("EXTRA_USER_ACCESS_DISABLED", "").strip().lower()
    return flag in ("1", "true", "yes")


def _extra_must_succeed() -> bool:
    flag = os.environ.get("EXTRA_USER_ACCESS_FAIL_CLOSED", "").strip().lower()
    return flag in ("1", "true", "yes")


def normalized_email(s: str | None) -> str:
    return (s or "").strip().lower()


def parse_allocation_admin_emails() -> set[str]:
    """
    อีเมลที่กำหนดใน ALLOCATION_ADMIN_EMAILS — เห็นรหัส Supervisor/Manager ทั้งหมดจาก trf เหมือนไม่กรอง ACC
    (คั่นด้วย comma/semicolon)
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


def parse_trf_managers_metadata(mdata: dict[str, Any]) -> tuple[set[str], set[str], dict[str, list[str]]]:
    """
    คืนชุดรหัส Supervisor / Manager และ map manager → supervisors จาก payload เดียวกับ /managers
    """
    src = str(mdata.get("source") or "").strip()
    by_m = _normalized_by_manager(mdata.get("by_manager"))
    if not (mdata.get("supervisors") or []) and src == "dim_fallback":
        supervisors = {str(x).strip().upper() for x in (mdata.get("managers") or []) if x}
        manager_codes: set[str] = set()
        by_m = {}
    else:
        supervisors = {str(x).strip().upper() for x in (mdata.get("supervisors") or [])}
        manager_codes = {str(x).strip().upper() for x in (mdata.get("manager_codes") or [])}
    return supervisors, manager_codes, by_m


def classify_userpls_picks(
    userpls: set[str],
    supervisors: set[str],
    manager_codes: set[str],
) -> tuple[set[str], set[str]]:
    """
    แยก USERPL ว่าเป็นป้ายเข้าระบบแบบ Supervisor / Manager

    รหัสเดียวโผล่ทั้งเป็น Supervisor และเป็น Manager — เก็บทั้งสอง (ไม่ใช้ elif)
    เพื่อให้เลือก role ใน login และให้ชุด allowed_sup ครบทั้งรหัสตัวเองและหมดภายใต้ depend_on (เมื่อรวมกับ compute_allowed_supervisor_codes)
    """
    sup_pick: set[str] = set()
    mgr_pick: set[str] = set()
    for u in userpls:
        if u in supervisors:
            sup_pick.add(u)
        if u in manager_codes:
            mgr_pick.add(u)
    return sup_pick, mgr_pick


def compute_allowed_supervisor_codes(
    email: str,
    acc_rows: list[dict],
    mdata: dict[str, Any],
) -> set[str]:
    """
    ผู้ที่ USERPL = รหัส Supervisor → ได้ใช้ sup_id = รหัสนั้นตรงๆ
    ผู้ที่ USERPL = รหัส Manager → ได้ใช้ sup_id เท่ากับ Supervisor ภายใต้ Manager (จาก depend_on/by_manager)

    ถ้า USERPL เป็นได้ทั้งคู่ — ให้สิทธิครบทั้งทีม (manager) และรหัส supervisor ตัวเอง

    EMAIL/USERPL ซ้ำหลายแถว → ถือว่าเหมือนกันแล้วรวม set
    """
    supervisors, manager_codes, by_m = parse_trf_managers_metadata(mdata)

    userpls = _unique_userpls_for_email(acc_rows, email)

    allowed: set[str] = set()
    for upl in userpls:
        in_sup = upl in supervisors
        in_mgr = upl in manager_codes
        # ขยายจาก manager ก่อน — เคสถ้าใช้ if/elif อย่างเดียว รหัสที่อยู่ทั้งใน supervisors และ manager_codes อาจไม่ได้ expand ทีมใต้ depend_on
        if in_mgr:
            allowed.update(by_m.get(upl, []))
        if in_sup:
            allowed.add(upl)
        if not in_sup and not in_mgr:
            logger.warning(
                "ACC_USER_CONTROL USERPL=%s ไม่ตรง trf supervisor/manager — ข้าม", upl
            )

    return allowed


def _try_load_acc_from_dev_json() -> list[dict] | None:
    """
    โหลด ACC จากไฟล์ JSON เฉพาะเมื่อ ALLOCATION_ALLOW_ACC_DEV_JSON=1 — ไว้ทดสอบใน dev โดยไม่ต้องพึ่งข้อมูลจาก Fabric

    JSON: รายการอ็อบเจ็กต์ {"email":"...","userpl":"SL330"} (หรือ EMAIL/USERPL ตัวพิมพ์ใหญ่)
    """
    flag = os.environ.get("ALLOCATION_ALLOW_ACC_DEV_JSON", "").strip().lower()
    if flag not in ("1", "true", "yes"):
        return None
    path = (os.environ.get("ACC_USER_CONTROL_DEV_JSON") or "").strip()
    if not path:
        logger.warning(
            "ALLOCATION_ALLOW_ACC_DEV_JSON เปิดอยู่ แต่ยังไม่ตั้ง ACC_USER_CONTROL_DEV_JSON",
        )
        return None
    path = os.path.normpath(os.path.abspath(path))
    if not os.path.isfile(path):
        logger.warning("ไม่พบไฟล์ ACC dev: %s", path)
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("อ่าน/แปลง JSON ACC dev ไม่ได้ %s: %s", path, e)
        return None
    if not isinstance(data, list):
        logger.warning("ACC dev JSON คาดว่าเป็น array ของแถว")
        return None
    out: list[dict] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        em = normalized_email(row.get("email") or row.get("EMAIL"))
        upl_raw = row.get("userpl") if row.get("userpl") is not None else row.get("USERPL")
        upl = str(upl_raw or "").strip().upper()
        if not em or not upl:
            continue
        out.append({"email": em, "userpl": upl})
    logger.warning(
        "ใช้ ACC จากไฟล์ dev (%s) จำนวน %d แถว — อย่าปิด env ใน production",
        path,
        len(out),
    )
    return out


def load_acc_rows() -> list[dict]:
    """
    ACC rows with short in-process cache (เหมือน managers cache TTL ที่สั้นกว่า — อย่าเก็บนานเกินสิทธิ์จริง)
    """
    dev_rows = _try_load_acc_from_dev_json()
    if dev_rows is not None:
        return dev_rows

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
        fabric = FabricDAXConnector()
        rows = fabric.get_acc_user_control_rows()
    except Exception as e:
        logger.error("ACC_USER_CONTROL fetch failed: %s", e)
        raise PermissionError(
            "ไม่สามารถโหลดตารางสิทธิ์ผู้ใช้ (ACC_USER_CONTROL) จาก Fabric"
        ) from e

    with _ACC_LOCK:
        _ACC_ROWS_CACHE = rows
        _ACC_CACHE_AT = time.time()
    return list(rows)


def load_extra_user_access_rows() -> list[dict]:
    """
    ตารางเสริมใน Semantic model เพื่อ map EMAIL → USERPL (Supervisor / Manager login code)

    Default: ดึงจาก acc_extra_user (EMAIL, USERPL) — ไม่ต้องตั้ง env เพื่อเปิด
    ปิดได้ด้วย EXTRA_USER_ACCESS_DISABLED=1
    """
    if _extra_disabled():
        return []

    global _EXTRA_ROWS_CACHE, _EXTRA_CACHE_AT
    ttl = _extra_cache_ttl_sec()
    now = time.time()
    with _EXTRA_LOCK:
        if (
            _EXTRA_ROWS_CACHE is not None
            and ttl > 0
            and (now - _EXTRA_CACHE_AT) < ttl
        ):
            return list(_EXTRA_ROWS_CACHE)

    try:
        fabric = FabricDAXConnector()
        rows = fabric.get_extra_user_access_rows()
    except Exception as e:
        msg = str(e)
        table = (os.environ.get("EXTRA_USER_ACCESS_TABLE_NAME", "acc_extra_user").strip() or "acc_extra_user")
        logger.error("%s fallback fetch failed: %s", table, msg)
        if _extra_must_succeed():
            raise PermissionError(
                "ไม่สามารถโหลดตารางสิทธิ์ผู้ใช้เสริมจาก Fabric "
                f"({table})"
            ) from e
        rows = []

    with _EXTRA_LOCK:
        _EXTRA_ROWS_CACHE = rows
        _EXTRA_CACHE_AT = time.time()
    return list(rows)


def _combine_acc_and_extra(acc_rows: list[dict], extra_rows: list[dict]) -> list[dict]:
    if not extra_rows:
        return list(acc_rows)
    seen: set[tuple[str, str]] = set()
    merged: list[dict] = []
    for batch in (acc_rows, extra_rows):
        for r in batch or []:
            em = normalized_email(r.get("email"))
            upl = str(r.get("userpl") or "").strip().upper()
            if not em or not upl:
                continue
            key = (em, upl)
            if key in seen:
                continue
            seen.add(key)
            merged.append({"email": em, "userpl": upl})
    return merged


def unrestricted_user_context() -> dict[str, Any]:
    """เมื่อปิด Entra — ไม่จำกัดรหัส"""
    return {
        "auth_disabled": True,
        "email": None,
        "allowed_supervisor_codes": None,
    }


def build_user_access_context(email: str) -> dict[str, Any]:
    """
    หลังยืนยัน Microsoft token แล้ว — ตรวจ ACC + trf และคืนชุด Supervisor ที่อนุญาติ
    """
    ne = normalized_email(email)
    if not ne:
        raise ValueError("ไม่มีอีเมลสำหรับตรวจสิทธิ์ ACC_USER_CONTROL")

    if ne in parse_allocation_admin_emails():
        logger.info(
            "สิทธิ์ผู้ดูแล (ALLOCATION_ADMIN_EMAILS): เข้าถึงทุกรหัสผ่าน Supervisor/API — ไม่อิง ACC_USER_CONTROL",
        )
        return {
            "auth_disabled": False,
            "email": ne,
            "acc_admin_full_access": True,
            "allowed_supervisor_codes": None,
            "userpls_supervisor_pick": set(),
            "userpls_manager_pick": set(),
        }

    acc_rows_base = load_acc_rows()
    extra_rows = load_extra_user_access_rows()
    acc_rows = _combine_acc_and_extra(acc_rows_base, extra_rows)

    user_rows = _rows_for_email(acc_rows, ne)
    if not user_rows:
        raise PermissionError(
            "ไม่พบสิทธิ์การใช้งาน — "
            "อีเมลของคุณยังไม่อยู่ในตาราง ACC_USER_CONTROL"
            + (" และ acc_extra_user" if not _extra_disabled() else "")
        )

    mdata = managers_svc.load_full_managers_payload()
    supervisors, manager_codes, _ = parse_trf_managers_metadata(mdata)
    userpls = _unique_userpls_for_email(acc_rows, ne)
    sup_pick, mgr_pick = classify_userpls_picks(userpls, supervisors, manager_codes)

    allowed = compute_allowed_supervisor_codes(ne, acc_rows, mdata)

    if not allowed:
        raise PermissionError(
            "บัญชีนี้ไม่มีรหัส Supervisor/Manager ที่ใช้งานได้ — "
            "ตรวจสอบ USERPL ใน ACC_USER_CONTROL ให้ตรงกับรหัสในระบบ"
        )

    return {
        "auth_disabled": False,
        "email": ne,
        "allowed_supervisor_codes": allowed,
        "userpls_supervisor_pick": sup_pick,
        "userpls_manager_pick": mgr_pick,
    }


def filter_managers_payload_for_user(full: dict, user: dict[str, Any]) -> dict:
    """
    จำกัด dropdown login เหลือเฉพาะรหัส Supervisor / Manager ที่ตรง USERPL ใน ACC (ไม่โชว์
    Manager ที่เป็นหัวหน้าของเราโดยอ้อมจาก depend_on เท่านั้น)
    """
    if user.get("auth_disabled") or user.get("acc_admin_full_access"):
        return full

    sup_pick = {str(x).strip().upper() for x in (user.get("userpls_supervisor_pick") or ())}
    mgr_pick = {str(x).strip().upper() for x in (user.get("userpls_manager_pick") or ())}

    raw_bm_src = full.get("by_manager") or {}
    raw_bm = _normalized_by_manager(raw_bm_src)

    rows_f: list[dict] = []
    bad_dep = {"NONE", "0", "(BLANK)"}

    def _bad_dep(dep: str) -> bool:
        u = dep.strip().upper()
        return not u or u in bad_dep

    for row in full.get("rows") or []:
        sc = str(row.get("supervisor_code") or "").strip().upper()
        dep = str(row.get("depend_on") or "").strip().upper()
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

    by_manager_f: dict[str, list[str]] = {}
    for m in sorted(mgr_pick):
        # USERPL = manager นี้ → ให้สลับครบทุก sup ใต้โครงสร้าง trf (ชุดตรงกับ allowed สำหรับ role นี้)
        by_manager_f[m] = sorted(raw_bm.get(m, []))

    pick_labels: list[str] = []
    for c in sorted(sup_pick):
        pick_labels.append(f"{c} (Supervisor)")
    for c in sorted(mgr_pick):
        pick_labels.append(f"{c} (Manager)")

    out = dict(full)
    out["supervisors"] = sorted(sup_pick)
    out["manager_codes"] = sorted(mgr_pick)
    out["by_manager"] = by_manager_f
    out["rows"] = rows_f
    out["managers"] = pick_labels
    out["filtered_by_acc"] = True
    out["filtered_by_userpl_only"] = True
    return out
