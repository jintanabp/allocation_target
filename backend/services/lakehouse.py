import io
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import msal
import pandas as pd
import requests
from fastapi import HTTPException

from ..core.atomic_io import read_locked
from ..core.paths import safe_id, target_boxes_cache_path, tga_grain_cache_path
from ..fabric_dax_connector import FabricDAXConnector
from ..schemas import LakehouseUploadRequest

logger = logging.getLogger("target_allocation")

LAKEHOUSE_TEXT_DATE_COLUMNS = frozenset({"EFFECTIVEDATE", "UPDATEDATE"})

LAKEHOUSE_CSV_COLUMNS = [
    "PRODUCTCODE",
    "SALESTYPE",
    "DIVISIONCODE",
    "SALESMANCODE",
    "AREACODE",
    "PROVINCECODE",
    "WAREHOUSECODE",
    "QUANTITYCASE",
    "EFFECTIVEDATE",
    "UPDATEDATE",
    "USERCODE",
]


def _get_storage_token() -> str:
    tenant_id = (os.environ.get("FABRIC_TENANT_ID") or "").strip()
    client_id = (os.environ.get("FABRIC_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("FABRIC_CLIENT_SECRET") or "").strip()
    if not (tenant_id and client_id and client_secret):
        raise HTTPException(
            500,
            detail=(
                "ยังไม่ได้ตั้งค่า Service Principal สำหรับอัปโหลดเข้า OneLake "
                "(ต้องมี FABRIC_TENANT_ID / FABRIC_CLIENT_ID / FABRIC_CLIENT_SECRET)"
            ),
        )

    authority = f"https://login.microsoftonline.com/{tenant_id}"
    cca = msal.ConfidentialClientApplication(
        client_id,
        client_credential=client_secret,
        authority=authority,
    )
    scopes = ["https://storage.azure.com/.default"]
    result = cca.acquire_token_for_client(scopes=scopes)
    token = result.get("access_token")
    if token:
        return token
    err = result.get("error_description") or result.get("error") or str(result)
    raise HTTPException(500, detail=f"ขอ token สำหรับอัปโหลด OneLake ไม่สำเร็จ: {err}")


def _onelake_base_path() -> tuple[str, str]:
    ws = (os.environ.get("ONELAKE_WORKSPACE_ID") or "").strip()
    lh = (os.environ.get("ONELAKE_LAKEHOUSE_ID") or "").strip()
    if not ws or not lh:
        raise HTTPException(
            500,
            detail=(
                "ยังไม่ได้ตั้งค่าเป้าหมาย Lakehouse (ต้องมี ONELAKE_WORKSPACE_ID / ONELAKE_LAKEHOUSE_ID)"
            ),
        )
    return ws, lh


def _onelake_file_url(file_path: str) -> tuple[str, str]:
    ws, lh = _onelake_base_path()
    base = "https://onelake.dfs.fabric.microsoft.com"
    fp = file_path.lstrip("/").replace("\\", "/")
    if fp.lower().startswith("files/"):
        fp = fp[6:]
    return f"{base}/{ws}/{lh}/Files/{fp}", fp


def _onelake_delete_if_exists(url: str, headers: dict) -> None:
    r = requests.delete(url, headers=headers, timeout=60)
    if r.status_code in (200, 202, 404):
        return
    logger.warning(
        "OneLake delete before upload: HTTP %s — %s", r.status_code, (r.text or "")[:200]
    )


def _bangkok_date_yyyymmdd() -> str:
    return datetime.now(ZoneInfo("Asia/Bangkok")).strftime("%Y%m%d")


def _format_datetime_bangkok_be(dt: datetime) -> str:
    """รูปแบบ d/M/yyyy HH:mm:ss ปี พ.ศ. แบบ 24 ชม. (ไม่มี AM/PM)"""
    return (
        f"{dt.day}/{dt.month}/{dt.year + 543} "
        f"{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}"
    )


def _format_updatedate_bangkok_be() -> str:
    return _format_datetime_bangkok_be(datetime.now(ZoneInfo("Asia/Bangkok")))


def _format_effectivedate_bangkok_be(target_year: int, target_month: int) -> str:
    """วันแรกของเดือนเป้า เวลา 00:00:00 (ปฏิทิน พ.ศ.)"""
    dt = datetime(
        int(target_year),
        int(target_month),
        1,
        0,
        0,
        0,
        tzinfo=ZoneInfo("Asia/Bangkok"),
    )
    return _format_datetime_bangkok_be(dt)


def _cell_str(val) -> str:
    if val is None:
        return ""
    try:
        if pd.isna(val):
            return ""
    except (TypeError, ValueError):
        pass
    return str(val).strip()


def _areacode_str(val) -> str:
    """
    ค่า AREACODE ใน semantic model — รวม **0** — ต้องส่งออกตามนั้น เพื่อ import กลับ TGA
    เดิมเคยตัด 0 ออกเป็น "" (ถือว่าว่าง) ทำให้ดูเหมือนโมเดลไม่มีรหัสพื้นที่แม้ฐานข้อมูลเป็น 0
    """
    if val is None:
        return ""
    try:
        if pd.isna(val):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        if float(val) == int(val):
            return str(int(val))
        return str(val).strip()
    s = str(val).strip()
    if not s or s.lower() in ("nan", "none"):
        return ""
    low = s.lower()
    if low in ("0", "0.0", "-0", "-0.0"):
        return "0"
    return s


def _resolve_user_code(req: LakehouseUploadRequest) -> str:
    """
    รหัสผู้บันทึก: ส่งจาก frontend (manager หรือ supervisor ที่ล็อกอิน)
    สำรองเป็น sup_id ของทีมที่กำลังเกลี่ย
    """
    if req.upload_user_code and str(req.upload_user_code).strip():
        return str(req.upload_user_code).strip().upper()
    return str(req.sup_id or "").strip().upper()


def _coalesce_col(df: pd.DataFrame, col: str, fallback: pd.Series | None = None) -> pd.Series:
    base = df[col].map(_cell_str) if col in df.columns else pd.Series([""] * len(df), index=df.index)
    if fallback is not None:
        return base.where(base.ne(""), fallback.map(_cell_str))
    return base


def _integer_split_by_weights(weights: list[float], total: int) -> list[int]:
    """หีบแบ่งจำนวนเต็มผลรวม total ตาม weights (เกลี่ยเศษตาม leftover มากที่สุด)"""
    total = max(0, int(round(total)))
    n = len(weights)
    if n == 0:
        return []
    if total == 0:
        return [0] * n
    w = [max(0.0, float(x)) for x in weights]
    s = sum(w)
    if s <= 0:
        w = [1.0] * n
        s = float(n)
    raw = [total * (wi / s) for wi in w]
    base = [int(x) for x in raw]
    rem = total - sum(base)
    frac = sorted(range(n), key=lambda i: -(raw[i] - base[i]))
    for j in range(rem):
        base[frac[j % n]] += 1
    return base


def norm_emp_code(code) -> str:
    """
    รูปมาตรฐานของรหัสพนักงานสำหรับ "จับคู่" ทั้งสองฝั่ง (grain ↔ ผลกระจาย)

    ต้องใช้กติกาเดียวกับ targetsun_read._normalize_salesman_code ไม่งั้นแถวที่
    เขียนตัวพิมพ์ต่างกันหรือรหัสตัวเลขที่เติมศูนย์ไม่เท่ากันจะจับคู่ไม่ติด
    ผลคือ SKU นั้นถูกตัดทั้งตัวตามนโยบาย S3.5 ทั้งที่ข้อมูลไม่ได้ผิดอะไร

    ข้อมูลจริงตอนนี้เป็นรหัสผสมตัวอักษรทั้งหมด (เช่น B320) เงื่อนไข zfill จึงยัง
    ไม่เคยทำงาน — ใส่ไว้กันไว้ก่อนให้ตรงกับฝั่งอ่าน Target Sun
    """
    s = str(code or "").strip().upper()
    return s.zfill(5) if s.isdigit() else s


def _normalize_grain_dtype(df_grain: pd.DataFrame) -> pd.DataFrame:
    g = df_grain.copy()
    if "emp_id" in g.columns:
        g["emp_id"] = g["emp_id"].map(norm_emp_code)
    for c in ("sku",):
        if c in g.columns:
            g[c] = g[c].astype(str).str.strip()
    for c in ("salestype", "divisioncode", "areacode", "provincecode", "warehouse_code"):
        if c not in g.columns:
            g[c] = ""
        else:
            g[c] = g[c].map(_cell_str)
    if "qty" not in g.columns:
        g["qty"] = 0.0
    g["qty"] = pd.to_numeric(g["qty"], errors="coerce").fillna(0.0)
    return g


def _read_tga_grain_cache(
    sup_id: str,
    target_month: int,
    target_year: int,
    emp_list: list[str] | None = None,
) -> pd.DataFrame:
    """อ่าน TGA grain จาก cache ขั้นที่ 1 (ไม่เรียก Fabric)"""
    p = tga_grain_cache_path(sup_id, target_month, target_year)
    if not os.path.exists(p):
        return pd.DataFrame()
    try:
        dg = pd.read_csv(p, dtype=str, keep_default_na=False)
        dg = _normalize_grain_dtype(dg)
        if emp_list:
            # ทั้งสองฝั่งต้องผ่านตัวเดียวกัน ไม่งั้นกรองทิ้งเพราะรูปรหัสต่างกันเฉย ๆ
            emps = {norm_emp_code(e) for e in emp_list}
            dg = dg[dg["emp_id"].isin(emps)]
        return dg
    except Exception as e:
        logger.warning("read tga grain cache: %s", e)
        return pd.DataFrame()


def _dim_key_series(g: pd.DataFrame) -> list[pd.Series]:
    """ค่า dim ที่ normalize แล้วเหมือนตอนเขียนลงไฟล์ — ใช้เทียบคีย์ upsert"""
    return [
        g["salestype"].map(_cell_str),
        g["divisioncode"].map(_cell_str),
        g["areacode"].map(_areacode_str),
        g["provincecode"].map(_cell_str),
    ]


def _collapse_grain_duplicate_keys(grp: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """
    ยุบแถว grain ของคู่ (พนักงาน×สินค้า) ที่ dim ตรงกันให้เหลือแถวเดียว แล้วรวม qty

    คีย์ upsert ของ Target Sun คือ PRODUCTCODE+SALESTYPE+DIVISIONCODE+SALESMANCODE
    +AREACODE+PROVINCECODE — ไม่มี WAREHOUSECODE อยู่ในคีย์ สองแถวที่ต่างกันแค่คลัง
    จึงเป็น "แถวเดียวกัน" สำหรับ Oracle ถ้าไม่ยุบตรงนี้ หีบจะถูกแบ่งลงทั้งสองแถว
    แล้วตัวนำเข้าจะข้ามแถวหลังทิ้ง — หีบหายโดยระบบยังรายงานว่าส่งสำเร็จ
    (พบจริงในแคช: 13/78 ไฟล์ หนักสุดหายถึง 35% ของยอดทีมนั้น)

    คลังที่เก็บไว้เป็นของแถวที่ qty มากสุด เพราะ WAREHOUSECODE ถูกใช้ตอน insert เท่านั้น
    """
    n = len(grp)
    if n < 2:
        return grp, 0
    g = grp.copy()
    kcols = ["_k_st", "_k_div", "_k_area", "_k_prov"]
    g["_k_st"], g["_k_div"], g["_k_area"], g["_k_prov"] = _dim_key_series(g)
    if not g.duplicated(subset=kcols).any():
        return grp, 0
    g = g.sort_values("qty", ascending=False, kind="stable")
    agg: dict[str, str] = {c: "first" for c in g.columns if c not in kcols and c != "qty"}
    agg["qty"] = "sum"
    merged = g.groupby(kcols, sort=False, as_index=False).agg(agg)
    return merged[list(grp.columns)], n - len(merged)


def emp_dims_from_own_grain(dg: pd.DataFrame) -> dict[str, dict[str, str]]:
    """
    dim ประจำตัวพนักงาน อนุมานจากแถว grain ของ "คนคนนั้นเอง" ในสินค้าตัวอื่น

    SALESTYPE / DIVISIONCODE / AREACODE / PROVINCECODE เป็นคุณสมบัติของพนักงาน
    ไม่ได้ผูกกับสินค้า คนที่มีเป้าสินค้าอื่นอยู่แล้วจึงบอกได้ว่าเขาอยู่เขตไหน

    **อนุมานเฉพาะเมื่อทุกแถวของคนนั้นตรงกันหมด** ถ้าขัดกันเอง (เช่นขายหลายเขต)
    จะไม่เดา — ปล่อยให้ SKU นั้นถูกตัดตามนโยบายเดิมดีกว่าสร้างแถวผิดเขตใน Oracle

    คอลัมน์ที่ว่างทุกแถวถือว่า "ไม่มีค่า" ไม่ใช่ "ขัดกัน" — PROVINCECODE ว่างเป็นเรื่อง
    ปกติและไม่ได้อยู่ในคีย์ upsert · ส่วน SALESTYPE / DIVISIONCODE / AREACODE ต้องมีครบ
    ไม่งั้นเดาไปก็ส่งไม่ได้อยู่ดี

    ใช้ตอนกระจายรวมทั้งหน่วย: พนักงานทีมอื่นที่ไม่เคยมีเป้าสินค้าตัวนี้จะได้แถวใหม่
    (Target Sun รองรับ insert — ดู targetsun-importTargetSalesmanNextFromExcel.md)
    """
    if dg is None or dg.empty or "emp_id" not in dg.columns:
        return {}
    cols = ["salestype", "divisioncode", "areacode", "provincecode"]
    out: dict[str, dict[str, str]] = {}
    for emp, grp in dg.groupby("emp_id", sort=False):
        emp_key = str(emp).strip()
        if not emp_key:
            continue
        dims: dict[str, str] = {}
        conflicted = False
        for c in cols:
            vals = {
                _areacode_str(v) if c == "areacode" else _cell_str(v)
                for v in grp.get(c, pd.Series(dtype=str))
            }
            vals.discard("")
            # ว่างทุกแถว = "ไม่มีค่า" ไม่ใช่ "ขัดกัน" — ของเดิมนับเป็นขัดกันแล้วทิ้ง
            # พนักงานทั้งคน ทั้งที่ PROVINCECODE ว่างเป็นเรื่องปกติและไม่ได้อยู่ใน
            # เงื่อนไขบังคับของการส่งด้วยซ้ำ
            if len(vals) > 1:
                conflicted = True
                break
            dims[c] = next(iter(vals)) if vals else ""
        if conflicted:
            continue
        # สามตัวนี้เป็นคีย์ upsert ของ Target Sun — ขาดตัวใดตัวหนึ่งก็ส่งไม่ได้อยู่ดี
        # (ดู _import_key_mask) เดาไปก็ไม่มีประโยชน์
        if not all(dims.get(k) for k in ("salestype", "divisioncode", "areacode")):
            continue
        out[emp_key] = dims
    return out


def _read_tga_grain_across_teams(
    month: int, year: int, emp_ids: set[str] | list[str]
) -> pd.DataFrame:
    """
    grain ของพนักงานชุดที่ระบุ จากไฟล์ของ "ทุกทีม" ในงวดนั้น

    grain ถูกเก็บแยกไฟล์ต่อทีม (data/tga_lines_{SL}_{Y}_{MM}.csv) เพราะสร้างตอน
    โหลดข้อมูลขั้นที่ 1 ของแต่ละทีม · แต่ผลกระจายรวมภาค/รวมหน่วยมีพนักงานของหลายทีม
    อยู่ในคำขอเดียว ถ้าอ่านแต่ไฟล์ของทีมเจ้าของ พนักงานทีมอื่นจะไม่มี SALESTYPE /
    DIVISIONCODE / AREACODE ให้ใช้เลย แล้วแถวของเขาถูกตัดทิ้งทั้งหมด

    dim พวกนี้เป็นคุณสมบัติของ "พนักงาน" ไม่ได้ผูกกับว่าใครเป็นหัวหน้า การหยิบจาก
    ไฟล์ทีมไหนจึงให้ผลเดียวกัน · กรองเฉพาะรหัสที่อยู่ในคำขอ ไม่ลากทั้งบริษัทมา
    """
    want = {norm_emp_code(e) for e in (emp_ids or []) if str(e).strip()}
    if not want:
        return pd.DataFrame()
    prefix = "tga_lines_"
    suffix = f"_{int(year):04d}_{int(month):02d}.csv"
    frames: list[pd.DataFrame] = []
    try:
        names = os.listdir("data")
    except OSError:
        return pd.DataFrame()
    for name in names:
        if not (name.startswith(prefix) and name.endswith(suffix)):
            continue
        path = os.path.join("data", name)
        try:
            dg = pd.read_csv(path, dtype=str, keep_default_na=False)
        except Exception as e:
            logger.warning("อ่าน grain ข้ามทีมไม่ได้ %s: %s", name, e)
            continue
        if dg.empty or "emp_id" not in dg.columns:
            continue
        dg = _normalize_grain_dtype(dg)
        dg = dg[dg["emp_id"].isin(want)]
        if dg.empty:
            continue
        try:
            dg = dg.assign(_src_mtime=os.path.getmtime(path))
        except OSError:
            dg = dg.assign(_src_mtime=0.0)
        frames.append(dg)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)

    # พนักงานย้ายซุปกลางงวดได้ (ในหน่วยเดียวกันก็ย้ายกันบ่อย) แล้วโผล่ทั้งไฟล์ทีมเก่า
    # และทีมใหม่ · ถ้าเอามารวมกันดื้อ ๆ คนนั้นจะได้แถวเป้าสองแถว (เขตเก่า + เขตใหม่)
    # แล้วหีบถูกแบ่งครึ่งไปลงทั้งคู่ — ครึ่งหนึ่งไปเขียนทับแถวของเขตที่เขาย้ายออกมาแล้ว
    # ยอดรวมยังครบ ด่านไหนจึงไม่จับ
    #
    # ใช้ไฟล์ที่ "ใหม่ที่สุด" ของคนนั้นชุดเดียว — ไฟล์ถูกเขียนตอนโหลดข้อมูลขั้นที่ 1
    # ของแต่ละทีม ไฟล์ล่าสุดจึงสะท้อนว่าตอนนี้เขาอยู่ทีมไหน (วิธีเดียวกับที่ใช้ตัดสิน
    # ราคาที่ขัดกันข้ามทีมใน employees._newest_price)
    newest = out.groupby("emp_id")["_src_mtime"].transform("max")
    picked = out[out["_src_mtime"] == newest].drop(columns=["_src_mtime"])
    dropped_stale = len(out) - len(picked)
    if dropped_stale:
        logger.info(
            "grain ข้ามทีม: ข้าม %d แถวจากไฟล์ทีมเก่า (พนักงานย้ายทีมกลางงวด)",
            dropped_stale,
        )
    return picked.drop_duplicates().reset_index(drop=True)


def _grain_by_pair(dg: pd.DataFrame) -> dict[tuple[str, str], pd.DataFrame]:
    if dg.empty:
        return {}
    out: dict[tuple[str, str], pd.DataFrame] = {}
    collapsed = 0
    pairs = 0
    for k, grp in dg.groupby(["emp_id", "sku"], sort=False):
        merged, removed = _collapse_grain_duplicate_keys(grp)
        if removed:
            collapsed += removed
            pairs += 1
        out[(str(k[0]).strip(), str(k[1]).strip())] = merged
    if collapsed:
        logger.warning(
            "TGA grain: ยุบแถวคีย์ซ้ำ %d แถว จาก %d คู่พนักงาน×สินค้า "
            "(ต่างกันแค่ WAREHOUSECODE ซึ่งไม่อยู่ในคีย์ upsert ของ Target Sun)",
            collapsed,
            pairs,
        )
    return out


def _import_key_mask(df: pd.DataFrame) -> pd.Series:
    """แถวที่มี SALESTYPE + DIVISION + AREACODE ครบ (vectorized)"""
    st = df.get("salestype", pd.Series([""] * len(df), index=df.index)).map(_cell_str)
    div = df.get("divisioncode", pd.Series([""] * len(df), index=df.index)).map(_cell_str)
    area = df.get("areacode", pd.Series([""] * len(df), index=df.index)).map(_areacode_str)
    return st.ne("") & div.ne("") & area.ne("")


def _merge_duplicate_import_keys(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """
    ตาข่ายสุดท้ายก่อนเขียนไฟล์ — รวมแถวที่คีย์ upsert ซ้ำกันให้เหลือแถวเดียว

    ต้อง "บวก" จำนวนหีบเสมอ ห้ามทิ้งแถว เพราะยอดรวมที่ส่งต้องไม่เปลี่ยน
    แถวที่ dim ยังไม่ครบจะไม่ถูกรวม (ยังไม่ใช่คีย์จริง และเดี๋ยวถูกคัดออกอยู่แล้ว)
    ตัวยุบต้นทางอยู่ที่ _collapse_grain_duplicate_keys — ตรงนี้กันแถวซ้ำที่มาจาก
    ทางอื่น เช่น การเติมแถวศูนย์หรือการเติม dim จาก Fabric
    """
    if df.empty:
        return df, 0
    d = df.copy().reset_index(drop=True)
    d["_ord"] = range(len(d))
    kcols = ["_k_sku", "_k_emp", "_k_st", "_k_div", "_k_area", "_k_prov"]
    d["_k_sku"] = d["sku"].astype(str).str.strip()
    d["_k_emp"] = d["emp_id"].astype(str).str.strip()
    d["_k_st"], d["_k_div"], d["_k_area"], d["_k_prov"] = _dim_key_series(d)

    mask = _import_key_mask(d)
    part = d[mask]
    if len(part) < 2 or not part.duplicated(subset=kcols).any():
        return df, 0

    rest = d[~mask]
    part = part.sort_values("allocated_boxes", ascending=False, kind="stable")
    agg: dict[str, str] = {
        c: "first" for c in d.columns if c not in kcols and c not in ("allocated_boxes", "_ord")
    }
    agg["allocated_boxes"] = "sum"
    agg["_ord"] = "min"
    merged = part.groupby(kcols, sort=False, as_index=False).agg(agg)
    out = pd.concat([merged, rest], ignore_index=True, sort=False)
    out = out.sort_values("_ord", kind="stable").reset_index(drop=True)
    return out[list(df.columns)], len(d) - len(out)


def _boxes_by_sku(df: pd.DataFrame) -> dict[str, int]:
    """ยอดหีบรวมต่อ SKU — ใช้เทียบว่าท่อแปลงข้อมูลไม่ได้ทำยอดหายหรืองอก"""
    if df is None or df.empty:
        return {}
    boxes = pd.to_numeric(df["allocated_boxes"], errors="coerce").fillna(0).astype(int)
    return {
        str(k): int(v)
        for k, v in boxes.groupby(df["sku"].astype(str).str.strip()).sum().items()
    }


def _assert_file_preserves_payload_totals(
    df_final: pd.DataFrame,
    payload_by_sku: dict[str, int],
    *,
    sup_id: str,
    exempt_skus: set[str],
) -> None:
    """
    ยอดหีบต่อ SKU ใน "ไฟล์ที่จะอัปโหลดจริง" ต้องเท่ากับ payload ที่ผ่านประตูแรกมาแล้ว

    ประตูแรกตรวจตั้งแต่ก่อนแตกแถวตาม TGA grain / เติมแถวศูนย์ / ยุบคีย์ซ้ำ / ตัดแถว
    ตัวเลขในไฟล์สุดท้ายจึงไม่เคยถูกตรวจซ้ำเลย — นี่คือด่านที่ตรวจ "ของจริงที่จะส่ง"

    ข้ามไม่ได้ ไม่มี flag ยืนยัน โดยตั้งใจ เพราะส่วนต่างตรงนี้ไม่ใช่การตัดสินใจของผู้ใช้
    แต่แปลว่าขั้นแปลงข้อมูลทำหีบหายหรืองอกเอง (เช่นเคสคีย์ upsert ซ้ำ)
    SKU ที่ถูกตัดเพราะไม่มีแถวใน Target Sun ถูกยกเว้นตรงนี้ — ประตูที่สองรายงานแยก
    """
    file_by_sku = _boxes_by_sku(df_final)
    diffs: list[dict] = []
    for sku in sorted(set(payload_by_sku) | set(file_by_sku)):
        if sku in exempt_skus:
            continue
        want = int(payload_by_sku.get(sku, 0))
        got = int(file_by_sku.get(sku, 0))
        if got != want:
            diffs.append({"sku": sku, "payload_boxes": want, "file_boxes": got, "diff": got - want})
    if not diffs:
        return

    diff_boxes = sum(int(d["diff"]) for d in diffs)
    logger.error(
        "ไฟล์ที่จะส่งยอดไม่ตรงกับผลกระจาย %s: %d SKU ต่างรวม %+d หีบ — %s",
        str(sup_id or "").strip().upper(),
        len(diffs),
        diff_boxes,
        diffs[:5],
    )
    raise HTTPException(
        status_code=409,
        detail={
            "code": "send_file_total_changed",
            "message": (
                f"ยังไม่ได้ส่ง — ไฟล์ที่จะอัปโหลดมียอดไม่ตรงกับผลกระจายหีบ "
                f"{len(diffs)} SKU (ต่างรวม {diff_boxes:+,} หีบ)"
            ),
            "hint_th": (
                "เป็นข้อผิดพลาดฝั่งระบบ ไม่ใช่การแก้ตัวเลขของผู้ใช้ — "
                "กรุณาแจ้ง IT พร้อมรหัสทีมและงวดนี้ อย่าเพิ่งส่งซ้ำ"
            ),
            "diffs": diffs[:20],
            "diff_count": len(diffs),
            "diff_boxes": diff_boxes,
        },
    )


def _needs_fabric_enrichment(df: pd.DataFrame) -> bool:
    """แถวใดขาด SALESTYPE/DIVISION/AREACODE ต้องดึง Fabric — มิฉะนั้นข้าม DAX ได้"""
    if df.empty:
        return False
    st = df.get("salestype", pd.Series([""] * len(df), index=df.index)).map(_cell_str)
    div = df.get("divisioncode", pd.Series([""] * len(df), index=df.index)).map(_cell_str)
    area = df.get("areacode", pd.Series([""] * len(df), index=df.index)).map(_areacode_str)
    return bool((st.eq("") | div.eq("") | area.eq("")).any())


def _apply_wh_hints(df: pd.DataFrame, rows_raw: list[dict]) -> pd.DataFrame:
    wh_hint: dict[str, str] = {}
    for r in rows_raw:
        emp = str(r.get("emp_id") or "").strip()
        wh = _cell_str(r.get("warehouse_code"))
        if emp and wh:
            wh_hint[emp] = wh
    if "warehouse_code" not in df.columns:
        df = df.copy()
        df["warehouse_code"] = ""
    else:
        df = df.copy()
    if wh_hint:
        existing = df["warehouse_code"].map(_cell_str)
        fb = df["emp_id"].astype(str).str.strip().map(lambda e: wh_hint.get(e, ""))
        df["warehouse_code"] = existing.where(existing.ne(""), fb.map(_cell_str))
    df["warehouse_code"] = df["warehouse_code"].map(_cell_str)
    if "areacode" in df.columns:
        df["areacode"] = df["areacode"].map(_areacode_str)
    return df


def _expand_allocations_with_tga_grain(
    df_alloc: pd.DataFrame,
    sup_id: str,
    target_month: int,
    target_year: int,
    *,
    dg: pd.DataFrame | None = None,
    grain_lookup: dict[tuple[str, str], pd.DataFrame] | None = None,
    infer_missing_dims: bool = False,
) -> tuple[pd.DataFrame, bool]:
    """
    จากแถว (emp×sku × allocated_boxes) แตกเป็นหลายแถวตาม grain cache จาก tga_target_salesman_next
    ให้ SALESTYPE / DIVISIONCODE / AREACODE / PROVINCECODE / WAREHOUSECODE ตรงกับบรรทัดเป้า
    และรักษายอด QUANTITYCASE รวมต่อ emp×sku

    infer_missing_dims — คู่ที่ไม่มีใน Target Sun ให้เติม dim จากแถวอื่นของพนักงานคนเดียวกัน
    เพื่อสร้างเป้าใหม่ได้ (ใช้ตอนกระจายรวมทั้งหน่วย ที่คนทีมอื่นยังไม่เคยมีเป้าสินค้านั้น)
    """
    if dg is None:
        dg = _read_tga_grain_cache(sup_id, target_month, target_year)
    if dg.empty:
        return df_alloc, False
    if grain_lookup is None:
        grain_lookup = _grain_by_pair(dg)
    emp_dims = emp_dims_from_own_grain(dg) if infer_missing_dims else {}

    out: list[dict] = []

    # แปลง DataFrame iterate — เก็บ warehouse จากคำขอเป็น hint
    for _, arow in df_alloc.iterrows():
        e = str(arow["emp_id"]).strip()
        sku = str(arow["sku"]).strip()
        boxes_val = pd.to_numeric(arow.get("allocated_boxes", 0), errors="coerce")
        boxes = 0 if pd.isna(boxes_val) else int(round(float(boxes_val)))
        wh_req = ""
        raw_wh = arow.get("warehouse_code")
        if raw_wh is not None and str(raw_wh).strip():
            wh_req = str(raw_wh).strip()

        sub = grain_lookup.get((e, sku), pd.DataFrame())

        # ไม่พบใน cache → เก็บบรรทัดเดิมให้ชั้นถัดไปเติม dim จาก Fabric
        # (หรือเติมจากแถวอื่นของพนักงานคนเดียวกัน เมื่อเปิด infer_missing_dims)
        if sub.empty:
            inferred = emp_dims.get(e) if emp_dims else None
            out.append(
                {
                    "emp_id": e,
                    "sku": sku,
                    "allocated_boxes": boxes,
                    "salestype": inferred["salestype"] if inferred else "",
                    "divisioncode": inferred["divisioncode"] if inferred else "",
                    "areacode": inferred["areacode"] if inferred else "",
                    "provincecode": inferred["provincecode"] if inferred else "",
                    "warehouse_code": wh_req,
                    "dims_inferred": bool(inferred),
                }
            )
            continue

        sub_pos = sub[sub["qty"] > 0]
        dims_only = sub[sub["qty"] <= 0]

        if not sub_pos.empty:
            wvals = sub_pos["qty"].astype(float).tolist()
            split = _integer_split_by_weights(wvals, boxes)
            for (_, r), b in zip(sub_pos.iterrows(), split):
                wh = (
                    _cell_str(r.get("warehouse_code", ""))
                    or wh_req
                    or ""
                )
                out.append(
                    {
                        "emp_id": e,
                        "sku": sku,
                        "allocated_boxes": int(b),
                        "salestype": _cell_str(r.get("salestype", "")),
                        "divisioncode": _cell_str(r.get("divisioncode", "")),
                        "areacode": _areacode_str(r.get("areacode", "")),
                        "provincecode": _cell_str(r.get("provincecode", "")),
                        "warehouse_code": wh,
                    }
                )

            # แถว TGA เดิมที่ qty = 0: เขียน QUANTITYCASE = 0 เพื่อให้ครบ dim ตอนนำเข้ากลับ
            if not dims_only.empty:
                for _, r in dims_only.iterrows():
                    wh = (
                        _cell_str(r.get("warehouse_code", ""))
                        or wh_req
                        or ""
                    )
                    out.append(
                        {
                            "emp_id": e,
                            "sku": sku,
                            "allocated_boxes": 0,
                            "salestype": _cell_str(r.get("salestype", "")),
                            "divisioncode": _cell_str(r.get("divisioncode", "")),
                            "areacode": _areacode_str(r.get("areacode", "")),
                            "provincecode": _cell_str(r.get("provincecode", "")),
                            "warehouse_code": wh,
                        }
                    )
        else:
            # เฉพาะแถวเป้ารวมเป็น 0 ใน TGA → เกลี่ยหีบเท่า ๆ กันบนทุกความเป็นไปได้ของ dim
            wvals = [1.0] * len(sub)
            split = _integer_split_by_weights(wvals, boxes)
            for (_, r), b in zip(sub.iterrows(), split):
                wh = (
                    _cell_str(r.get("warehouse_code", ""))
                    or wh_req
                    or ""
                )
                out.append(
                    {
                        "emp_id": e,
                        "sku": sku,
                        "allocated_boxes": int(b),
                        "salestype": _cell_str(r.get("salestype", "")),
                        "divisioncode": _cell_str(r.get("divisioncode", "")),
                        "areacode": _areacode_str(r.get("areacode", "")),
                        "provincecode": _cell_str(r.get("provincecode", "")),
                        "warehouse_code": wh,
                    }
                )

    return pd.DataFrame(out), True


def _normalize_allocation_payload(df: pd.DataFrame) -> pd.DataFrame:
    """
    ใช้เฉพาะแถวจาก payload (ผลขั้นกระจายหีบ) — ไม่ขยายพนักงาน/สินค้าทั้งทีม
    (เช่น SL359 จะไม่ส่งคนที่ไม่มีเป้าแต่แรกและไม่ได้อยู่ในผลลัพธ์ขั้นที่ 3)
    """
    if df.empty:
        return df
    if "warehouse_code" not in df.columns:
        df = df.copy()
        df["warehouse_code"] = ""
    df = df.copy()
    df["warehouse_code"] = df["warehouse_code"].fillna("").astype(str).str.strip()
    g = (
        df.groupby(["emp_id", "sku", "warehouse_code"], as_index=False)
        .agg(allocated_boxes=("allocated_boxes", "sum"))
        .reset_index(drop=True)
    )
    g["allocated_boxes"] = g["allocated_boxes"].astype(int)
    logger.info(
        "lakehouse payload (step 3 only): %d rows (zeros=%d)",
        len(g),
        int((g["allocated_boxes"] == 0).sum()),
    )
    return g


def _zero_sum_emp_sku_pairs(df: pd.DataFrame) -> set[tuple[str, str]]:
    if df.empty:
        return set()
    gsum = df.groupby(["emp_id", "sku"], as_index=False)["allocated_boxes"].sum()
    return {
        (str(r.emp_id).strip(), str(r.sku).strip())
        for _, r in gsum.iterrows()
        if int(r.allocated_boxes) == 0
    }


def _load_tga_grain_frame(
    sup_id: str,
    target_month: int,
    target_year: int,
    emp_list: list[str],
    *,
    dg: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """อ่าน grain จาก cache ขั้น Step 1; ถ้าไม่พอค่อยดึง Fabric สด"""
    if dg is not None and not dg.empty:
        if emp_list:
            emps = {str(e).strip() for e in emp_list}
            part = dg[dg["emp_id"].isin(emps)]
            if not part.empty:
                return part
        else:
            return dg
    part = _read_tga_grain_cache(sup_id, target_month, target_year, emp_list)
    if not part.empty:
        return part
    try:
        fabric = FabricDAXConnector()
        return fabric.get_tga_target_salesman_granular(emp_list, target_month, target_year)
    except Exception as e:
        logger.warning("fabric granular for zero align: %s", e)
        return pd.DataFrame()


def _align_zero_allocations_to_tga_grain(
    df: pd.DataFrame,
    sup_id: str,
    target_month: int,
    target_year: int,
    *,
    dg: pd.DataFrame | None = None,
    grain_lookup: dict[tuple[str, str], pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """
    คู่ emp×sku ที่หีบรวม = 0 ต้องส่งแถวที่ key ตรง Oracle (SALESTYPE+DIVISION+AREA+PROVINCE…)
    มิฉะนั้น import จะ insert แถวใหม่ qty=0 แต่แถวเดิม qty=1 ยังค้าง
    """
    zero_pairs = _zero_sum_emp_sku_pairs(df)
    if not zero_pairs:
        return df

    emp_list = sorted({e for e, _ in zero_pairs})
    dg = _load_tga_grain_frame(
        sup_id, target_month, target_year, emp_list, dg=dg
    )
    if dg.empty:
        logger.warning(
            "zero allocations: ไม่มี TGA grain สำหรับ %d คู่ emp×sku — "
            "อาจอัปเดต Oracle ไม่ครบ (โหลด Step 1 ใหม่ก่อนส่ง)",
            len(zero_pairs),
        )
        return df

    if grain_lookup is None:
        grain_lookup = _grain_by_pair(dg)
    pair_tuples = list(zip(df["emp_id"].astype(str).str.strip(), df["sku"].astype(str).str.strip()))
    mask_keep = [p not in zero_pairs for p in pair_tuples]
    kept = df[mask_keep].copy()
    zero_rows: list[dict] = []
    missing_grain: list[tuple[str, str]] = []

    for e, sku in sorted(zero_pairs):
        sub = grain_lookup.get((e, sku), pd.DataFrame())
        hint = df[(df["emp_id"] == e) & (df["sku"] == sku)]
        wh_hint = _cell_str(hint.iloc[0].get("warehouse_code", "")) if not hint.empty else ""
        if sub.empty:
            missing_grain.append((e, sku))
            if not hint.empty:
                zero_rows.extend(hint.to_dict(orient="records"))
            continue
        for _, r in sub.iterrows():
            zero_rows.append(
                {
                    "emp_id": e,
                    "sku": sku,
                    "allocated_boxes": 0,
                    "salestype": _cell_str(r.get("salestype", "")),
                    "divisioncode": _cell_str(r.get("divisioncode", "")),
                    "areacode": _areacode_str(r.get("areacode", "")),
                    "provincecode": _cell_str(r.get("provincecode", "")),
                    "warehouse_code": _cell_str(r.get("warehouse_code", "")) or wh_hint,
                }
            )

    if missing_grain:
        logger.warning(
            "zero allocations without TGA grain rows: %s",
            missing_grain[:10],
        )

    if not zero_rows:
        return df
    return pd.concat([kept, pd.DataFrame(zero_rows)], ignore_index=True)


def _ensure_zero_pairs_have_rows(
    df: pd.DataFrame,
    zero_pairs: set[tuple[str, str]],
    sup_id: str,
    target_month: int,
    target_year: int,
    *,
    dg: pd.DataFrame | None = None,
    grain_lookup: dict[tuple[str, str], pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """เติมคู่หีบ=0 ที่หลุด — เฉพาะที่มี grain ใน TGA (มี SALESTYPE/DIVISION/AREA)"""
    if not zero_pairs:
        return df
    present = set(
        zip(
            df["emp_id"].astype(str).str.strip(),
            df["sku"].astype(str).str.strip(),
        )
    )
    emp_subset = sorted({e for e, _ in zero_pairs})
    dg = _load_tga_grain_frame(
        sup_id, target_month, target_year, emp_subset, dg=dg
    )
    if dg.empty:
        return df
    if grain_lookup is None:
        grain_lookup = _grain_by_pair(dg)
    extra: list[dict] = []
    skipped_no_grain = 0
    for e, sku in sorted(zero_pairs):
        if (e, sku) in present:
            continue
        sub = grain_lookup.get((e, sku), pd.DataFrame())
        if sub.empty:
            skipped_no_grain += 1
            continue
        for _, r in sub.iterrows():
            extra.append(
                {
                    "emp_id": e,
                    "sku": sku,
                    "allocated_boxes": 0,
                    "salestype": _cell_str(r.get("salestype", "")),
                    "divisioncode": _cell_str(r.get("divisioncode", "")),
                    "areacode": _areacode_str(r.get("areacode", "")),
                    "provincecode": _cell_str(r.get("provincecode", "")),
                    "warehouse_code": _cell_str(r.get("warehouse_code", "")),
                }
            )
    if skipped_no_grain:
        logger.warning(
            "zero pairs without TGA grain (not sent): %d — reload Step 1 if needed",
            skipped_no_grain,
        )
    if not extra:
        return df
    logger.info("added %d zero rows from TGA grain", len(extra))
    return pd.concat([df, pd.DataFrame(extra)], ignore_index=True)


def _preview_not_in_targetsun(df: pd.DataFrame, limit: int = 80) -> list[dict]:
    """คู่พนักงาน×สินค้าที่ไม่มี SALESTYPE/DIVISION/AREACODE จากเป้า TGA ณ ตอนส่ง"""
    if df.empty:
        return []
    bad = df[~_import_key_mask(df)]
    out: list[dict] = []
    for _, r in bad.iterrows():
        out.append(
            {
                "emp_id": str(r["emp_id"]).strip(),
                "sku": str(r["sku"]).strip(),
                "allocated_boxes": int(pd.to_numeric(r.get("allocated_boxes", 0), errors="coerce") or 0),
            }
        )
        if len(out) >= limit:
            break
    return out


def _drop_rows_missing_tga_import_key(
    df: pd.DataFrame,
) -> tuple[pd.DataFrame, int, list[dict]]:
    """
    ตัดแถวที่ไม่มี grain จากเป้า TGA (SALESTYPE / DIVISION / AREACODE) — ไม่ส่งเข้า Target Sun
    ไม่เติมค่า dim เอง — ใช้เฉพาะข้อมูลจาก tga_target_salesman_next (cache ขั้นที่ 1 / Fabric)
    """
    if df.empty:
        return df, 0, []

    mask = _import_key_mask(df)
    dropped = int((~mask).sum())
    preview = _preview_not_in_targetsun(df)

    if dropped:
        logger.warning(
            "not in Target Sun now: %d rows (no SALESTYPE/DIVISION/AREACODE from TGA grain)",
            dropped,
        )

    kept = df[mask].copy()
    zero_kept = int((kept["allocated_boxes"].fillna(0).astype(int) == 0).sum()) if not kept.empty else 0
    logger.info("lakehouse TGA rows: kept=%d zero_qty=%d not_in_targetsun=%d", len(kept), zero_kept, dropped)
    return kept, dropped, preview


def _shortfall_from_dropped_rows(
    df: pd.DataFrame,
    sup_id: str,
    month: int,
    year: int,
    *,
    max_skus: int = 50,
    max_pairs: int = 300,
) -> list[dict]:
    """
    หีบที่จะหายไปจริง เพราะแถวถูกตัดที่ _drop_rows_missing_tga_import_key
    เรียกด้วย df **ก่อน** drop

    ทำไมไม่ย้าย _assert_send_matches_sup_targets มาตรวจหลัง drop แทน:
      1. ฟังก์ชันนั้นวนจาก df.groupby("sku") คือ "SKU ที่ยังเหลือ" — ถ้า SKU ถูกตัดทั้งตัว
         (สินค้าใหม่ที่ TGA ยังไม่ตั้งเป้าให้ใครในทีมเลย) มันหายไปจาก groupby แล้วผ่านเงียบ
         ตัวนี้ดูจาก "แถวที่ถูกตัด" จึงจับเคสนั้นได้
      2. 409 ของฟังก์ชันนั้นแปลว่า "แก้มือไม่ตรงเป้า" (ข้ามได้ด้วย confirm_target_mismatch
         ซึ่งในโหมดรวมภาคเป็นเรื่องปกติตาม I7) — ถ้าเอาปัญหา master data ไปรวม
         จะถูกกดยืนยันข้ามไปโดยไม่ได้ตั้งใจ

    นับเฉพาะแถวที่ allocated_boxes > 0 — ตัดแถวหีบ 0 ไม่ทำให้เป้าขาด (ไม่มีอะไรให้ทับใน Oracle)
    """
    if df is None or df.empty:
        return []
    mask = _import_key_mask(df)
    bad = df[~mask]
    if bad.empty:
        return []

    bad = bad.assign(
        _sku=bad["sku"].astype(str).str.strip(),
        _boxes=pd.to_numeric(bad["allocated_boxes"], errors="coerce").fillna(0).astype(int),
    )
    bad = bad[bad["_boxes"] > 0]
    if bad.empty:
        return []

    kept = df[mask]
    if kept.empty:
        sending: dict[str, int] = {}
    else:
        sending = (
            pd.to_numeric(kept["allocated_boxes"], errors="coerce")
            .fillna(0)
            .astype(int)
            .groupby(kept["sku"].astype(str).str.strip())
            .sum()
            .to_dict()
        )

    # อ่านเป้าไม่ได้ก็ยังรายงานได้ — จำนวนหีบที่หายไม่ได้ขึ้นกับไฟล์เป้า
    targets = _sup_target_boxes_by_sku(sup_id, month, year) or {}

    out: list[dict] = []
    for sku, grp in bad.groupby("_sku", sort=False):
        # ผู้ใช้ต้องเอารายการนี้ไปกรอกเองใน Target Sun — ห้ามตัดทิ้งเงียบ ๆ
        # ถ้าเกิน max_pairs จริง ๆ ให้ pair_count บอกจำนวนเต็มไว้ (ดูครบได้จากไฟล์ตรวจก่อนส่ง)
        pairs = grp.groupby("emp_id", sort=False)["_boxes"].sum().sort_values(ascending=False)
        out.append(
            {
                "sku": str(sku),
                "missing_boxes": int(grp["_boxes"].sum()),
                "sending_boxes": int(sending.get(str(sku), 0)),
                "expected_boxes": targets.get(str(sku)),
                "pairs": [
                    {"emp_id": str(e), "allocated_boxes": int(b)} for e, b in pairs.items()
                ],
                "pair_count": int(len(pairs)),
            }
        )
    out.sort(key=lambda x: (-x["missing_boxes"], x["sku"]))
    out = out[:max_skus]

    # เพดานรวมกันเพย์โหลดบวม — ตัดจาก SKU ที่ขาดน้อยสุดก่อน และ pair_count ยังบอกจำนวนจริง
    budget = max_pairs
    for item in out:
        if budget <= 0:
            item["pairs"] = []
            continue
        if len(item["pairs"]) > budget:
            item["pairs"] = item["pairs"][:budget]
        budget -= len(item["pairs"])
    return out


def _live_target_boxes_by_sku(
    sup_id: str, month: int, year: int, emp_codes: list[str]
) -> dict[str, int] | None:
    """
    เป้าปัจจุบันใน Target Sun ต่อ SKU ของทีมนี้ — best effort คืน None เมื่อดูไม่ได้

    อ่านอย่างเดียว ไม่เขียนอะไรกลับ ใช้สองที่:
      - ก่อนส่ง: เทียบว่าเป้าที่ดึงมาตอนขั้นที่ 1 ยังตรงกับของจริงไหม
      - หลังส่ง: เทียบว่ายอดลงจริงครบตามไฟล์ที่ส่งไปไหม

    เทียบได้เฉพาะตอนที่แหล่งเป้าคือ Target Sun เท่านั้น ถ้าระบบตั้งให้อ่านจาก Fabric
    ตัวเลขสองฝั่งมาจากคนละที่ การเอามาเทียบกันจะฟ้องผิดตลอด
    """
    from . import targetsun_read as tsr

    codes = [str(c).strip() for c in (emp_codes or []) if str(c).strip()]
    if not codes:
        return None
    try:
        if not tsr.is_enabled() or tsr.get_target_read_source() != "targetsun":
            return None
        result = tsr.fetch_target_rows(int(year), int(month), codes)
        rows = result.get("rows")
        if not isinstance(rows, list):
            return None
        out: dict[str, int] = {}
        for r in rows:
            if not isinstance(r, dict):
                continue
            sku = str(r.get("PRODUCTCODE") or "").strip()
            if not sku:
                continue
            try:
                qty = int(float(r.get("QUANTITYCASE") or 0))
            except (TypeError, ValueError):
                qty = 0
            out[sku] = out.get(sku, 0) + qty
        return out
    except Exception as e:  # อ่านไม่ได้ต้องไม่ทำให้เส้นทางหลักพัง
        logger.warning("อ่านเป้าปัจจุบันจาก Target Sun ไม่ได้ (%s): %s", sup_id, e)
        return None


def team_emp_codes_from_grain(sup_id: str, month: int, year: int) -> list[str]:
    """
    รหัสพนักงานทั้งทีมจาก grain ที่ขั้นที่ 1 เก็บไว้

    ต้องเป็นชุดเดียวกับที่ใช้สร้างไฟล์เป้า ไม่งั้นตอนเทียบกับเป้าปัจจุบัน
    ยอดสองฝั่งจะครอบคลุมคนละกลุ่มคนแล้วฟ้องผิด
    """
    dg = _read_tga_grain_cache(sup_id, int(month), int(year))
    if dg.empty or "emp_id" not in dg.columns:
        return []
    return sorted({str(e).strip() for e in dg["emp_id"] if str(e).strip()})


def target_drift_for_sups(
    sup_ids: list[str], month: int, year: int
) -> dict[str, Any]:
    """
    เป้าใน Target Sun เปลี่ยนไปจากตอนโหลดขั้นที่ 1 หรือยัง — ของหลายทีมพร้อมกัน

    ทำไมต้องมี: คนที่เกลี่ยเป้าทั้งภาคเปิดหน้าค้างไว้ทีละหลายชั่วโมง ระหว่างนั้น
    ฝั่ง Target Sun อัปเดตเป้าได้ตลอด · ของเดิมรู้ได้สองทางและสายเกินไปทั้งคู่ —
    ตอนกด "คำนวณ" (เทียบ snapshot ในเบราว์เซอร์) กับตอนกด "ส่ง" (409) ซึ่งกว่าจะรู้
    ก็เกลี่ยหีบข้ามซุปไปหมดแล้ว

    อ่านอย่างเดียว ไม่เขียนอะไรกลับ และไม่โยน exception — ทีมไหนอ่านไม่ได้ก็บอกว่า
    ตรวจไม่ได้ ไม่ใช่ทำให้ทั้งหน้าพัง
    """
    ids: list[str] = []
    for raw in sup_ids or []:
        sid = str(raw or "").strip().upper()
        if sid and sid not in ids:
            ids.append(sid)

    drifted: list[dict[str, Any]] = []
    unavailable: list[dict[str, str]] = []
    checked: list[str] = []
    for sid in ids:
        snapshot = _sup_target_boxes_by_sku(sid, month, year)
        if not snapshot:
            unavailable.append({"sup_id": sid, "reason": "ยังไม่มีไฟล์เป้าของทีมนี้"})
            continue
        try:
            codes = team_emp_codes_from_grain(sid, month, year)
            live = _live_target_boxes_by_sku(sid, month, year, codes)
        except Exception as e:                      # อ่านไม่ได้ต้องไม่ทำให้ทั้งหน้าพัง
            logger.warning("ตรวจเป้าเปลี่ยนของ %s ไม่ได้: %s", sid, e)
            live = None
        if live is None:
            unavailable.append({"sup_id": sid, "reason": "อ่านเป้าจาก Target Sun ไม่ได้"})
            continue
        checked.append(sid)
        for sku in sorted(set(snapshot) | set(live)):
            was = int(snapshot.get(sku, 0))
            now = int(live.get(sku, 0))
            if was != now:
                drifted.append({
                    "sup_id": sid,
                    "sku": sku,
                    "loaded_boxes": was,
                    "current_boxes": now,
                    "diff": now - was,
                })

    by_sup: dict[str, dict[str, int]] = {}
    for d in drifted:
        cur = by_sup.setdefault(d["sup_id"], {"sku_count": 0, "diff_boxes": 0})
        cur["sku_count"] += 1
        cur["diff_boxes"] += int(d["diff"])

    return {
        "checked_sup_ids": checked,
        "unavailable": unavailable,
        "drifted": drifted[:200],
        "drift_count": len(drifted),
        "drift_boxes": sum(int(d["diff"]) for d in drifted),
        "by_sup": by_sup,
        "changed_skus": sorted({str(d["sku"]) for d in drifted}),
    }


def assert_target_snapshot_is_fresh(
    sup_id: str,
    month: int,
    year: int,
    *,
    emp_codes: list[str] | None = None,
    confirmed: bool = False,
) -> None:
    """
    เตือนเมื่อเป้าใน Target Sun เปลี่ยนไปหลังจากผู้ใช้โหลดข้อมูลขั้นที่ 1

    หลักการเทียบยอดของระบบยึด "เป้าที่ดึงเข้ามาคำนวณรอบนั้น" เสมอ ไฟล์ที่ส่งจึงตรง
    กับเป้าชุดที่ผู้ใช้เห็น — แต่ถ้าเป้าต้นทางเปลี่ยนไปแล้ว การส่งทับด้วยแผนเก่า
    อาจไม่ใช่สิ่งที่ต้องการ ให้ผู้ใช้ตัดสินใจเอง (ยืนยันได้ ไม่บล็อกตาย)

    ถ้าอ่านของจริงไม่ได้ → ไม่บล็อกด้วยเหตุนี้ เพราะการเทียบกับ snapshot
    ยังถูกบังคับเต็มที่จากด่านอื่นอยู่แล้ว

    **เรียกจากเส้นทางส่งจริงเท่านั้น** ห้ามย้ายกลับเข้าไปใน _build_tga_upload_dataframe
    ตัวสร้างไฟล์ต้องทำงานได้แบบออฟไลน์ล้วน (อ่านแต่ cache ในเครื่อง) ไม่งั้นการ
    ดาวน์โหลด Excel และเทสต์ที่สร้างไฟล์จะยิงเน็ตขึ้น Target Sun โดยไม่มีใครตั้งใจ
    """
    if confirmed:
        return
    snapshot = _sup_target_boxes_by_sku(sup_id, month, year)
    if not snapshot:
        return
    codes = list(emp_codes) if emp_codes is not None else team_emp_codes_from_grain(sup_id, month, year)
    live = _live_target_boxes_by_sku(sup_id, month, year, codes)
    if live is None:
        return

    drifts = []
    for sku in sorted(set(snapshot) | set(live)):
        was = int(snapshot.get(sku, 0))
        now = int(live.get(sku, 0))
        if was != now:
            drifts.append(
                {"sku": sku, "loaded_boxes": was, "current_boxes": now, "diff": now - was}
            )
    if not drifts:
        return

    diff_boxes = sum(int(d["diff"]) for d in drifts)
    logger.warning(
        "เป้าใน Target Sun เปลี่ยนหลังโหลดขั้นที่ 1 %s %s-%02d: %d SKU (%+d หีบ)",
        str(sup_id or "").strip().upper(),
        year,
        month,
        len(drifts),
        diff_boxes,
    )
    raise HTTPException(
        status_code=409,
        detail={
            "code": "send_target_stale",
            "message": (
                f"ยังไม่ได้ส่ง — เป้าใน Target Sun เปลี่ยนไปหลังจากคุณโหลดข้อมูล "
                f"{len(drifts)} SKU (ต่างรวม {diff_boxes:+,} หีบ)"
            ),
            "hint_th": (
                "ถ้าจะกระจายตามเป้าใหม่ ให้โหลดข้อมูลขั้นที่ 1 ใหม่แล้วกระจายอีกครั้ง — "
                "หรือกดยืนยันเพื่อส่งตามแผนที่กระจายไว้เดิม"
            ),
            "drifts": drifts[:20],
            "drift_count": len(drifts),
            "drift_boxes": diff_boxes,
            "confirm_field": "confirm_stale_target",
        },
    )


def verify_after_send(
    sup_id: str,
    month: int,
    year: int,
    *,
    sent_by_sku: dict[str, int],
    emp_codes: list[str],
) -> dict:
    """
    ตรวจซ้ำหลังส่ง — ยอดที่ "ลงจริง" ใน Target Sun ต้องเท่าไฟล์ที่เพิ่งส่งไป

    เป็นตาข่ายชั้นเดียวที่จับได้ว่าฝั่งปลายทางปฏิเสธหรือข้ามแถวบางแถวเงียบ ๆ
    (เช่นเคสคีย์ upsert ซ้ำ ที่ตัวนำเข้าข้ามแถวหลังโดยยังตอบว่าสำเร็จ)

    ห้าม raise เด็ดขาด — ของส่งไปแล้ว ถ้าตรวจไม่ได้ก็แค่บอกว่าตรวจไม่ได้
    ไม่ใช่ทำให้การส่งที่สำเร็จแล้วดูเหมือนล้มเหลว
    """
    try:
        if not sent_by_sku:
            return {"checked": False, "reason": "no_rows"}
        live = _live_target_boxes_by_sku(sup_id, month, year, emp_codes)
        if live is None:
            return {"checked": False, "reason": "read_unavailable"}

        diffs = []
        for sku in sorted(sent_by_sku):
            sent = int(sent_by_sku.get(sku, 0))
            got = int(live.get(sku, 0))
            if got != sent:
                diffs.append(
                    {"sku": sku, "sent_boxes": sent, "landed_boxes": got, "diff": got - sent}
                )
        if not diffs:
            return {"checked": True, "ok": True, "skus_checked": len(sent_by_sku)}

        diff_boxes = sum(int(d["diff"]) for d in diffs)
        logger.error(
            "ยอดที่ลงจริงใน Target Sun ไม่ตรงกับไฟล์ที่ส่ง %s %s-%02d: %d SKU (%+d หีบ) — %s",
            str(sup_id or "").strip().upper(),
            year,
            month,
            len(diffs),
            diff_boxes,
            diffs[:5],
        )
        return {
            "checked": True,
            "ok": False,
            "diffs": diffs[:20],
            "diff_count": len(diffs),
            "diff_boxes": diff_boxes,
        }
    except Exception as e:
        logger.warning("ตรวจยอดหลังส่งไม่สำเร็จ (%s): %s", sup_id, e)
        return {"checked": False, "reason": "error"}


def verify_send_batch(metas: list[dict]) -> dict:
    """
    ด่านระดับชุด — ยอดรวมของ "ทุกทีมที่จะส่งรอบนี้" ต้องเท่าเป้ารวมของทีมเหล่านั้น ราย SKU

    ทำไมต้องมีทั้งที่มีด่านรายทีมแล้ว: ในโหมดรวมภาค autoRebalance ย้ายหีบข้ามทีม
    ราย SKU ตามที่ออกแบบไว้ (I7) ยอดรายทีมไม่ตรงเป้าทีมจึงเป็นเรื่องปกติจนผู้ใช้กด
    ยืนยันจนชิน สิ่งที่ต้องไม่เปลี่ยนคือ **ยอดรวมของทั้งภาค** — ถ้าตรงนี้เพี้ยน
    แปลว่าหีบหายหรืองอกจริง ไม่ใช่แค่ย้ายที่ จึงไม่มี flag ให้กดข้าม

    ตรวจสองเรื่อง:
      1. SKU ที่ถูกตัดในทีมใดทีมหนึ่ง ต้องถูกตัดทุกทีมในชุด ไม่งั้นเป้าของ SKU นั้น
         ทั้งภาคจะครึ่ง ๆ กลาง ๆ (บางทีมถูกทับด้วยเลขใหม่ บางทีมค้างเลขเก่า)
      2. ยอดรวมราย SKU ของทั้งชุด เท่าเป้ารวมของทุกทีมในชุด

    เทียบเฉพาะ SKU ที่ชุดนี้กำลังส่งจริง — SKU ที่มีเป้าแต่ไม่ได้ส่งเลยเป็นเรื่องปกติ
    ของการส่งแยกแบรนด์ และมีด่านรายทีม (check_missing_skus) ดูแลตอนส่งทุกแบรนด์อยู่แล้ว
    """
    periods = {
        (int(m["target_year"]), int(m["target_month"]))
        for m in metas
        if m.get("target_year") and m.get("target_month")
    }
    if len(periods) > 1:
        raise HTTPException(
            400,
            detail="ไฟล์ที่เตรียมไว้เป็นคนละงวดกัน — กรุณากดส่งใหม่อีกครั้ง",
        )

    per_team: list[tuple[str, dict[str, int]]] = []
    file_by_sku: dict[str, int] = {}
    excluded: set[str] = set()
    missing_totals = False
    for m in metas:
        sid = str(m.get("sup_id") or "").strip().upper()
        raw = m.get("sku_totals")
        if not isinstance(raw, dict):
            missing_totals = True
            raw = {}
        totals = {str(k).strip(): int(v) for k, v in raw.items() if str(k).strip()}
        per_team.append((sid, totals))
        for k, v in totals.items():
            file_by_sku[k] = file_by_sku.get(k, 0) + int(v)
        excluded |= {
            str(s).strip() for s in (m.get("excluded_skus") or []) if str(s).strip()
        }

    sup_ids = [sid for sid, _ in per_team]

    # (1) SKU ที่ทีมหนึ่งตัดทิ้ง แต่อีกทีมยังส่งอยู่
    partial = [
        {"sup_id": sid, "sku": sku, "boxes": int(totals[sku])}
        for sid, totals in per_team
        for sku in sorted(excluded)
        if int(totals.get(sku, 0)) > 0
    ]
    if partial:
        logger.error("ส่งชุดนี้จะทำให้ SKU ที่ถูกตัดหลุดไปบางทีม: %s", partial[:10])
        raise HTTPException(
            status_code=409,
            detail={
                "code": "send_batch_sku_partial",
                "message": (
                    f"ยังไม่ได้ส่ง — มี {len({p['sku'] for p in partial})} SKU ที่ทีมหนึ่งส่งไม่ได้ "
                    "แต่อีกทีมยังส่งอยู่ ต้องตัด SKU นั้นออกให้เหมือนกันทุกทีมในชุดนี้"
                ),
                "hint_th": (
                    "ระบบจะเตรียมไฟล์ใหม่โดยตัด SKU เหล่านี้ออกทุกทีม "
                    "แล้วให้ไปเกลี่ยหีบของ SKU นั้นเองใน Target Sun"
                ),
                "exclude_skus": sorted(excluded),
                "partial": partial[:50],
                "partial_count": len(partial),
            },
        )

    if missing_totals:
        logger.warning("ตรวจยอดรวมทั้งชุดไม่ได้: ไฟล์ที่เตรียมไว้บางใบไม่มียอดต่อ SKU (%s)", sup_ids)
        return {"verified": False, "reason": "no_totals", "sup_ids": sup_ids}

    # ทีมเดียว = ไม่มีการย้ายหีบข้ามทีม ด่านรายทีมตรวจเรื่องเดียวกันไปแล้วและ
    # ผู้ใช้อาจกดยืนยันความต่างไว้โดยตั้งใจ — ตรงนี้จึงไม่ไปตัดสินซ้ำ
    if len(per_team) < 2:
        return {"verified": True, "scope": "single_team", "sup_ids": sup_ids}

    targets_total: dict[str, int] = {}
    unreadable: list[str] = []
    year, month = next(iter(periods)) if periods else (None, None)
    for sid, _ in per_team:
        if year is None:
            unreadable.append(sid)
            continue
        t = _sup_target_boxes_by_sku(sid, int(month), int(year))
        if t is None:
            unreadable.append(sid)
            continue
        for k, v in t.items():
            targets_total[str(k).strip()] = targets_total.get(str(k).strip(), 0) + int(v)

    if unreadable:
        # ด่านรายทีมบล็อกเรื่องนี้ไปแล้ว (send_target_unverifiable) ถ้ามาถึงตรงนี้แปลว่า
        # ผู้ใช้ยืนยันไปแล้วว่ายอมส่งทั้งที่ตรวจไม่ได้ — อย่าฟ้องซ้ำด้วยตัวเลขที่ไม่ครบ
        logger.warning("ตรวจยอดรวมทั้งชุดไม่ได้: อ่านเป้าไม่ได้ %s", unreadable)
        return {
            "verified": False,
            "reason": "missing_targets",
            "sup_ids": sup_ids,
            "unreadable_sup_ids": unreadable,
        }

    diffs = []
    for sku in sorted(set(file_by_sku)):
        if sku in excluded:
            continue
        tgt = targets_total.get(sku)
        if tgt is None:
            continue  # ไม่มีเป้าในงวดนี้ — ด่านรายทีมดูแลอยู่
        got = int(file_by_sku.get(sku, 0))
        if got != int(tgt):
            diffs.append(
                {"sku": sku, "sending_boxes": got, "expected_boxes": int(tgt), "diff": got - int(tgt)}
            )

    if diffs:
        diff_boxes = sum(int(d["diff"]) for d in diffs)
        logger.error("ยอดรวมทั้งชุดไม่ตรงเป้ารวม %s: %s", sup_ids, diffs[:10])
        raise HTTPException(
            status_code=409,
            detail={
                "code": "send_batch_total_mismatch",
                "message": (
                    f"ยังไม่ได้ส่ง — ยอดรวมของทั้ง {len(per_team)} ทีมไม่เท่าเป้ารวม "
                    f"{len(diffs)} SKU (ต่างรวม {diff_boxes:+,} หีบ)"
                ),
                "hint_th": (
                    "ย้ายหีบข้ามทีมได้ แต่ยอดรวมของภาคต้องเท่าเดิม — "
                    "ส่วนต่างแปลว่าหีบหายหรืองอกจริง ให้กลับไปตรวจตารางผลกระจาย "
                    "หรือโหลดข้อมูลขั้นที่ 1 ใหม่แล้วกระจายอีกครั้ง"
                ),
                "diffs": diffs[:20],
                "diff_count": len(diffs),
                "diff_boxes": diff_boxes,
                "sup_ids": sup_ids,
            },
        )

    return {
        "verified": True,
        "scope": "batch",
        "sup_ids": sup_ids,
        "skus_checked": len([s for s in file_by_sku if s not in excluded]),
        "excluded_skus": sorted(excluded),
    }


def _normalize_brand_label(value: object) -> str:
    return str(value or "").strip()


def _brand_filter_mask(df: pd.DataFrame, brand_filter: str) -> pd.Series:
    """จับคู่ชื่อแบรนด์ไทยหรืออังกฤษ (ตัดช่องว่างหัวท้าย)"""
    bf = _normalize_brand_label(brand_filter)
    th = (
        df["brand_name_thai"].map(_normalize_brand_label)
        if "brand_name_thai" in df.columns
        else pd.Series("", index=df.index)
    )
    en = (
        df["brand_name_english"].map(_normalize_brand_label)
        if "brand_name_english" in df.columns
        else pd.Series("", index=df.index)
    )
    return (th == bf) | (en == bf)


def _enrich_brand_names(
    df: pd.DataFrame,
    sup_id: str,
    month: int,
    year: int,
) -> pd.DataFrame:
    """เติม brand_name_thai / brand_name_english จาก global product cache / target_boxes.csv"""
    from .fabric_cache import read_product_info_df

    out = df.copy()
    brand_th_map: dict[str, str] = {}
    brand_en_map: dict[str, str] = {}
    cached = read_product_info_df(year, month)
    if cached is not None and not cached.empty and "sku" in cached.columns:
        th_col = "brand_name_thai" if "brand_name_thai" in cached.columns else (
            "brand" if "brand" in cached.columns else None
        )
        en_col = "brand_name_english" if "brand_name_english" in cached.columns else None
        for _, row in cached.iterrows():
            sku = str(row.get("sku") or "").strip()
            if not sku:
                continue
            if th_col:
                brand = str(row.get(th_col) or "").strip()
                if brand:
                    brand_th_map[sku] = brand
            if en_col:
                brand = str(row.get(en_col) or "").strip()
                if brand:
                    brand_en_map[sku] = brand
    if not brand_th_map and not brand_en_map:
        # fallback ชั้นสุดท้าย (แค่ label แบรนด์) — ใช้ไฟล์ราย sup ก่อน แล้วค่อยตกไป global เดิม
        tgt_path = target_boxes_cache_path(sup_id, month, year)
        if not os.path.isfile(tgt_path):
            tgt_path = "data/target_boxes.csv"
        if os.path.isfile(tgt_path):
            try:
                with read_locked(tgt_path):  # ไฟล์นี้ถูกเขียนด้วย atomic_write_csv — ต้องถือ lock
                    df_tgt = pd.read_csv(tgt_path, dtype=str)
                if "sku" in df_tgt.columns:
                    for _, row in df_tgt.iterrows():
                        sku = str(row.get("sku") or "").strip()
                        if not sku:
                            continue
                        if "brand_name_thai" in df_tgt.columns:
                            brand = str(row.get("brand_name_thai") or "").strip()
                            if brand:
                                brand_th_map[sku] = brand
                        if "brand_name_english" in df_tgt.columns:
                            brand = str(row.get("brand_name_english") or "").strip()
                            if brand:
                                brand_en_map[sku] = brand
            except Exception as e:
                logger.warning("brand enrich from target_boxes: %s", e)
    out["brand_name_thai"] = out["sku"].astype(str).map(lambda s: brand_th_map.get(s, ""))
    out["brand_name_english"] = out["sku"].astype(str).map(lambda s: brand_en_map.get(s, ""))
    return out


def _enrich_emp_dimensions(
    df: pd.DataFrame,
    rows_raw: list[dict],
    skip_emp_sku_dim_merge: bool = False,
) -> pd.DataFrame:
    if not _needs_fabric_enrichment(df):
        logger.info("lakehouse enrich: skip Fabric (dims จาก TGA cache ครบแล้ว)")
        return _apply_wh_hints(df, rows_raw)

    emp_list = sorted({str(e).strip() for e in df["emp_id"].unique() if str(e).strip()})
    sku_list = sorted({str(s).strip() for s in df["sku"].unique() if str(s).strip()})
    wh_hint = {}
    for r in rows_raw:
        emp = str(r.get("emp_id") or "").strip()
        wh = _cell_str(r.get("warehouse_code"))
        if emp and wh:
            wh_hint[emp] = wh

    df_es = pd.DataFrame()
    df_emp = pd.DataFrame()
    df_wh = pd.DataFrame()
    logger.info(
        "lakehouse enrich: Fabric DAX (emp=%d sku=%d skip_emp_sku=%s)",
        len(emp_list),
        len(sku_list),
        skip_emp_sku_dim_merge,
    )
    try:
        fabric = FabricDAXConnector()
        if not skip_emp_sku_dim_merge:
            try:
                df_es = fabric.get_tga_lakehouse_dims_by_emp_sku(emp_list, sku_list)
            except Exception as e:
                logger.warning("get_tga_lakehouse_dims_by_emp_sku: %s", e)
        try:
            df_emp = fabric.get_tga_lakehouse_dims_by_emp(emp_list)
        except Exception as e:
            logger.warning("get_tga_lakehouse_dims_by_emp: %s", e)
        try:
            df_wh = fabric.get_warehouse_by_emp(emp_list)
        except Exception as e:
            logger.warning("get_warehouse_by_emp (lakehouse): %s", e)
    except Exception as e:
        logger.warning("Fabric connector (lakehouse enrich): %s", e)

    for c in ("salestype", "divisioncode", "areacode", "provincecode", "warehouse_code"):
        if c not in df.columns:
            df[c] = ""

    if not skip_emp_sku_dim_merge and not df_es.empty:
        df = df.merge(df_es, on=["emp_id", "sku"], how="left", suffixes=("", "_tga"))

    emp_fb = {}
    if not df_emp.empty:
        emp_fb = df_emp.set_index("emp_id").to_dict(orient="index")

    def _emp_fb_series(col: str) -> pd.Series:
        if not emp_fb:
            return pd.Series([""] * len(df), index=df.index)
        return df["emp_id"].map(lambda e: _cell_str((emp_fb.get(str(e).strip()) or {}).get(col)))

    df["salestype"] = _coalesce_col(df, "salestype", _emp_fb_series("salestype"))
    df["divisioncode"] = _coalesce_col(df, "divisioncode", _emp_fb_series("divisioncode"))
    df["areacode"] = _coalesce_col(df, "areacode", _emp_fb_series("areacode"))
    df["provincecode"] = _coalesce_col(df, "provincecode", _emp_fb_series("provincecode"))

    if not df_wh.empty:
        df = df.merge(
            df_wh.rename(columns={"warehouse_code": "warehouse_hist"}),
            on="emp_id",
            how="left",
        )
        if "warehouse_code" not in df.columns:
            df["warehouse_code"] = ""
        df["warehouse_code"] = df.apply(
            lambda row: _cell_str(row.get("warehouse_code"))
            or _cell_str(row.get("warehouse_hist"))
            or wh_hint.get(str(row["emp_id"]).strip(), ""),
            axis=1,
        )
        if "warehouse_hist" in df.columns:
            df = df.drop(columns=["warehouse_hist"])
    else:
        df["warehouse_code"] = df.apply(
            lambda row: _cell_str(row.get("warehouse_code"))
            or wh_hint.get(str(row["emp_id"]).strip(), ""),
            axis=1,
        )

    wh_tga = _coalesce_col(df, "warehouse_code", _emp_fb_series("warehouse_code"))
    df["warehouse_code"] = wh_tga
    df["areacode"] = df["areacode"].map(_areacode_str)
    df["warehouse_code"] = df["warehouse_code"].map(_cell_str)
    return df


def _allow_send_mismatch() -> bool:
    """ทางออกฉุกเฉิน — ใช้ตัวเดียวกับประตูใน /optimize เพื่อไม่ให้มีสวิตช์หลายตัว"""
    return (os.environ.get("ALLOC_ALLOW_MISMATCH") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _sup_target_boxes_by_sku(sup_id: str, month: int, year: int) -> dict[str, int] | None:
    """
    เป้าหีบต่อ SKU ของทีมนี้จากไฟล์เป้า — คืน None เมื่ออ่านไม่ได้

    ห้ามตกไปอ่านไฟล์เป้า global เดิม (allow_legacy_fallback=False) เพราะไฟล์นั้น
    ไม่มี sup_id อยู่ในชื่อ ทีมที่โหลดทีหลังเขียนทับของทีมก่อน — ประตูตรวจเป้า
    อาจไปเทียบ payload ของทีมนี้กับเป้าของอีกทีมแล้วผ่าน/ฟ้องผิดแบบเงียบ ๆ
    ไม่มีไฟล์ราย sup = ตรวจไม่ได้ ต้องให้ผู้เรียกบล็อกไว้ ไม่ใช่เดาจากไฟล์อื่น

    ใช้ร่วมกันระหว่าง _assert_send_matches_sup_targets (ตรวจก่อน drop)
    และ _shortfall_from_dropped_rows (ตรวจหลัง drop) — ต้องอ่านจากแหล่งเดียวกัน
    """
    from ..core.targets import load_target_csv_for

    sid = str(sup_id or "").strip().upper()
    try:
        df_sku, _ = load_target_csv_for(
            sid, int(month), int(year), allow_legacy_fallback=False
        )
    except Exception as e:
        logger.warning("อ่านเป้าทีมก่อนส่ง Target Sun ไม่ได้ (%s): %s", sid, e)
        return None
    if df_sku is None or df_sku.empty:
        logger.warning("อ่านเป้าทีมก่อนส่ง Target Sun: ไม่พบไฟล์เป้าของ %s %s-%02d", sid, year, month)
        return None

    targets: dict[str, int] = {}
    for _, r in df_sku.iterrows():
        sku = str(r.get("sku") or "").strip()
        if not sku:
            continue
        try:
            targets[sku] = int(round(float(r.get("supervisor_target_boxes") or 0)))
        except (TypeError, ValueError):
            targets[sku] = 0
    return targets


def _assert_send_matches_sup_targets(
    df: pd.DataFrame,
    sup_id: str,
    month: int,
    year: int,
    *,
    confirmed: bool = False,
    check_missing_skus: bool = False,
    unverifiable_confirmed: bool = False,
) -> None:
    """
    ประตูสุดท้ายก่อนส่งเข้า Target Sun — ผลรวมหีบต่อ SKU ของทีมนี้ต้องตรงเป้าของทีมนี้

    ทำไมต้องตรวจซ้ำทั้งที่ /optimize ตรวจแล้ว:
      ระหว่าง optimize -> ส่ง ยังมีอีกหลายก้าวที่ไม่มีใครตรวจเลย
        - แก้มือในตาราง (โหมดรวมภาคย้ายหีบข้ามทีมได้ตามที่ออกแบบไว้)
        - PUT /data/allocations บันทึก snapshot โดยไม่ตรวจผลรวม
        - โหลด snapshot เก่ากลับมาส่ง ทั้งที่เป้า TGA เปลี่ยนไปแล้ว
      ตัวเลขที่ถึง Target Sun จึงต่างจากเป้าของทีมได้ ทั้งที่ตอนกระจายถูกต้อง

    check_missing_skus — ตรวจ "SKU ที่มีเป้าแต่ไม่มีใน payload เลย" ด้วย
      เปิดเฉพาะตอนส่งทุกแบรนด์ เพราะตอนนั้น payload ต้องครอบคลุมทุก SKU ที่มีเป้า
      ถ้าส่งแยกแบรนด์ payload มีแค่บาง SKU อยู่แล้ว เปิดไว้จะฟ้องผิดทั้งกระดาน

      เคสที่จับได้: หน้าเว็บจำรายชื่อ SKU ไว้ตั้งแต่โหลดขั้นที่ 1 ถ้าหลังจากนั้น
      มีการนำเข้าเป้า TGA ที่เพิ่ม SKU ใหม่ SKU นั้นจะไม่อยู่ใน payload เลย
      การวนจาก payload อย่างเดียวจึงไม่มีทางเห็นมัน
    """
    if df is None or df.empty:
        return
    sid = str(sup_id or "").strip().upper()
    targets = _sup_target_boxes_by_sku(sid, month, year)
    if targets is None:
        # อ่านเป้าไม่ได้ = ตรวจไม่ได้ → ต้อง "บล็อกไว้ก่อน" ไม่ใช่ปล่อยผ่านเงียบ ๆ
        #
        # เดิมตรงนี้ return เฉย ๆ ผลคือประตูที่แข็งแรงที่สุดปิดตัวเองอัตโนมัติ
        # ในสถานการณ์ที่มันควรทำงานที่สุด: ไฟล์เป้าถูกล้างตามอายุ cache แล้ว
        # ผู้ใช้เปิด snapshot เก่ามาส่ง — ส่งอะไรก็ได้โดยไม่มีอะไรทัดทาน
        # ทางแก้ที่ถูกคือโหลดขั้นที่ 1 ใหม่ให้ระบบดึงเป้ามาเก็บอีกรอบ
        if unverifiable_confirmed:
            logger.warning(
                "ผู้ใช้ยืนยันส่งทั้งที่ไม่มีไฟล์เป้าให้ตรวจ %s %s-%02d", sid, year, month
            )
            return
        logger.error("ไม่มีไฟล์เป้าให้ตรวจก่อนส่ง %s %s-%02d — บล็อกไว้", sid, year, month)
        raise HTTPException(
            status_code=409,
            detail={
                "code": "send_target_unverifiable",
                "message": (
                    "ยังไม่ได้ส่ง — ระบบไม่มีไฟล์เป้าของทีมนี้งวดนี้ให้ตรวจสอบ "
                    "จึงยืนยันไม่ได้ว่ายอดที่จะส่งตรงกับเป้า"
                ),
                "hint_th": (
                    "กลับไปโหลดข้อมูลขั้นที่ 1 ใหม่เพื่อดึงเป้าเข้ามาเก็บอีกครั้ง "
                    "แล้วค่อยส่ง — ถ้ายืนยันจะส่งทั้งที่ตรวจไม่ได้ ให้กดยืนยัน"
                ),
                "confirm_field": "confirm_unverifiable_target",
                "sup_id": sid,
                "target_month": int(month),
                "target_year": int(year),
            },
        )

    got = df.groupby("sku")["allocated_boxes"].sum().astype(int).to_dict()
    got = {str(k).strip(): int(v) for k, v in got.items()}

    # วนจาก "SKU ที่มีเป้า" ไม่ใช่ "SKU ที่ส่ง" เมื่อ payload ควรครบ —
    # ไม่งั้น SKU ที่หายไปทั้งตัวจะไม่เคยถูกหยิบมาเทียบ
    if check_missing_skus:
        keys = sorted(set(targets) | set(got))
    else:
        keys = sorted(got)

    problems = []
    for sku in keys:
        tgt = targets.get(sku)
        if tgt is None:
            continue  # SKU ไม่มีเป้าในงวดนี้ — ปล่อยให้เส้นทางเดิมจัดการ
        total = got.get(sku, 0)
        if total != int(tgt):
            problems.append(
                {
                    "sku": sku,
                    "sending_boxes": int(total),
                    "expected_boxes": int(tgt),
                    # ไม่มีแถวเลย ต่างจากส่งมาแต่จำนวนไม่ตรง — หน้าเว็บใช้แยกข้อความ
                    "missing_from_payload": sku not in got,
                }
            )

    if not problems:
        return

    logger.error(
        "ส่ง Target Sun ไม่ตรงเป้าทีม %s %s-%02d: %s", sid, year, month, problems[:20]
    )
    if confirmed:
        # ผู้ใช้เห็นรายการแล้วและกดยืนยัน — เช่นย้ายหีบข้ามทีมในโหมดรวมภาคโดยตั้งใจ
        logger.warning(
            "ผู้ใช้ยืนยันส่งทั้งที่ไม่ตรงเป้าทีม %s (%d SKU ไม่ตรง)", sid, len(problems)
        )
        return
    if _allow_send_mismatch():
        logger.warning("ALLOC_ALLOW_MISMATCH เปิดอยู่ — ปล่อยให้ส่งทั้งที่ไม่ตรงเป้า")
        return

    missing = [p for p in problems if p.get("missing_from_payload")]
    hint = (
        "มักเกิดจากการแก้ตัวเลขข้ามทีมในโหมดรวมภาค หรือเป้า Target Sun "
        "เปลี่ยนหลังจากกระจายไปแล้ว — กด「คำนวณใหม่」แล้วส่งอีกครั้ง"
    )
    if missing:
        # SKU ที่ไม่มีในสิ่งที่ส่งเลย = หน้าเว็บยังไม่รู้จักมัน (เป้าเพิ่มมาหลังโหลดขั้นที่ 1)
        # กด「คำนวณใหม่」เฉย ๆ ไม่พอ ต้องโหลดขั้นที่ 1 ใหม่ให้เห็น SKU ก่อน
        hint = (
            f"มี {len(missing)} SKU ที่มีเป้าแต่ไม่มีอยู่ในผลกระจายเลย — "
            "แปลว่าเป้า TGA เปลี่ยนหลังจากคุณโหลดข้อมูลขั้นที่ 1 "
            "ให้โหลดขั้นที่ 1 ใหม่ แล้วกระจายหีบอีกครั้งก่อนส่ง"
        )
    raise HTTPException(
        status_code=409,
        detail={
            "code": "send_target_mismatch",
            "message": (
                f"ยอดหีบที่จะส่งไม่ตรงเป้าของทีม {sid} — ระบบยังไม่ส่ง "
                f"({len(problems)} SKU ไม่ตรง"
                + (f", {len(missing)} SKU ไม่มีในผลกระจาย" if missing else "")
                + ")"
            ),
            "hint_th": hint,
            "mismatches": problems[:20],
            "mismatch_count": len(problems),
            "missing_sku_count": len(missing),
            "sup_id": sid,
            "confirm_field": "confirm_target_mismatch",
        },
    )


def _build_tga_upload_dataframe(
    req: LakehouseUploadRequest,
    *,
    drop_incomplete_rows: bool = False,
    enforce_targets: bool = False,
) -> tuple[pd.DataFrame, int, list[dict], list[dict]]:
    """
    enforce_targets — ตรวจว่าผลรวมหีบต่อ SKU ตรงเป้าทีมหรือไม่ (409 ถ้าไม่ตรง)

    เปิดเฉพาะ "เส้นทางส่งจริง" เท่านั้น ห้ามเปิดกับการสร้างไฟล์เพื่อดาวน์โหลด
    เพราะผู้ใช้ต้องโหลด Excel มาตรวจได้แม้ตัวเลขยังไม่ตรง — ถ้าบล็อกตรงนั้นด้วย
    จะกลายเป็นว่ายิ่งมีปัญหายิ่งตรวจไม่ได้
    """
    t0 = time.perf_counter()
    rows_raw = [a.model_dump() for a in req.allocations]
    df = pd.DataFrame(rows_raw)
    df["allocated_boxes"] = pd.to_numeric(df["allocated_boxes"], errors="coerce").fillna(0).astype(int)
    if df.empty:
        raise HTTPException(400, detail="ไม่มีข้อมูล allocations สำหรับส่งออก")

    # รหัสพนักงานต้องผ่านตัว normalize ตัวเดียวกับฝั่ง grain — ถ้ารูปต่างกัน
    # จะจับคู่ไม่ติดแล้ว SKU นั้นถูกตัดทั้งตัวโดยที่ข้อมูลไม่ได้ผิดอะไร
    df["emp_id"] = df["emp_id"].map(norm_emp_code)
    df["sku"] = df["sku"].astype(str).str.strip()
    df = df[(df["emp_id"] != "") & (df["sku"] != "")].copy()
    if df.empty:
        raise HTTPException(400, detail="ไม่มีแถว emp×sku ที่สมบูรณ์สำหรับส่งออก")

    brand_filter = _normalize_brand_label(getattr(req, "brand_filter", None) or "ALL")
    if brand_filter and brand_filter.upper() != "ALL":
        needs_brand_enrich = (
            "brand_name_thai" not in df.columns
            or df["brand_name_thai"].astype(str).str.strip().eq("").all()
        )
        if needs_brand_enrich:
            df = _enrich_brand_names(df, req.sup_id, int(req.target_month), int(req.target_year))
        mask = _brand_filter_mask(df, brand_filter)
        df = df[mask].copy()
        if df.empty:
            raise HTTPException(
                404,
                detail={
                    "message": f"ไม่พบข้อมูลสำหรับแบรนด์ '{brand_filter}'",
                    "hint_th": "ตรวจว่าแบรนด์นี้มี SKU ในผลกระจายหีบ — หรือลองส่งทุกแบรนด์",
                },
            )
        if int(df["allocated_boxes"].sum()) == 0:
            raise HTTPException(
                400,
                detail={
                    "message": f"แบรนด์ '{brand_filter}' ส่งเป็นหีบ 0 ทั้งหมด — Target Sun จะทับเป้าเดิมเป็น 0",
                    "hint_th": "ดาวน์โหลด Excel ตรวจก่อน หรือรีเฟรชหน้าแล้วส่งใหม่",
                },
            )

    # ส่งเฉพาะ SKU ที่เลือก (เช่น "ส่งเฉพาะผลกระจายใหม่") — กลไกเดียวกับส่งเฉพาะแบรนด์:
    # SKU นอกรายการไม่ถูกแตะใน Target Sun และประตู S1 ตรวจเฉพาะ SKU ใน payload
    sku_filter = [
        str(s).strip() for s in (getattr(req, "sku_filter", None) or []) if str(s).strip()
    ]
    if sku_filter:
        df = df[df["sku"].isin(set(sku_filter))].copy()
        if df.empty:
            raise HTTPException(
                404,
                detail={
                    "message": "ไม่พบข้อมูลของสินค้าที่เลือกส่ง",
                    "hint_th": "ตรวจว่าสินค้าที่เลือกยังอยู่ในผลกระจายหีบ — หรือส่งทุกสินค้าแทน",
                },
            )
        if int(df["allocated_boxes"].sum()) == 0:
            raise HTTPException(
                400,
                detail={
                    "message": "สินค้าที่เลือกส่งเป็นหีบ 0 ทั้งหมด — Target Sun จะทับเป้าเดิมเป็น 0",
                    "hint_th": "ตรวจผลกระจายของสินค้าที่เลือกก่อนส่ง",
                },
            )

    df = _normalize_allocation_payload(df)
    payload_by_sku = _boxes_by_sku(df)
    if enforce_targets:
        _assert_send_matches_sup_targets(
            df,
            req.sup_id,
            int(req.target_month),
            int(req.target_year),
            confirmed=bool(getattr(req, "confirm_target_mismatch", False)),
            # ส่งทุกแบรนด์ครบทุกสินค้าเท่านั้นที่ payload ควรครอบคลุมทุก SKU ที่มีเป้า
            check_missing_skus=(brand_filter or "ALL").upper() == "ALL" and not sku_filter,
            unverifiable_confirmed=bool(getattr(req, "confirm_unverifiable_target", False)),
        )
    zero_pairs_full = _zero_sum_emp_sku_pairs(df)

    grain_dg = _read_tga_grain_cache(req.sup_id, int(req.target_month), int(req.target_year))
    # ผลกระจายรวมภาค/รวมหน่วยมีพนักงานของหลายทีมในคำขอเดียว — เติม grain ของคนที่
    # ไม่ได้อยู่ในไฟล์ของทีมเจ้าของ จากไฟล์ของทีมอื่นในงวดเดียวกัน
    # ไม่ทำแบบนี้ แถวของเขาจะไม่มี dim แล้วถูกตัดทิ้งทั้งหมด (SKU ก็ถูกตัดตามไปด้วย)
    _req_emps = {
        norm_emp_code(a.emp_id) for a in (req.allocations or []) if str(a.emp_id).strip()
    }
    _have = (
        set(grain_dg["emp_id"].tolist())
        if not grain_dg.empty and "emp_id" in grain_dg.columns
        else set()
    )
    _missing_emps = _req_emps - _have
    if _missing_emps:
        _extra = _read_tga_grain_across_teams(
            int(req.target_month), int(req.target_year), _missing_emps
        )
        if not _extra.empty:
            logger.info(
                "เติม grain ข้ามทีม %d แถว ให้พนักงาน %d คนที่ไม่ได้อยู่ในไฟล์ของ %s",
                len(_extra), _extra["emp_id"].nunique(), str(req.sup_id or "").upper(),
            )
            grain_dg = (
                _extra if grain_dg.empty
                else pd.concat([grain_dg, _extra], ignore_index=True)
            )
    grain_lookup = _grain_by_pair(grain_dg)
    t_grain = time.perf_counter()


    df_expand, grain_ok = _expand_allocations_with_tga_grain(
        df,
        req.sup_id,
        int(req.target_month),
        int(req.target_year),
        dg=grain_dg,
        grain_lookup=grain_lookup,
        infer_missing_dims=bool(getattr(req, "allow_new_targetsun_rows", False)),
    )
    df = df_expand if grain_ok else df
    df = _align_zero_allocations_to_tga_grain(
        df,
        req.sup_id,
        int(req.target_month),
        int(req.target_year),
        dg=grain_dg,
        grain_lookup=grain_lookup,
    )
    df = _ensure_zero_pairs_have_rows(
        df,
        zero_pairs_full,
        req.sup_id,
        int(req.target_month),
        int(req.target_year),
        dg=grain_dg,
        grain_lookup=grain_lookup,
    )
    t_expand = time.perf_counter()

    # grain จากขั้นที่ 1 ครบทุกแถว → ไม่ยิง Fabric ซ้ำ (เร็วขึ้น ~2–3s)
    # WAREHOUSECODE ไม่บังคับ import — เติมจาก payload / cache เท่านั้น
    if grain_ok and not df.empty and bool(_import_key_mask(df).all()):
        logger.info(
            "lakehouse enrich: skip Fabric (grain_ok + SALESTYPE/DIVISION/AREACODE ครบ %d แถว)",
            len(df),
        )
        df = _apply_wh_hints(df, rows_raw)
    else:
        df = _enrich_emp_dimensions(
            df, rows_raw, skip_emp_sku_dim_merge=bool(grain_ok)
        )
    t_enrich = time.perf_counter()

    # ต้องคิดจาก df ก่อน drop — หลัง drop แถวที่หายไปไม่เหลือให้นับแล้ว
    shortfall = _shortfall_from_dropped_rows(
        df, req.sup_id, int(req.target_month), int(req.target_year)
    )

    # SKU ที่ส่งได้ไม่ครบ → ไม่ส่ง SKU นั้นทั้งตัว
    #
    # เหตุที่ส่งไม่ครบคือ Target Sun ไม่เคยมีแถวของคู่พนักงาน×สินค้านั้น จึงเขียนทับไม่ได้
    # ถ้าส่งเฉพาะส่วนที่ส่งได้ เป้าของ SKU นั้นใน Target Sun จะกลายเป็นครึ่ง ๆ กลาง ๆ
    # (บางคนถูกทับด้วยเลขใหม่ บางคนค้างเลขเก่า) ซึ่งแย่กว่าไม่แตะเลย
    # ตัดทั้ง SKU แล้วของเดิมยังอยู่ครบ ผู้ใช้ไปเกลี่ยหีบเองใน Target Sun ได้ตามรายการที่แจ้ง
    # ผลพลอยได้: SKU ที่เหลือในไฟล์จึงต้องตรงเป้าเป๊ะทุกตัว ไม่มีข้อยกเว้น
    # (นับจาก df ก่อน drop และไม่ใช้ shortfall เพราะ shortfall ถูกจำกัดจำนวนไว้)
    excluded_skus: set[str] = set()
    if drop_incomplete_rows and not df.empty:
        _bad = ~_import_key_mask(df)
        _has_boxes = pd.to_numeric(df["allocated_boxes"], errors="coerce").fillna(0) > 0
        excluded_skus = set(df.loc[_bad & _has_boxes, "sku"].astype(str).str.strip())

    # SKU ที่ด่านระดับชุดสั่งให้ตัดเหมือนกันทุกทีม (ทีมอื่นในภาคส่ง SKU นี้ไม่ได้)
    if drop_incomplete_rows:
        _from_batch = {
            str(s).strip() for s in (getattr(req, "exclude_skus", None) or []) if str(s).strip()
        }
        _payload_skus = set(payload_by_sku)
        for _sku in sorted(_from_batch & _payload_skus):
            if _sku in excluded_skus:
                continue
            excluded_skus.add(_sku)
            # แจ้งให้ครบเหมือนกรณีที่ตัดเพราะทีมตัวเอง ผู้ใช้จะได้เห็นว่าต้องไปเกลี่ยอะไรบ้าง
            shortfall.append(
                {
                    "sku": _sku,
                    "missing_boxes": 0,
                    "excluded_boxes": int(payload_by_sku.get(_sku, 0)),
                    "sending_boxes": 0,
                    "expected_boxes": None,
                    "pairs": [],
                    "pair_count": 0,
                    "excluded_whole_sku": True,
                    "excluded_by_batch": True,
                }
            )

    if drop_incomplete_rows and excluded_skus:
        _before_rows = len(df)
        df = df[~df["sku"].astype(str).str.strip().isin(excluded_skus)].copy()
        logger.warning(
            "ไม่ส่ง %d SKU ทั้งตัวเพราะมีคู่พนักงาน×สินค้าที่ไม่มีใน Target Sun %s: ตัด %d แถว — %s",
            len(excluded_skus),
            str(req.sup_id or "").strip().upper(),
            _before_rows - len(df),
            sorted(excluded_skus)[:10],
        )
        for _item in shortfall:
            _sku = str(_item.get("sku") or "").strip()
            if _sku in excluded_skus:
                _item["excluded_whole_sku"] = True
                _item["excluded_boxes"] = int(payload_by_sku.get(_sku, 0))
                _item["sending_boxes"] = 0

    if drop_incomplete_rows:
        df, dropped_dims, not_in_ts = _drop_rows_missing_tga_import_key(df)
        if df.empty:
            raise HTTPException(
                400,
                detail={
                    "message": (
                        "ไม่มีแถวที่ส่งเข้า Target Sun ได้ — ทุก SKU มีคู่พนักงาน×สินค้า "
                        "ที่ไม่มีเป้าใน Target Sun งวดนี้ จึงถูกตัดออกทั้งหมด"
                    ),
                    "excluded_skus": sorted(excluded_skus),
                    "excluded_sku_count": len(excluded_skus),
                    "rows_not_in_targetsun": not_in_ts,
                    "rows_not_in_targetsun_count": dropped_dims,
                    "hint_th": "กลับไปโหลดข้อมูลขั้นที่ 1 ใหม่ แล้วกระจายหีบอีกครั้ง",
                },
            )
    else:
        not_in_ts = _preview_not_in_targetsun(df)
        dropped_dims = int((~_import_key_mask(df)).sum())

    # ประตูที่สอง: มี SKU ที่ส่งไม่ครบ → SKU นั้นถูกตัดออกจากไฟล์ทั้งตัว
    # ยืนยันข้ามได้ แต่ต้องผ่าน confirm_manual_topup เท่านั้น (ไม่ใช่ประตูแรก)
    # เพราะการยืนยันตรงนี้แปลว่า "รับทราบว่า SKU เหล่านี้จะไม่ถูกส่ง และจะไปเกลี่ยเองใน Target Sun"
    if enforce_targets and shortfall:
        total_missing = sum(int(s["missing_boxes"]) for s in shortfall)
        total_excluded = sum(int(s.get("excluded_boxes") or 0) for s in shortfall)
        if getattr(req, "confirm_manual_topup", False):
            logger.warning(
                "ผู้ใช้ยืนยันส่งโดยข้าม %d SKU %s (หีบที่ไม่ถูกส่ง %d) — ต้องไปเกลี่ยเองใน Target Sun: %s",
                len(shortfall),
                str(req.sup_id or "").strip().upper(),
                total_excluded or total_missing,
                shortfall[:5],
            )
        else:
            logger.error(
                "ส่ง Target Sun ไม่ครบ %s: %d SKU ถูกตัดทั้งตัว (หีบที่ไม่ถูกส่ง %d) — %s",
                str(req.sup_id or "").strip().upper(),
                len(shortfall),
                total_excluded or total_missing,
                shortfall[:5],
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "send_target_shortfall",
                    "message": (
                        f"ยังไม่ได้ส่ง — มี {len(shortfall)} SKU ที่ส่งไม่ครบ "
                        f"เพราะบางคู่พนักงาน×สินค้าไม่เคยมีใน Target Sun งวดนี้ "
                        f"ระบบจะ 'ไม่ส่ง SKU เหล่านี้ทั้งตัว' "
                        f"(รวม {total_excluded or total_missing:,} หีบ) "
                        "เพื่อไม่ให้เป้าของ SKU นั้นกลายเป็นครึ่ง ๆ กลาง ๆ"
                    ),
                    "hint_th": (
                        "ทางเลือกที่ดีที่สุด: โหลดข้อมูลขั้นที่ 1 ใหม่ ถ้ายังขาดอยู่แปลว่าคู่นั้นไม่มีเป้าใน TGA จริง "
                        "ให้ย้ายหีบไปให้คนอื่นในทีมที่มีเป้าของ SKU นั้น — "
                        "หรือกดยืนยันเพื่อส่งเฉพาะ SKU ที่ครบ แล้วไปเกลี่ยหีบของ SKU ที่เหลือเองใน Target Sun "
                        "(ของเดิมใน Target Sun จะไม่ถูกแตะ ยอดจึงไม่หาย)"
                    ),
                    "shortfall": shortfall,
                    "shortfall_skus": len(shortfall),
                    "shortfall_boxes": total_missing,
                    "excluded_boxes": total_excluded,
                    "excluded_skus": sorted(excluded_skus),
                    "excluded_sku_count": len(excluded_skus),
                    "whole_sku_excluded": True,
                    "rows_not_in_targetsun": not_in_ts,
                    "rows_not_in_targetsun_count": dropped_dims,
                    "confirm_field": "confirm_manual_topup",
                },
            )

    if df.empty:
        raise HTTPException(400, detail="ไม่มีข้อมูล allocations สำหรับส่งออก")

    df, merged_dupes = _merge_duplicate_import_keys(df)
    if merged_dupes:
        logger.warning(
            "รวมแถวคีย์ซ้ำก่อนออกไฟล์ %s: %d แถว (บวกจำนวนหีบเข้าด้วยกัน ยอดรวมเท่าเดิม)",
            str(req.sup_id or "").strip().upper(),
            merged_dupes,
        )

    _assert_file_preserves_payload_totals(
        df, payload_by_sku, sup_id=req.sup_id, exempt_skus=excluded_skus
    )

    # แถวที่จะถูก "สร้างใหม่" ใน Target Sun (เดิมไม่มีคู่นี้อยู่) — ต้องบอกให้รู้
    # เพราะเป็นการแตะ master data ไม่ใช่แค่ทับตัวเลขเป้าเดิม
    if "dims_inferred" in df.columns:
        # คอลัมน์นี้เป็น object (แถวจากเส้นทางอื่นไม่มีค่า) — เทียบตรง ๆ เลี่ยง
        # การ downcast ที่ pandas เตือนว่าจะเปลี่ยนพฤติกรรมในอนาคต
        new_rows = int((df["dims_inferred"] == True).sum())  # noqa: E712
        if new_rows:
            logger.warning(
                "จะสร้างเป้าใหม่ใน Target Sun %s: %d แถว (เติมเขต/พื้นที่จากแถวอื่นของพนักงานคนเดียวกัน)",
                str(req.sup_id or "").strip().upper(),
                new_rows,
            )

    user_code = _resolve_user_code(req)
    updatedate = _format_updatedate_bangkok_be()
    effectivedate = _format_effectivedate_bangkok_be(req.target_year, req.target_month)

    out = pd.DataFrame(
        {
            "PRODUCTCODE": df["sku"],
            "SALESTYPE": df["salestype"].map(_cell_str),
            "DIVISIONCODE": df["divisioncode"].map(_cell_str),
            "SALESMANCODE": df["emp_id"],
            "AREACODE": df["areacode"].map(_areacode_str),
            "PROVINCECODE": df["provincecode"].map(_cell_str),
            "WAREHOUSECODE": df["warehouse_code"].map(_cell_str),
            "QUANTITYCASE": df["allocated_boxes"].astype(int),
            "EFFECTIVEDATE": effectivedate,
            "UPDATEDATE": updatedate,
            "USERCODE": user_code,
        }
    )
    t_done = time.perf_counter()
    logger.info(
        "lakehouse build timing: grain=%.2fs expand=%.2fs enrich=%.2fs finalize=%.2fs total=%.2fs rows=%d grain_ok=%s",
        t_grain - t0,
        t_expand - t_grain,
        t_enrich - t_expand,
        t_done - t_enrich,
        t_done - t0,
        len(out),
        grain_ok,
    )
    return out[LAKEHOUSE_CSV_COLUMNS], dropped_dims, not_in_ts, shortfall


def _export_basename(req: LakehouseUploadRequest) -> str:
    day_tag = _bangkok_date_yyyymmdd()
    return f"alloc_{safe_id(req.sup_id)}_{req.target_year}_{req.target_month:02d}_{day_tag}"


def prepare_lakehouse_csv(req: LakehouseUploadRequest) -> tuple[bytes, str, pd.DataFrame]:
    """CSV สำหรับ ingest / OneLake (ค่าวันที่เป็นข้อความ d/M/yyyy HH:mm:ss)"""
    df, _dropped, _preview, _shortfall = _build_tga_upload_dataframe(req, drop_incomplete_rows=True)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    content = ("\ufeff" + buf.getvalue()).encode("utf-8")
    return content, f"{_export_basename(req)}.csv", df


def _build_xlsx_bytes(df: pd.DataFrame) -> bytes:
    """สร้าง .xlsx จาก DataFrame — เร็วกว่า openpyxl append ทีละแถว"""
    export_df = df[LAKEHOUSE_CSV_COLUMNS].copy()
    for name in LAKEHOUSE_TEXT_DATE_COLUMNS:
        if name in export_df.columns:
            export_df[name] = export_df[name].astype(str)
    buf = io.BytesIO()
    try:
        with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
            export_df.to_excel(writer, sheet_name="TGA", index=False)
            ws = writer.sheets["TGA"]
            wb = writer.book
            text_fmt = wb.add_format({"num_format": "@"})
            for i, name in enumerate(LAKEHOUSE_CSV_COLUMNS):
                if name in LAKEHOUSE_TEXT_DATE_COLUMNS:
                    ws.set_column(i, i, None, text_fmt)
    except ImportError:
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            export_df.to_excel(writer, sheet_name="TGA", index=False)
            ws = writer.sheets["TGA"]
            col_idx = {name: i + 1 for i, name in enumerate(LAKEHOUSE_CSV_COLUMNS)}
            for name in LAKEHOUSE_TEXT_DATE_COLUMNS:
                ci = col_idx[name]
                for r in range(2, len(export_df) + 2):
                    ws.cell(row=r, column=ci).number_format = "@"
    return buf.getvalue()


def prepare_lakehouse_xlsx(
    req: LakehouseUploadRequest,
    *,
    drop_incomplete_rows: bool = False,
    enforce_targets: bool = False,
) -> tuple[bytes, str, pd.DataFrame, int, list[dict], list[dict]]:
    """
    Excel รูปแบบ tga_target_salesman_next — ชีตเดียวชื่อ TGA (เหมือน alloc_*.xlsx)
    คอลัมน์วันที่เป็นข้อความ (@) เลี่ยง Excel แปลงเป็น 12:00 AM

    enforce_targets=True เฉพาะเส้นทางส่งจริง — การดาวน์โหลดไฟล์มาตรวจต้องทำได้เสมอ

    ตัวสุดท้ายที่คืน = shortfall (SKU ที่เป้าจะขาดเพราะแถวถูกตัด) — ผู้เรียกเอาไปบอกผู้ใช้
    ว่าต้องไปเพิ่มจำนวนเองใน Target Sun คู่ไหนบ้าง
    """
    t0 = time.perf_counter()
    df, dropped_dims, not_in_ts, shortfall = _build_tga_upload_dataframe(
        req,
        drop_incomplete_rows=drop_incomplete_rows,
        enforce_targets=enforce_targets,
    )
    t_df = time.perf_counter()
    content = _build_xlsx_bytes(df)
    t_xlsx = time.perf_counter()
    logger.info(
        "lakehouse xlsx timing: dataframe=%.2fs write_xlsx=%.2fs total=%.2fs rows=%d",
        t_df - t0,
        t_xlsx - t_df,
        t_xlsx - t0,
        len(df),
    )
    return content, f"{_export_basename(req)}.xlsx", df, dropped_dims, not_in_ts, shortfall


def _upload_bytes_to_onelake(file_path: str, content: bytes, token: str) -> None:
    url, _fp = _onelake_file_url(file_path)

    headers = {
        "Authorization": f"Bearer {token}",
        "x-ms-version": "2021-08-06",
    }

    _onelake_delete_if_exists(url, headers)

    r0 = requests.put(url + "?resource=file", headers=headers, timeout=60)
    if r0.status_code not in (201, 200, 202):
        raise HTTPException(
            502,
            detail=f"สร้างไฟล์บน OneLake ไม่สำเร็จ (HTTP {r0.status_code}): {r0.text[:300]}",
        )

    r1 = requests.patch(
        url + "?action=append&position=0",
        headers={**headers, "Content-Type": "application/octet-stream"},
        data=content,
        timeout=120,
    )
    if r1.status_code not in (202, 200):
        raise HTTPException(
            502,
            detail=f"อัปโหลดเนื้อหาไป OneLake ไม่สำเร็จ (HTTP {r1.status_code}): {r1.text[:300]}",
        )

    r2 = requests.patch(
        url + f"?action=flush&position={len(content)}",
        headers=headers,
        timeout=60,
    )
    if r2.status_code not in (200, 201):
        raise HTTPException(
            502,
            detail=f"ยืนยันไฟล์ (flush) บน OneLake ไม่สำเร็จ (HTTP {r2.status_code}): {r2.text[:300]}",
        )


def export_allocations_excel(req: LakehouseUploadRequest) -> dict:
    """สร้าง Excel รูปแบบ tga_target_salesman_next — รวม QUANTITYCASE=0 สำหรับทับข้อมูลเดิม"""
    if not req.allocations:
        raise HTTPException(400, detail="ไม่มีข้อมูล allocations สำหรับส่งออก")

    content, fname, df, dropped_dims, not_in_ts, shortfall = prepare_lakehouse_xlsx(
        req, drop_incomplete_rows=True
    )
    zero_rows = int((df["QUANTITYCASE"] == 0).sum())
    return {
        "content": content,
        "filename": fname,
        "rows": int(len(df)),
        "zero_rows": zero_rows,
        "dropped_missing_dims": dropped_dims,
        "rows_not_in_targetsun": not_in_ts,
        "rows_not_in_targetsun_count": dropped_dims,
        "shortfall": shortfall,
        "shortfall_boxes": sum(int(s["missing_boxes"]) for s in shortfall),
        "columns": LAKEHOUSE_CSV_COLUMNS,
    }


def upload_allocations_to_lakehouse(req: LakehouseUploadRequest) -> dict:
    """อัปโหลด CSV ไป OneLake (ใช้เมื่อเปิด ingest อัตโนมัติในอนาคต)"""
    content, fname, df = prepare_lakehouse_csv(req)
    batch_id = str(uuid.uuid4())
    uploaded_at = datetime.now(timezone.utc).isoformat()

    prefix = (os.environ.get("ONELAKE_UPLOAD_DIR") or "Files/target_allocation_uploads").strip()
    prefix = prefix.strip("/").replace("\\", "/")
    if prefix.lower().startswith("files/"):
        prefix = prefix[6:]

    remote_path = f"{prefix}/{fname}"

    token = _get_storage_token()
    _upload_bytes_to_onelake(remote_path, content, token)

    logger.info(
        "uploaded TGA-format allocations to OneLake: %s (%d rows) batch=%s",
        remote_path,
        len(df),
        batch_id,
    )
    return {
        "status": "ok",
        "rows": int(len(df)),
        "remote_path": remote_path,
        "upload_batch_id": batch_id,
        "uploaded_at_utc": uploaded_at,
        "columns": LAKEHOUSE_CSV_COLUMNS,
    }
