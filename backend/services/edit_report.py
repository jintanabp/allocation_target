"""
รายงานการแก้มือ (เฟส F1) — ซุปแก้เลขที่ระบบกระจายให้มากแค่ไหน ต่อทีม × งวด

อ่านอย่างเดียวจาก snapshot ผลกระจาย (data/allocations) ไม่แตะ Target Sun / Fabric
ตัวชี้ขาดจากผลสำรวจคือ "ต้องแก้มือแค่ไหน" — หน้านี้ทำให้เห็นเป็นตัวเลขโดยไม่ต้องไปถามทีละทีม

เทียบเฉพาะแถวที่มีเลขจากระบบ (engine_boxes) และไม่ได้มาจากเป้าเดิมใน Target Sun
(row_source="targetsun" ไม่ใช่ผลของตัวกระจาย เทียบไปก็ไม่มีความหมาย)
"""

from __future__ import annotations

from typing import Any

TOP_N = 5
# ต่างกันไม่ถึงเศษหีบ = ไม่ได้แก้ (กันเลขทศนิยมจากการปัดเศษ)
_EPS = 1e-6


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _usable_rows(snap: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for r in snap.get("allocations") or []:
        if not isinstance(r, dict) or r.get("engine_boxes") is None:
            continue
        if str(r.get("row_source") or "").strip().lower() == "targetsun":
            continue
        out.append(r)
    return out


def _top(bucket: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = sorted(bucket.values(), key=lambda d: (-d["boxes"], -d["cells"], d["code"]))
    return [
        {**d, "boxes": round(d["boxes"], 2)} for d in rows[:TOP_N] if d["boxes"] > _EPS
    ]


def team_metrics(snap: dict[str, Any]) -> dict[str, Any]:
    """ตัวเลขของหนึ่งทีม — ไม่มีแถวที่ใช้ได้คืน rows=0 พร้อม note"""
    rows = _usable_rows(snap)
    by_sku: dict[str, dict[str, Any]] = {}
    by_emp: dict[str, dict[str, Any]] = {}
    edited = 0
    abs_sum = 0.0
    to_zero = 0
    from_zero = 0
    for r in rows:
        eng = _num(r.get("engine_boxes"))
        got = _num(r.get("allocated_boxes"))
        diff = abs(got - eng)
        if diff <= _EPS:
            continue
        edited += 1
        abs_sum += diff
        if eng > _EPS and got <= _EPS:
            to_zero += 1
        elif eng <= _EPS and got > _EPS:
            from_zero += 1
        sku = str(r.get("sku") or "").strip()
        emp = str(r.get("emp_id") or "").strip()
        s = by_sku.setdefault(sku, {"code": sku, "name": "", "cells": 0, "boxes": 0.0})
        s["cells"] += 1
        s["boxes"] += diff
        s["name"] = s["name"] or str(r.get("product_name_thai") or r.get("sku_name") or "").strip()
        e = by_emp.setdefault(emp, {"code": emp, "name": "", "cells": 0, "boxes": 0.0})
        e["cells"] += 1
        e["boxes"] += diff
        e["name"] = e["name"] or str(r.get("emp_name") or "").strip()

    n = len(rows)
    out: dict[str, Any] = {
        "rows": n,
        "edited_cells": edited,
        "edited_pct": round(edited * 100.0 / n, 1) if n else None,
        # ย้ายหีบจากช่องหนึ่งไปอีกช่อง นับทั้งขาออกและขาเข้า จึงหารสอง
        "boxes_moved": round(abs_sum / 2.0, 2),
        "to_zero": to_zero,
        "from_zero": from_zero,
        "top_skus": _top(by_sku),
        "top_emps": _top(by_emp),
    }
    if not n:
        out["note"] = (
            "ไม่มีแถวที่มีเลขจากระบบให้เทียบ — บันทึกก่อนมีการเก็บเลขระบบ "
            "หรือเป็นเป้าเดิมจาก Target Sun ทั้งหมด"
        )
    return out


def nightly_for(state: dict[str, Any] | None, sup_id: str, month: int, year: int) -> dict[str, Any] | None:
    teams = (state or {}).get("teams") or {}
    v = teams.get(f"{str(sup_id).strip().upper()}|{int(year)}-{int(month):02d}")
    if not isinstance(v, dict):
        return None
    keys = ("status", "reason", "changed", "missing", "extra", "boxes_changed", "date")
    return {k: v.get(k) for k in keys if k in v}


def build_report(
    items: list[dict[str, Any]],
    snapshots: dict[str, dict[str, Any]],
    month: int,
    year: int,
    nightly_state: dict[str, Any] | None = None,
    nightly_enabled: bool = False,
) -> dict[str, Any]:
    """
    items = รายการทีมที่ผ่านการกรองขอบเขตแล้ว (จาก _scoped_allocation_items)
    snapshots = sup_id → snapshot เต็ม
    """
    teams: list[dict[str, Any]] = []
    tot_rows = tot_edited = 0
    tot_moved = 0.0
    for it in items:
        sid = str(it.get("sup_id") or "").strip().upper()
        snap = snapshots.get(sid)
        if not sid or not snap:
            continue
        m = team_metrics(snap)
        tot_rows += m["rows"]
        tot_edited += m["edited_cells"]
        tot_moved += m["boxes_moved"]
        teams.append({
            "sup_id": sid,
            "full_name": it.get("full_name") or "",
            "acc_region": it.get("acc_region") or "",
            "acc_division": it.get("acc_division") or "",
            "status": snap.get("status") or it.get("status") or "",
            "updated_by": snap.get("updated_by") or "",
            "updated_at": snap.get("updated_at") or "",
            "target_sun_sent_at": snap.get("target_sun_sent_at") or "",
            **m,
            "nightly": nightly_for(nightly_state, sid, month, year),
        })
    # ทีมที่แก้มากสุดขึ้นก่อน · ทีมที่ไม่มีแถวให้เทียบไปท้ายสุด
    teams.sort(key=lambda t: (t["edited_pct"] is None, -(t["edited_pct"] or 0), -t["boxes_moved"], t["sup_id"]))
    return {
        "target_month": int(month),
        "target_year": int(year),
        "team_count": len(teams),
        "totals": {
            "rows": tot_rows,
            "edited_cells": tot_edited,
            "edited_pct": round(tot_edited * 100.0 / tot_rows, 1) if tot_rows else None,
            "boxes_moved": round(tot_moved, 2),
        },
        # หน้าจอซ่อนคอลัมน์ Target Sun ถ้ายังไม่เคยเปิดตัวตรวจรายคืน (ไม่งั้นเห็นแต่ขีดทั้งคอลัมน์)
        "nightly_enabled": bool(nightly_enabled),
        "teams": teams,
    }
