#!/usr/bin/env python3
"""
นำเข้า user_access จากไฟล์ Excel แยก Division (B/E/S)
- Div.B/E: ภาคจากคอลัมน์ตำแหน่ง, บทบาทจากข้อความตำแหน่ง
- Div.S: ภาคจากคอลัมน์ภูมิภาค + ขอบเขต (All / Credit All / Van All)
- merge: อัปเดตแถวที่ตรง email+userpl, เก็บแถวเดิมที่ไม่อยู่ใน Excel
- คำนวณ visible_supervisor_codes และ rebuild access_hierarchy
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

try:
    import pandas as pd
except ImportError:
    print("ต้องติดตั้ง pandas และ openpyxl: pip install pandas openpyxl", file=sys.stderr)
    raise SystemExit(1)

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services.access_hierarchy import (  # noqa: E402
    apply_roster_overrides,
    enrich_rows_with_visibility,
    parse_div_s_scope,
    parse_region_from_position,
    parse_role_from_position,
    persist_hierarchy,
    build_hierarchy_payload,
    normalize_div_s_region,
)

USER_ACCESS_PATH = os.path.join(REPO, "config", "user_access.json")

DEFAULT_BE_XLSX = os.path.join(
    os.path.expanduser("~"),
    "Downloads",
    "Email และ รหัส SL ผจก.และซุปฯ B,E.xlsx",
)
DEFAULT_S_XLSX = os.path.join(
    os.path.expanduser("~"),
    "Downloads",
    "รหัสSL-Mail ทีมขายDiv.S.xlsx",
)


def _norm_email(s: str | None) -> str:
    return (s or "").strip().lower()


def _norm_upl(s: str | None) -> str:
    return (s or "").strip().upper()


def row_from_be(
    division: str,
    userpl: str,
    email: str,
    full_name: str,
    position: str,
) -> dict[str, Any]:
    login_kind, acc_unit, acc_scope = parse_role_from_position(position)
    acc_region = parse_region_from_position(position)
    out: dict[str, Any] = {
        "email": _norm_email(email),
        "userpl": _norm_upl(userpl),
        "full_name": (full_name or "").strip(),
        "can_import_targetsun": False,
        "note": "",
        "acc_division": division,
        "acc_position": (position or "").strip(),
        "login_kind": login_kind,
        "acc_scope": acc_scope,
    }
    if acc_region:
        out["acc_region"] = acc_region
    if acc_unit:
        out["acc_unit"] = acc_unit
    if login_kind in ("manager_acc", "supervisor_acc"):
        out["acc_type"] = "NON"
        out["acc_joblevel"] = "1"
    return out


def row_from_s(
    userpl: str,
    email: str,
    full_name: str,
    region_raw: str,
    scope_raw: str | None,
) -> dict[str, Any] | None:
    parsed = parse_div_s_scope(scope_raw)
    if parsed is None:
        return None
    login_kind, acc_scope, acc_unit = parsed
    acc_region = normalize_div_s_region(region_raw)
    out: dict[str, Any] = {
        "email": _norm_email(email),
        "userpl": _norm_upl(userpl),
        "full_name": (full_name or "").strip(),
        "can_import_targetsun": False,
        "note": "",
        "acc_division": "Div.S",
        "login_kind": login_kind,
        "acc_scope": acc_scope,
        "acc_type": "NON",
        "acc_joblevel": "1",
    }
    if acc_region:
        out["acc_region"] = acc_region
    if acc_unit:
        out["acc_unit"] = acc_unit
    return apply_roster_overrides(out)


def load_be_rows(path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sheet in ("Div.B", "Div.E"):
        df = pd.read_excel(path, sheet_name=sheet, header=3)
        for _, r in df.iterrows():
            vals = list(r)
            if len(vals) < 6:
                continue
            upl = _norm_upl(str(vals[1]))
            full_name = str(vals[3]) if vals[3] == vals[3] else ""
            pos = str(vals[4]) if vals[4] == vals[4] else ""
            em = _norm_email(str(vals[5]))
            if not upl or "@" not in em:
                continue
            rows.append(row_from_be(sheet, upl, em, full_name, pos))
    return rows


def load_s_rows(path: str) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    df = pd.read_excel(path, sheet_name="Div.S", header=1)
    for i, r in df.iterrows():
        vals = list(r)
        if len(vals) < 4:
            continue
        upl = _norm_upl(str(vals[1]))
        full_name = str(vals[2]) if len(vals) > 2 and vals[2] == vals[2] else ""
        em = _norm_email(str(vals[3]))
        region_raw = str(vals[4]) if len(vals) > 4 and vals[4] == vals[4] else ""
        scope_raw = vals[5] if len(vals) > 5 else None
        if not upl or "@" not in em:
            continue
        row = row_from_s(upl, em, full_name, region_raw, scope_raw)
        if row is None:
            warnings.append(
                f"Div.S แถว {i + 2}: {upl} — ขอบเขตไม่รู้จัก ({scope_raw!r}) ข้าม"
            )
            continue
        rows.append(row)
    return rows, warnings


def merge_rows(
    existing: list[dict[str, Any]],
    imported: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for r in existing:
        key = (_norm_email(r.get("email")), _norm_upl(r.get("userpl")))
        if "@" in key[0] and key[1]:
            by_key[key] = dict(r)

    stats = {"added": 0, "updated": 0, "kept_legacy": 0}
    import_keys: set[tuple[str, str]] = set()

    for r in imported:
        key = (_norm_email(r.get("email")), _norm_upl(r.get("userpl")))
        import_keys.add(key)
        old = by_key.get(key)
        merged = dict(r)
        if old:
            if old.get("can_import_targetsun"):
                merged["can_import_targetsun"] = True
            if str(old.get("note") or "").strip():
                merged["note"] = str(old.get("note") or "").strip()
            stats["updated"] += 1
        else:
            stats["added"] += 1
        by_key[key] = merged

    for key in by_key:
        if key not in import_keys:
            stats["kept_legacy"] += 1

    out = sorted(by_key.values(), key=lambda x: (x["email"], x["userpl"]))
    return out, stats


def main() -> int:
    ap = argparse.ArgumentParser(description="Import division user access from Excel")
    ap.add_argument("--be-xlsx", default=DEFAULT_BE_XLSX, help="ไฟล์ Div.B/E")
    ap.add_argument("--s-xlsx", default=DEFAULT_S_XLSX, help="ไฟล์ Div.S")
    ap.add_argument("--output", default=USER_ACCESS_PATH, help="ปลายทาง user_access.json")
    ap.add_argument("--dry-run", action="store_true", help="ไม่เขียนไฟล์")
    ap.add_argument("--skip-hierarchy", action="store_true", help="ไม่ rebuild hierarchy")
    args = ap.parse_args()

    if not os.path.isfile(args.be_xlsx):
        print(f"ไม่พบไฟล์ B/E: {args.be_xlsx}", file=sys.stderr)
        return 1
    if not os.path.isfile(args.s_xlsx):
        print(f"ไม่พบไฟล์ Div.S: {args.s_xlsx}", file=sys.stderr)
        return 1

    with open(args.output, encoding="utf-8") as f:
        existing = json.load(f)
    if not isinstance(existing, list):
        print("user_access.json ต้องเป็น array", file=sys.stderr)
        return 1

    be_rows = load_be_rows(args.be_xlsx)
    s_rows, s_warnings = load_s_rows(args.s_xlsx)
    for w in s_warnings:
        print(f"WARN: {w}", file=sys.stderr)

    imported = be_rows + s_rows
    merged, stats = merge_rows(existing, imported)
    enriched = enrich_rows_with_visibility(merged)

    print(f"Excel B/E: {len(be_rows)} แถว, Div.S: {len(s_rows)} แถว")
    print(f"merge: +{stats['added']} ใหม่, ~{stats['updated']} อัปเดต, {stats['kept_legacy']} legacy คงไว้")
    print(f"รวม: {len(enriched)} แถว")

    if args.dry_run:
        print("(dry-run — ไม่เขียนไฟล์)")
        return 0

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(enriched, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"เขียนแล้ว: {args.output}")

    if not args.skip_hierarchy:
        payload = build_hierarchy_payload(enriched)
        path = persist_hierarchy(payload)
        print(f"rebuild hierarchy -> {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
