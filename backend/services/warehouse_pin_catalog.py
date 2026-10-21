"""
รายการสินค้า + สถิติคลังสำหรับหน้าแอดมิน「กติกาบังคับคลัง」 — อ่านจากไฟล์ขั้นที่ 1 ของทุกทีม

ทำไมไม่อ่าน Fabric TGA แบบเดิม: ตั้งแต่ 6 ต.ค. 2026 เป้ามาจาก Target Sun อย่างเดียว ตาราง
tga_target_salesman_next ใน Fabric ไม่มีใครดูแลแล้ว (ดู targetsun_read.fallback_to_fabric)
เดิมหน้านี้ยังดึงจากตารางนั้น → งวดใหม่ไม่มีข้อมูล หน้าจอขึ้น「ไม่พบกลุ่มสินค้าที่มีเป้างวดนี้」
ทั้งที่เป้าใน Target Sun เข้าแล้ว (ผู้ใช้เจอ 21 ต.ค. 2026)

ไฟล์ที่ใช้ (เขียนตอนแต่ละทีมโหลดขั้นที่ 1 จาก Target Sun — ชุดเดียวกับที่ตอนส่งจริงใช้):
  data/target_boxes_<ทีม>_<ปี>_<เดือน>.csv — sku, section, ชื่อสินค้า (ทำรายการให้เลือก)
  data/tga_lines_<ทีม>_<ปี>_<เดือน>.csv   — ภาค/division/คลังต่อพนักงาน×สินค้า (สถิติคลัง)

ถ้างวดที่ขอยังไม่มีทีมไหนโหลด (ช่วงต้นเดือนที่เป้ายังไม่เข้า หรือเพิ่งเข้าแต่ยังไม่มีใครเปิด)
ถอยไปใช้งวดล่าสุดที่มีไฟล์ — กติกาผูกกับรหัสสินค้า ไม่ใช่งวด รายการงวดก่อนจึงใช้ตั้งกติกาได้
และบอกบนจอเสมอว่าใช้งวดไหน ไม่ใช้เงียบ ๆ · อ่านไฟล์อย่างเดียว ไม่ต่อระบบนอก
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import pandas as pd

from .lakehouse import _normalize_grain_dtype

logger = logging.getLogger("target_allocation")

_DATA_DIR = "data"


def _files_by_period(prefix: str) -> dict[tuple[int, int], list[str]]:
    """{(ปี, เดือน): [path, ...]} ของไฟล์ <prefix><ทีม>_<ปี>_<เดือน>.csv ใน data/"""
    pat = re.compile(rf"^{re.escape(prefix)}(.+)_(\d{{4}})_(\d{{2}})\.csv$")
    out: dict[tuple[int, int], list[str]] = {}
    try:
        names = os.listdir(_DATA_DIR)
    except OSError:
        return out
    for name in names:
        m = pat.match(name)
        if not m:
            continue
        out.setdefault((int(m.group(2)), int(m.group(3))), []).append(os.path.join(_DATA_DIR, name))
    return out


def _pick_period(
    by_period: dict[tuple[int, int], list[str]], year: int, month: int
) -> tuple[int, int] | None:
    """งวดที่ขอถ้ามีไฟล์ ไม่งั้นงวดล่าสุดที่ไม่เกินงวดที่ขอ ไม่งั้นงวดล่าสุดที่มี"""
    want = (int(year), int(month))
    if want in by_period:
        return want
    earlier = [p for p in by_period if p <= want]
    if earlier:
        return max(earlier)
    return max(by_period) if by_period else None


def _read_all(paths: list[str]) -> pd.DataFrame:
    frames = []
    for p in paths:
        try:
            frames.append(pd.read_csv(p, dtype=str, keep_default_na=False))
        except Exception as e:
            logger.warning("กติกาบังคับคลัง: อ่าน %s ไม่ได้ (%s) — ข้ามไฟล์นี้", p, e)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _period_note(used: tuple[int, int], year: int, month: int, n_files: int) -> str:
    label = f"{used[1]:02d}/{used[0]}"
    if used != (int(year), int(month)):
        return (
            f"งวด {int(month):02d}/{int(year)} ยังไม่มีทีมไหนโหลดข้อมูลขั้นที่ 1 — "
            f"ใช้ข้อมูลเป้า Target Sun งวด {label} แทน ({n_files} ทีม)"
        )
    return f"ข้อมูลเป้า Target Sun งวด {label} จาก {n_files} ทีมที่โหลดขั้นที่ 1 แล้ว"


def catalog(year: int, month: int) -> dict[str, Any]:
    """สินค้าที่มีเป้าหีบ > 0 พร้อม section/ชื่อ — รูปแถวเดียวกับ /admin/sku-links/catalog"""
    by_period = _files_by_period("target_boxes_")
    used = _pick_period(by_period, year, month)
    if used is None:
        return {
            "skus": [], "count": 0, "source_year": None, "source_month": None,
            "hint": "ยังไม่มีทีมไหนโหลดข้อมูลขั้นที่ 1 เลย — ให้ซุปเปิดทีมอย่างน้อย 1 ทีมก่อน แล้วกดโหลดใหม่",
        }
    paths = by_period[used]
    df = _read_all(paths)
    if df.empty or "sku" not in df.columns:
        return {
            "skus": [], "count": 0, "source_year": used[0], "source_month": used[1],
            "hint": f"อ่านไฟล์เป้างวด {used[1]:02d}/{used[0]} ไม่ได้",
        }
    for c in ("section", "product_name_thai", "product_name_english", "supervisor_target_boxes"):
        if c not in df.columns:
            df[c] = ""
    df["sku"] = df["sku"].astype(str).str.strip()
    df["_boxes"] = pd.to_numeric(df["supervisor_target_boxes"], errors="coerce").fillna(0)
    agg = (
        df[df["sku"] != ""]
        .groupby("sku", sort=True)
        .agg(
            target_boxes=("_boxes", "sum"),
            # ทุกทีมได้ section/ชื่อจาก Dim_Product ชุดเดียวกัน — เลือกค่าที่ไม่ว่างค่าแรก
            section=("section", lambda s: next((str(v).strip() for v in s if str(v).strip()), "")),
            product_name_thai=("product_name_thai", lambda s: next((str(v).strip() for v in s if str(v).strip()), "")),
            product_name_english=("product_name_english", lambda s: next((str(v).strip() for v in s if str(v).strip()), "")),
        )
        .reset_index()
    )
    agg = agg[agg["target_boxes"] > 0]
    skus = [
        {
            "sku": r.sku,
            "section": r.section,
            "product_name_thai": r.product_name_thai,
            "product_name_english": r.product_name_english,
            "target_boxes": float(r.target_boxes),
        }
        for r in agg.itertuples()
    ]
    return {
        "skus": skus,
        "count": len(skus),
        "source_year": used[0],
        "source_month": used[1],
        "hint": _period_note(used, year, month, len(paths)),
    }


def combos(skus: list[str], year: int, month: int) -> dict[str, Any]:
    """ภาค × division × คลัง ที่เป้าจริงของ SKU ชุดนี้ใช้อยู่ + หีบรวม/จำนวนคน
    (คอลัมน์เดียวกับ Fabric get_tga_dim_combos_by_product เดิม หน้าเว็บจึงใช้ได้เลย)"""
    want = {str(s).strip() for s in skus if str(s).strip()}
    cols = ["areacode", "divisioncode", "warehouse_code", "qty", "emp_count"]
    by_period = _files_by_period("tga_lines_")
    # งวดที่ขอก่อน แล้วถอยไปงวดก่อนหน้าอีก 2 งวด — ต้นงวดมีทีมโหลดแล้วแค่บางทีม ถ้าใช้งวดเดียว
    # ภาค/division ของทีมที่ยังไม่โหลดจะไม่มีให้เลือก (เช่น ภาคกลาง B) · ชุดภาค×division×คลังเดียวกัน
    # ใช้ตัวเลขจากงวดใหม่สุดที่มีเสมอ ไม่บวกข้ามงวด
    order = sorted((p for p in by_period if p <= (int(year), int(month))), reverse=True)[:3]
    if not order:
        order = sorted(by_period, reverse=True)[:1]
    seen: dict[tuple[str, str, str], dict[str, Any]] = {}
    used_periods: list[tuple[int, int]] = []
    for used in order:
        g = _read_all(by_period[used])
        if g.empty or "sku" not in g.columns:
            continue
        g = _normalize_grain_dtype(g)
        g = g[g["sku"].isin(want)]
        if g.empty:
            continue
        agg = (
            g.groupby(["areacode", "divisioncode", "warehouse_code"], sort=True)
            .agg(qty=("qty", "sum"), emp_count=("emp_id", "nunique"))
            .reset_index()
        )
        added = False
        for r in agg[cols].to_dict(orient="records"):
            key = (r["areacode"], r["divisioncode"], r["warehouse_code"])
            if key not in seen:
                seen[key] = r
                added = True
        if added:
            used_periods.append(used)
    if not seen:
        return {
            "combos": [], "source_year": None, "source_month": None,
            "hint": f"ไม่พบข้อมูลคลังของ {len(want)} SKU ที่เลือกในไฟล์ขั้นที่ 1 ของทุกทีม",
        }
    newest = used_periods[0]
    labels = ", ".join(f"{m:02d}/{y}" for y, m in used_periods)
    hint = f"สถิติคลังจากเป้า Target Sun งวด {labels} (ไฟล์ขั้นที่ 1 ที่ทีมโหลดไว้)"
    if newest != (int(year), int(month)):
        hint = f"งวด {int(month):02d}/{int(year)} ยังไม่มีข้อมูลของสินค้าชุดนี้ — " + hint
    return {
        "combos": sorted(seen.values(), key=lambda r: (r["areacode"], r["divisioncode"], r["warehouse_code"])),
        "source_year": newest[0],
        "source_month": newest[1],
        "hint": hint,
    }
