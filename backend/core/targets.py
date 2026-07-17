import logging
import os

import pandas as pd

from .atomic_io import read_locked
from .paths import target_boxes_cache_path, target_sun_cache_path

logger = logging.getLogger("target_allocation")


def _read_sku_csv(path: str) -> pd.DataFrame | None:
    if not os.path.exists(path):
        return None
    with read_locked(path):  # กัน writer เรียก os.replace ตอนเราถือ handle อยู่ (Windows)
        df = pd.read_csv(path, dtype={"sku": str}).dropna(subset=["sku"]).fillna(0)
    df["sku"] = df["sku"].astype(str).str.strip()
    return df


def _read_sun_csv(path: str) -> pd.DataFrame | None:
    if not os.path.exists(path):
        return None
    with read_locked(path):
        df = pd.read_csv(path, dtype={"emp_id": str}).dropna(subset=["emp_id"]).fillna(0)
    df["emp_id"] = df["emp_id"].astype(str).str.strip()
    return df


def load_target_csv() -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    """
    ไฟล์ global เดิม — ใช้เฉพาะโหมด USE_LEGACY_TARGET_CSV (dev) เท่านั้น

    อย่าเรียกจาก path ปกติ: ไฟล์นี้ไม่มี sup_id ทั้งในชื่อไฟล์และในคอลัมน์
    ทีมที่โหลดทีหลังจึงเขียนทับของทีมก่อนหน้า แล้วอีกทีมจะคำนวณจากเป้าของคนอื่นแบบเงียบ ๆ
    ใช้ load_target_csv_for(sup_id, month, year) แทน
    """
    return _read_sku_csv("data/target_boxes.csv"), _read_sun_csv("data/target_sun.csv")


def load_target_csv_for(
    sup_id: str,
    month: int,
    year: int,
    *,
    allow_legacy_fallback: bool = True,
) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    """
    เป้าหีบ/Target Sun ของทีมนี้งวดนี้เท่านั้น

    allow_legacy_fallback: ถ้ายังไม่มีไฟล์ราย sup (เช่นเพิ่ง deploy และ payload cache ยังค้าง)
    ให้ตกไปอ่านไฟล์ global เดิมเพื่อไม่ให้ผู้ใช้เจอ error — ถอดออกได้ใน release ถัดไป
    เมื่อ log ไม่มี warning นี้แล้ว
    """
    df_sku = _read_sku_csv(target_boxes_cache_path(sup_id, month, year))
    df_sun = _read_sun_csv(target_sun_cache_path(sup_id, month, year))
    if df_sku is not None:
        return df_sku, df_sun

    if not allow_legacy_fallback:
        return None, df_sun

    legacy_sku, legacy_sun = load_target_csv()
    if legacy_sku is not None:
        logger.warning(
            "target CSV: ใช้ไฟล์ global เดิมสำหรับ %s %s/%s — "
            "ยังไม่มีไฟล์ราย sup (ข้อมูลอาจเป็นของทีมอื่น) โหลด Dashboard ใหม่เพื่อสร้างไฟล์",
            sup_id,
            month,
            year,
        )
    return legacy_sku, df_sun if df_sun is not None else legacy_sun


def target_boxes_source_path(
    sup_id: str, month: int | None, year: int | None
) -> str:
    """
    path ไฟล์เป้าที่จะส่งให้ generate_excel.create_target_excel

    ต้องเช็คว่ามีไฟล์จริงก่อน เพราะ generate_excel._load_sku_official คืน {} เงียบ ๆ
    เมื่อ path ไม่มีไฟล์ → ได้ Excel ที่แถว "เป้าหีบ (หัวหน้า)" ว่างโดยไม่มี error
    (เกิดได้ช่วงเปลี่ยนผ่านที่ยังไม่มีไฟล์ราย sup แต่ df_sku มาจาก fallback global)
    """
    if month and year:
        p = target_boxes_cache_path(sup_id, month, year)
        if os.path.exists(p):
            return p
    return "data/target_boxes.csv"


def target_csv_ready(sup_id: str, month: int, year: int) -> bool:
    """มีไฟล์เป้าราย sup ครบทั้งคู่แล้วหรือยัง — ใช้กัน payload cache บังการสร้างไฟล์"""
    return os.path.isfile(target_boxes_cache_path(sup_id, month, year)) and os.path.isfile(
        target_sun_cache_path(sup_id, month, year)
    )
