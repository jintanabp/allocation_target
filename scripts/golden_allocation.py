#!/usr/bin/env python
"""
Golden-file harness สำหรับการกระจายหีบ

ใช้ก่อน/หลังแก้ "ชุด B" (ข้อที่ทำให้ตัวเลขเปลี่ยน) เพื่อพิสูจน์ว่าหีบทุกใบที่ขยับ
อธิบายได้ว่ามาจากข้อไหน — ไม่ใช่ผลข้างเคียงที่ไม่ได้ตั้งใจ

ทำงานแบบ offline ล้วน: อ่านเฉพาะ cache ที่มีอยู่แล้วใน data/
  data/target_boxes_{SUP}_{YYYY}_{MM}.csv   เป้าหีบราย SKU
  data/target_sun_{SUP}_{YYYY}_{MM}.csv     เป้าเงินรายคน
  data/hist_cache_{SUP}_{YYYY}_{MM}.csv     ประวัติ 3 เดือน
ไม่ยิง DAX ไม่แตะ Target Sun ไม่เขียนทับอะไรใน data/

วิธีใช้:
    python scripts/golden_allocation.py capture            # เก็บผลปัจจุบันเป็นไฟล์อ้างอิง
    python scripts/golden_allocation.py compare            # เทียบผลตอนนี้กับไฟล์อ้างอิง
    python scripts/golden_allocation.py compare --verbose  # โชว์รายเซลล์ที่ต่าง

ไฟล์อ้างอิงเก็บที่ tests/golden/ (commit ได้ ขนาดเล็ก)

⚠️ ข้อจำกัดที่ต้องรู้ก่อนอ่านผล compare
────────────────────────────────────────
CBC มี time limit ต่อการแก้หนึ่งครั้ง (`_lp_optimize`: สูงสุด 60 วินาที)
ทีมที่ SKU เยอะพอจะชน limit จะได้คำตอบที่ "ดีที่สุดเท่าที่หาทัน" ซึ่ง
**ขึ้นกับความเร็วเครื่องขณะนั้น** รันโค้ดเดิมซ้ำจึงอาจต่างกัน 1-2 เคสได้

วิธีแยกว่าเป็นของจริงหรือ noise:
  - รัน compare ซ้ำอีกรอบ ถ้าเคสที่ต่างเปลี่ยนไปเรื่อย ๆ = noise จาก CBC
  - ถ้าเคสเดิมต่างทุกรอบ และ/หรือ `even`/`push` (ไม่ผ่าน LP) ต่างด้วย = ของจริง
  - `sku_mismatches` ต้องเป็น 0 เสมอไม่ว่ากรณีไหน — ถ้าไม่ใช่คือกฎ I1 พัง
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import re
import sys

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.OR_engine import allocate_boxes  # noqa: E402

logging.disable(logging.CRITICAL)

# console บน Windows ไทยเป็น cp874 เข้ารหัสบางอักขระไม่ได้ -> บังคับ UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

GOLDEN_DIR = os.path.join(REPO, "tests", "golden")
DATA_DIR = os.path.join(REPO, "data")

# ชุดพารามิเตอร์ที่สะท้อนการใช้งานจริงจากหน้าเว็บ
#
# l3m_tiered คือค่าที่หน้าเว็บส่งมาจริงเป็นค่าเริ่มต้น (ดู basePayload ใน app.js:
# tiered_allocation=true, tier_pct=0.80) จึงเป็นตัวที่สำคัญที่สุด
# even/push ไม่ผ่าน LP จึงเร็ว — ใส่ไว้กันเส้นทาง _proportional ล้วนถดถอย
#
# ตั้ง GOLDEN_ALL_SCENARIOS=1 เพื่อรันชุดเต็ม (ช้ากว่ามาก เพราะ CBC ต่อรอบถึง 60 วินาที)
SCENARIOS = [
    {"name": "l3m_tiered", "strategy": "L3M", "tiered_allocation": True, "force_min_one": False},
    {"name": "even", "strategy": "EVEN", "tiered_allocation": False, "force_min_one": False},
    {"name": "push", "strategy": "PUSH", "tiered_allocation": False, "force_min_one": False},
]
if (os.environ.get("GOLDEN_ALL_SCENARIOS") or "").strip() in ("1", "true", "yes"):
    SCENARIOS += [
        {"name": "l3m_plain", "strategy": "L3M", "tiered_allocation": False, "force_min_one": False},
        {"name": "l3m_minone", "strategy": "L3M", "tiered_allocation": True, "force_min_one": True},
        {"name": "l6m_tiered", "strategy": "L6M", "tiered_allocation": True, "force_min_one": False},
    ]


def discover_cases() -> list[tuple[str, int, int]]:
    """หา (sup_id, year, month) ที่มี cache ครบทั้งสามไฟล์"""
    out = []
    pat = re.compile(r"target_boxes_(.+)_(\d{4})_(\d{2})\.csv$")
    for path in sorted(glob.glob(os.path.join(DATA_DIR, "target_boxes_*.csv"))):
        m = pat.search(os.path.basename(path))
        if not m:
            continue
        sup, year, month = m.group(1), int(m.group(2)), int(m.group(3))
        need = [
            f"target_sun_{sup}_{year}_{month:02d}.csv",
            f"hist_cache_{sup}_{year}_{month:02d}.csv",
        ]
        if all(os.path.exists(os.path.join(DATA_DIR, n)) for n in need):
            out.append((sup, year, month))
    return out


def load_case(sup: str, year: int, month: int):
    base = os.path.join(DATA_DIR, "")
    df_sku = pd.read_csv(f"{base}target_boxes_{sup}_{year}_{month:02d}.csv", dtype={"sku": str})
    df_sku = df_sku.dropna(subset=["sku"]).fillna(0)
    df_sku["sku"] = df_sku["sku"].astype(str).str.strip()
    df_sku["supervisor_target_boxes"] = pd.to_numeric(
        df_sku["supervisor_target_boxes"], errors="coerce"
    ).fillna(0)
    df_sku = df_sku[df_sku["supervisor_target_boxes"] > 0].copy()

    df_sun = pd.read_csv(f"{base}target_sun_{sup}_{year}_{month:02d}.csv", dtype={"emp_id": str})
    df_sun = df_sun.dropna(subset=["emp_id"]).fillna(0)
    df_emp = pd.DataFrame({
        "emp_id": df_sun["emp_id"].astype(str).str.strip(),
        "yellow_target": pd.to_numeric(df_sun["target_sun"], errors="coerce").fillna(0.0),
    })
    df_emp = df_emp[df_emp["yellow_target"] > 0].copy()

    df_hist = pd.read_csv(
        f"{base}hist_cache_{sup}_{year}_{month:02d}.csv", dtype={"sku": str, "emp_id": str}
    )
    df_hist = df_hist[df_hist["emp_id"].isin(set(df_emp["emp_id"]))]
    return df_emp, df_sku, df_hist


def run_all() -> dict:
    """คืน {case_key: {sku|emp: boxes}} — คีย์เรียงแล้วเพื่อให้ diff เสถียร"""
    result: dict = {}
    for sup, year, month in discover_cases():
        try:
            df_emp, df_sku, df_hist = load_case(sup, year, month)
        except Exception as e:  # ไฟล์เสีย/คอลัมน์ขาด — ข้ามไปเคสอื่น
            print(f"  ! ข้าม {sup} {year}-{month:02d}: {e}")
            continue
        if df_emp.empty or df_sku.empty:
            print(f"  ! ข้าม {sup} {year}-{month:02d}: ไม่มีเป้าเงินหรือเป้าหีบ")
            continue

        for sc in SCENARIOS:
            key = f"{sup}_{year}_{month:02d}__{sc['name']}"
            try:
                out = allocate_boxes(
                    df_emp, df_sku, df_hist,
                    strategy=sc["strategy"],
                    tiered_allocation=sc["tiered_allocation"],
                    force_min_one=sc["force_min_one"],
                )
            except Exception as e:
                result[key] = {"__error__": f"{type(e).__name__}: {e}"}
                continue

            cells = {
                f"{r.emp_id}|{r.sku}": int(r.allocated_boxes)
                for r in out.itertuples()
                if int(r.allocated_boxes) != 0
            }
            per_sku = out.groupby("sku")["allocated_boxes"].sum().astype(int).to_dict()
            targets = {
                str(r["sku"]).strip(): int(round(float(r["supervisor_target_boxes"])))
                for _, r in df_sku.iterrows()
            }
            mismatched = {
                s: [int(per_sku.get(s, 0)), t] for s, t in targets.items()
                if int(per_sku.get(s, 0)) != t
            }
            result[key] = {
                "cells": dict(sorted(cells.items())),
                "total_boxes": int(out["allocated_boxes"].sum()),
                "sku_mismatches": dict(sorted(mismatched.items())),
                "fallback": bool(out.attrs.get("optimization_fallback")),
                # ลายนิ้วมือของ "ข้อมูลเข้า" — ใช้แยกว่าที่ต่างเป็นเพราะโค้ดหรือเพราะข้อมูล
                # data/ เป็น cache ที่แอปเขียนทับได้ตลอด เป้าจึงเปลี่ยนโดยไม่มีใครแก้โค้ดเลย
                "inputs": {
                    "target_total": int(sum(targets.values())),
                    "sku_count": len(targets),
                    "emp_count": int(len(df_emp)),
                    "hist_rows": int(len(df_hist)),
                },
            }
            print(
                f"  {key}: {len(cells)} เซลล์ | {result[key]['total_boxes']} หีบ | "
                f"ไม่ตรงเป้า {len(mismatched)} SKU"
                + (" | fallback" if result[key]["fallback"] else "")
            )
    return result


def cmd_capture() -> int:
    os.makedirs(GOLDEN_DIR, exist_ok=True)
    print("เก็บผลปัจจุบันเป็นไฟล์อ้างอิง…")
    data = run_all()
    if not data:
        print("ไม่พบเคสที่ใช้ได้ใน data/ — ต้องมี target_boxes_ + target_sun_ + hist_cache_ ครบคู่")
        return 1
    path = os.path.join(GOLDEN_DIR, "allocation_golden.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
    print(f"\nบันทึก {len(data)} เคส -> {os.path.relpath(path, REPO)}")
    return 0


def cmd_compare(verbose: bool) -> int:
    path = os.path.join(GOLDEN_DIR, "allocation_golden.json")
    if not os.path.exists(path):
        print("ยังไม่มีไฟล์อ้างอิง — รัน `python scripts/golden_allocation.py capture` ก่อน")
        return 1
    with open(path, encoding="utf-8") as f:
        old = json.load(f)
    print("คำนวณผลปัจจุบันเพื่อเทียบ…")
    new = run_all()

    print("\n" + "=" * 70)
    changed = 0
    data_changed = 0
    for key in sorted(set(old) | set(new)):
        o, n = old.get(key), new.get(key)
        if o is None:
            print(f"[เคสใหม่]  {key}")
            changed += 1
            continue
        if n is None:
            print(f"[เคสหาย]   {key}")
            changed += 1
            continue
        o_cells, n_cells = o.get("cells", {}), n.get("cells", {})
        if o_cells == n_cells and o.get("fallback") == n.get("fallback"):
            continue

        # ข้อมูลเข้าเปลี่ยน = เทียบผลลัพธ์ไม่ได้ ไม่ใช่โค้ดถดถอย
        o_in, n_in = o.get("inputs"), n.get("inputs")
        if o_in and n_in and o_in != n_in:
            data_changed += 1
            diffs = ", ".join(
                f"{k}: {o_in.get(k)} -> {n_in.get(k)}"
                for k in sorted(set(o_in) | set(n_in))
                if o_in.get(k) != n_in.get(k)
            )
            print(f"\n[ข้อมูลต้นทางเปลี่ยน] {key}")
            print(f"   {diffs}")
            print("   -> เทียบผลลัพธ์ไม่ได้ ต้อง capture ใหม่ (ไม่ใช่โค้ดถดถอย)")
            continue

        changed += 1
        moved = sum(
            abs(n_cells.get(k, 0) - o_cells.get(k, 0))
            for k in set(o_cells) | set(n_cells)
        )
        print(f"\n[ต่าง] {key}")
        print(f"   หีบรวม  {o.get('total_boxes')} -> {n.get('total_boxes')}")
        print(f"   เซลล์ที่ขยับ {sum(1 for k in set(o_cells)|set(n_cells) if o_cells.get(k,0)!=n_cells.get(k,0))} เซลล์ | ขยับรวม {moved} หีบ")
        print(f"   SKU ไม่ตรงเป้า {len(o.get('sku_mismatches', {}))} -> {len(n.get('sku_mismatches', {}))}")
        if o.get("fallback") != n.get("fallback"):
            print(f"   fallback {o.get('fallback')} -> {n.get('fallback')}")
        if verbose:
            for k in sorted(set(o_cells) | set(n_cells)):
                a, b = o_cells.get(k, 0), n_cells.get(k, 0)
                if a != b:
                    print(f"      {k}: {a} -> {b}")

    print("\n" + "=" * 70)
    if data_changed:
        print(f"{data_changed} เคส: ข้อมูลใน data/ เปลี่ยนไปจากตอน capture — เทียบผลลัพธ์ไม่ได้")
        print("   รัน `python scripts/golden_allocation.py capture` ใหม่เพื่อตั้งฐานใหม่")
    if changed:
        print(f"มี {changed} เคสที่ผลต่างโดยข้อมูลเข้าเหมือนเดิม — ตรวจว่าอธิบายได้ด้วยข้อที่แก้")
    elif not data_changed:
        print("ทุกเคสตรงกับไฟล์อ้างอิง — ไม่มีตัวเลขไหนขยับ")
    else:
        print("ไม่มีเคสไหนที่ผลต่างโดยข้อมูลเข้าเหมือนเดิม — โค้ดไม่ได้เปลี่ยนพฤติกรรม")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["capture", "compare"])
    ap.add_argument("--verbose", "-v", action="store_true", help="โชว์รายเซลล์ที่ต่าง")
    args = ap.parse_args()
    return cmd_capture() if args.command == "capture" else cmd_compare(args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
