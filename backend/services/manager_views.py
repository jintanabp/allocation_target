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
    # ทีมที่เอาไปรวมเป้า — รวมรหัสของผู้จัดการเองด้วย "เมื่อเขามีพนักงานสังกัดตรง"
    #
    # เดิมตัดรหัสผู้จัดการออกเสมอเพื่อให้ยอดรวมไม่ขยับจากของเดิม แต่ผลคือเป้าของ
    # พนักงานที่สังกัดผู้จัดการโดยตรงหายไปจากยอดรวมภาคทั้งก้อน ทีมของเขาจึงไม่เคย
    # ถูกเกลี่ยร่วมกับใคร ทั้งที่เป็นทีมในภาคเดียวกันแท้ ๆ
    #
    # ผู้จัดการที่ไม่มีพนักงานสังกัดตรงยังถูกตัดเหมือนเดิม ยอดของเขาจึงไม่ขยับ
    team_only = team_supervisor_codes(
        team_codes, mgr, exclude_manager_codes, keep_own_code=own_has_staff
    )
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
        # อยู่ในรายการให้เลือกเปิดทีละทีมได้ แต่ไม่ถูกนับรวมเป้า (ไม่มีพนักงานสังกัดตรง)

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


def has_team_data_in_period(
    code: str, month: int, year: int, data_dir: str = "data"
) -> bool:
    """
    รหัสนี้มีพนักงาน/แถวเป้าของงวดที่ระบุไหม — ดูจากไฟล์ในเครื่อง ไม่ยิงอะไร

    ต่างจาก codes_with_own_salesmen() ตรงที่ "ผูกกับงวด" · ตัวนั้นตอบว่าเคยมีไหม
    (ใช้ตอนล็อกอินซึ่งยังไม่รู้งวด) ตัวนี้ตอบว่างวดนี้มีไหม

    ดูจาก **แถวเป้า** ไม่ใช่รายชื่อพนักงาน — ทีมที่มีคนแต่ไม่มีเป้าเลยเอาไปรวมก็ไม่มี
    อะไรให้รวม และกระจายไม่ได้ (ของจริง: ผู้จัดการคนหนึ่งมีพนักงาน 3 คนในงวดนี้
    แต่ทั้งสามไม่มีแถวเป้าสักแถว ทีมนั้นจึงเปิดไม่ได้และไม่ควรนับเป็นทีมหนึ่ง)
    """
    c = str(code or "").strip().upper()
    if not c:
        return False
    path = os.path.join(
        data_dir, f"tga_lines_{c}_{int(year):04d}_{int(month):02d}.csv"
    )
    try:
        with open(path, encoding="utf-8-sig") as f:
            f.readline()                          # header
            return bool(f.readline().strip())     # มีอย่างน้อยหนึ่งแถวเป้า
    except OSError:
        return False


def drop_manager_code_without_team(
    codes: list[str], manager_code: str, month: int, year: int
) -> list[str]:
    """
    เอารหัสผู้จัดการที่งวดนี้ไม่มีพนักงานสังกัดตรง ออกจากขอบเขต

    ไม่ใช่แค่รหัสของคนที่กำลังเปิดดู — ผู้จัดการภาคเดียวกันเห็นกันและกันได้แล้ว
    รหัสของอีกฝ่ายจึงเข้ามาอยู่ในขอบเขตด้วย ถ้างวดนั้นเขาไม่มีเป้า ทีมนั้นจะ
    โหลดไม่ได้แล้วกลายเป็น "ทีมที่ถูกข้าม" พร้อมคำเตือนทุกครั้งที่เปิดหน้า

    ผู้จัดการที่มีพนักงานสังกัดตรงยังนับเป็นทีมหนึ่งเหมือนซุปคนหนึ่ง — เป้าของ
    พนักงานกลุ่มนั้นต้องอยู่ในยอดรวมภาคด้วย · ทีมซุปจริงที่ยังไม่มีข้อมูลไม่ถูกตัด
    """
    mgr = str(manager_code or "").strip().upper()
    try:
        roster = _row_by_userpl()
    except Exception:                             # อ่าน user_access ไม่ได้ = ตัดสินจากรหัสที่ส่งมา
        roster = {}

    def _is_manager(code: str) -> bool:
        if code == mgr:
            return True
        row = roster.get(code) or {}
        return str(row.get("login_kind") or "") == "manager_acc"

    out: list[str] = []
    for raw in codes or []:
        c = str(raw or "").strip().upper()
        if not c:
            continue
        if _is_manager(c) and not has_team_data_in_period(c, month, year):
            continue
        out.append(c)
    # ตัดจนไม่เหลืออะไรเลย = ตัดสินผิดแน่ ๆ — คืนของเดิมให้ด่านถัดไปว่ากันต่อ
    return out or list(codes or [])


def units_of_codes(codes: list[str]) -> dict[str, str]:
    """หน่วยขายของแต่ละรหัสทีมจาก user_access — "" = ไม่ได้ระบุ"""
    roster = _row_by_userpl()
    out: dict[str, str] = {}
    for raw in codes or []:
        c = str(raw or "").strip().upper()
        if not c:
            continue
        unit = str((roster.get(c) or {}).get("acc_unit") or "").strip().lower()
        # "all" ถือเหมือนไม่ระบุ — ติดไปกับทุกหน่วยที่เลือก และไม่นับเป็นหน่วยของตัวเอง
        out[c] = unit if unit in ("credit", "van") else ""
    return out


def filter_codes_by_unit(codes: list[str], unit: str | None) -> list[str]:
    """
    เหลือเฉพาะทีมของหน่วยขายที่เลือก — unit ว่าง/ไม่รู้จัก = ไม่กรอง

    ทีมที่ยังไม่ได้ระบุหน่วยจะติดมาด้วยเสมอ ไม่ว่าเลือกหน่วยไหน (ถือว่าเป็น all
    ไปก่อน) · ข้อมูลไม่ครบ
    ต้องไม่ทำให้ทีมหายไปจากมุมมองเงียบ ๆ (ของจริง acc_unit ว่างเกือบครึ่ง)
    ด่านกันกระจายข้ามหน่วยก็ไม่นับทีมที่ไม่รู้หน่วยเหมือนกัน สองที่จึงสอดคล้องกัน
    """
    # "all" / ว่าง / ค่าที่ไม่รู้จัก = ดูทั้งสองหน่วย — ทีมที่ยังไม่ระบุหน่วยก็ใช้ทางนี้
    # เพื่อไม่ให้เปิดอะไรไม่ได้เลย (ติดธง "ต้องตรวจสอบ" ในหน้าแอดมินแทน)
    want = str(unit or "").strip().lower()
    if want not in ("credit", "van"):
        return list(codes or [])
    units = units_of_codes(list(codes or []))
    return [c for c in (codes or []) if units.get(str(c).strip().upper(), "") in (want, "")]


def units_present_in(codes: list[str]) -> list[str]:
    """หน่วยขายที่มีจริงในชุดรหัสนี้ — หน้าเว็บใช้ตัดสินว่าต้องโชว์ตัวเลือกหน่วยไหม"""
    return sorted({u for u in units_of_codes(list(codes or [])).values() if u})


def resolve_aggregate_supervisor_codes(
    manager_code: str,
    team_codes: list[str],
    view: str,
    region: str | None = None,
    *,
    own_salesmen_codes: set[str] | None = None,
) -> list[str]:
    opts = build_manager_view_options(
        manager_code, team_codes, own_salesmen_codes=own_salesmen_codes
    )
    mgr = manager_code.strip().upper()
    view = (view or "").strip().lower()
    if view == "all":
        if "all" not in opts["modes"]:
            raise ValueError("ไม่มีสิทธิ์ดูแบบรวมทั้งหมด")
        # supervisor_codes มีรหัสของผู้จัดการเองรวมอยู่ด้วย (ไว้ให้เลือกเปิดทีละทีม)
        # แต่การรวมเป้านับเฉพาะทีมที่ build_manager_view_options ตัดสินแล้วว่าควรนับ
        # — ซึ่งรวมรหัสผู้จัดการเมื่อเขามีพนักงานสังกัดตรง (ดู keep_own_code ที่นั่น)
        countable = set()
        for entry in opts.get("regions") or []:
            countable.update(entry.get("supervisor_codes") or [])
        if not countable:
            countable = {c for c in opts["supervisor_codes"] if c != mgr}
        return sorted(countable)

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
