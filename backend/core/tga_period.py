"""
ตรวจสอบงวดเป้าที่ผู้ใช้เลือกกับ EFFECTIVEDATE ของ tga_target_salesman_next

กติกาธุรกิจ: วันที่มีผล (EFFECTIVEDATE) ระบุว่า snapshot นี้เริ่มใช้ตั้งแต่เดือนนั้น
เพื่อกำหนดเป้าของ **เดือนถัดไป** (เช่น EFFECTIVEDATE = พ.ค. → กำหนดเป้าเดือน มิ.ย.)
"""

from __future__ import annotations

import logging
import os

import pandas as pd
from fastapi import HTTPException

logger = logging.getLogger("target_allocation")

_MONTH_TH = (
    "",
    "มกราคม",
    "กุมภาพันธ์",
    "มีนาคม",
    "เมษายน",
    "พฤษภาคม",
    "มิถุนายน",
    "กรกฎาคม",
    "สิงหาคม",
    "กันยายน",
    "ตุลาคม",
    "พฤศจิกายน",
    "ธันวาคม",
)


def _parse_effective_raw(raw) -> pd.Timestamp | None:
    if raw is None or raw == "":
        return None
    dt = pd.to_datetime(raw, errors="coerce")
    if pd.isna(dt):
        return None
    return dt


def _to_ce_year_month(y: int, m: int) -> tuple[int, int]:
    """ถ้าปีจากโมเดลเป็น พ.ศ. (เช่น 2569) แปลงเป็น ค.ศ. สำหรับเทียบกับ target_year ของแอป"""
    if y >= 2400:
        return y - 543, m
    return y, m


def implied_target_year_month(eff_y_ce: int, eff_m: int) -> tuple[int, int]:
    """เดือนที่ snapshot นี้ใช้กำหนดเป้า = เดือนถัดจาก EFFECTIVEDATE"""
    ty, tm = eff_y_ce, eff_m
    tm += 1
    if tm > 12:
        tm = 1
        ty += 1
    return ty, tm


def enforce_tga_selection_matches_effective_window(
    fabric,
    target_month: int,
    target_year: int,
) -> None:
    """
    ถ้างวดที่เลือกน้อยกว่างวดเป้าที่ snapshot ปัจจุบันรองรับ → 409
    """
    if os.environ.get("TGA_ENFORCE_EFFECTIVE_WINDOW", "1").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    ):
        return

    try:
        raw = fabric.get_tga_max_effective_raw()
    except Exception as e:
        logger.warning("TGA EFFECTIVEDATE check skipped (query error): %s", e)
        return

    if raw is None:
        logger.warning("TGA EFFECTIVEDATE check skipped (empty / null)")
        return

    ts = _parse_effective_raw(raw)
    if ts is None:
        logger.warning("TGA EFFECTIVEDATE check skipped (unparseable: %r)", raw)
        return

    eff_y, eff_m = _to_ce_year_month(int(ts.year), int(ts.month))
    implied_y, implied_m = implied_target_year_month(eff_y, eff_m)
    sel_y, sel_m = int(target_year), int(target_month)

    if (sel_y, sel_m) >= (implied_y, implied_m):
        return

    sel_th = f"{_MONTH_TH[sel_m]} {sel_y + 543}"
    imp_th = f"{_MONTH_TH[implied_m]} {implied_y + 543}"
    eff_disp_y = eff_y + 543
    eff_label = f"{int(ts.day)} {_MONTH_TH[eff_m]} {eff_disp_y}"

    raise HTTPException(
        status_code=409,
        detail={
            "code": "TGA_EFFECTIVE_WINDOW",
            "title": "งวดที่เลือกหมดช่วงกำหนดแล้ว",
            "message": (
                f"ตอนนี้ข้อมูลเป้าจาก HQ (TGA) อัปเดตไปสำหรับงวด {imp_th} แล้ว "
                f"จึงไม่สามารถกำหนดเป้างวด {sel_th} ได้"
            ),
            "selected": {"month": sel_m, "year": sel_y},
            "suggested": {"month": implied_m, "year": implied_y},
            "effectiveDateLabel": eff_label,
        },
    )
