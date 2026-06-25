"""
สิทธิ์ลำดับชั้น Manager → Supervisor จาก Excel roster (user_access.json)
ไม่อ้างอิง trf_select_supervisor / ACC
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

from .user_access_store import read_rows

logger = logging.getLogger("target_allocation")

DIV_S_REGION_MAP = {
    "BKK": "กรุงเทพ",
    "CENTRAL": "กลาง",
    "NORTHEASTERN": "อีสาน",
    "NORTH": "เหนือ",
    "SOUTH": "ใต้",
    "DIV.S": "",
}

# Excel Div.S บางแถวระบุ All แต่จริงๆ เป็นซุป (เช่น SL330 ภายใต้ SL384 ภาคกรุงเทพ)
DIV_S_FORCE_SUPERVISOR_USERPLS = frozenset({"SL330"})


def _repo_root() -> str:
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))


def access_hierarchy_json_path() -> str:
    raw = (os.environ.get("ACCESS_HIERARCHY_JSON_PATH") or "").strip()
    if raw:
        return os.path.normpath(os.path.abspath(raw))
    return os.path.join(_repo_root(), "config", "access_hierarchy.json")


def normalize_div_s_region(raw: str | None) -> str:
    key = (raw or "").strip().upper()
    if key in DIV_S_REGION_MAP:
        return DIV_S_REGION_MAP[key]
    return (raw or "").strip()


def parse_div_s_scope(raw: str | None) -> tuple[str, str, str] | None:
    """
    คืน (login_kind, acc_scope, acc_unit) หรือ None ถ้าค่าไม่รู้จัก
    acc_scope: all | credit | van | self
    """
    if raw is None:
        return None
    if hasattr(raw, "strftime"):
        return None
    s = str(raw).strip()
    if not s:
        return None
    low = re.sub(r"\s+", " ", s).strip().lower()
    if low == "all":
        return "manager_acc", "all", ""
    if low in ("credit all", "credit"):
        return "supervisor_acc", "credit", "credit"
    if low in ("van all", "van"):
        return "supervisor_acc", "van", "van"
    return None


def parse_region_from_position(pos: str) -> str:
    p = re.sub(r"\s+", "", (pos or ""))
    if not p:
        return ""
    checks = [
        ("ภาคกรุงเทพ", "กรุงเทพ"),
        ("ภาคกทม", "กรุงเทพ"),
        ("กทม", "กรุงเทพ"),
        ("ภาคเหนือ", "เหนือ"),
        ("ภาคใต้", "ใต้"),
        ("ภาคอีสาน", "อีสาน"),
        ("ภาคกลาง", "กลาง"),
        ("เหนือ", "เหนือ"),
        ("ใต้", "ใต้"),
        ("อีสาน", "อีสาน"),
        ("กลาง", "กลาง"),
    ]
    for needle, region in checks:
        if needle in p:
            return region
    return ""


def parse_role_from_position(pos: str) -> tuple[str, str, str]:
    """คืน (login_kind, acc_unit, acc_scope)"""
    p = re.sub(r"\s+", "", (pos or ""))
    if re.search(r"ผจก|ผช\.?ผจก|ผู้จัดการ", p):
        return "manager_acc", "", "all"
    if "ซุป" in p:
        if "เครดิต" in p:
            return "supervisor_acc", "credit", "self"
        if "หน่วยรถ" in p or ("รถ" in p and "เครดิต" not in p):
            return "supervisor_acc", "van", "self"
        return "supervisor_acc", "", "self"
    return "standard", "", "self"


def _build_division_supervisor_index(
    rows: list[dict[str, Any]],
) -> dict[tuple[str, str], set[str]]:
    idx: dict[tuple[str, str], set[str]] = {}
    for r in rows:
        if str(r.get("login_kind") or "") != "supervisor_acc":
            continue
        div = str(r.get("acc_division") or "").strip()
        if not div:
            continue
        region = str(r.get("acc_region") or "").strip()
        upl = str(r.get("userpl") or "").strip().upper()
        if upl:
            idx.setdefault((div, region), set()).add(upl)
    return idx


def _all_div_s_supervisors(rows: list[dict[str, Any]]) -> set[str]:
    out: set[str] = set()
    for r in rows:
        if str(r.get("acc_division") or "") != "Div.S":
            continue
        if str(r.get("login_kind") or "") != "supervisor_acc":
            continue
        upl = str(r.get("userpl") or "").strip().upper()
        if upl:
            out.add(upl)
    return out


def compute_visible_supervisors_for_row(
    row: dict[str, Any],
    *,
    all_rows: list[dict[str, Any]] | None = None,
    division_index: dict[tuple[str, str], set[str]] | None = None,
    div_s_supervisors: set[str] | None = None,
) -> list[str]:
    """คำนวณรหัส SL ที่แถวนี้ดูได้ (sorted)"""
    source = all_rows if all_rows is not None else read_rows()
    if division_index is None:
        division_index = _build_division_supervisor_index(source)
    if div_s_supervisors is None:
        div_s_supervisors = _all_div_s_supervisors(source)

    upl = str(row.get("userpl") or "").strip().upper()
    login_kind = str(row.get("login_kind") or "standard")
    div = str(row.get("acc_division") or "").strip()
    region = str(row.get("acc_region") or "").strip()
    scope = str(row.get("acc_scope") or "").strip().lower()

    def _mgr_team(codes: set[str]) -> list[str]:
        if upl:
            codes = set(codes)
            codes.add(upl)
        return sorted(codes)

    if login_kind == "manager_acc":
        if div == "Div.S":
            if scope == "all" and not region:
                return _mgr_team(div_s_supervisors)
            if scope == "all" and region:
                return _mgr_team(division_index.get((div, region), set()))
        if div in ("Div.B", "Div.E") and region:
            return _mgr_team(division_index.get((div, region), set()))
        if div:
            allowed: set[str] = set()
            for (d, r), codes in division_index.items():
                if d == div and (not region or r == region):
                    allowed.update(codes)
            return _mgr_team(allowed)

    if login_kind == "supervisor_acc":
        return [upl] if upl else []

    if upl:
        return [upl]
    return []


def apply_roster_overrides(row: dict[str, Any]) -> dict[str, Any]:
    """แก้ edge case จาก Excel ที่ไม่ตรงโครงสร้างจริง"""
    nr = dict(row)
    upl = str(nr.get("userpl") or "").strip().upper()
    div = str(nr.get("acc_division") or "").strip()
    if div == "Div.S" and upl in DIV_S_FORCE_SUPERVISOR_USERPLS:
        nr["login_kind"] = "supervisor_acc"
        nr["acc_scope"] = "self"
        nr.pop("acc_unit", None)
    return nr


def enrich_rows_with_visibility(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = [apply_roster_overrides(r) for r in rows]
    division_index = _build_division_supervisor_index(normalized)
    div_s_supervisors = _all_div_s_supervisors(normalized)
    out: list[dict[str, Any]] = []
    for r in normalized:
        nr = dict(r)
        vis = compute_visible_supervisors_for_row(
            nr,
            all_rows=normalized,
            division_index=division_index,
            div_s_supervisors=div_s_supervisors,
        )
        nr["visible_supervisor_codes"] = vis
        out.append(nr)
    return out


def build_hierarchy_payload(rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """สร้าง payload สำหรับ GET /managers และ access_control"""
    source = enrich_rows_with_visibility(rows if rows is not None else read_rows())
    supervisors: set[str] = set()
    manager_codes: set[str] = set()
    by_manager: dict[str, set[str]] = {}
    pair_rows: list[dict[str, str]] = []

    for r in source:
        upl = str(r.get("userpl") or "").strip().upper()
        lk = str(r.get("login_kind") or "")
        vis = [str(x).strip().upper() for x in (r.get("visible_supervisor_codes") or []) if x]

        if lk == "manager_acc" and upl:
            manager_codes.add(upl)
            team = set(vis)
            if upl in team or not team:
                team.add(upl)
            by_manager[upl] = team
            for sc in sorted(team):
                supervisors.add(sc)
                pair_rows.append({"supervisor_code": sc, "depend_on": upl, "manager_code": upl})
        elif lk == "supervisor_acc" and upl:
            supervisors.add(upl)
            pair_rows.append({"supervisor_code": upl, "depend_on": "", "manager_code": ""})
        elif upl:
            supervisors.add(upl)

    by_manager_sorted: dict[str, list[str]] = {
        m: sorted(codes) for m, codes in sorted(by_manager.items())
    }
    pick_labels: list[str] = []
    for c in sorted(supervisors - manager_codes):
        pick_labels.append(f"{c} (Supervisor)")
    for c in sorted(manager_codes):
        pick_labels.append(f"{c} (Manager)")

    return {
        "source": "excel_roster",
        "synced_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "rows": pair_rows,
        "by_manager": by_manager_sorted,
        "supervisors": sorted(supervisors),
        "manager_codes": sorted(manager_codes),
        "managers": pick_labels,
    }


def persist_hierarchy(payload: dict[str, Any]) -> str:
    path = access_hierarchy_json_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    cache_path = os.path.join(_repo_root(), "data", "managers_cache.json")
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    logger.info(
        "access hierarchy persisted: %d managers, %d supervisors → %s",
        len(payload.get("manager_codes") or []),
        len(payload.get("supervisors") or []),
        path,
    )
    return path


def load_hierarchy_payload() -> dict[str, Any]:
    path = access_hierarchy_json_path()
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("by_manager") is not None:
                return data
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("access_hierarchy read failed: %s", e)
    payload = build_hierarchy_payload()
    try:
        persist_hierarchy(payload)
    except OSError as e:
        logger.warning("access_hierarchy persist failed: %s", e)
    return payload


def parse_hierarchy_metadata(mdata: dict[str, Any]) -> tuple[set[str], set[str], dict[str, list[str]]]:
    supervisors = {str(x).strip().upper() for x in (mdata.get("supervisors") or []) if x}
    manager_codes = {str(x).strip().upper() for x in (mdata.get("manager_codes") or []) if x}
    by_m: dict[str, list[str]] = {}
    for k, v in (mdata.get("by_manager") or {}).items():
        ku = str(k).strip().upper()
        by_m[ku] = sorted({str(x).strip().upper() for x in (v or []) if x})
    return supervisors, manager_codes, by_m
