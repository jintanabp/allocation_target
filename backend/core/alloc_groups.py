"""
หน่วยกลุ่มที่ใช้กระจาย — แบรนด์ หรือ แบรนด์ · กลุ่มสินค้า (Section) สำหรับแบรนด์ที่ผู้ใช้เลือกแยก

ผู้ใช้ขอ (8 ต.ค. 2026): เลือกบางแบรนด์ (เช่น มาม่า) ให้แยกเป็นกลุ่มสินค้าตาม Dim_Product[Section]
ส่วนแบรนด์อื่นยังเป็นแบรนด์เหมือนเดิม · หน่วยนี้ใช้กับวิธีกระจายรายหน่วย (brand_strategy_map)
และการหมุนหีบ SKU เล็ก — ไม่แตะเป้าหีบ คลัง หรือแถวที่ส่ง Target Sun

คีย์ต้องตรงกับหน้าเว็บ (frontend/app.js: _allocGroupKey) ตัวอักษรต่อตัวอักษร
"""

from __future__ import annotations

import re
from typing import Iterable

import pandas as pd

SEP = " · "
BLANK_SECTION = "-"
COLUMN = "alloc_group"


def _clean(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v).strip()


def norm_section(v) -> str:
    """
    รหัส Section — CSV ที่อ่านเป็นตัวเลขได้ "702.0" ต้องเป็น "702" เหมือนหน้าเว็บ
    ช่องว่างใน target_boxes_*.csv ถูก _read_sku_csv เติมเป็น 0 (คอลัมน์ที่ไม่ใช่ชื่อ) — "0" จึงแปลว่า
    "ไม่มีกลุ่ม" ไม่ใช่รหัส 0 (ตรวจซ้ำ 8 ต.ค. 2026: เดิมได้ "มาม่า · 0" แต่หน้าเว็บได้ "มาม่า · -" วิธีที่เลือกหายเงียบ)
    """
    s = _clean(v)
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".", 1)[0]
    if re.fullmatch(r"0+", s):
        return ""
    return s


def brand_of(row) -> str:
    """แบรนด์ของแถว — ไทยก่อน แล้วอังกฤษ (ตรงกับหน้าเว็บ)"""
    for col in ("brand_name_thai", "brand_name_english"):
        v = _clean(row.get(col, ""))
        if v:
            return v
    return ""


def norm_split_brands(brands: Iterable | None) -> set[str]:
    return {str(b).strip() for b in (brands or []) if str(b or "").strip()}


def group_key(brand: str, section, split: set[str]) -> str:
    """แบรนด์ที่ไม่ได้เลือกแยก = ชื่อแบรนด์ (เหมือนเดิม) · ที่เลือกแยก = แบรนด์ · รหัส Section"""
    b = _clean(brand)
    if not b or b not in split:
        return b
    return f"{b}{SEP}{norm_section(section) or BLANK_SECTION}"


def add_group_column(df_sku: pd.DataFrame, split_brands: Iterable | None) -> pd.DataFrame:
    """ใส่คอลัมน์ alloc_group — ไม่มีแบรนด์ที่เลือกแยก = ไม่แตะ df (พฤติกรรมเดิมทุกอย่าง)"""
    split = norm_split_brands(split_brands)
    if df_sku is None or df_sku.empty or not split:
        return df_sku
    out = df_sku.copy()
    has_sec = "section" in out.columns
    out[COLUMN] = [
        group_key(brand_of(r), r.get("section") if has_sec else "", split)
        for _, r in out.iterrows()
    ]
    return out


def row_group(row) -> str:
    """หน่วยกลุ่มของแถว — มีคอลัมน์ alloc_group ใช้ค่านั้น ไม่มีก็คือแบรนด์"""
    v = _clean(row.get(COLUMN, "")) if hasattr(row, "get") else ""
    return v or brand_of(row)
