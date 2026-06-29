"""
ตรวจสอบงวดเป้าที่ผู้ใช้เลือกกับ EFFECTIVEDATE ของ tga_target_salesman_next

กติกาธุรกิจ: วันที่มีผล (EFFECTIVEDATE) บอกว่า snapshot ใช้กำหนดเป้าของ **เดือนเดียวกันกับตัวเดือนของวันที่ค่านั้น**
(เช่น EFFECTIVEDATE ใน พ.ค. → เป้างวด พ.ค.)

ถ้าในโมเดล EFFECTIVEDATE เป็น null ทุกแถว: Fabric connector จะลอง MAX(UPDATEDATE)
(หรือคอลัมน์ที่ตั้ง TGA_COL_EFFECTIVE_FALLBACK) แทน — ใช้เดือนของค่านั้นเข้ากติกาช่วงประกาศเหมือนกัน

ตั้ง `TGA_EFFECTIVE_IMPLIED_TARGET=next` ได้ถ้าต้องการพฤติกรรมเก่า (เป้า = เดือนถัดจากวันที่อ้างอิง)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

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


def tga_filter_by_selected_period() -> bool:
    """
    กรองแถว TGA ตาม YEAR/MONTH(EFFECTIVEDATE) = งวดที่ผู้ใช้เลือก

    ค่า TGA_FILTER_BY_EFFECTIVE=0 ใน .env ไม่ปิดการกรอง — แอปใช้กติกานี้เสมอ
    """
    return True


def expected_allocation_period_ce() -> tuple[int, int]:
    """งวดที่ต้องทำตามแอป = เดือนถัดจากวันนี้ (ค.ศ.) — สอดคล้อง getNextMonthPeriod ใน frontend"""
    now = datetime.now(ZoneInfo("Asia/Bangkok"))
    m = now.month + 1
    y = now.year
    if m > 12:
        m = 1
        y += 1
    return y, m


def is_expected_work_period(target_month: int, target_year: int) -> bool:
    """งวดที่ผู้ใช้ควรกระจายเป้า = เดือนหน้าจากปฏิทินปัจจุบัน"""
    ey, em = expected_allocation_period_ce()
    return int(target_year) == ey and int(target_month) == em


def period_label_th(month: int, year_ce: int) -> str:
    m = int(month)
    y = int(year_ce)
    if m < 1 or m > 12:
        return f"งวด {y + 543}"
    return f"{_MONTH_TH[m]} {y + 543}"


_MSG_NOT_UPDATED_WORK_PERIOD = "ระบบยังไม่อัปเดตเป้า"


def tga_empty_period_message(
    fabric,
    target_month: int,
    target_year: int,
) -> tuple[str, str]:
    """
    ข้อความเมื่อไม่มีแถวเป้าในงวดที่เลือก (หลังกรอง EFFECTIVEDATE)

    Returns (status, message):
      - no_effective — ไม่มี EFFECTIVEDATE (และ fallback) ในตาราง TGA เลย
      - not_updated — เลือกงวดล่วงหน้ากว่าที่ HQ อัปเดตล่าสุด
      - no_data — งวดไม่เกิน snapshot แต่ไม่มีแถวในงวดนั้น
    """
    period_th = period_label_th(target_month, target_year)
    sel_y, sel_m = int(target_year), int(target_month)
    work_period = is_expected_work_period(sel_m, sel_y)
    try:
        raw = fabric.get_tga_max_effective_raw()
        ts = _parse_effective_raw(raw)
        if ts is None:
            if work_period:
                return (
                    "not_updated",
                    f"{_MSG_NOT_UPDATED_WORK_PERIOD} สำหรับงวด {period_th} — กรุณารอ HQ อัปเดตเป้าเข้าระบบ",
                )
            return (
                "no_effective",
                (
                    f"ระบบยังไม่พบวันที่มีผล (EFFECTIVEDATE) ของเป้าใน Target Sun "
                    f"สำหรับงวด {period_th} — กรุณาให้ HQ อัปเดตเป้าเข้าระบบก่อน"
                ),
            )
        eff_y, eff_m = _to_ce_year_month(int(ts.year), int(ts.month))
        implied_y, implied_m = implied_target_year_month(eff_y, eff_m)
        if (sel_y, sel_m) > (implied_y, implied_m):
            if work_period:
                return (
                    "not_updated",
                    f"{_MSG_NOT_UPDATED_WORK_PERIOD} สำหรับงวด {period_th} — กรุณารอ HQ อัปเดตเป้าเข้าระบบ",
                )
            return (
                "not_updated",
                (
                    f"ยังไม่มีการอัปเดตเป้างวด {period_th} เข้ามาในระบบเป้า Target Sun "
                    f"(ข้อมูลล่าสุดในระบบคืองวด {period_label_th(implied_m, implied_y)}) "
                    "— กรุณาเลือกดูงวดที่มีข้อมูลแล้ว"
                ),
            )
    except Exception as e:
        logger.warning("tga_empty_period_message: %s", e)

    return (
        "no_data",
        f"ไม่มีข้อมูลเป้างวด {period_th} ในระบบเป้า Target Sun",
    )


def implied_target_year_month(eff_y_ce: int, eff_m: int) -> tuple[int, int]:
    """
    เดือนเป้าที่ snapshot TGA นี้อ้างอิงโดย implied จากวันที่ (EFFECTIVEDATE หรือ fallback)

    Default: เดือนเดียวกับ EFFECTIVEDATE
    พฤติกรรมเก่า: ตั้ง env TGA_EFFECTIVE_IMPLIED_TARGET=next เพื่อใช้เดือนถัดไป
    """
    mode = os.environ.get("TGA_EFFECTIVE_IMPLIED_TARGET", "same").strip().lower()
    if mode in ("next", "1", "yes", "true"):
        ty, tm = eff_y_ce, eff_m
        tm += 1
        if tm > 12:
            tm = 1
            ty += 1
        return ty, tm
    return eff_y_ce, eff_m


_TGA_PERIOD_EMPTY_TITLES = {
    "not_updated": "ยังไม่มีการอัปเดตเป้างวดนี้",
    "no_data": "ไม่มีข้อมูลเป้างวดนี้",
    "no_effective": "ยังไม่มีข้อมูลเป้าในระบบ",
}


def enforce_tga_has_targets_for_period(
    fabric,
    target_month: int,
    target_year: int,
    df_tga: pd.DataFrame | None,
    total_sup_boxes: int,
    *,
    debug: dict | None = None,
) -> None:
    """ไม่มีเป้าหีบในงวดที่เลือก (หลังกรอง EFFECTIVEDATE) → 409 ห้ามเข้า Dashboard"""
    has_positive = df_tga is not None and not df_tga.empty
    if has_positive and int(total_sup_boxes) > 0:
        return

    status, msg = tga_empty_period_message(fabric, target_month, target_year)
    work_period = is_expected_work_period(target_month, target_year)
    if work_period and status == "not_updated":
        title = _MSG_NOT_UPDATED_WORK_PERIOD
    else:
        title = _TGA_PERIOD_EMPTY_TITLES.get(status, "ไม่มีเป้างวดนี้")
    raise HTTPException(
        status_code=409,
        detail={
            "code": "TGA_PERIOD_EMPTY",
            "tga_period_status": status,
            "is_expected_work_period": work_period,
            "title": title,
            "message": msg,
            "selected": {"month": int(target_month), "year": int(target_year)},
            "debug": debug or {},
        },
    )


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
