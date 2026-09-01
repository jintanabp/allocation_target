"""
สรุปการใช้งานระบบต่องวด — ใครใช้จริงบ้าง เทียบกับทั้งหมดที่มีในระบบ

คำถามที่ตอบ:
  - ระดับทีม  : ทีมที่กระจายเป้าได้มีกี่ทีม เข้ามาใช้จริงกี่ทีม ส่ง Target Sun กี่ทีม
  - ระดับคน   : พนักงานทั้งหมดกี่คน ถูกกระจายเป้ากี่คน และกระจาย+ส่งแล้วกี่คน

หลักการนับที่ต้องไม่หลุด:
  1. **นับพนักงานเป็นคู่ (ทีม, รหัส) เสมอ** รหัสพนักงานซ้ำข้ามทีมได้ (invariant I7)
     ถ้ายุบเป็นเซ็ตรหัสล้วน ยอดรวมทั้งบริษัทจะต่ำกว่าความจริงแบบดูไม่ออก
  2. **ผู้จัดการนับเป็น 1 รหัส ไม่กระจายออกเป็นทีมลูกน้อง** ทีมของผู้จัดการซ้อนทับ
     กันเอง ถ้านับซ้ำตัวหารจะเฟ้อ · ผลกระจายของผู้จัดการถูกบันทึกลงทีมของแต่ละซุป
     ตามจริงอยู่แล้ว จึงไปโผล่เป็น "เข้ามาใช้" ของซุปเหล่านั้น ไม่ใช่ของรหัสผู้จัดการ
  3. **ตัวหารต้องหดตามขอบเขตของแอดมินด้วย** ไม่ใช่หดแค่ตัวเศษ ไม่งั้นแอดมินรายภาค
     เห็นยอดรวมทั้งบริษัทที่ตัวเองไม่มีสิทธิ์รู้
  4. **"ส่งแล้ว" ดูจาก target_sun_sent_at ไม่ใช่ status** เพราะสถานะกลับไปเป็น
     draft ได้ถ้ามีคนแก้ต่อหลังส่ง แต่ความจริงคือส่งไปแล้ว

อ่านไฟล์ในเครื่องล้วน — ไม่ยิง Fabric ไม่ยิง Target Sun (รายชื่อพนักงานมาจาก
แคชที่ company_roster ดูแล ซึ่งเป็นคนละจังหวะกับการเปิดหน้านี้)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from . import company_roster, fabric_cache
from .allocating_teams import allocating_teams
from .allocation_store import allocations_dir, list_all_snapshots
from .sl_link_store import read_links, resolve_to_canonical
from .usage_log_store import logs_dir
from .user_access_store import user_access_json_path

logger = logging.getLogger("target_allocation")

# ช่วงวันที่ของไฟล์ log ที่อาจมีการส่งของงวดนี้อยู่ — คนส่งเป้าเดือนหน้าตั้งแต่
# เดือนนี้ และตามแก้อีกหลายสัปดาห์ กว้าง ๆ ไว้ปลอดภัยกว่าพลาดการส่งไปเงียบ ๆ
_LOG_WINDOW_DAYS = 90

# read_logs ค่าเริ่มต้นตัดที่ 200 แถวแล้วทิ้งที่เหลือ — สำหรับรายงานต้องอ่านให้ครบ
_LOG_ROW_CAP = 200_000

_PERIOD_IN_DETAIL = re.compile(r"งวด\s*(\d{4})-(\d{1,2})")

NO_REGION_LABEL = "ไม่ระบุภาค"

# วิธีนับ "กระจาย+ส่ง" ของแต่ละทีม
METHOD_EXACT = "exact"            # มีรายชื่อคนที่ส่งจริงในบันทึกการใช้งาน
METHOD_TEAM_APPROX = "team_approx"  # รู้แค่ว่าทีมนี้ส่งแล้ว — ประมาณด้วยคนที่ได้หีบทั้งทีม
METHOD_NONE = ""                  # ยังไม่ได้ส่ง


# ── บันทึกการส่ง: ใครถูกส่งไปบ้าง ────────────────────────────────────────

def _period_from_log(row: dict[str, Any]) -> tuple[int, int] | None:
    """
    งวดเป้าของแถว log — ฟิลด์ตรง ๆ ก่อน ไม่มีค่อยแกะจากข้อความ

    แถวที่เขียนก่อนแก้บั๊กไม่มี target_month/target_year เลย ต้องพึ่ง detail
    ที่ขึ้นต้นด้วย "งวด YYYY-MM" ตลอดไป — ห้ามลบ fallback นี้ทิ้ง
    """
    m, y = row.get("target_month"), row.get("target_year")
    try:
        if m and y:
            return int(m), int(y)
    except (TypeError, ValueError):
        pass
    hit = _PERIOD_IN_DETAIL.search(str(row.get("detail") or ""))
    if hit:
        try:
            return int(hit.group(2)), int(hit.group(1))
        except ValueError:
            return None
    return None


def _log_paths_near(month: int, year: int) -> list[str]:
    """
    ไฟล์ log ที่อาจมีการส่งของงวดนี้ — คัดจาก **ชื่อไฟล์** เอง

    ห้ามส่ง target_month/target_year เข้า read_logs เพราะตัวนั้นกรองตามวันที่ของ
    ไฟล์ (วันที่กด) ไม่ใช่งวดเป้า — ใช้แล้วจะได้ผลที่ดูสมเหตุสมผลแต่ผิด
    """
    root = logs_dir()
    if not os.path.isdir(root):
        return []
    start = date(int(year), int(month), 1)
    lo, hi = start - timedelta(days=_LOG_WINDOW_DAYS), start + timedelta(days=_LOG_WINDOW_DAYS)
    out: list[str] = []
    for name in sorted(os.listdir(root)):
        if not (name.startswith("usage_") and name.endswith(".jsonl")):
            continue
        try:
            stamp = date.fromisoformat(name[len("usage_"):-len(".jsonl")])
        except ValueError:
            continue
        if lo <= stamp <= hi:
            out.append(os.path.join(root, name))
    return out


def _send_ok(row: dict[str, Any]) -> bool:
    """ส่งสำเร็จไหม — ยอมรับได้แม้ตรวจยอดย้อนกลับไม่ผ่าน (ของลงปลายทางไปแล้ว)"""
    ctx = row.get("context")
    if isinstance(ctx, dict) and "ok" in ctx:
        return bool(ctx.get("ok"))
    msg = str(row.get("message") or "")
    return "ไม่สำเร็จ" not in msg and "สำเร็จ" in msg


def sent_emp_ids_by_team(month: int, year: int) -> dict[str, dict[str, Any]]:
    """
    {รหัสทีม: {"emp_ids": set, "truncated": bool, "sends": int, "last_ts": str}}

    มีเฉพาะทีมที่มีบันทึกการส่งของงวดนี้ · ทีมที่ส่งก่อนระบบเริ่มเก็บรายชื่อจะได้
    emp_ids ว่างแต่ยังนับเป็น "เคยส่ง" — ผู้เรียกต้องถอยไปใช้ค่าประมาณระดับทีม
    """
    links = read_links()
    out: dict[str, dict[str, Any]] = {}
    for path in _log_paths_near(month, year):
        try:
            with open(path, encoding="utf-8") as fh:
                lines = fh.readlines()
        except OSError as e:
            logger.warning("อ่านบันทึกการใช้งาน %s ไม่ได้: %s", path, e)
            continue
        for line in lines[:_LOG_ROW_CAP]:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict) or row.get("action") != "send_targetsun":
                continue
            if _period_from_log(row) != (int(month), int(year)) or not _send_ok(row):
                continue
            sup = resolve_to_canonical(str(row.get("sup_id") or "").strip(), links)
            if not sup:
                continue
            entry = out.setdefault(
                sup, {"emp_ids": set(), "truncated": False, "sends": 0, "last_ts": ""}
            )
            entry["sends"] += 1
            ts = str(row.get("ts") or "")
            if ts > entry["last_ts"]:
                entry["last_ts"] = ts
            ctx = row.get("context") if isinstance(row.get("context"), dict) else {}
            ids = ctx.get("emp_ids")
            if isinstance(ids, list):
                entry["emp_ids"].update(str(e).strip().upper() for e in ids if str(e).strip())
            if ctx.get("emp_ids_truncated"):
                entry["truncated"] = True
    return out


# ── แคชผลนับต่องวด ───────────────────────────────────────────────────────

def _summary_cache_path(month: int, year: int) -> str:
    return os.path.join(fabric_cache.cache_dir(), f"usage_summary_{int(year)}_{int(month):02d}.json")


def _period_signature(month: int, year: int) -> str:
    """
    ลายเซ็นของ "ข้อมูลดิบทั้งหมดที่ผลนับขึ้นกับมัน"

    ใช้ mtime+ขนาดไฟล์แทน TTL เพราะแม่นยำกว่าและไม่มีช่วงข้อมูลเก่าค้าง —
    ส่งใหม่ กระจายใหม่ แก้สิทธิ์ หรือดึงรายชื่อพนักงานใหม่ ลายเซ็นขยับหมด
    การ listdir + stat ทั้งหมดนี้ใช้เวลาต่ำกว่ามิลลิวินาที ต่างจากการเปิดอ่าน
    snapshot จริงซึ่งเป็นไฟล์เมกะไบต์
    """
    parts: list[str] = []
    suffix = f"_{int(year)}_{int(month):02d}.json"
    root = allocations_dir()
    try:
        names = sorted(n for n in os.listdir(root) if n.endswith(suffix))
    except OSError:
        names = []
    paths = [os.path.join(root, n) for n in names]
    paths += _log_paths_near(month, year)
    paths += [user_access_json_path(), fabric_cache._roster_path()]
    for path in paths:
        try:
            st = os.stat(path)
            parts.append(f"{os.path.basename(path)}:{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            parts.append(f"{os.path.basename(path)}:-")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def _read_cached_facts(month: int, year: int, signature: str) -> dict[str, Any] | None:
    path = _summary_cache_path(month, year)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(doc, dict) or doc.get("signature") != signature:
        return None
    return doc


def _write_cached_facts(month: int, year: int, doc: dict[str, Any]) -> None:
    try:
        fabric_cache._write_json_cache(_summary_cache_path(month, year), doc)
    except OSError as e:
        logger.warning("เขียนแคชสรุปการใช้งานไม่ได้: %s", e)


# ── สแกนข้อมูลดิบของงวด ─────────────────────────────────────────────────

def _scan_period_facts(month: int, year: int) -> dict[str, Any]:
    """
    ข้อเท็จจริงต่อทีมของงวดนี้ — ส่วนที่แพงที่สุด (เปิดอ่าน snapshot ทุกไฟล์)

    แยกออกมาเป็นก้อนเดียวเพื่อให้แคชได้ทั้งก้อน และ **ไม่กรองขอบเขตที่นี่**
    แคชก้อนเดียวจะได้ใช้ร่วมกันได้ทุกแอดมิน แล้วค่อยกรองในหน่วยความจำทีหลัง
    """
    links = read_links()
    sent = sent_emp_ids_by_team(month, year)
    facts: dict[str, dict[str, Any]] = {}
    for snap in list_all_snapshots(month, year, with_emp_ids=True):
        sup = resolve_to_canonical(str(snap.get("sup_id") or ""), links)
        if not sup:
            continue
        emp_ids = {str(e).strip().upper() for e in (snap.get("emp_ids") or set())}
        cur = facts.get(sup)
        if cur:
            # สองรหัสที่ผูกกันมี snapshot คนละไฟล์ — รวมเป็นทีมเดียว
            cur["allocated_emp_ids"] = sorted(set(cur["allocated_emp_ids"]) | emp_ids)
            cur["allocation_rows"] += int(snap.get("allocation_rows") or 0)
            if not cur.get("target_sun_sent_at"):
                cur["target_sun_sent_at"] = snap.get("target_sun_sent_at")
            continue
        facts[sup] = {
            "allocated_emp_ids": sorted(emp_ids),
            "allocation_rows": int(snap.get("allocation_rows") or 0),
            "status": snap.get("status") or "",
            "updated_at": snap.get("updated_at") or "",
            "updated_by": snap.get("updated_by") or "",
            "target_sun_sent_at": snap.get("target_sun_sent_at") or "",
        }
    for sup, info in sent.items():
        entry = facts.setdefault(sup, {
            "allocated_emp_ids": [], "allocation_rows": 0, "status": "",
            "updated_at": "", "updated_by": "", "target_sun_sent_at": "",
        })
        entry["sent_emp_ids"] = sorted(info["emp_ids"])
        entry["sent_emp_ids_truncated"] = bool(info["truncated"])
        entry["send_events"] = int(info["sends"])
        entry["last_send_ts"] = info["last_ts"]
    return {"teams": facts}


# ── ประกอบรายงาน ────────────────────────────────────────────────────────

def _team_numbers(
    team: dict[str, Any],
    fact: dict[str, Any],
    roster_ids: set[str],
) -> dict[str, Any]:
    allocated = {str(e).upper() for e in fact.get("allocated_emp_ids") or []}
    sent_ids = {str(e).upper() for e in fact.get("sent_emp_ids") or []}
    ever_sent = bool(fact.get("target_sun_sent_at")) or bool(fact.get("send_events"))
    truncated = bool(fact.get("sent_emp_ids_truncated"))

    if sent_ids and not truncated:
        sent_count, method = len(allocated & sent_ids), METHOD_EXACT
    elif ever_sent:
        # รู้แค่ว่าทีมนี้ส่งแล้ว — การส่งหนึ่งครั้งครอบคลุมพนักงานที่มีสิทธิ์ทุกคน
        # จึงประมาณด้วยคนที่ได้หีบทั้งทีม · อาจสูงกว่าจริงเล็กน้อยถ้ามีการตัด SKU
        # ทั้งตัวออกจนบางคนไม่เหลืออะไรให้ส่ง
        sent_count, method = len(allocated), METHOD_TEAM_APPROX
    else:
        sent_count, method = 0, METHOD_NONE

    return {
        **team,
        "employees": len(roster_ids),
        "allocated": len(allocated),
        "allocated_not_in_roster": len(allocated - roster_ids) if roster_ids else 0,
        "sent": sent_count,
        "method": method,
        "used": len(allocated) > 0,
        "ever_sent": ever_sent,
        "has_snapshot": bool(fact.get("status") or fact.get("allocation_rows")),
        "status": fact.get("status") or "",
        "updated_at": fact.get("updated_at") or "",
        "updated_by": fact.get("updated_by") or "",
        "target_sun_sent_at": fact.get("target_sun_sent_at") or "",
        "allocation_rows": int(fact.get("allocation_rows") or 0),
    }


def _pct(part: int, whole: int | None) -> float | None:
    """สัดส่วน — ตัวหารเป็น 0 หรือยังไม่รู้ คือ "ไม่มีอะไรให้เทียบ" ไม่ใช่ 0%"""
    if not whole:
        return None
    return round(min(100.0, part * 100.0 / whole), 1)


def _region_label(team: dict[str, Any]) -> str:
    return str(team.get("acc_region") or "").strip() or NO_REGION_LABEL


def _roll_up(rows: list[dict[str, Any]]) -> dict[str, Any]:
    teams_total = len(rows)
    used = sum(1 for r in rows if r["used"])
    sent_teams = sum(1 for r in rows if r["ever_sent"])
    # employees เป็น None ได้เมื่อยังไม่มีทะเบียนพนักงาน — รวมแล้วต้องยังเป็น None
    # (ไม่มีทีมเลย = 0 คนจริง ๆ ไม่ใช่ "ไม่รู้")
    unknown = any(r["employees"] is None for r in rows)
    employees = None if unknown else sum(r["employees"] for r in rows)
    allocated = sum(r["allocated"] for r in rows)
    sent = sum(r["sent"] for r in rows)
    return {
        "teams": teams_total,
        "used": used,
        "used_pct": _pct(used, teams_total),
        "sent_teams": sent_teams,
        "sent_teams_pct": _pct(sent_teams, teams_total),
        "employees": employees,
        "allocated": allocated,
        "allocated_pct": _pct(allocated, employees),
        "sent": sent,
        "sent_pct": _pct(sent, employees),
    }


def _overall_method(rows: list[dict[str, Any]]) -> str:
    methods = {r["method"] for r in rows if r["method"]}
    if not methods:
        return METHOD_NONE
    if methods == {METHOD_EXACT}:
        return METHOD_EXACT
    if methods == {METHOD_TEAM_APPROX}:
        return METHOD_TEAM_APPROX
    return "mixed"


def build_usage_summary(
    *,
    month: int,
    year: int,
    sl_codes: set[str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """
    รายงานสรุปการใช้งานของงวดหนึ่ง

    sl_codes = None คือไม่จำกัด (dev) · ถ้าส่งมา **ทั้งตัวเศษและตัวหารหดตามทั้งคู่**
    """
    t0 = time.perf_counter()
    signature = _period_signature(month, year)
    cached = None if force else _read_cached_facts(month, year, signature)
    if cached:
        facts = cached.get("teams") or {}
        from_cache = True
    else:
        scanned = _scan_period_facts(month, year)
        facts = scanned["teams"]
        # ตรวจลายเซ็นซ้ำหลังสแกน — ถ้ามีคนบันทึกผลกระจายคั่นกลาง ตัวเลขที่ได้เป็น
        # ภาพที่ขาดครึ่ง ห้ามแช่ลงแคชให้คนอื่นอ่านต่อ (รอบหน้าค่อยคำนวณใหม่)
        if _period_signature(month, year) == signature:
            _write_cached_facts(month, year, {"signature": signature, **scanned})
        from_cache = False

    teams = allocating_teams()
    scoped = sl_codes is not None
    if scoped:
        want = {str(c).strip().upper() for c in sl_codes}
        teams = [t for t in teams if t["sup_id"] in want]
    code_set = {t["sup_id"] for t in teams}

    roster = company_roster.get_company_roster()
    by_team = company_roster.roster_by_team(roster.get("rows") or [])

    rows = [
        _team_numbers(t, facts.get(t["sup_id"]) or {}, by_team.get(t["sup_id"], set()))
        for t in teams
    ]

    # พนักงานที่มีอยู่แต่สังกัดรหัสที่กระจายเป้าไม่ได้ — ไม่อยู่ในตัวหาร แต่ต้อง
    # บอกไว้ ไม่งั้นยอดรวมกับ Dim_Salesman ไม่กระทบกันแล้วหาสาเหตุไม่เจอ
    outside = sum(len(v) for k, v in by_team.items() if k not in code_set)

    # คนที่ SuperCode ว่าง/NONE/(BLANK) ใน Dim_Salesman — ไม่มีทีมให้สังกัด
    # roster_by_team ข้ามไป จึงไม่อยู่ทั้งในตัวหารและในกอง outside ถ้าไม่นับไว้
    # ตรงนี้ คนกลุ่มนี้จะหายไปเฉย ๆ แล้ว "N คนทั้งบริษัท" ที่โชว์ข้างการ์ด
    # จะไม่เท่ากับผลบวกของช่องอื่น — กระทบยอดไม่ได้ทั้งที่โฆษณาไว้ว่ากระทบได้
    no_team = sum(
        1 for r in (roster.get("rows") or [])
        if not str(r.get("super_code") or "").strip()
    )

    # รหัสพนักงานที่โผล่ในหลายทีม — เหตุผลที่ต้องนับเป็นคู่ (ทีม, รหัส)
    seen: dict[str, int] = {}
    for code in code_set:
        for emp in by_team.get(code, set()):
            seen[emp] = seen.get(emp, 0) + 1
    duplicates = sum(1 for n in seen.values() if n > 1)

    totals = _roll_up(rows)
    roster_ok = bool(roster.get("available"))
    if not roster_ok:
        # ยังไม่เคยดึงทะเบียนพนักงาน = "ไม่รู้" ไม่ใช่ "ศูนย์คน" — ต้องเป็น None ทุกที่
        # ไม่งั้นรายภาคขึ้น 0 แต่แถวรวมขึ้น — อ่านแล้วนึกว่าตัวเลขขัดกันเอง
        for r in rows:
            r["employees"] = None
    by_region: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_region.setdefault(_region_label(r), []).append(r)
    regions = [
        {"region": name, "method": _overall_method(group), **_roll_up(group)}
        for name, group in sorted(by_region.items(), key=lambda kv: (kv[0] == NO_REGION_LABEL, kv[0]))
    ]

    return {
        "period": {"target_month": int(month), "target_year": int(year)},
        "scope": {
            "scoped": scoped,
            "sl_codes_count": len(code_set),
            "label": "ทั้งระบบ" if not scoped else "เฉพาะทีมในขอบเขตที่ดูแล",
            "note": (
                "ผู้ดูแลรายภาคเห็นเฉพาะทีมซุปในขอบเขตของตน — รหัสผู้จัดการ"
                "ไม่ถูกรวมอยู่ในขอบเขต ตัวเลขจึงน้อยกว่าที่ dev เห็น"
                if scoped else ""
            ),
        },
        "teams": {
            "total": totals["teams"],
            "used": totals["used"],
            "used_pct": totals["used_pct"],
            "sent": totals["sent_teams"],
            "sent_pct": totals["sent_teams_pct"],
            "opened_no_boxes": sum(1 for r in rows if r["has_snapshot"] and not r["used"]),
        },
        "employees": {
            "total": totals["employees"] if roster.get("available") else None,
            "allocated": totals["allocated"],
            "allocated_pct": totals["allocated_pct"] if roster.get("available") else None,
            "sent": totals["sent"],
            "sent_pct": totals["sent_pct"] if roster.get("available") else None,
            "method": _overall_method(rows),
            # ทั้งสามช่องนี้เป็น None พร้อมกันเมื่อยังไม่มีทะเบียน = "ไม่รู้" ไม่ใช่ "ศูนย์"
            "not_under_allocating_team": outside if roster_ok else None,
            "no_super_code": no_team if roster_ok else None,
            "in_dim_salesman": roster.get("row_count") if roster_ok else None,
            "allocated_not_in_roster": sum(r["allocated_not_in_roster"] for r in rows),
            "duplicate_emp_ids_across_teams": duplicates,
        },
        "by_region": regions,
        "teams_detail": sorted(rows, key=lambda r: r["sup_id"]),
        "roster": {
            "available": bool(roster.get("available")),
            "stale": bool(roster.get("stale")),
            "cached_at": roster.get("cached_at"),
            "row_count": roster.get("row_count") or 0,
            "age_hours": (
                round((roster.get("age_sec") or 0) / 3600.0, 1)
                if roster.get("age_sec") is not None else None
            ),
            "error": roster.get("error"),
        },
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0)
        .isoformat().replace("+00:00", "Z"),
        "cached": from_cache,
        "computed_ms": int((time.perf_counter() - t0) * 1000),
    }


# ── แถวสำหรับ Excel ─────────────────────────────────────────────────────

_METHOD_TH = {
    METHOD_EXACT: "รายคน (จากบันทึกการส่ง)",
    METHOD_TEAM_APPROX: "ประมาณระดับทีม",
    "mixed": "ผสม (บางทีมรายคน บางทีมประมาณ)",
    METHOD_NONE: "—",
}

_LOGIN_KIND_TH = {"supervisor_acc": "Supervisor", "manager_acc": "Manager"}
_LEVEL_TH = {"regional": "รายภาค", "division": "ระดับ Division"}


def _fmt_pct(v: float | None) -> str:
    return "—" if v is None else f"{v:.1f}%"


def _fmt_int(v: int | None) -> Any:
    """None = ยังไม่รู้ (ไม่มีทะเบียนพนักงาน) — เขียน "—" ไม่ใช่ 0"""
    return "—" if v is None else v


def summary_kv_rows(s: dict[str, Any]) -> list[dict[str, Any]]:
    """ชีต "สรุป" — หัวข้อ / ค่า / หมายเหตุ (หมายเหตุคือคำนิยามที่ใช้บนหน้าจอ)"""
    t, e, r = s["teams"], s["employees"], s["roster"]
    p = s["period"]
    rows = [
        ("งวด", f"{p['target_month']:02d}/{p['target_year']}", s["scope"]["label"]),
        ("ทีมที่กระจายเป้าได้", t["total"],
         "รหัสล็อกอินที่ตำแหน่งเป็น Supervisor หรือ Manager และมีรหัส SL จริง "
         "· ไม่นับบัญชีแอดมินอย่างเดียว ไม่นับทีมสาธิต · รหัสที่ผูกกันแล้วนับเป็นรหัสเดียว "
         "· ผู้จัดการนับรหัสละ 1 ไม่กระจายออกเป็นทีมลูกน้อง (ทีมของผู้จัดการซ้อนทับกันเอง)"),
        ("ทีมที่เข้ามาใช้", t["used"],
         "มีผลกระจายบันทึกไว้ในงวดนี้ และมีหีบมากกว่า 0 อย่างน้อย 1 แถว "
         "· ไม่ใช่แค่ 'เปิดหน้าจอ' และไม่ใช่แค่ 'มีไฟล์'"),
        ("% ทีมที่เข้ามาใช้", _fmt_pct(t["used_pct"]), ""),
        ("ทีมที่มีผลกระจายแต่ยังไม่มีหีบ", t["opened_no_boxes"],
         "กดกระจายแล้วได้ 0 ทั้งทีม เช่น งวดนั้นไม่มีเป้า"),
        ("ทีมที่ส่ง Target Sun", t["sent"],
         "เคยส่งสำเร็จในงวดนี้ ดูจาก target_sun_sent_at ซึ่งอยู่ถาวร "
         "แม้ภายหลังจะกลับมาแก้จนสถานะเป็นแบบร่าง"),
        ("% ทีมที่ส่ง Target Sun", _fmt_pct(t["sent_pct"]), ""),
        ("พนักงานทั้งหมด", _fmt_int(e["total"]),
         "พนักงานใน Dim_Salesman ที่สังกัดทีมที่กระจายได้ ตัดรหัสขึ้นต้น V (รถเงินสด) ออก"
         + (f" · ข้อมูล ณ {r['cached_at']}" if r.get("cached_at") else "")),
        ("พนักงานที่ถูกกระจายเป้า", e["allocated"],
         "นับเป็นคู่ (ทีม, รหัสพนักงาน) ที่ได้หีบมากกว่า 0 "
         "— รหัสพนักงานซ้ำข้ามทีมได้ ถ้านับรหัสล้วนยอดรวมจะต่ำกว่าความจริง"),
        ("% พนักงานที่ถูกกระจายเป้า", _fmt_pct(e["allocated_pct"]), ""),
        ("พนักงานที่กระจาย+ส่ง Target Sun", e["sent"],
         "งวดที่มีบันทึกรายคน = แม่นระดับคน · งวดเก่าที่ยังไม่มี = ประมาณระดับทีม "
         "(ทีมไหนส่งแล้ว ถือว่าคนที่ได้หีบในทีมนั้นถูกส่งทั้งหมด) อาจสูงกว่าจริงเล็กน้อย"),
        ("% พนักงานที่กระจาย+ส่ง", _fmt_pct(e["sent_pct"]), ""),
        ("วิธีนับ 'กระจาย+ส่ง'", _METHOD_TH.get(e["method"], e["method"] or "—"), ""),
        ("พนักงานที่สังกัดรหัสซึ่งกระจายเป้าไม่ได้", _fmt_int(e["not_under_allocating_team"]),
         "ไม่อยู่ในตัวหาร เช่น สังกัดรหัสที่ไม่มีบัญชีในระบบ หรือทีมสาธิต"),
        ("พนักงานที่ไม่ระบุทีมใน Dim_Salesman", _fmt_int(e["no_super_code"]),
         "SuperCode ว่างหรือเป็น NONE — ไม่มีทีมให้สังกัด จึงไม่อยู่ในตัวหาร"),
        ("รวมพนักงานใน Dim_Salesman", _fmt_int(e["in_dim_salesman"]),
         "= พนักงานทั้งหมด + สังกัดรหัสที่กระจายไม่ได้ + ไม่ระบุทีม "
         "(ตัดรหัสขึ้นต้น V ออกแล้ว) — ใช้กระทบยอดว่าไม่มีใครหายไประหว่างทาง"),
        ("พนักงานที่ได้เป้าแต่ไม่อยู่ในทีมตามทะเบียน", e["allocated_not_in_roster"],
         "มักเป็นคนที่ถูกย้ายข้ามทีมเพื่อเกลี่ยเป้า"),
        ("รหัสพนักงานที่ซ้ำข้ามทีม", e["duplicate_emp_ids_across_teams"],
         "เหตุผลที่ทุกตัวเลขต้องนับเป็นคู่ (ทีม, รหัส)"),
        ("ข้อมูลพนักงาน ณ", r.get("cached_at") or "ยังไม่เคยดึง",
         "ค้างเก่า — ดึงใหม่ไม่สำเร็จ" if r.get("stale") else ""),
        ("คำนวณเมื่อ", s["generated_at"], "จากแคช" if s["cached"] else "คำนวณสด"),
    ]
    return [{"topic": a, "value": b, "note": c} for a, b, c in rows]


def region_rows(s: dict[str, Any]) -> list[dict[str, Any]]:
    """ชีต "รายภาค" — แถวรวมท้ายตารางต้องเท่ากับการ์ดตัวเลขเสมอ (หลักฐานว่าไม่นับซ้ำ)"""
    out = []
    for r in s["by_region"]:
        out.append({
            "region": r["region"],
            "teams": r["teams"],
            "used": r["used"],
            "used_pct": _fmt_pct(r["used_pct"]),
            "sent_teams": r["sent_teams"],
            "sent_teams_pct": _fmt_pct(r["sent_teams_pct"]),
            "employees": _fmt_int(r["employees"]),
            "allocated": r["allocated"],
            "allocated_pct": _fmt_pct(r["allocated_pct"]),
            "sent": r["sent"],
            "sent_pct": _fmt_pct(r["sent_pct"]),
            "method": _METHOD_TH.get(r["method"], r["method"] or "—"),
        })
    t, e = s["teams"], s["employees"]
    out.append({
        "region": "รวมทั้งหมด",
        "teams": t["total"], "used": t["used"], "used_pct": _fmt_pct(t["used_pct"]),
        "sent_teams": t["sent"], "sent_teams_pct": _fmt_pct(t["sent_pct"]),
        "employees": _fmt_int(e["total"]),
        "allocated": e["allocated"], "allocated_pct": _fmt_pct(e["allocated_pct"]),
        "sent": e["sent"], "sent_pct": _fmt_pct(e["sent_pct"]),
        "method": _METHOD_TH.get(e["method"], e["method"] or "—"),
    })
    return out


def team_rows(s: dict[str, Any]) -> list[dict[str, Any]]:
    """ชีต "รายทีม" — รวมทีมที่ยังไม่เคยใช้ด้วย นั่นคือประเด็นทั้งหมดของรายงานนี้"""
    return [{
        "sup_id": r["sup_id"],
        "full_name": r.get("full_name") or "",
        "login_kind": _LOGIN_KIND_TH.get(r.get("login_kind"), r.get("login_kind") or ""),
        "manager_level": _LEVEL_TH.get(r.get("manager_level"), r.get("manager_level") or ""),
        "acc_division": r.get("acc_division") or "",
        "acc_region": _region_label(r),
        "acc_unit": r.get("acc_unit") or "",
        "employees": _fmt_int(r["employees"]),
        "allocated": r["allocated"],
        "sent": r["sent"],
        "status": r.get("status") or "",
        "updated_at": r.get("updated_at") or "",
        "updated_by": r.get("updated_by") or "",
        "target_sun_sent_at": r.get("target_sun_sent_at") or "",
        "method": _METHOD_TH.get(r["method"], r["method"] or "—"),
    } for r in s["teams_detail"]]
