#!/usr/bin/env python3
"""
หา "เป้ารวมหายไปไหน" ของทีมหนึ่งในงวดหนึ่ง — อ่านไฟล์ที่แอปแคชไว้เท่านั้น

เป้ารวมบนหน้าจอ = ผลบวกของ (ราคา/หีบ × เป้าหีบของทีม) ทุก SKU ตัวเลขจึงต่ำกว่า
ความจริงได้ 3 ทางเท่านั้น สคริปต์นี้ชั่งทั้งสามทางให้เห็นเป็นตัวเลข:

  1. ราคาเป็น 0   — SKU มีหีบแต่ไม่มีราคา (ช่องเหลืองในหน้าจอ) หีบนั้นคิดเป็นเงินไม่ได้
  2. หีบหาย       — แถวเป้าใน Target Sun ที่เจ้าของไม่อยู่ในทีมตาม Dim_Salesman
                    (ย้ายทีม / ลาออก / รหัส V หน่วยรถ) จะถูกตัดทิ้งเงียบ ๆ
  3. เป้าเปลี่ยน  — เป้างวดนี้ถูกแก้หลังเปิดครั้งแรก (เทียบกับ baseline ที่เก็บไว้)

ออฟไลน์ล้วน — ไม่ต่อ Fabric ไม่ต่อ Target Sun ไม่แตะ production
รันจากรากโปรเจกต์:

    python scripts/dev/diagnose_target_total.py --sup SL359 --month 9 --year 2026
    python scripts/dev/diagnose_target_total.py --sup SL359 --month 9 --year 2026 --expect 40949718.68

ไฟล์ที่ใช้ (แอปเขียนเองตอนเปิดหน้าทีมนั้นในงวดนั้น):
    data/target_boxes_<SUP>_<YYYY>_<MM>.csv   ราคา + เป้าหีบราย SKU (ตัวตั้งของยอดบนหน้าจอ)
    data/target_sun_<SUP>_<YYYY>_<MM>.csv     เป้าเงินรายคน
    data/tga_lines_<SUP>_<YYYY>_<MM>.csv      แถวเป้าดิบจาก Target Sun
    data/emp_cache_<SUP>_<YYYY>_<MM>.csv      ทีมที่ใช้ตอนนั้น (ตัดรหัส V แล้ว)
    data/baselines/<SUP>_<YYYY>_<MM>.json     เป้าตอนเปิดงวดครั้งแรก
    data/cache/price_per_box_<YYYY>_<MM>.json ราคาจากยอดขายจริง (ใช้ประเมินของที่ราคาหาย)
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
DATA_DIR = os.path.join(REPO, "data")


def _data(*parts: str) -> str:
    return os.path.join(DATA_DIR, *parts)


def _baht(x: float) -> str:
    return f"{float(x):,.2f}"


def _read_csv(path: str) -> pd.DataFrame | None:
    if not os.path.isfile(path):
        return None
    try:
        return pd.read_csv(path, dtype={"emp_id": str, "sku": str})
    except Exception as e:            # ไฟล์เดียวเสีย ไม่ควรทำให้ข้ออื่นไม่ได้ตรวจ
        print(f"  ! อ่าน {os.path.basename(path)} ไม่ได้: {e}")
        return None


def _read_json(path: str) -> dict | None:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"  ! อ่าน {os.path.basename(path)} ไม่ได้: {e}")
        return None


def _price_map(year: int, month: int) -> dict[str, float]:
    doc = _read_json(_data("cache", f"price_per_box_{year:04d}_{month:02d}.json")) or {}
    return {str(k).strip(): float(v or 0) for k, v in (doc.get("prices") or {}).items()}


def _credit_map(year: int, month: int) -> dict[str, float]:
    doc = _read_json(_data("cache", f"dim_product_{year:04d}_{month:02d}.json")) or {}
    return {
        str(r.get("sku") or "").strip(): float(r.get("credit_unit_price") or 0)
        for r in (doc.get("rows") or [])
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="หาสาเหตุที่เป้ารวมของทีมต่ำกว่าความจริง")
    ap.add_argument("--sup", required=True, help="รหัสทีม เช่น SL359")
    ap.add_argument("--month", type=int, required=True, choices=range(1, 13))
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--expect", type=float, default=None, help="เป้ารวมที่ควรเป็น (บาท)")
    ap.add_argument(
        "--data-dir",
        default=None,
        help="โฟลเดอร์ data ของแอป (ถ้าไม่ได้อยู่ใต้รากโปรเจกต์ เช่นบน server)",
    )
    args = ap.parse_args()

    if args.data_dir:
        global DATA_DIR
        DATA_DIR = os.path.abspath(args.data_dir)
    if not os.path.isdir(DATA_DIR):
        print(f"ไม่พบโฟลเดอร์ {DATA_DIR} — ระบุด้วย --data-dir")
        return 1

    sup = str(args.sup).strip().upper()
    y, m = int(args.year), int(args.month)
    suffix = f"{sup}_{y:04d}_{m:02d}"

    print(f"=== {sup} งวด {m:02d}/{y} ===\n")

    df_sku = _read_csv(_data(f"target_boxes_{suffix}.csv"))
    if df_sku is None:
        print(f"ไม่พบ data/target_boxes_{suffix}.csv")
        print("→ เปิดหน้าทีมนี้ในงวดนี้บนแอปหนึ่งครั้ง แล้วรันใหม่ (แอปเขียนไฟล์ให้เอง)")
        return 1

    df_sku["sku"] = df_sku["sku"].astype(str).str.strip()
    boxes = pd.to_numeric(df_sku["supervisor_target_boxes"], errors="coerce").fillna(0.0)
    price = pd.to_numeric(df_sku["price_per_box"], errors="coerce").fillna(0.0)
    shown = float((boxes * price).sum())
    total_boxes = float(boxes.sum())

    print("[1] ยอดที่หน้าจอแสดง")
    print(f"    เป้ารวม (Σ ราคา × เป้าหีบ) = {_baht(shown)} บาท")
    print(f"    SKU {len(df_sku):,} รายการ · เป้าหีบรวม {total_boxes:,.0f} หีบ")
    if args.expect:
        gap = args.expect - shown
        pct = gap / args.expect * 100
        print(f"    ควรเป็น {_baht(args.expect)} → ขาด {_baht(gap)} บาท ({pct:.2f}%)")
    print()

    # ── ทางที่หนึ่ง: ราคาเป็น 0 ────────────────────────────────────────
    print("[2] ทางที่หนึ่ง — SKU ที่ไม่มีราคา (มีหีบ แต่คิดเป็นเงินไม่ได้)")
    zero = df_sku[(price <= 0) & (boxes > 0)].copy()
    if zero.empty:
        print("    ไม่มี — ทุก SKU ที่มีเป้าหีบมีราคาครบ  ✔ ตัดสาเหตุนี้ออกได้")
    else:
        zb = pd.to_numeric(zero["supervisor_target_boxes"], errors="coerce").fillna(0.0)
        pct_boxes = zb.sum() / total_boxes * 100 if total_boxes else 0.0
        print(f"    {len(zero):,} SKU · {zb.sum():,.0f} หีบ ({pct_boxes:.2f}% ของหีบทั้งทีม)")
        pmap = _price_map(y, m)
        cmap = _credit_map(y, m)
        est = sum(
            (cmap.get(str(r["sku"]).strip(), 0.0) or pmap.get(str(r["sku"]).strip(), 0.0))
            * float(r["supervisor_target_boxes"] or 0)
            for _, r in zero.iterrows()
        )
        if est > 0:
            print(f"    ถ้าเติมราคาจากแคชงวดนี้ให้ครบ จะได้เพิ่มอีกราว {_baht(est)} บาท")
        else:
            print("    แคชราคางวดนี้ก็ไม่มีราคาของกลุ่มนี้ — ประเมินมูลค่าไม่ได้")
        print("    10 อันดับหีบมากสุด:")
        for _, r in zero.nlargest(10, "supervisor_target_boxes").iterrows():
            name = str(r.get("product_name_thai") or r.get("product_name_english") or "")
            print(
                f"      {str(r['sku']):>10}"
                f"  {float(r['supervisor_target_boxes']):>9,.0f} หีบ  {name[:40]}"
            )
    print()

    # ── ทางที่สอง: หีบที่หลุดตอนกรองด้วยรายชื่อทีม ──────────────────────
    print("[3] ทางที่สอง — แถวเป้าที่ถูกตัดเพราะเจ้าของไม่อยู่ในทีม")
    df_tga = _read_csv(_data(f"tga_lines_{suffix}.csv"))
    df_emp = _read_csv(_data(f"emp_cache_{suffix}.csv"))
    if df_tga is None or df_tga.empty:
        print(f"    ไม่พบ (หรือว่างเปล่า) data/tga_lines_{suffix}.csv — ข้ามการตรวจนี้")
    else:
        df_tga["emp_id"] = df_tga["emp_id"].astype(str).str.strip()
        qty = pd.to_numeric(df_tga["qty"], errors="coerce").fillna(0.0)
        team = (
            set(df_emp["emp_id"].astype(str).str.strip())
            if df_emp is not None and "emp_id" in df_emp.columns
            else set()
        )
        in_tga = set(df_tga["emp_id"].unique())
        print(f"    แถวเป้าดิบ {len(df_tga):,} แถว · รวม {qty.sum():,.0f} หีบ")
        print(
            f"    เป้าหีบที่นับเข้าหน้าจอ {total_boxes:,.0f} หีบ"
            f" · ต่างกัน {qty.sum() - total_boxes:+,.0f} หีบ"
        )
        if team:
            orphan = sorted(in_tga - team)
            if orphan:
                lost = float(qty[df_tga["emp_id"].isin(orphan)].sum())
                print(f"    ⚠ มีเป้าของคนนอกทีม {len(orphan)} คน — {lost:,.0f} หีบถูกตัดทิ้ง")
                print(f"      {', '.join(orphan[:20])}")
            else:
                print("    ทุกแถวเป็นของคนในทีม  ✔ ตัดสาเหตุนี้ออกได้")
            idle = sorted(team - in_tga)
            if idle:
                print(f"    หมายเหตุ: คนในทีมที่ไม่มีเป้าเลย {len(idle)} คน — {', '.join(idle[:20])}")
        else:
            print(f"    ไม่พบ data/emp_cache_{suffix}.csv — เทียบรายชื่อทีมไม่ได้")
        for col in ("salestype", "warehouse_code"):
            if col in df_tga.columns:
                g = df_tga.assign(_k=df_tga[col].fillna("").astype(str)).groupby("_k")["qty"].sum()
                if len(g) > 1:
                    parts = ", ".join(f"{k or '(ว่าง)'}={v:,.0f}" for k, v in g.items())
                    print(f"    แยกตาม {col}: {parts}")
    print()

    # ── เป้าเงินรายคน ─────────────────────────────────────────────────
    print("[4] เป้าเงินรายคน")
    df_sun = _read_csv(_data(f"target_sun_{suffix}.csv"))
    if df_sun is None:
        print(f"    ไม่พบ data/target_sun_{suffix}.csv")
    else:
        s = pd.to_numeric(df_sun["target_sun"], errors="coerce").fillna(0.0)
        print(f"    รวม {_baht(s.sum())} บาท จาก {len(df_sun)} คน")
        for _, r in df_sun.sort_values("target_sun", ascending=False).iterrows():
            print(f"      {str(r['emp_id']):>8}  {_baht(r['target_sun']):>18}")
        if abs(float(s.sum()) - shown) > 1:
            print(f"    ⚠ ไม่เท่ากับเป้ารวมบนหน้าจอ — ต่าง {_baht(shown - float(s.sum()))} บาท")
    print()

    # ── ทางที่สาม: เป้าเปลี่ยนหลังเปิดงวด ───────────────────────────────
    print("[5] ทางที่สาม — เป้าเปลี่ยนหลังเปิดงวดครั้งแรก")
    base = _read_json(_data("baselines", f"{suffix}.json"))
    if not base:
        print(f"    ไม่มี data/baselines/{suffix}.json — งวดนี้ยังไม่เคยเก็บเป้าตั้งต้น")
    else:
        b_sun = float(base.get("total_target_sun") or 0)
        b_box = float(base.get("total_target_boxes") or 0)
        print(f"    ตอนเปิดครั้งแรก ({base.get('captured_at')}): {_baht(b_sun)} บาท · {b_box:,.0f} หีบ")
        print(f"    ตอนนี้: {_baht(shown)} บาท · {total_boxes:,.0f} หีบ")
        d_sun, d_box = shown - b_sun, total_boxes - b_box
        if abs(d_sun) < 1 and abs(d_box) < 1:
            print("    ไม่เปลี่ยน  ✔ ตัดสาเหตุนี้ออกได้")
        else:
            print(f"    ⚠ ต่าง {_baht(d_sun)} บาท · {d_box:+,.0f} หีบ — เป้าถูกแก้หลังเปิดงวด")
    print()

    print("สรุป: ถ้า [2] [3] [5] ขึ้น ✔ ครบทั้งสามข้อ แปลว่าเป้าไม่ได้หายในแอป")
    print("      แต่ Target Sun ส่งมาไม่ครบตั้งแต่ต้น — ต้องเทียบต้นทางราย SALESMANCODE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
