#!/usr/bin/env python3
"""
ตรวจและซ่อม config/user_access.json:
- เติม acc_region จาก region_teams / รหัสภูมิภาคพิเศษ
- ตรวจว่าทุกแถวมีรหัส SL ของตัวเองในรายการที่ดูได้ (logic เดียวกับ access_control)
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
USER_ACCESS_PATH = os.path.join(REPO, "config", "user_access.json")
REGION_TEAMS_PATH = os.path.join(REPO, "config", "region_teams.json")
MANAGERS_CACHE_PATH = os.path.join(REPO, "data", "managers_cache.json")

REGIONAL_MANAGER_CODES = {
    "SL459": "ใต้",
    "SL526": "กลางหน่วยรถ",
    "SL535": "เหนือ",
}
DISTRICT_MANAGER_PREFIX = {
    "SL452": "อีสาน",
    "SL456": "อีสาน",
}


def load_region_teams() -> dict[str, list[str]]:
    if not os.path.isfile(REGION_TEAMS_PATH):
        return {}
    with open(REGION_TEAMS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return {}
    return {
        str(k): [str(x).strip().upper() for x in v if x]
        for k, v in data.items()
        if isinstance(v, list)
    }


def load_managers_metadata() -> dict[str, Any]:
    if os.path.isfile(MANAGERS_CACHE_PATH):
        try:
            with open(MANAGERS_CACHE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
    return {"supervisors": [], "manager_codes": [], "by_manager": {}}


def parse_trf(mdata: dict[str, Any]) -> tuple[set[str], set[str], dict[str, list[str]]]:
    by_m: dict[str, list[str]] = {}
    for k, v in (mdata.get("by_manager") or {}).items():
        by_m[str(k).strip().upper()] = [str(x).strip().upper() for x in (v or [])]
    supervisors = {str(x).strip().upper() for x in (mdata.get("supervisors") or []) if x}
    manager_codes = {str(x).strip().upper() for x in (mdata.get("manager_codes") or []) if x}
    if not supervisors and str(mdata.get("source") or "") == "dim_fallback":
        supervisors = {str(x).strip().upper() for x in (mdata.get("managers") or []) if x}
        manager_codes = set()
        by_m = {}
    return supervisors, manager_codes, by_m


def expand_visible(
    upl: str,
    meta: dict[str, Any] | None,
    supervisors: set[str],
    manager_codes: set[str],
    by_m: dict[str, list[str]],
    region_teams: dict[str, list[str]],
) -> set[str]:
    upl = upl.strip().upper()
    allowed: set[str] = set()
    login_kind = str((meta or {}).get("login_kind") or "standard")
    region = str((meta or {}).get("acc_region") or "")

    if login_kind in ("regional_manager", "district_manager") and region:
        for code in region_teams.get(region, []):
            cu = str(code).strip().upper()
            if cu in supervisors:
                allowed.add(cu)
        if allowed:
            if upl:
                allowed.add(upl)
            return allowed

    if login_kind == "supervisor_acc" and region and upl not in supervisors:
        for code in region_teams.get(region, []):
            cu = str(code).strip().upper()
            if cu in supervisors:
                allowed.add(cu)
        if allowed:
            if upl:
                allowed.add(upl)
            return allowed

    in_sup = upl in supervisors
    in_mgr = upl in manager_codes
    if in_mgr:
        allowed.update(by_m.get(upl, []))
    if in_sup:
        allowed.add(upl)
    if not allowed and login_kind == "supervisor_acc" and in_sup:
        allowed.add(upl)
    if upl:
        allowed.add(upl)
    return allowed


def region_lookup() -> dict[str, str]:
    out = dict(REGIONAL_MANAGER_CODES)
    out.update(DISTRICT_MANAGER_PREFIX)
    for region, codes in load_region_teams().items():
        for c in codes:
            out[str(c).strip().upper()] = region
    return out


def repair_rows(rows: list[dict]) -> tuple[list[dict], list[str]]:
    by_upl = region_lookup()
    logs: list[str] = []
    out: list[dict] = []
    for r in rows:
        nr = dict(r)
        upl = str(nr.get("userpl") or "").strip().upper()
        em = str(nr.get("email") or "").strip().lower()
        if not upl or "@" not in em:
            continue
        if not str(nr.get("acc_region") or "").strip() and upl in by_upl:
            nr["acc_region"] = by_upl[upl]
            logs.append(f"{em} / {upl} -> acc_region={nr['acc_region']}")
        out.append(nr)
    out.sort(key=lambda x: (x["email"], x["userpl"]))
    return out, logs


def audit_rows(rows: list[dict]) -> list[dict[str, Any]]:
    mdata = load_managers_metadata()
    supervisors, manager_codes, by_m = parse_trf(mdata)
    region_teams = load_region_teams()
    report: list[dict[str, Any]] = []
    for r in rows:
        upl = str(r.get("userpl") or "").strip().upper()
        vis = sorted(
            expand_visible(upl, r, supervisors, manager_codes, by_m, region_teams)
        )
        ok = bool(vis) and upl in vis
        report.append(
            {
                "email": r.get("email"),
                "userpl": upl,
                "visible": vis,
                "ok": ok,
            }
        )
    return report


def main() -> int:
    with open(USER_ACCESS_PATH, encoding="utf-8") as f:
        rows = json.load(f)

    repaired, repair_logs = repair_rows(rows)
    report = audit_rows(repaired)
    bad = [x for x in report if not x["ok"]]

    with open(USER_ACCESS_PATH, "w", encoding="utf-8") as f:
        json.dump(repaired, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"rows: {len(repaired)}")
    print(f"acc_region filled: {len(repair_logs)}")
    for line in repair_logs:
        print(f"  {line}")

    ok_count = sum(1 for x in report if x["ok"])
    print(f"visible audit: {ok_count}/{len(report)} OK (own SL included)")

    if bad:
        print("FAILED:")
        for x in bad:
            print(f"  {x['email']} / {x['userpl']} visible={x['visible']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
