"""มุมมองรวมสำหรับ Manager — รายคน / ทั้งทีม / แยกภาค"""

from __future__ import annotations

import os
import re
from typing import Any

from .user_access_store import read_rows

_EMP_FILE_RE = re.compile(r"^(?:emp_cache|tga_lines)_(.+)_\d{4}_\d{2}\.csv$")


def codes_with_own_salesmen(data_dir: str = "data") -> set[str]:
    """
    รหัสที่ "เคยดึงข้อมูลมาแล้วพบพนักงานสังกัดตรง" — ดูจากไฟล์ที่แคชไว้

    ระบบรู้ล่วงหน้าไม่ได้ว่าผู้จัดการคนไหนมีพนักงานขายสังกัดรหัสตัวเอง ต้องดึงจาก
    Fabric ก่อนถึงจะรู้ ซึ่งเป็นไก่กับไข่ — ยิงทุกครั้งที่ล็อกอินก็ช้าเกินไป
    จึงอ่านชื่อไฟล์ในโฟลเดอร์ data รอบเดียว: รหัสไหนเคยมีข้อมูลพนักงานจริง
    รหัสนั้นถือว่ามีทีมของตัวเอง · เปิดทีมตัวเองครั้งแรกเมื่อไหร่ ระบบจะจำได้เอง
    ตั้งแต่นั้น (คนที่ยังไม่เคยเปิด ก็ยังเข้าหน้าทีมซุปทีมแรกเหมือนเดิม)
    """
    out: set[str] = set()
    try:
        names = os.listdir(data_dir)
    except OSError:
        return out
    for name in names:
        m = _EMP_FILE_RE.match(name)
        if not m:
            continue
        code = m.group(1).strip().upper()
        if not code or code in out:
            continue
        try:
            with open(os.path.join(data_dir, name), encoding="utf-8-sig") as f:
                f.readline()                      # header
                if f.readline().strip():          # มีอย่างน้อยหนึ่งแถว
                    out.add(code)
        except OSError:
            continue
    return out


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
    if str(row.get("login_kind") or "") != "manager_acc":
        return False
    ml = str(row.get("manager_level") or "").strip().lower()
    if ml == "division":
        return True
    if ml == "regional":
        return False
    return (
        str(row.get("acc_division") or "") == "Div.S"
        and not str(row.get("acc_region") or "").strip()
    )


def supervisor_region_for_code(code: str, roster: dict[str, dict[str, Any]]) -> str:
    row = roster.get(code.strip().upper())
    if not row:
        return ""
    return str(row.get("acc_region") or "").strip()


def team_supervisor_codes(
    team: list[str],
    manager_code: str,
    exclude_manager_codes: set[str] | None = None,
    *,
    keep_own_code: bool = False,
) -> list[str]:
    """
    รหัส Supervisor ในทีม — ตัดรหัส Manager ที่เกี่ยวข้อง (ไม่ใช่ Manager ทั้งองค์กร)

    keep_own_code: บางรหัสผู้จัดการมีพนักงานขายสังกัดตรง ไม่ได้ผ่านทีมซุปเลย
    (เจอจริง 5 คนจาก 25 เช่น SL359 มีพนักงาน 4 คนที่มีเป้าเต็ม ๆ) พอตัดรหัสตัวเอง
    ออกเสมอ คนกลุ่มนี้จึงไม่มีทีมไหนเปิดถึงได้เลย ทั้งที่ ensure_supervisor_allowed
    ผ่านอยู่แล้ว — สิทธิ์มีแต่ไม่มีปุ่มให้กด · รหัสพ้องที่ผูกไว้ (sl_links) ยังตัดเหมือนเดิม
    """
    mgr = manager_code.strip().upper()
    excl = {str(x).strip().upper() for x in (exclude_manager_codes or ())}
    if keep_own_code:
        excl.discard(mgr)
    else:
        excl.add(mgr)
    out: list[str] = []
    seen: set[str] = set()
    for raw in team:
        c = str(raw or "").strip().upper()
        if not c or c in excl or c in seen:
            continue
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
    exclude_manager_codes: set[str] | None = None,
    own_salesmen_codes: set[str] | None = None,
) -> dict[str, Any]:
    """
    คืนตัวเลือกมุมมองสำหรับ Manager:
    - division-wide: individual + all + regions[]
    - regional: individual + region (ทั้งภาคเดียว)

    own_salesmen_codes: รหัสที่รู้แล้วว่ามีพนักงานสังกัดตรง (ดู codes_with_own_salesmen)
    ใช้บอกหน้าเว็บว่าควรเปิดหน้าทีมของตัวเองเป็นหน้าแรกไหม
    """
    mgr = manager_code.strip().upper()
    known = own_salesmen_codes if own_salesmen_codes is not None else codes_with_own_salesmen()
    own_has_staff = bool(mgr) and mgr in known
    roster = _row_by_userpl()
    mgr_row = roster.get(mgr)
    # ทีมซุปจริง ๆ ใต้ผู้จัดการ — ใช้กับการรวมเป้า (ยอดรวมต้องไม่ขยับจากของเดิม)
    team_only = team_supervisor_codes(team_codes, mgr, exclude_manager_codes)
    # รายการให้ "เลือกเปิดทีละทีม" มีรหัสตัวเองด้วย เผื่อมีพนักงานขายสังกัดตรง
    # (ดู team_supervisor_codes) ไม่มีพนักงานก็เปิดแล้วเจอทีมว่าง ซึ่งเป็นความจริง
    supers = sorted({*team_only, mgr}) if mgr else list(team_only)

    meta: dict[str, dict[str, str]] = {}
    by_region: dict[str, list[str]] = {}
    for sc in team_only:
        reg = supervisor_region_for_code(sc, roster)
        meta[sc] = {"region": reg}
        if reg:
            by_region.setdefault(reg, []).append(sc)

    if mgr and mgr not in meta:
        meta[mgr] = {"region": supervisor_region_for_code(mgr, roster)}

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
            "own_team_has_staff": own_has_staff,
        }

    mgr_region = str((mgr_row or {}).get("acc_region") or "").strip()
    modes = ["individual", "region"]
    region_entry = {
        "id": mgr_region or "__team__",
        "label": _region_display_label(mgr_region) if mgr_region else "ทั้งทีม",
        "supervisor_codes": list(team_only),
    }
    return {
        "manager_code": mgr,
        "scope_kind": "region",
        "modes": modes,
        "regions": [region_entry],
        "supervisor_meta": meta,
        "supervisor_codes": supers,
        "own_team_has_staff": own_has_staff,
        "manager_region": mgr_region,
    }


def build_manager_views_map(
    by_manager: dict[str, list[str]] | None,
    manager_codes: list[str] | None = None,
    manager_pick: set[str] | list[str] | None = None,
    sl_links: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """สร้าง manager_views สำหรับทุกรหัส Manager ในทีม (ใช้ทั้ง user ปกติและแอดมิน)"""
    from .sl_link_store import manager_codes_to_exclude_from_team, read_links

    links = sl_links if sl_links is not None else read_links()
    picks = manager_pick if manager_pick is not None else (manager_codes or [])
    bm: dict[str, list[str]] = {}
    for k, v in (by_manager or {}).items():
        mk = str(k or "").strip().upper()
        if not mk:
            continue
        bm[mk] = sorted({str(x).strip().upper() for x in (v or []) if str(x).strip()})
    codes = manager_codes if manager_codes is not None else sorted(bm.keys())
    known = codes_with_own_salesmen()      # อ่านชื่อไฟล์รอบเดียวสำหรับผู้จัดการทุกคน
    out: dict[str, Any] = {}
    for m in sorted({str(c).strip().upper() for c in codes if str(c).strip()}):
        excl = manager_codes_to_exclude_from_team(m, picks, links)
        out[m] = build_manager_view_options(m, bm.get(m, []), excl, known)
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
        # supervisor_codes มีรหัสของผู้จัดการเองรวมอยู่ด้วย (ไว้ให้เลือกเปิดทีละทีม)
        # แต่การรวมเป้ายังนับเฉพาะทีมซุปเหมือนเดิม ยอดรวมจึงไม่ขยับจากของเก่า
        return [c for c in opts["supervisor_codes"] if c != mgr]

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
