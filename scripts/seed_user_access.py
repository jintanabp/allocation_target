#!/usr/bin/env python3
"""
สร้าง config/user_access.json จาก export ACC_USER_CONTROL (CSV)

Usage:
  python scripts/seed_user_access.py --csv path/to/export.csv
  python scripts/seed_user_access.py --csv export.csv --out config/user_access.json
  python scripts/seed_user_access.py --csv export.csv --targetsun config/acc_local_test.json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys

# คู่เสริมที่ต้องมีในรายการ (dedupe กับ CSV)
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


def _repo_root() -> str:
    return os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))


def _norm_email(s: str) -> str:
    return (s or "").strip().lower()


def _norm_upl(s: str) -> str:
    return (s or "").strip().upper()


def load_targetsun_emails(path: str | None) -> set[str]:
    if not path or not os.path.isfile(path):
        return set()
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    out: set[str] = set()
    if not isinstance(data, list):
        return out
    for row in data:
        if not isinstance(row, dict):
            continue
        em = _norm_email(row.get("email") or row.get("EMAIL"))
        if "@" in em:
            out.add(em)
    return out


def _open_csv(path: str):
    for enc in ("utf-8-sig", "utf-8", "cp874", "tis-620", "latin-1"):
        try:
            f = open(path, encoding=enc, newline="")
            f.read(4096)
            f.seek(0)
            return f, enc
        except (UnicodeDecodeError, LookupError):
            continue
    return open(path, encoding="latin-1", newline=""), "latin-1"


def rows_from_csv(csv_path: str) -> list[dict]:
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    f, enc = _open_csv(csv_path)
    with f:
        reader = csv.DictReader(f)
        for row in reader:
            upl = _norm_upl(row.get("USERPL") or row.get("userpl") or "")
            em = _norm_email(row.get("EMAIL") or row.get("email") or "")
            if not upl or not em or "@" not in em:
                continue
            key = (em, upl)
            if key in seen:
                continue
            seen.add(key)
            out.append({"email": em, "userpl": upl, "can_import_targetsun": False, "note": ""})
    for em, upl in EXTRA_PAIRS:
        key = (_norm_email(em), _norm_upl(upl))
        if key in seen:
            continue
        seen.add(key)
        out.append({"email": key[0], "userpl": key[1], "can_import_targetsun": False, "note": ""})
    out.sort(key=lambda r: (r["email"], r["userpl"]))
    return out


def apply_targetsun_flags(rows: list[dict], ts_emails: set[str]) -> None:
    for r in rows:
        if _norm_email(r["email"]) in ts_emails:
            r["can_import_targetsun"] = True


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed config/user_access.json from ACC CSV")
    parser.add_argument("--csv", required=True, help="Path to ACC_USER_CONTROL CSV export")
    parser.add_argument(
        "--out",
        default=os.path.join(_repo_root(), "config", "user_access.json"),
        help="Output JSON path (default: config/user_access.json)",
    )
    parser.add_argument(
        "--targetsun",
        default=os.path.join(_repo_root(), "config", "acc_local_test.json"),
        help="Optional JSON to copy can_import_targetsun emails from",
    )
    args = parser.parse_args()

    if not os.path.isfile(args.csv):
        print(f"ไม่พบไฟล์ CSV: {args.csv}", file=sys.stderr)
        return 1

    rows = rows_from_csv(args.csv)
    ts = load_targetsun_emails(args.targetsun)
    apply_targetsun_flags(rows, ts)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"Wrote {len(rows)} rows -> {args.out}")
    if ts:
        print(f"Target Sun flag: {len(ts)} email(s) from {args.targetsun}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
