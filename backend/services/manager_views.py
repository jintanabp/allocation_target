"""มุมมองรวมสำหรับ Manager — รายคน / ทั้งทีม / แยกภาค"""

from __future__ import annotations

from typing import Any

from .user_access_store import read_rows


def _row_by_userpl() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for r in read_rows():
        upl = str(r.get("userpl") or "").strip().upper()
        if upl:
            out[upl] = r
    return out


def is_division_wide_manager(row: dict[str, Any] | None) -> bool:
    if not row:
        return False
    return (
        str(row.get("login_kind") or "") == "manager_acc"
        and str(row.get("acc_division") or "") == "Div.S"
        and str(row.get("acc_scope") or "").lower() == "all"
        and not str(row.get("acc_region") or "").strip()
    )


def supervisor_region_for_code(code: str, roster: dict[str, dict[str, Any]]) -> str:
    row = roster.get(code.strip().upper())
    if not row:
        return ""
    return str(row.get("acc_region") or "").strip()


def team_supervisor_codes(team: list[str], manager_code: str) -> list[str]:
    mgr = manager_code.strip().upper()
    out: list[str] = []
    seen: set[str] = set()
    for raw in team:
        c = str(raw or "").strip().upper()
        if not c or c == mgr or c in seen:
            continue
        row = _row_by_userpl().get(c)
        if row and str(row.get("login_kind") or "") == "supervisor_acc":
            out.append(c)
            seen.add(c)
        elif not row:
            out.append(c)
            seen.add(c)
    return sorted(out)


def _region_display_label(region_id: str) -> str:
    r = (region_id or "").strip()
    if not r:
        return "ไม่ระบุภาค"
    if r.startswith("ภาค"):
        return r
    return f"ภาค{r}"


def build_manager_view_options(
    manager_code: str,
    team_codes: list[str],
) -> dict[str, Any]:
    """
    คืนตัวเลือกมุมมองสำหรับ Manager:
    - division-wide: individual + all + regions[]
    - regional: individual + region (ทั้งภาคเดียว)
    """
    mgr = manager_code.strip().upper()
    roster = _row_by_userpl()
    mgr_row = roster.get(mgr)
    supers = team_supervisor_codes(team_codes, mgr)

    meta: dict[str, dict[str, str]] = {}
    by_region: dict[str, list[str]] = {}
    for sc in supers:
        reg = supervisor_region_for_code(sc, roster)
        meta[sc] = {"region": reg}
        if reg:
            by_region.setdefault(reg, []).append(sc)

    for reg in by_region:
        by_region[reg] = sorted(by_region[reg])

    regions_sorted = sorted(by_region.keys(), key=lambda x: (x == "", x))

    if is_division_wide_manager(mgr_row):
        modes = ["individual", "all"]
        if len(regions_sorted) > 1 or (len(regions_sorted) == 1 and regions_sorted[0]):
            modes.append("region")
        return {
            "manager_code": mgr,
            "scope_kind": "division",
            "modes": modes,
            "regions": [
                {"id": r, "label": _region_display_label(r), "supervisor_codes": by_region[r]}
                for r in regions_sorted
            ],
            "supervisor_meta": meta,
            "supervisor_codes": supers,
        }

    mgr_region = str((mgr_row or {}).get("acc_region") or "").strip()
    modes = ["individual", "region"]
    region_entry = {
        "id": mgr_region or "__team__",
        "label": _region_display_label(mgr_region) if mgr_region else "ทั้งทีม",
        "supervisor_codes": supers,
    }
    return {
        "manager_code": mgr,
        "scope_kind": "region",
        "modes": modes,
        "regions": [region_entry],
        "supervisor_meta": meta,
        "supervisor_codes": supers,
        "manager_region": mgr_region,
    }


def build_manager_views_map(
    by_manager: dict[str, list[str]] | None,
    manager_codes: list[str] | None = None,
) -> dict[str, Any]:
    """สร้าง manager_views สำหรับทุกรหัส Manager ในทีม (ใช้ทั้ง user ปกติและแอดมิน)"""
    bm: dict[str, list[str]] = {}
    for k, v in (by_manager or {}).items():
        mk = str(k or "").strip().upper()
        if not mk:
            continue
        bm[mk] = sorted({str(x).strip().upper() for x in (v or []) if str(x).strip()})
    codes = manager_codes if manager_codes is not None else sorted(bm.keys())
    out: dict[str, Any] = {}
    for m in sorted({str(c).strip().upper() for c in codes if str(c).strip()}):
        out[m] = build_manager_view_options(m, bm.get(m, []))
    return out


def resolve_aggregate_supervisor_codes(
    manager_code: str,
    team_codes: list[str],
    view: str,
    region: str | None = None,
) -> list[str]:
    opts = build_manager_view_options(manager_code, team_codes)
    mgr = manager_code.strip().upper()
    view = (view or "").strip().lower()
    if view == "all":
        if "all" not in opts["modes"]:
            raise ValueError("ไม่มีสิทธิ์ดูแบบรวมทั้งหมด")
        return list(opts["supervisor_codes"])

    if view == "region":
        if "region" not in opts["modes"]:
            raise ValueError("ไม่มีสิทธิ์ดูแบบรวมภาค")
        reg_key = (region or "").strip()
        if opts["scope_kind"] == "region" and not reg_key:
            reg_key = str(opts.get("manager_region") or opts["regions"][0]["id"])
        for entry in opts["regions"]:
            if entry["id"] == reg_key or (not reg_key and entry["id"] == "__team__"):
                return list(entry["supervisor_codes"])
        raise ValueError(f"ไม่พบภาค {region!r} ในขอบเขตที่ดูได้")

    raise ValueError("view ต้องเป็น all หรือ region")
