#!/usr/bin/env python3
"""อัปเดต user_access.json จาก ACC CSV — เพิ่ม region, login_kind และสร้าง region_teams.json"""

from __future__ import annotations

import csv
import json
import os
import re
import sys

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
REGION_TEAMS_PATH = os.path.join(REPO, "config", "region_teams.json")

# รหัส login แบบ Manager ภูมิภาค (ไม่มีใน trf แต่มีใน ACC)
REGIONAL_MANAGER_CODES = {
    "SL459": "ใต้",
    "SL526": "กลางหน่วยรถ",
    "SL535": "เหนือ",
}

# รหัส Manager อีสาน (USERPL = รหัสภูมิภาคย่อย)
DISTRICT_MANAGER_PREFIX = {
    "SL452": "อีสาน",
    "SL456": "อีสาน",
}

EXTRA_PAIRS = [
    ("payom.k@sahapat.co.th", "SL330"),
    ("apichat.t@sahapat.co.th", "SL459"),
    ("chatree.j@sahapat.co.th", "SL526"),
    ("sarawut.p@sahapat.co.th", "SL535"),
    ("phanuwat.j@sahapat.co.th", "SL392"),
    ("nattapol.b@sahapat.co.th", "SL456"),
    ("thavorn.k@sahapat.co.th", "SL452"),
    ("tree.p@sahapat.co.th", "SL510"),
]

REGION_RE = re.compile(
    r"(ใต้|เหนือ|อีสาน|กลางหน่วยรถ|กลางเครดิต)"
)


def _open_csv(path: str):
    for enc in ("utf-8-sig", "utf-8", "cp874", "tis-620", "latin-1"):
        try:
            with open(path, encoding=enc, newline="") as f:
                f.read(8192)
            return enc
        except (UnicodeDecodeError, LookupError):
            continue
    return "latin-1"


def _norm_email(s: str) -> str:
    return (s or "").strip().lower()


def _norm_upl(s: str) -> str:
    return (s or "").strip().upper()


def _region_from_row(row: dict) -> str:
    for v in row.values():
        if not v:
            continue
        m = REGION_RE.search(str(v))
        if m:
            return m.group(1)
    ds = str(row.get("DIVISIONSALE") or "").strip().upper()
    sec = str(row.get("SECTIONCODE") or "").strip()
    if ds == "S" and sec in ("3", "4", "5", "6"):
        return {
            "3": "กลางเครดิต",
            "4": "อีสาน",
            "5": "เหนือ",
            "6": "ใต้",
        }[sec]
    return ""


def _login_kind_for_row(upl: str, acc_type: str, joblevel: str) -> str:
    if upl in REGIONAL_MANAGER_CODES:
        return "regional_manager"
    if upl in DISTRICT_MANAGER_PREFIX:
        return "district_manager"
    jl = str(joblevel or "").strip()
    if jl == "2":
        return "manager_acc"
    if jl == "1" or (acc_type == "S" and jl == "1"):
        return "supervisor_acc"
    return "standard"


def parse_csv(csv_path: str) -> tuple[list[dict], dict[str, list[str]]]:
    enc = _open_csv(csv_path)
    rows: list[dict] = []
    region_teams: dict[str, set[str]] = {}
    seen: set[tuple[str, str]] = set()

    with open(csv_path, encoding=enc, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            upl = _norm_upl(row.get("USERPL") or row.get("userpl") or "")
            em = _norm_email(row.get("EMAIL") or row.get("email") or "")
            if not upl or not em or "@" not in em:
                continue
            region = _region_from_row(row)
            acc_type = str(row.get("TYPE") or "").strip().upper()
            joblevel = str(row.get("JOBLEVEL") or "").strip()
            key = (em, upl)
            if key in seen:
                continue
            seen.add(key)

            login_kind = _login_kind_for_row(upl, acc_type, joblevel)
            if upl in REGIONAL_MANAGER_CODES:
                region = region or REGIONAL_MANAGER_CODES[upl]
            elif upl in DISTRICT_MANAGER_PREFIX:
                region = region or DISTRICT_MANAGER_PREFIX[upl]

            if region and upl not in REGIONAL_MANAGER_CODES and upl not in DISTRICT_MANAGER_PREFIX:
                region_teams.setdefault(region, set()).add(upl)

            rows.append(
                {
                    "email": em,
                    "userpl": upl,
                    "can_import_targetsun": False,
                    "note": "",
                    "acc_region": region,
                    "acc_type": acc_type,
                    "acc_joblevel": joblevel,
                    "login_kind": login_kind,
                }
            )

    for em, upl in EXTRA_PAIRS:
        key = (_norm_email(em), _norm_upl(upl))
        if key in seen:
            continue
        seen.add(key)
        region = REGIONAL_MANAGER_CODES.get(upl) or DISTRICT_MANAGER_PREFIX.get(upl, "")
        lk = "standard"
        if upl in REGIONAL_MANAGER_CODES:
            lk = "regional_manager"
        elif upl in DISTRICT_MANAGER_PREFIX:
            lk = "district_manager"
        rows.append(
            {
                "email": key[0],
                "userpl": key[1],
                "can_import_targetsun": False,
                "note": "",
                "acc_region": region,
                "acc_type": "",
                "acc_joblevel": "",
                "login_kind": lk,
            }
        )

    rows.sort(key=lambda r: (r["email"], r["userpl"]))
    teams_out = {k: sorted(v) for k, v in sorted(region_teams.items())}
    return rows, teams_out


def apply_targetsun(rows: list[dict], path: str | None) -> None:
    if not path or not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    emails: set[str] = set()
    if isinstance(data, list):
        for row in data:
            if isinstance(row, dict):
                em = _norm_email(row.get("email") or row.get("EMAIL"))
                if "@" in em:
                    emails.add(em)
    for r in rows:
        if r["email"] in emails:
            r["can_import_targetsun"] = True


def main() -> int:
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--csv", required=True)
    p.add_argument("--out", default=os.path.join(REPO, "config", "user_access.json"))
    p.add_argument("--targetsun", default=os.path.join(REPO, "config", "acc_local_test.json"))
    args = p.parse_args()

    rows, teams = parse_csv(args.csv)
    apply_targetsun(rows, args.targetsun)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
        f.write("\n")

    with open(REGION_TEAMS_PATH, "w", encoding="utf-8") as f:
        json.dump(teams, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"user_access: {len(rows)} rows -> {args.out}")
    print(f"region_teams: {len(teams)} regions -> {REGION_TEAMS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
