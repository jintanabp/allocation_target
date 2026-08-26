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

from .user_access_store import apply_inferred_access_fields, read_rows, real_userpl

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
    """คืน (login_kind, acc_unit, acc_scope) — scope จะถูกอนุมานใหม่ตอน normalize"""
    p = re.sub(r"\s+", "", (pos or ""))
    if re.search(r"ผจก|ผช\.?ผจก|ผู้จัดการ", p):
        return "manager_acc", "", "all"
    if "ซุป" in p:
        if "เครดิต" in p:
            return "supervisor_acc", "credit", "region_peers"
        if "หน่วยรถ" in p or ("รถ" in p and "เครดิต" not in p):
            return "supervisor_acc", "van", "region_peers"
        return "supervisor_acc", "", "region_peers"
    return "standard", "", ""


def infer_manager_level_from_roster(
    *,
    login_kind: str,
    acc_division: str = "",
    acc_region: str = "",
    div_s_region_raw: str = "",
) -> str:
    """อนุมาน manager_level จาก Excel roster"""
    if str(login_kind or "").strip() != "manager_acc":
        return ""
    div = str(acc_division or "").strip()
    region = str(acc_region or "").strip()
    raw = str(div_s_region_raw or "").strip().upper()
    if div == "Div.S" and raw in ("DIV.S", "DIV.S."):
        return "division"
    if region:
        return "regional"
    if div in ("Div.E", "Div.S"):
        return "division"
    return "regional"


def is_div_s_division_manager_region_raw(region_raw: str | None) -> bool:
    return str(region_raw or "").strip().upper() in ("DIV.S", "DIV.S.")


def _build_division_supervisor_index(
    rows: list[dict[str, Any]],
) -> dict[tuple[str, str], set[str]]:
    """
    ทีมที่ถือว่าเป็น "เพื่อนร่วมภาค" ของกันและกัน — ใช้หาว่าซุปคนหนึ่งเห็นทีมไหนบ้าง

    รวมผู้จัดการที่มีพนักงานขายสังกัดรหัสตัวเองด้วย เพราะทีมของเขาเป็นทีมที่ต้อง
    เกลี่ยเป้าร่วมกันจริง ๆ · เดิมนับเฉพาะ supervisor_acc ซุปในภาคเดียวกันจึงเปิด
    หน้ารวมภาคแล้วไม่เห็นทีมนั้นเลย ทั้งที่ฝั่งผู้จัดการเองนับทีมตัวเองเข้ารวมภาค
    — สองฝั่งเห็นคนละยอดบนหน้าจอเดียวกัน

    ผู้จัดการที่ไม่มีลูกน้องตรงยังไม่โผล่เหมือนเดิม (ทีมว่าง ไม่มีอะไรให้เกลี่ย)
    """
    from .manager_views import codes_with_own_salesmen

    try:
        with_staff = codes_with_own_salesmen()
    except Exception:                       # อ่านโฟลเดอร์ไม่ได้ = ไม่เพิ่มใคร
        with_staff = set()

    idx: dict[tuple[str, str], set[str]] = {}
    for r in rows:
        kind = str(r.get("login_kind") or "")
        upl = real_userpl(r.get("userpl"))
        if not upl:
            continue
        if kind != "supervisor_acc":
            if not (kind == "manager_acc" and upl in with_staff):
                continue
        div = str(r.get("acc_division") or "").strip()
        if not div:
            continue
        region = str(r.get("acc_region") or "").strip()
        idx.setdefault((div, region), set()).add(upl)
    return idx


def _all_div_s_supervisors(rows: list[dict[str, Any]]) -> set[str]:
    out: set[str] = set()
    for r in rows:
        if str(r.get("acc_division") or "") != "Div.S":
            continue
        if str(r.get("login_kind") or "") != "supervisor_acc":
            continue
        upl = real_userpl(r.get("userpl"))
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

    upl = real_userpl(row.get("userpl"))
    login_kind = str(row.get("login_kind") or "standard")
    div = str(row.get("acc_division") or "").strip()
    region = str(row.get("acc_region") or "").strip()
    scope = str(row.get("acc_scope") or "").strip().lower()
    mgr_level = str(row.get("manager_level") or "").strip().lower()

    def _mgr_team(codes: set[str]) -> list[str]:
        if upl:
            codes = set(codes)
            codes.add(upl)
        return sorted(codes)

    def _unit_by_upl() -> dict[str, str]:
        out: dict[str, str] = {}
        for r in source:
            code = real_userpl(r.get("userpl"))
            if code:
                out[code] = str(r.get("acc_unit") or "").strip().lower()
        return out

    if login_kind == "manager_acc":
        unit = str(row.get("acc_unit") or "").strip().lower()

        def _limit_to_unit(codes: set[str]) -> set[str]:
            """
            ผู้จัดการที่ระบุหน่วย (credit/van) เห็นเฉพาะซุปหน่วยเดียวกัน — กติกาเดียวกับซุป

            ไม่กรองรหัสของตัวเอง: _mgr_team เติมทีหลังเพื่อให้ผู้จัดการโหลด
            หน้าตัวเองได้ ถ้ากรองทิ้งจะล็อกอินเข้ามาแล้วไม่เห็นอะไรเลย
            """
            if unit not in ("credit", "van"):
                return codes
            units = _unit_by_upl()
            return {c for c in codes if units.get(c) == unit}

        if not mgr_level and div == "Div.S" and not region:
            mgr_level = "division"
        elif not mgr_level and region:
            mgr_level = "regional"
        if mgr_level == "division":
            if div == "Div.S":
                return _mgr_team(_limit_to_unit(div_s_supervisors))
            if div:
                allowed: set[str] = set()
                for (d, _r), codes in division_index.items():
                    if d == div:
                        allowed.update(codes)
                return _mgr_team(_limit_to_unit(allowed))
        if div and region:
            return _mgr_team(_limit_to_unit(division_index.get((div, region), set())))
        if div:
            allowed = set()
            for (d, r), codes in division_index.items():
                if d == div and (not region or r == region):
                    allowed.update(codes)
            return _mgr_team(_limit_to_unit(allowed))

    if login_kind == "supervisor_acc":
        if div and region and scope in ("region_peers", "credit", "van", "all", ""):
            peers = set(division_index.get((div, region), set()))
            if scope in ("credit", "van"):
                units = _unit_by_upl()
                peers = {c for c in peers if units.get(c) == scope}
            if peers:
                return sorted(peers)
        return [upl] if upl else []

    if upl:
        return [upl]
    return []


def apply_roster_overrides(row: dict[str, Any]) -> dict[str, Any]:
    """แก้ edge case จาก Excel ที่ไม่ตรงโครงสร้างจริง"""
    nr = dict(row)
    upl = real_userpl(nr.get("userpl"))
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
        apply_inferred_access_fields(nr)
        vis = compute_visible_supervisors_for_row(
            nr,
            all_rows=normalized,
            division_index=division_index,
            div_s_supervisors=div_s_supervisors,
        )
        nr["visible_supervisor_codes"] = vis
        out.append(nr)
    return out


def existing_by_manager() -> dict[str, list[str]]:
    """
    อ่าน by_manager จากไฟล์ปัจจุบันตรง ๆ — ไม่ rebuild ต่อถ้าไฟล์หาย

    ใช้ load_hierarchy_payload ตรงนี้ไม่ได้ เพราะไฟล์หายเมื่อไหร่มันจะเรียก
    build_hierarchy_payload ต่อ ซึ่งเป็นตัวที่เรียกฟังก์ชันนี้อยู่ = วนไม่จบ
    """
    path = access_hierarchy_json_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("อ่าน by_manager เดิมไม่ได้: %s", e)
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, list[str]] = {}
    for k, v in (data.get("by_manager") or {}).items():
        out[str(k).strip().upper()] = [str(x).strip().upper() for x in (v or []) if x]
    return out


def build_hierarchy_payload(
    rows: list[dict[str, Any]] | None = None,
    *,
    keep_uncomputable_teams: bool = True,
) -> dict[str, Any]:
    """
    สร้าง payload สำหรับ GET /managers และ access_control

    keep_uncomputable_teams: ผู้จัดการที่แถวไม่มี division/ภาค คำนวณทีมกลับไม่ได้
    เหลือแค่รหัสตัวเอง — ให้คงทีมเดิมจาก roster ไว้ ไม่งั้น rebuild หนึ่งครั้งเท่ากับ
    ตัดสิทธิ์เขาถาวรและกู้ไม่ได้ (ข้อมูลชุดนั้นมาจาก Excel ที่แอปสร้างใหม่เองไม่ได้)
    """
    source = enrich_rows_with_visibility(rows if rows is not None else read_rows())
    previous = existing_by_manager() if keep_uncomputable_teams else {}
    supervisors: set[str] = set()
    manager_codes: set[str] = set()
    by_manager: dict[str, set[str]] = {}
    pair_rows: list[dict[str, str]] = []

    from .demo_data import is_demo_supervisor

    for r in source:
        upl = real_userpl(r.get("userpl"))
        lk = str(r.get("login_kind") or "")
        # ทีมสาธิตมีทางเข้าของตัวเอง (inject_into_managers_payload ต่อผู้ใช้)
        # ห้ามลงไฟล์ที่ทุกคนใช้ร่วมกัน ไม่งั้นผู้ใช้จริงเห็นทีม SLDEMO
        if is_demo_supervisor(upl):
            continue
        vis = [
            c for c in (str(x).strip().upper() for x in (r.get("visible_supervisor_codes") or []) if x)
            if not is_demo_supervisor(c)
        ]

        if lk == "manager_acc" and upl:
            manager_codes.add(upl)
            team = set(vis)
            if upl in team or not team:
                team.add(upl)
            # คำนวณแล้วได้แค่ตัวเอง = ข้อมูลไม่พอ ไม่ใช่ "ทีมว่างจริง"
            if team <= {upl}:
                kept = {c for c in (previous.get(upl) or ()) if c}
                if kept - team:
                    logger.info("USERPL=%s คำนวณทีมไม่ได้ — คงทีมเดิม %d รหัส", upl, len(kept))
                    team |= kept
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
