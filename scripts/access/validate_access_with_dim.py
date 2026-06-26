#!/usr/bin/env python3
"""
ตรวจ supervisor_acc ใน user_access.json กับ Dim_Salesman:
- SuperCode มีใน Dim หรือไม่
- Area_NameThai ตรง acc_region หรือไม่
- SalesType ตรง acc_unit (0↔credit, 1↔van) หรือแจ้ง warning
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO)

from backend.services.user_access_store import user_access_json_path  # noqa: E402

AREA_TO_REGION = {
    "ภาคกรุงเทพ": "กรุงเทพ",
    "ภาคกทม": "กรุงเทพ",
    "กรุงเทพ": "กรุงเทพ",
    "ภาคกลาง": "กลาง",
    "กลาง": "กลาง",
    "ภาคเหนือ": "เหนือ",
    "เหนือ": "เหนือ",
    "ภาคใต้": "ใต้",
    "ใต้": "ใต้",
    "ภาคอีสาน": "อีสาน",
    "อีสาน": "อีสาน",
}


def normalize_area(area: str) -> str:
    a = (area or "").strip()
    if not a:
        return ""
    compact = a.replace(" ", "")
    for needle, region in AREA_TO_REGION.items():
        if needle.replace(" ", "") in compact or compact in needle.replace(" ", ""):
            return region
    return a


def sales_type_to_unit(st: int | None) -> str:
    if st == 0:
        return "credit"
    if st == 1:
        return "van"
    return ""


def load_dim_index(*, offline: str | None = None) -> dict[str, list[dict[str, Any]]]:
    if offline:
        with open(offline, encoding="utf-8") as f:
            rows = json.load(f)
        if not isinstance(rows, list):
            raise SystemExit("offline JSON ต้องเป็น array")
    else:
        from backend.fabric_dax_connector import FabricDAXConnector

        rows = FabricDAXConnector().get_dim_salesman_supervisor_index()

    by_sc: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        sc = str(r.get("super_code") or r.get("SuperCode") or "").strip().upper()
        if sc:
            by_sc.setdefault(sc, []).append(r)
    return by_sc


def validate_row(row: dict[str, Any], dim_by_sc: dict[str, list[dict[str, Any]]]) -> list[str]:
    issues: list[str] = []
    if str(row.get("login_kind") or "") != "supervisor_acc":
        return issues

    upl = str(row.get("userpl") or "").strip().upper()
    em = str(row.get("email") or "").strip()
    dim_rows = dim_by_sc.get(upl) or []

    if not dim_rows:
        issues.append(f"{em} / {upl}: SuperCode ไม่พบใน Dim_Salesman")
        return issues

    acc_region = str(row.get("acc_region") or "").strip()
    acc_unit = str(row.get("acc_unit") or "").strip().lower()
    areas = {normalize_area(str(d.get("area_name_thai") or "")) for d in dim_rows}
    areas.discard("")

    if acc_region and areas:
        matched = any(
            acc_region in a or a in acc_region or normalize_area(a) == acc_region
            for a in areas
        )
        if not matched:
            issues.append(
                f"{em} / {upl}: acc_region={acc_region!r} ไม่ตรง Dim Area {sorted(areas)!r}"
            )

    if acc_unit in ("credit", "van"):
        dim_units = {
            sales_type_to_unit(d.get("sales_type"))
            for d in dim_rows
            if d.get("sales_type") is not None
        }
        dim_units.discard("")
        if dim_units and acc_unit not in dim_units:
            issues.append(
                f"{em} / {upl}: acc_unit={acc_unit!r} ไม่ตรง Dim SalesType {sorted(dim_units)!r}"
            )

    return issues


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate user_access against Dim_Salesman")
    ap.add_argument("--input", default=user_access_json_path())
    ap.add_argument(
        "--offline-dim",
        help="JSON array จาก get_dim_salesman_supervisor_index (ไม่เรียก Fabric)",
    )
    ap.add_argument("--warn-only", action="store_true", help="exit 0 แม้มี warning")
    args = ap.parse_args()

    if not os.path.isfile(args.input):
        print(f"ไม่พบ: {args.input}", file=sys.stderr)
        return 1

    with open(args.input, encoding="utf-8") as f:
        rows = json.load(f)
    if not isinstance(rows, list):
        print("user_access ต้องเป็น array", file=sys.stderr)
        return 1

    dim_by_sc = load_dim_index(offline=args.offline_dim)
    all_issues: list[str] = []
    checked = 0
    for r in rows:
        if str(r.get("login_kind") or "") != "supervisor_acc":
            continue
        checked += 1
        all_issues.extend(validate_row(r, dim_by_sc))

    print(f"ตรวจ supervisor_acc: {checked} แถว")
    if not all_issues:
        print("OK — ไม่พบปัญหา")
        return 0

    for msg in all_issues:
        print(f"WARN: {msg}")
    print(f"รวม {len(all_issues)} รายการ")
    return 0 if args.warn_only else 1


if __name__ == "__main__":
    raise SystemExit(main())
