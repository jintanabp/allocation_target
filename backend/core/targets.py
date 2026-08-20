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


def _first_meaningful(series: pd.Series):
    """ค่าแรกที่ "มีความหมาย" ในกลุ่ม — ข้ามค่าว่าง/ศูนย์ที่มาจาก fillna(0) ของไฟล์ทีมอื่น"""
    for v in series:
        if v is None:
            continue
        if isinstance(v, str):
            if v.strip():
                return v
            continue
        try:
            if float(v) != 0:
                return v
        except (TypeError, ValueError):
            return v
    return series.iloc[0] if len(series) else 0


def load_summed_target_boxes(
    sup_ids: list[str], month: int, year: int
) -> tuple[pd.DataFrame | None, list[str]]:
    """
    เป้าหีบต่อ SKU ของหลายทีมบวกรวมกัน — ใช้ตอน "กระจายรวมทั้งภาค"

    คืน (df, รายชื่อทีมที่ยังไม่มีไฟล์เป้า) — ผู้เรียกต้องปฏิเสธเมื่อมีทีมขาด
    ไม่งั้นเป้าที่ใช้กระจายจะน้อยกว่าที่ผู้ใช้เห็นบนตารางรวมภาคโดยไม่มีสัญญาณ

    **ห้าม fallback ไปไฟล์ global เด็ดขาด** — ถ้าหลายทีมตกไปอ่านไฟล์เดียวกัน
    ผลรวมจะกลายเป็นเป้าเดิม × จำนวนทีม แล้วประตู I1 จะบังคับให้ผลกระจายเกินจริง
    ออกไปทั้งภาคโดยที่ทุกด่านมองว่า "ตรงเป้า"
    """
    ids: list[str] = []
    for raw in sup_ids or []:
        sid = str(raw or "").strip().upper()
        # รหัสซ้ำ = เป้าทีมนั้นถูกบวกสองรอบ
        if sid and sid not in ids:
            ids.append(sid)
    if not ids:
        return None, []

    frames: list[pd.DataFrame] = []
    missing: list[str] = []
    for sid in ids:
        df = _read_sku_csv(target_boxes_cache_path(sid, month, year))
        if df is None:
            missing.append(sid)
            continue
        df = df.copy()
        df["sku"] = df["sku"].astype(str).str.strip()
        df = df[df["sku"] != ""]
        # SKU ซ้ำในไฟล์ของทีมเดียว = ข้อมูลเสีย (I6) — ยุบก่อนบวกข้ามทีม
        # ไม่งั้นความผิดพลาดของทีมเดียวจะบวกเข้าเป้าของทั้งภาค
        if df["sku"].duplicated().any():
            logger.warning(
                "เป้าหีบของ %s %s-%02d มี SKU ซ้ำ — ยุบเหลือแถวเดียวก่อนรวมภาค",
                sid, year, month,
            )
            df = df.drop_duplicates(subset=["sku"], keep="last")
        frames.append(df)

    if not frames:
        return None, missing

    all_df = pd.concat(frames, ignore_index=True)
    if "supervisor_target_boxes" not in all_df.columns:
        all_df["supervisor_target_boxes"] = 0
    all_df["supervisor_target_boxes"] = pd.to_numeric(
        all_df["supervisor_target_boxes"], errors="coerce"
    ).fillna(0.0)

    # sort=False = คงลำดับ SKU ตามที่พบ และให้ทีมแรกในลิสต์ชนะตอนหยิบ meta
    totals = all_df.groupby("sku", as_index=False, sort=False)[
        "supervisor_target_boxes"
    ].sum()
    meta_cols = [
        c for c in all_df.columns if c not in ("sku", "supervisor_target_boxes")
    ]
    if meta_cols:
        meta = all_df.groupby("sku", as_index=False, sort=False)[meta_cols].agg(
            _first_meaningful
        )
        totals = totals.merge(meta, on="sku", how="left")
    return totals, missing


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
