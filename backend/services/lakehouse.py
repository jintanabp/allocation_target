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
from . import no_target_store
from . import warehouse_pin_rules_store

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
    """
    ค่า dim ที่ normalize แล้วเหมือนตอนเขียนลงไฟล์ — ใช้เทียบคีย์ upsert

    **WAREHOUSECODE อยู่ในคีย์แล้ว** ตั้งแต่ 7 ก.ย. 2026 เจ้าของ Target Sun เพิ่มเข้าไป
    เองบน production เพราะตั้งใจให้เก็บเป้าแยกรายคลังจริง ๆ (ก่อนหน้านั้นคีย์มีแค่ 6
    คอลัมน์ ทำให้สองแถวที่ต่างกันแค่คลังชนกันจนยอดปลายทางเกินไฟล์ทุกครั้ง)

    คอลัมน์ warehouse_code อาจไม่มีในบางเฟรมที่ยังไม่ผ่านการเติม dim — ถือเป็นค่าว่าง
    ซึ่งก็เป็นค่าคีย์ที่ถูกต้องค่าหนึ่ง ไม่ใช่ข้อผิดพลาด
    """
    wh = (
        g["warehouse_code"].map(_cell_str)
        if "warehouse_code" in g.columns
        else pd.Series([""] * len(g), index=g.index)
    )
    return [
        g["salestype"].map(_cell_str),
        g["divisioncode"].map(_cell_str),
        g["areacode"].map(_areacode_str),
        g["provincecode"].map(_cell_str),
        wh,
    ]


def _collapse_grain_duplicate_keys(grp: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """
    ยุบแถว grain ของคู่ (พนักงาน×สินค้า) ที่คีย์ตรงกันให้เหลือแถวเดียว แล้วรวม qty

    คีย์ upsert ของ Target Sun คือ PRODUCTCODE+SALESTYPE+DIVISIONCODE+SALESMANCODE
    +AREACODE+PROVINCECODE **+WAREHOUSECODE** — สองแถวที่ต่างกันที่คลังจึงเป็น
    "คนละแถว" ต้องส่งแยกกัน ห้ามยุบรวม ไม่งั้นคลังหนึ่งได้เป้าทั้งก้อนและอีกคลัง
    ค้างเลขเดิม

    ที่ยังต้องมีตัวยุบอยู่ เพราะ grain อาจมีแถวที่ **เหมือนกันทุกคอลัมน์รวมทั้งคลัง**
    (เช่นแคชจากสองงวดที่ทับกัน) แถวแบบนั้นเป็นคีย์ซ้ำจริง ตัวนำเข้าจะข้ามแถวหลังทิ้ง
    ต้องบวก qty รวมเป็นแถวเดียวก่อนส่ง

    ประวัติ: ก่อน 7 ก.ย. 2026 คีย์ไม่มี WAREHOUSECODE ตรงนี้จึงยุบแถวที่ต่างกันแค่คลัง
    ทิ้งด้วย ซึ่งถูกต้องกับกติกาตอนนั้น (พบในแคช 15/78 ไฟล์) พอเจ้าของระบบเพิ่มคลัง
    เข้าคีย์แล้ว การยุบแบบนั้นกลายเป็นสิ่งที่ทำให้ยอดไม่ตรงเสียเอง
    """
    n = len(grp)
    if n < 2:
        return grp, 0
    g = grp.copy()
    kcols = ["_k_st", "_k_div", "_k_area", "_k_prov", "_k_wh"]
    (
        g["_k_st"],
        g["_k_div"],
        g["_k_area"],
        g["_k_prov"],
        g["_k_wh"],
    ) = _dim_key_series(g)
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

    **WAREHOUSECODE คิดแยกจากตัวอื่น** (เพิ่ม 11 ก.ย. 2026) — มันอยู่ในคีย์ upsert
    ตั้งแต่ 7 ก.ย. แต่ไม่ใช่ "คุณสมบัติของพนักงาน" แบบเขต/ดิวิชัน คนหนึ่งขายหลายคลังได้
    จึงเดาเฉพาะเมื่อทุกแถวของเขาใช้คลังเดียวกันหมด และ**ขัดกันแล้วต้องไม่ทิ้งทั้งคน**
    เหมือน dim ตัวอื่น (ไม่งั้น SKU ของคนที่ขายหลายคลังจะถูกตัดทิ้งไปด้วย)

    ของจริงที่ทำให้ต้องเพิ่ม: SL376 พนักงาน B033 มีเป้าที่ปลายทาง 246 แถว ใช้คลัง
    R082 ทุกแถว แต่แถวใหม่ที่เราสร้างให้เขาได้คลังว่าง — เพราะคีย์รวมคลัง คู่เดียวกัน
    จึงกลายเป็นสองแถวที่ปลายทางได้ (วัดจากชุดพัฒนา 11 ก.ย.: ~7,800 แถวเป็นแบบนี้)

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
        # คลังคิดหลังด่านข้างบน และไม่มีสิทธิ์ทำให้ทั้งคนตกไป — ขัดกัน = ไม่เดา เท่านั้น
        wh_vals = {
            _cell_str(v) for v in grp.get("warehouse_code", pd.Series(dtype=str))
        }
        wh_vals.discard("")
        dims["warehouse_code"] = next(iter(wh_vals)) if len(wh_vals) == 1 else ""
        out[emp_key] = dims
    return out


def _drop_rows_of_reassigned_employees(
    rows: list[dict], sup_id: str
) -> tuple[list[dict], set[str]]:
    """
    ตัดแถวของพนักงานที่ถูกย้ายไปให้ทีมอื่นเกลี่ยเป้าแล้ว ออกจากแผนของทีมนี้

    แผนกระจายที่บันทึกไว้ "ก่อน" การย้ายยังมีคนคนนั้นอยู่ · ถ้าปล่อยให้ส่ง
    เป้าของเขาจะถูกเขียนทับด้วยตัวเลขจากแผนเก่า แล้วแต่ว่าใครกดส่งทีหลัง —
    ทั้งสองรอบส่งสำเร็จเหมือนกันหมด ไม่มีอะไรฟ้อง เพราะปลายทางรับ upsert

    ตัดที่ตัวสร้างไฟล์เพราะทั้งการส่งจริงและการดาวน์โหลด Excel ผ่านทางนี้ทางเดียว
    ตัวไหนที่หายไปจะทำให้ยอดไม่ตรงเป้าทีม แล้วด่านเทียบเป้า (S1) ฟ้องเองอยู่แล้ว
    ผู้ใช้จึงถูกบังคับให้โหลดใหม่ ซึ่งเป็นสิ่งที่ควรทำพอดี
    """
    sid = str(sup_id or "").strip().upper()
    if not rows:
        return rows, set()
    try:
        from .emp_assignment_store import moved_away_from

        away = moved_away_from(sid)
    except Exception as e:
        logger.warning("อ่านรายการย้ายพนักงานตอนส่งไม่ได้ (%s): %s", sid, e)
        return rows, set()
    if not away:
        return rows, set()
    kept: list[dict] = []
    dropped: set[str] = set()
    for r in rows:
        emp = norm_emp_code(r.get("emp_id"))
        if emp in away:
            dropped.add(emp)
            continue
        kept.append(r)
    if dropped:
        logger.warning(
            "ไม่ส่งแถวของพนักงานที่ย้ายไปทีมอื่นแล้ว %d คน จากแผนของ %s: %s",
            len(dropped), sid, sorted(dropped)[:10],
        )
    return kept, dropped


def employee_teams_in_period(month: int, year: int, emp_ids) -> dict[str, set[str]]:
    """
    พนักงานแต่ละคนอยู่ทีมไหนบ้างในงวดนี้ — จากไฟล์ grain ของทุกทีม + การย้ายทีม

    ใช้ตรวจตอนส่ง Target Sun ว่าพนักงานทุกคนในคำขออยู่ในทีมที่ผู้ส่งมีสิทธิ์
    (ผลตรวจ 28 ก.ย. 2026 §1.3) — ไฟล์ grain ชื่อ tga_lines_{SL}_{Y}_{MM}.csv
    จึงบอกทีมได้จากชื่อไฟล์ · คนที่ถูกย้ายไปเกลี่ยที่ทีมอื่น (emp_assignments) นับทีมปลายทางด้วย
    """
    from . import emp_assignment_store

    want = {norm_emp_code(e) for e in (emp_ids or []) if str(e).strip()}
    out: dict[str, set[str]] = {e: set() for e in want}
    if not want:
        return out
    suffix = f"_{int(year):04d}_{int(month):02d}.csv"
    try:
        names = os.listdir("data")
    except OSError:
        names = []
    # tga_lines_ = แถวเป้าเดิมใน Target Sun · emp_cache_ = รายชื่อทีมจากขั้นที่ 1
    # (คนที่เพิ่งย้ายซุปมาอาจมีแถวเป้าอยู่แค่ทีมเก่า แต่รายชื่ออยู่ทีมใหม่แล้ว)
    for prefix in ("tga_lines_", "emp_cache_"):
        for name in names:
            if not (name.startswith(prefix) and name.endswith(suffix)):
                continue
            sup = name[len(prefix):-len(suffix)].strip().upper()
            try:
                # ผลตรวจ §5.1-3: emp_cache_ ถูกเขียนแบบ atomic ใต้ lock ต่อ path แล้ว
                # reader ต้องจับ lock เดียวกัน ไม่งั้นบน Windows ตัวเขียนพัง PermissionError
                _p = os.path.join("data", name)
                with read_locked(_p):
                    dg = pd.read_csv(_p, dtype=str, keep_default_na=False, usecols=["emp_id"])
            except Exception:
                continue
            for e in {norm_emp_code(x) for x in dg["emp_id"]} & want:
                out[e].add(sup)
    try:
        for r in emp_assignment_store.read_rows():
            e = norm_emp_code(r.get("emp_id"))
            if e in want and r.get("to_sup"):
                out[e].add(str(r["to_sup"]).strip().upper())
    except Exception as ex:
        logger.warning("อ่านรายการย้ายทีมไม่ได้: %s", ex)
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
            "(เหมือนกันทุกคอลัมน์รวมทั้ง WAREHOUSECODE — คีย์ซ้ำจริง)",
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

    คีย์รวม WAREHOUSECODE แล้ว (ดู _dim_key_series) — แถวคนละคลังจึงรอดออกไปเป็น
    คนละบรรทัดในไฟล์ ซึ่งตรงกับกติกา "Duplicate keys within the same file are skipped"
    ของฝั่งนำเข้าพอดี เพราะมันไม่ใช่คีย์ซ้ำอีกต่อไป
    """
    if df.empty:
        return df, 0
    d = df.copy().reset_index(drop=True)
    d["_ord"] = range(len(d))
    kcols = ["_k_sku", "_k_emp", "_k_st", "_k_div", "_k_area", "_k_prov", "_k_wh"]
    d["_k_sku"] = d["sku"].astype(str).str.strip()
    d["_k_emp"] = d["emp_id"].astype(str).str.strip()
    (
        d["_k_st"],
        d["_k_div"],
        d["_k_area"],
        d["_k_prov"],
        d["_k_wh"],
    ) = _dim_key_series(d)

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


def _apply_wh_hints(
    df: pd.DataFrame, rows_raw: list[dict], *, trust_existing: bool = False
) -> pd.DataFrame:
    """trust_existing=True — คลังใน df resolve จาก grain มาครบแล้ว (รวมคลังว่างจริง)
    ห้ามเอาค่าจาก request (rows_raw) มาทับอีก มิฉะนั้นคลังว่างจริงของปลายทางจะถูกแทนที่
    ด้วยค่าเดา แล้วคีย์ upsert ที่ Target Sun ใช้จะมองว่าเป็นคนละแถว (ดู SL380/530/525)"""
    if "warehouse_code" not in df.columns:
        df = df.copy()
        df["warehouse_code"] = ""
    else:
        df = df.copy()
    if not trust_existing:
        wh_hint: dict[str, str] = {}
        for r in rows_raw:
            emp = str(r.get("emp_id") or "").strip()
            wh = _cell_str(r.get("warehouse_code"))
            if emp and wh:
                wh_hint[emp] = wh
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

    def _grain_row(r, b: int) -> dict:
        return {
            "emp_id": e,
            "sku": sku,
            "allocated_boxes": int(b),
            "salestype": _cell_str(r.get("salestype", "")),
            "divisioncode": _cell_str(r.get("divisioncode", "")),
            "areacode": _areacode_str(r.get("areacode", "")),
            "provincecode": _cell_str(r.get("provincecode", "")),
            "warehouse_code": _cell_str(r.get("warehouse_code", "")),
        }

    def _spread(rows: pd.DataFrame, boxes: int) -> None:
        """แตกหีบลงแถว grain ตามสัดส่วนเป้าเดิม — แถวเป้า 0 ได้ 0 (ให้ครบ dim ตอนนำเข้า)
        ถ้าทุกแถวเป็น 0 เกลี่ยเท่า ๆ กัน"""
        pos = rows[rows["qty"] > 0]
        if pos.empty:
            for (_, r), b in zip(rows.iterrows(), _integer_split_by_weights([1.0] * len(rows), boxes)):
                out.append(_grain_row(r, b))
            return
        for (_, r), b in zip(pos.iterrows(), _integer_split_by_weights(pos["qty"].astype(float).tolist(), boxes)):
            out.append(_grain_row(r, b))
        for _, r in rows[rows["qty"] <= 0].iterrows():
            out.append(_grain_row(r, 0))

    def _boxes(arow) -> int:
        v = pd.to_numeric(arow.get("allocated_boxes", 0), errors="coerce")
        return 0 if pd.isna(v) else int(round(float(v)))

    has_wh = "warehouse_code" in df_alloc.columns
    for (e, sku), grp in df_alloc.groupby(
        [df_alloc["emp_id"].astype(str).str.strip(), df_alloc["sku"].astype(str).str.strip()],
        sort=False,
    ):
        sub = grain_lookup.get((e, sku), pd.DataFrame())

        if sub.empty:
            # ไม่พบใน cache → เก็บบรรทัดเดิมให้ชั้นถัดไปเติม dim จาก Fabric
            # (หรือเติมจากแถวอื่นของพนักงานคนเดียวกัน เมื่อเปิด infer_missing_dims)
            # คู่ใหม่ที่ไม่เคยมีเป้าใน TGA เลยสักแถว — ไม่มีคลังจริงให้เชื่อ ผู้ใช้ตัดสินใจ
            # (22 ก.ย. 2026) ว่า "คู่ใหม่ควรได้คลังว่างไว้ก่อนดีกว่า" แทนที่จะเดาจากคลังของ
            # แถวอื่นของพนักงานคนเดียวกัน หรือจากประวัติขาย 2 ปี (wh_req เดิม) — ว่างไว้ชัดเจน
            # ดีกว่าเดาผิดแล้วชนคีย์ upsert (รวม WAREHOUSECODE) ภายหลังจนเป้าพองซ้อน
            inferred = emp_dims.get(e) if emp_dims else None
            out.append(
                {
                    "emp_id": e,
                    "sku": sku,
                    "allocated_boxes": sum(_boxes(a) for _, a in grp.iterrows()),
                    "salestype": inferred["salestype"] if inferred else "",
                    "divisioncode": inferred["divisioncode"] if inferred else "",
                    "areacode": inferred["areacode"] if inferred else "",
                    "provincecode": inferred["provincecode"] if inferred else "",
                    "warehouse_code": "",
                    "dims_inferred": bool(inferred),
                }
            )
            continue

        # หน้าจอแยกคลัง (พนักงาน wh_split) ส่งคลังมาด้วย — หีบรายคลังที่ผู้ใช้กระจาย/แก้มือต้อง
        # ลงคลังนั้นตรง ๆ (ผู้ใช้ขอ 30 ก.ย. 2026) เดิมไม่สนคลังในคำขอ แตกทุกแถวตามสัดส่วน grain
        # ใหม่ จอ W1=8/W2=2 จึงถูกส่งเป็น 5/5 · ใช้เฉพาะคลังที่มีอยู่ในเป้าปัจจุบัน (grain) ของคู่นี้
        # คลังที่ grain ไม่มี = ไม่เชื่อ (กันแถวซ้อน) ไปลงแถวที่ไม่มีใครระบุแทน
        sub_wh = sub["warehouse_code"].map(_cell_str)
        grain_whs = set(sub_wh)
        req_wh = (
            [_cell_str(w) for w in grp["warehouse_code"]] if has_wh else [""] * len(grp)
        )
        split_mode = any(w and w in grain_whs for w in req_wh)
        if not split_mode:
            _spread(sub, sum(_boxes(a) for _, a in grp.iterrows()))
            continue

        matched = {w for w in req_wh if w in grain_whs}
        touched = pd.Series(False, index=sub.index)
        for (_, arow), w in zip(grp.iterrows(), req_wh):
            if w in grain_whs:
                mask = sub_wh.eq(w)
            else:
                mask = ~sub_wh.isin(matched)
                if not mask.any():
                    mask = pd.Series(True, index=sub.index)
            touched |= mask
            _spread(sub[mask], _boxes(arow))
        # แถวของคู่นี้ที่ไม่มีใครระบุ ส่ง 0 ทับ — ไม่งั้นค้างเลขเก่าใน Target Sun เป้าเบิ้ล
        for _, r in sub[~touched].iterrows():
            out.append(_grain_row(r, 0))

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
                    "warehouse_code": _cell_str(r.get("warehouse_code", "")),
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


def _clear_no_target_employees_in_tga(
    df: pd.DataFrame,
    sup_id: str,
    *,
    dg: pd.DataFrame | None = None,
    only_skus: set[str] | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """
    ล้างเป้าเดิมของคนใน「ไม่ต้องตั้งเป้า」ที่ยังค้างอยู่ใน Target Sun

    only_skus — ส่งแยกแบรนด์/เฉพาะบางสินค้า: ล้างเฉพาะ SKU ในรอบส่งนี้เท่านั้น
      เดิมล้างทุก SKU ของคนนั้นเสมอ ส่งแบรนด์เดียวแล้วไปเขียนแถว 0 ให้ SKU แบรนด์อื่น
      ด่านยอดรวมทั้งชุดกับตัวตรวจหลังส่งจึงฟ้องผิดทั้งที่ข้อมูลถูก (ผลตรวจ §2.1)
      None = ส่งเต็ม ล้างได้ทุก SKU

    คนกลุ่มนี้ถูกตัดออกตั้งแต่ตอนกระจาย (`_drop_no_target_employees`) จึงไม่มีแถวใน
    ผลกระจาย → ไม่มีอะไรถูกส่ง → **Target Sun ยังถือเป้าของงวดก่อนไว้เหมือนเดิม**
    ผลคือคนที่ตั้งใจให้ไม่มีเป้า กลับมีเป้าค้างอยู่ปลายทาง (ค้างมาตั้งแต่ 26 ส.ค. 2026)

    ปลายทางลบแถวจากฝั่งเราไม่ได้ ทางเดียวที่ล้างได้คือ **ส่งหีบ 0 ไปทับ** — กลไก
    เดียวกับแถวหีบ 0 ปกติ · ใช้ dim จาก grain ของ TGA เท่านั้น (คือเป้าที่ปลายทาง
    มีอยู่จริงตอนนี้) จึงไม่มีทางไปสร้างแถวใหม่ให้ใคร ถ้าเขาไม่เคยมีเป้าก็ไม่มีอะไรให้ล้าง

    ขอบเขต: เฉพาะรายชื่อของทีม `sup_id` ที่กำลังส่งเท่านั้น — ไม่ไปยุ่งกับคนของทีมอื่น
    ที่อาจติดมาในคำขอเดียวกันตอนกระจายรวมภาค (ทีมนั้นจะล้างของตัวเองตอนถึงคิวส่ง)
    """
    sup_key = no_target_store.norm_sup(sup_id)
    if not sup_key or dg is None or dg.empty or "emp_id" not in dg.columns:
        return df, []
    try:
        blocked = no_target_store.no_target_emp_ids(sup_key)
    except Exception as e:
        logger.error("อ่านรายชื่อไม่ต้องตั้งเป้าไม่ได้ — ไม่ล้างแถวค้างรอบนี้: %s", e)
        return df, []
    if not blocked:
        return df, []

    present = set(
        zip(
            df["emp_id"].astype(str).str.strip(),
            df["sku"].astype(str).str.strip(),
        )
    )
    extra: list[dict] = []
    cleared: set[str] = set()
    for _, r in dg.iterrows():
        emp = no_target_store.norm_emp(r.get("emp_id"))
        sku = str(r.get("sku") or "").strip()
        if not emp or not sku or emp not in blocked:
            continue
        if only_skus is not None and sku not in only_skus:
            continue
        if (emp, sku) in present:
            # มีแถวอยู่ในผลกระจายแล้ว (เช่นผู้ใช้ปลดออกจากรายชื่อกลางคัน) — อย่าไปทับ
            continue
        extra.append(
            {
                "emp_id": emp,
                "sku": sku,
                "allocated_boxes": 0,
                "salestype": _cell_str(r.get("salestype", "")),
                "divisioncode": _cell_str(r.get("divisioncode", "")),
                "areacode": _areacode_str(r.get("areacode", "")),
                "provincecode": _cell_str(r.get("provincecode", "")),
                "warehouse_code": _cell_str(r.get("warehouse_code", "")),
            }
        )
        cleared.add(emp)
    if not extra:
        return df, []
    logger.info(
        "ล้างเป้าค้างของคนไม่ต้องตั้งเป้า %s: %d คน %d แถว (ส่งหีบ 0 ไปทับ)",
        sup_key,
        len(cleared),
        len(extra),
    )
    return pd.concat([df, pd.DataFrame(extra)], ignore_index=True), sorted(cleared)


def _key_of_row(emp, sku, salestype, division, area, province, warehouse) -> str:
    """คีย์เต็มแบบเดียวกับ import_row_key_series — ใช้เทียบแถวในไฟล์กับแถวที่ต้องล้าง"""
    return "|".join([
        str(sku or "").strip(), str(emp or "").strip(), _cell_str(salestype), _cell_str(division),
        _areacode_str(area), _cell_str(province), _cell_str(warehouse),
    ])


def _clear_leftover_rows_outside_round(
    df: pd.DataFrame, sup_id: str, month: int, year: int,
) -> tuple[pd.DataFrame, int]:
    """
    ส่ง 0 ไปทับทุกแถวของทีมที่ Target Sun จะ "ค้าง" ไว้หลังส่งรอบนี้ (ผลตรวจ 6 ต.ค. 2026 ก1/ก2)

    ด่านทุกด่านตรวจว่า "ไฟล์ที่ส่ง = เป้า" แต่ Target Sun เก็บแถวเก่าที่ไฟล์ไม่ได้ทับไว้เสมอ
    ยอดในระบบปลายทางจึงเป็น "ไฟล์ + แถวค้าง" — สองทางที่เคยหลุด:
      ก1 พนักงานที่มีแถวเป้าเดิมแต่ไม่ได้อยู่ในรอบนี้เลย (เช่นเป้าเงิน 0 เพราะสินค้าไม่มีราคา)
         ตัวล้างเดิม (_clear_stale_employee_sku_rows_in_tga) ดูเฉพาะคนที่อยู่ในไฟล์
         จำลอง: เป้า A=10 → หลังส่ง A=14
      ก2 แถวที่ "เราสร้างเอง" ตอนส่งรอบก่อน (ไม่อยู่ใน grain ขั้นที่ 1) แล้วรอบนี้คู่นั้นได้ 0
         ถูกตัดทิ้งเป็น "แถวใหม่เปล่า" — จำลอง: เป้า B=4 → หลังส่งรอบสอง B=7

    แหล่งแถวที่อาจค้าง: grain ของทีมนี้เอง (แถวเป้าตอนโหลดขั้นที่ 1) + sent ledger (ทุกคีย์ที่เคยส่ง
    ของทีม×งวดนี้) · ดูเฉพาะสินค้าที่อยู่ในรอบนี้ (ส่งแยกแบรนด์ = ไม่แตะสินค้าแบรนด์อื่น)
    **ไม่แตะ** คู่พนักงาน×สินค้าที่อยู่ในไฟล์แล้ว (คนละคลัง = ด่านคลังซ้ำถามผู้ใช้เอง) และ
    พนักงานที่อยู่หลายทีมในงวดนี้ (แถวของเขาอาจเป็นของอีกทีม) · แถว 0 ไม่เปลี่ยนยอดในไฟล์
    """
    if df is None or df.empty:
        return df, 0
    sid = str(sup_id or "").strip().upper()
    round_skus = set(df["sku"].astype(str).str.strip()) - {""}
    if not round_skus:
        return df, 0
    present_pairs = set(zip(df["emp_id"].astype(str).str.strip(), df["sku"].astype(str).str.strip()))

    cands: dict[str, dict] = {}

    def _add(emp, sku, st, dv, ar, pv, wh):
        emp, sku = str(emp or "").strip(), str(sku or "").strip()
        if not emp or not sku or sku not in round_skus or (emp, sku) in present_pairs:
            return
        k = _key_of_row(emp, sku, st, dv, ar, pv, wh)
        cands.setdefault(k, {
            "emp_id": emp, "sku": sku, "allocated_boxes": 0,
            "salestype": _cell_str(st), "divisioncode": _cell_str(dv), "areacode": _areacode_str(ar),
            "provincecode": _cell_str(pv), "warehouse_code": _cell_str(wh),
        })

    try:
        dg = _read_tga_grain_cache(sid, int(month), int(year))
    except Exception:
        dg = pd.DataFrame()
    if dg is not None and not dg.empty and {"emp_id", "sku"} <= set(dg.columns):
        for r in dg.to_dict("records"):
            _add(r.get("emp_id"), r.get("sku"), r.get("salestype", ""), r.get("divisioncode", ""),
                 r.get("areacode", ""), r.get("provincecode", ""), r.get("warehouse_code", ""))
    try:
        from . import sent_ledger

        from .targetsun_endpoints import targetsun_endpoints_summary

        led = sent_ledger.read_ledger(sid, int(month), int(year)) or {}
        # ledger ของปลายทางอื่น (เช่น UAT) ไม่ใช่แถวที่อยู่ในระบบที่จะส่งตอนนี้ — ส่ง 0 ไปคีย์นั้น
        # = สร้างแถวใหม่เปล่าใน Prod (ผลตรวจ 7 ต.ค. 2026 ข8) · เงื่อนไขเดียวกับ _own_sent_boxes_by_sku
        _url = str(targetsun_endpoints_summary().get("import_url") or "")
        # ledger เก่าที่ไม่ได้จดปลายทาง (ก่อน 1 ต.ค.) = ทำแบบเดิม
        if _url and led.get("import_url") and led.get("import_url") != _url:
            led = {}
    except Exception:
        led = {}
    for k, v in (led.get("rows") or {}).items():
        try:
            if int((v or {}).get("qty") or 0) <= 0:
                continue
        except (TypeError, ValueError):
            continue
        parts = (str(k).split("|") + [""] * 7)[:7]
        sku, emp, st, dv, ar, pv, wh = parts
        _add(emp, sku, st, dv, ar, pv, wh)

    if not cands:
        return df, 0
    teams = employee_teams_in_period(int(month), int(year), {c["emp_id"] for c in cands.values()})
    # คนที่ถูกย้ายทีม: ทีมของเขาคือ "ทีมปลายทาง" ทีมเดียว (ผลตรวจ 7 ต.ค. 2026 ก3)
    # เดิมนับเป็น "อยู่หลายทีม" เสมอ (แคชรายชื่อทีมต้นทางเก็บรายชื่อดิบไว้) จึงไม่เคยถูกล้าง
    # → คนย้ายที่ไม่อยู่ใน payload (เช่น เงิน 0) หีบเก่าค้างใน TS ทั้งที่ถูกแจกให้เพื่อนแล้ว
    try:
        from . import emp_assignment_store

        moved_to = {
            norm_emp_code(r.get("emp_id")): str(r.get("to_sup") or "").strip().upper()
            for r in emp_assignment_store.read_rows()
            if r.get("to_sup")
        }
    except Exception as ex:
        logger.warning("อ่านรายการย้ายทีมไม่ได้ (ล้างแถวค้าง): %s", ex)
        moved_to = {}
    for e in list(teams):
        if e in moved_to:
            teams[e] = {moved_to[e]}
    shared = {e for e, ts in teams.items() if len(ts) > 1 or (ts and sid not in ts)}
    rows = [c for c in cands.values() if norm_emp_code(c["emp_id"]) not in shared]
    if shared:
        logger.warning("ไม่ล้างแถวค้างของพนักงานที่อยู่หลายทีม/ย้ายไปทีมอื่นแล้ว %s: %s", sid, sorted(shared)[:20])
    if not rows:
        return df, 0
    logger.info(
        "ล้างแถวค้างนอกรอบนี้ %s: %d แถว %d คน (ส่งหีบ 0 ไปทับ — กันยอดใน Target Sun เกินเป้า)",
        sid, len(rows), len({r["emp_id"] for r in rows}),
    )
    return pd.concat([df, pd.DataFrame(rows)], ignore_index=True), len(rows)


def _clear_stale_employee_sku_rows_in_tga(
    df: pd.DataFrame,
    sup_id: str,
    *,
    dg: pd.DataFrame | None = None,
    full_send: bool,
) -> tuple[pd.DataFrame, int]:
    """
    ล้างแถวเป้าเก่าที่ "หลุดจากผลกระจายรอบนี้" แต่ยังค้างอยู่ใน Target Sun (ค8)

    คนละกรณีกับ _clear_no_target_employees_in_tga ด้านบน (ที่นั่นคือคนทั้งคนไม่มีแถว
    เลยเพราะอยู่ในบัญชีดำ「ไม่ต้องตั้งเป้า」) — ที่นี่คือ "พนักงานคนนี้ยังอยู่ในรอบส่งนี้จริง
    (มีอย่างน้อย 1 แถว) แต่ SKU บางตัวที่เขาเคยมีเป้า (เห็นใน grain ของ TGA) ไม่อยู่ใน
    รอบนี้แล้ว" เช่นทีมเลิกตั้งเป้า SKU นั้น หรือพนักงานคนนั้นไม่มีเป้าเงินของ SKU นั้นอีก
    ต่อไป — Target Sun ยังถือเลขงวดก่อนไว้เพราะไม่มีใครเคยส่ง 0 ไปทับ

    full_send=False (ส่งเฉพาะแบรนด์/สินค้าบางตัวผ่าน brand_filter/sku_filter) ต้อง
    **ไม่ทำอะไรเลย** — SKU ที่ไม่อยู่ใน payload รอบนั้นอาจแค่ "ไม่ได้เลือกส่งรอบนี้" ไม่ใช่
    "หมดเป้าแล้ว" ถ้าไปทับจะล้างเป้าที่ยังถูกต้องของ SKU อื่นทั้งหมดโดยไม่ตั้งใจ — ผู้เรียก
    ต้องส่ง full_send=True เฉพาะตอนที่ brand_filter=="ALL" และ sku_filter ว่าง เท่านั้น

    จับคู่แค่ระดับ (emp, sku) เหมือน _clear_no_target_employees_in_tga — ไม่แยกตามคลัง
    แม้คีย์ upsert จะรวม WAREHOUSECODE แล้วก็ตาม (ยังผูกกับคำถามที่ยังไม่มีคำตอบ ข7:
    แถวคลังว่างกับแถวมีคลังของคู่เดียวกัน ถือเป็นคนละเป้าไหม) — ถ้าพนักงานมีแถวของ SKU นี้
    อยู่ในรอบนี้แล้วไม่ว่าคลังไหน ถือว่า "ยังมีเป้า" ไม่ล้าง

    **ต้องเป็น SKU ที่อยู่ใน "จักรวาล SKU ของรอบนี้" ด้วย** (มีแถวของ SKU นั้นอยู่ใน df
    ไม่ว่าจะเป็นของพนักงานคนไหนก็ตาม) ไม่ใช่แค่ "ไม่อยู่ในแถวของพนักงานคนนี้" — พนักงาน
    ทีมอื่นที่ติดมาในโหมดรวมภาค/หน่วย (grain ถูกเติมข้ามทีมมาให้มีครบ dim) มักมี SKU อื่น
    ในประวัติ/เป้าของทีมตัวเองที่ไม่เกี่ยวอะไรกับรอบส่งนี้เลย (คนละ SKU กับที่ทีมเจ้าของ
    ก้อนกำลังตั้งเป้าอยู่) ถ้าไม่กันตรงนี้ไว้จะกลายเป็นสร้างแถว 0 ปลอมให้ SKU ที่รอบนี้
    ไม่เคยแตะเลยสักที (พบจากเทสจริง test_grain_across_teams.py /
    test_unit_wide_allocation.py ตอนพัฒนา)
    """
    if not full_send or dg is None or dg.empty or "emp_id" not in dg.columns or df.empty:
        return df, 0

    present_emps = set(df["emp_id"].astype(str).str.strip()) - {""}
    if not present_emps:
        return df, 0
    round_skus = set(df["sku"].astype(str).str.strip()) - {""}
    if not round_skus:
        return df, 0

    present_pairs = set(
        zip(
            df["emp_id"].astype(str).str.strip(),
            df["sku"].astype(str).str.strip(),
        )
    )
    extra: list[dict] = []
    touched: set[str] = set()
    for _, r in dg.iterrows():
        emp = str(r.get("emp_id") or "").strip()
        sku = str(r.get("sku") or "").strip()
        if not emp or not sku or emp not in present_emps or sku not in round_skus:
            continue
        if (emp, sku) in present_pairs:
            continue
        extra.append(
            {
                "emp_id": emp,
                "sku": sku,
                "allocated_boxes": 0,
                "salestype": _cell_str(r.get("salestype", "")),
                "divisioncode": _cell_str(r.get("divisioncode", "")),
                "areacode": _areacode_str(r.get("areacode", "")),
                "provincecode": _cell_str(r.get("provincecode", "")),
                "warehouse_code": _cell_str(r.get("warehouse_code", "")),
            }
        )
        touched.add(emp)
    if not extra:
        return df, 0
    logger.info(
        "ล้างแถวเป้าเก่าที่หลุดจากผลกระจายรอบนี้ %s: %d คน %d แถว (ส่งหีบ 0 ไปทับ)",
        str(sup_id or "").strip().upper(),
        len(touched),
        len(extra),
    )
    return pd.concat([df, pd.DataFrame(extra)], ignore_index=True), len(extra)


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


def _targetsun_boxes_now_by_sku(sup_id: str, month: int, year: int) -> dict[str, int]:
    """
    เป้าที่ Target Sun "ถืออยู่ตอนนี้" ต่อ SKU ของทีมนี้ — อ่านจากแคช grain ขั้นที่ 1

    ใช้ตอน SKU ถูกตัดทั้งตัว: แถวของ SKU นั้นจะไม่ถูกส่งเลยสักแถว **รวมทั้งแถวหีบ 0
    ที่ตั้งใจไปล้างเป้าเดิม** เลขงวดก่อนจึงค้างอยู่ปลายทางทั้งก้อน
    ถ้าไม่บอกตัวเลขนี้ คนที่ไปเพิ่มมือใน Target Sun จะเติมแต่ "ส่วนที่ขาด"
    แล้วเลขเก่าบวกทับอยู่ดี — ยอดรวมของ SKU นั้นจึงไม่มีวันตรงเป้า
    """
    try:
        dg = _read_tga_grain_cache(sup_id, month, year)
    except Exception as e:
        logger.warning("อ่านแคช grain เพื่อรายงานเป้าปลายทางไม่ได้ (%s): %s", sup_id, e)
        return {}
    if dg is None or dg.empty or not {"sku", "qty"} <= set(dg.columns):
        return {}
    qty = pd.to_numeric(dg["qty"], errors="coerce").fillna(0)
    return qty.groupby(dg["sku"].astype(str).str.strip()).sum().astype(int).to_dict()


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
      2. 409 ของฟังก์ชันนั้นแปลว่า "ยอดไม่ตรงเป้า" (โหมดรวมภาคยกให้ด่านยอดรวมทั้งภาค
         ตัดสินแทนตาม I7) — ปัญหา master data ต้องแยกเป็นอีกด่าน ไม่ปนกัน

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
    now_by_sku = _targetsun_boxes_now_by_sku(sup_id, month, year)

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
                # เลขที่ปลายทางถืออยู่ตอนนี้ — ตัวที่บอกว่าต้องไปแก้มือเท่าไรจริง ๆ
                "current_targetsun_boxes": now_by_sku.get(str(sku)),
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


def _live_target_row_key(row: dict) -> str:
    """
    คีย์เต็มของหนึ่งแถว Target Sun (sku + คีย์ upsert 6 ตัว) — normalize เหมือน
    ตอนสร้างไฟล์ส่งใน _build_tga_upload_dataframe เป๊ะ (คอลัมน์ out ที่นั่น: SALESTYPE/
    DIVISIONCODE/PROVINCECODE/WAREHOUSECODE ผ่าน _cell_str, AREACODE ผ่าน _areacode_str,
    SALESMANCODE ผ่าน norm_emp_code) ต้องใช้ฟังก์ชัน normalize ชุดเดียวกันทั้งสองฝั่ง
    ไม่งั้นสองฝั่ง drift แล้วฟ้องเท็จตั้งแต่วันแรก
    """
    return "|".join(
        [
            str(row.get("PRODUCTCODE") or "").strip(),
            norm_emp_code(row.get("SALESMANCODE")),
            _cell_str(row.get("SALESTYPE")),
            _cell_str(row.get("DIVISIONCODE")),
            _areacode_str(row.get("AREACODE")),
            _cell_str(row.get("PROVINCECODE")),
            _cell_str(row.get("WAREHOUSECODE")),
        ]
    )


def _live_target_snapshot(
    sup_id: str, month: int, year: int, emp_codes: list[str]
) -> dict[str, Any] | None:
    """
    อ่านสด 1 ครั้งจาก Target Sun — คืนทั้งยอดหีบต่อ SKU, จำนวนแถว, และชุดคีย์ระดับแถว

    เดิมมีแต่ยอดหีบต่อ SKU (_live_target_boxes_by_sku) ซึ่งจับไม่ได้เลยถ้าแถวซ้ำ
    คนละคลังสองแถวบวกกันแล้วยอดยังเท่าของเดิม (บั๊กจริงที่เคยเกิด — ดู
    verify_row_count_after_send) จึงต้องเก็บจำนวนแถว/คีย์ไว้ตั้งแต่อ่านครั้งแรก
    กันต้องยิง Target Sun ซ้ำสองรอบเวลาผู้เรียกต้องใช้ทั้งสองอย่าง
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
        if result.get("complete") is False:
            # อ่านไม่ครบ = นับแถวไม่ได้ — ห้ามเอาตัวเลขที่ขาดไปเทียบก่อน/หลังส่ง
            logger.warning("อ่านเป้าจาก Target Sun ได้ไม่ครบ (%s) — ถือว่าตรวจไม่ได้", sup_id)
            return None
        by_sku: dict[str, int] = {}
        keys: set[str] = set()
        qty_by_key: dict[str, int] = {}
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
            by_sku[sku] = by_sku.get(sku, 0) + qty
            k = _live_target_row_key(r)
            keys.add(k)
            qty_by_key[k] = qty_by_key.get(k, 0) + qty
        # raw_rows: แถวตามที่ Target Sun ส่งมา — เก็บเป็นสำเนาก่อนส่งไว้สร้างไฟล์คืนค่า (ts_row_snapshots)
        return {"by_sku": by_sku, "row_count": len(rows), "keys": keys, "qty_by_key": qty_by_key,
                "raw_rows": [r for r in rows if isinstance(r, dict)]}
    except Exception as e:  # อ่านไม่ได้ต้องไม่ทำให้เส้นทางหลักพัง
        logger.warning("อ่านเป้าปัจจุบันจาก Target Sun ไม่ได้ (%s): %s", sup_id, e)
        return None


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

    ตัวจริงอยู่ใน _live_target_snapshot (อ่านครั้งเดียวได้ทั้งยอดหีบ/จำนวนแถว/คีย์) —
    ฟังก์ชันนี้คง signature เดิมไว้เพราะมีที่เรียกอยู่แล้วหลายจุดและเทสต์ mock ชื่อนี้ตรง ๆ
    """
    snap = _live_target_snapshot(sup_id, month, year, emp_codes)
    return snap["by_sku"] if snap is not None else None


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


_UNSET = object()


def _own_sent_boxes_by_sku(sup_id: str, month: int, year: int) -> dict[str, int]:
    """
    ยอดหีบต่อ SKU ที่ระบบเราส่งเข้า Target Sun ครั้งล่าสุดของทีม×งวด (จาก sent ledger) — ใช้แยก
    "Target Sun เปลี่ยนเพราะเราส่งเอง" ออกจาก "เป้าต้นทางเปลี่ยน" · ledger ของปลายทางอื่น = ไม่นับ

    ใช้ได้เฉพาะเมื่อการส่งครั้งล่าสุดของทีมนี้เป็น "ส่งรวมหลายทีม" (มี send_batch_id) — ข้อยกเว้นนี้มีไว้
    ให้ส่งชุดรวมภาคซ้ำหลังล้มกลางทาง (ผลตรวจ 1 ต.ค. ข1) ถ้าครั้งล่าสุดเป็นการส่งทีมเดียว คืนว่าง
    """
    from . import sent_ledger
    from .targetsun_endpoints import targetsun_endpoints_summary

    try:
        led = sent_ledger.read_ledger(sup_id, month, year) or {}
        url = str(targetsun_endpoints_summary().get("import_url") or "")
    except Exception:
        return {}
    if not led.get("rows") or not led.get("import_url") or led.get("import_url") != url:
        return {}
    sends = led.get("sends") or []
    if not sends or not str((sends[-1] or {}).get("send_batch_id") or "").strip():
        return {}
    out: dict[str, int] = {}
    for k, v in (led.get("rows") or {}).items():
        sku = str(k).split("|")[0].strip()
        if sku:
            out[sku] = out.get(sku, 0) + int((v or {}).get("qty") or 0)
    return out


def _last_batch_send(sup_id: str, month: int, year: int) -> tuple[float, str] | None:
    """
    (เวลา, send_batch_id) ของการส่งครั้งล่าสุดของทีม×งวด ถ้าครั้งนั้นเป็น "ส่งรวมหลายทีม" ไปปลายทางเดียวกับ
    ตอนนี้ — ไม่ใช่คืน None (เงื่อนไขเดียวกับ _own_sent_boxes_by_sku: ทีมที่ได้ข้อยกเว้น「ค่าใน TS = ที่เราส่งเอง」)
    """
    from . import sent_ledger
    from .targetsun_endpoints import targetsun_endpoints_summary

    try:
        led = sent_ledger.read_ledger(sup_id, month, year) or {}
        url = str(targetsun_endpoints_summary().get("import_url") or "")
    except Exception:
        return None
    if not led.get("import_url") or led.get("import_url") != url:
        return None
    last = ((led.get("sends") or [None])[-1]) or {}
    bid = str(last.get("send_batch_id") or "").strip()
    if not bid:
        return None
    try:
        t = float(last.get("sent_at") or 0)
    except (TypeError, ValueError):
        return None
    return (t, bid) if t else None


def _target_file_mtime(sup_id: str, month: int, year: int) -> float | None:
    from ..core.paths import target_boxes_cache_path

    try:
        return os.path.getmtime(target_boxes_cache_path(str(sup_id or "").strip().upper(), int(month), int(year)))
    except OSError:
        return None


def assert_batch_snapshots_same_generation(sup_ids: list[str], month: int, year: int) -> None:
    """
    ส่งรวมภาคซ้ำ: ไฟล์เป้าทุกทีมในชุดต้องมาจาก "รุ่นเดียวกัน" เทียบกับการส่งรวมภาคครั้งก่อน
    (ผลตรวจ 7 ต.ค. 2026 ก2)

    ทำไม: ด่านเป้าเปลี่ยนยอมให้ทีมที่ไฟล์เป้าเป็นค่าก่อนส่ง (B=40) ผ่าน ถ้าค่าใน TS ตอนนี้ = ที่เราส่งเอง (30)
    ถ้าอีกทีมในชุดโหลดขั้นที่ 1 ใหม่หลังส่ง (A: ไฟล์ 70 = ค่าที่เราส่ง) ยอดเป้ารวมของชุด = 70+40 = 110
    ทั้งที่เป้าจริงทั้งภาค 100 → ทุกด่านผ่านแล้วหีบงอกเข้า TS 10 หีบ

    ตัดสินจากเวลาไฟล์เป้า (`target_boxes_`) เทียบ "เวลาส่งรวมภาคครั้งล่าสุดของทั้งชุด" (ค่าเดียวทุกทีม):
    เก่ากว่า = ไฟล์ก่อนส่งรอบล่าสุด · ใหม่กว่า = โหลดใหม่หลังส่งรอบล่าสุด · มีทั้งสองแบบในชุดเดียว = บล็อก
    ต้องใช้เวลาเดียวทั้งชุด ไม่ใช่เวลาของแต่ละทีม — ทีมที่ POST ล้มในรอบล่าสุดไม่มี ledger ของรอบนั้น
    ถ้าเทียบกับรอบก่อนของตัวเอง ส่งซ้ำหลังล้มกลางทาง (ทางที่ข้อยกเว้นตั้งใจให้ผ่าน) จะถูกบล็อกผิด
    ทีมที่ไม่เคยส่งรวมภาคเลย (ไม่มี ledger แบบชุด) ไม่ถูกนับ — ไฟล์เป้าของทีมนั้นเป็นค่าตั้งต้นอยู่แล้ว
    ปลอดภัยไว้ก่อน: เขียนไฟล์ซ้ำด้วยค่าเดิม (เช่น ปรับราคา) นับเป็น "ใหม่กว่า" ได้ = บล็อกเกิน ไม่ใช่หลุด

    คืนใน detail ด้วยว่า "รอบล่าสุดลงครบทุกทีมในชุดไหม" (ทุกทีมมี batch id ล่าสุดเดียวกัน) — ลงครบ = โหลดใหม่
    ทุกทีมได้ยอดที่ถูก · ลงไม่ครบ = โหลดใหม่ทุกทีมจะได้ยอดรวมผิด ต้องให้แอดมินตรวจก่อน (หน้าเว็บซ่อนปุ่มโหลด)
    """
    last: dict[str, tuple[float, str]] = {}
    for sid in sup_ids:
        sid = str(sid or "").strip().upper()
        lb = _last_batch_send(sid, month, year)
        if lb is not None:
            last[sid] = lb
    if not last:
        return
    t_ref, latest_bid = max(last.values())
    before: list[str] = []
    after: list[str] = []
    for sid in last:
        t_file = _target_file_mtime(sid, month, year)
        if t_file is None:
            continue
        (before if t_file < t_ref else after).append(sid)
    if not (before and after):
        return
    complete = all(
        (last.get(str(sid or "").strip().upper()) or (0, ""))[1] == latest_bid for sid in sup_ids
    )
    logger.error(
        "ส่งรวมภาค %s-%02d: ไฟล์เป้าต่างรุ่นกัน — โหลดใหม่หลังส่ง %s · ยังเป็นค่าก่อนส่ง %s",
        year, month, after, before,
    )
    raise HTTPException(
        status_code=409,
        detail={
            "code": "send_batch_mixed_snapshot",
            "message": (
                f"ยังไม่ได้ส่ง — ทีม {', '.join(after)} โหลดเป้าใหม่หลังการส่งครั้งก่อนแล้ว "
                f"แต่ทีม {', '.join(before)} ยังใช้เป้าชุดก่อนส่ง ถ้าส่งต่อ ยอดรวมทั้งภาคจะผิด"
            ),
            "hint_th": (
                "การส่งครั้งก่อนลงครบทุกทีมแล้ว — กด「ดึงเป้าใหม่ทุกทีม」แล้วกระจายอีกครั้งก่อนส่ง"
                if complete else
                "การส่งครั้งก่อนอาจลงไม่ครบทุกทีม — อย่าเพิ่งส่งและอย่าดึงเป้าใหม่ แจ้งแอดมินให้ตรวจยอดใน Target Sun ก่อน "
                "(แอดมินดาวน์โหลดไฟล์คืนค่าได้ที่ปุ่ม「เป้าตั้งต้น」)"
            ),
            "previous_send_complete": complete,
            "reloaded_sup_ids": after,
            "pre_send_sup_ids": before,
        },
    )


def assert_target_snapshot_is_fresh(
    sup_id: str,
    month: int,
    year: int,
    *,
    emp_codes: list[str] | None = None,
    live_by_sku: dict[str, int] | None = _UNSET,  # type: ignore[assignment]
    send_batch_id: str | None = None,
) -> None:
    """
    บล็อกเมื่อเป้าใน Target Sun เปลี่ยนไปหลังจากผู้ใช้โหลดข้อมูลขั้นที่ 1

    ไม่มีทางยืนยันข้ามแล้ว (ผู้ใช้ตัดสิน 29 ก.ย. 2026): ส่งตามแผนเดิมทั้งที่เป้าต้นทาง
    เปลี่ยน = ยอดใน Target Sun จะไม่เท่าเป้าล่าสุด ขัดกติกา "ยอดหีบต้องเท่าเป้าเสมอ"
    ต้องโหลดขั้นที่ 1 ใหม่แล้วกระจายอีกครั้ง · รายการที่คืนไปบอกทุก SKU ที่เปลี่ยน
    ให้หน้าจอพาไปดูทีละสินค้าได้

    ถ้าอ่านของจริงไม่ได้ → ไม่บล็อกด้วยเหตุนี้ เพราะการเทียบกับ snapshot
    ยังถูกบังคับเต็มที่จากด่านอื่นอยู่แล้ว

    **เรียกจากเส้นทางส่งจริงเท่านั้น** ห้ามย้ายกลับเข้าไปใน _build_tga_upload_dataframe
    ตัวสร้างไฟล์ต้องทำงานได้แบบออฟไลน์ล้วน (อ่านแต่ cache ในเครื่อง) ไม่งั้นการ
    ดาวน์โหลด Excel และเทสต์ที่สร้างไฟล์จะยิงเน็ตขึ้น Target Sun โดยไม่มีใครตั้งใจ

    `live_by_sku` — ให้ผู้เรียกที่อ่านสดมาแล้ว (เช่น ตอน import ที่อ่าน
    _live_target_snapshot อยู่แล้วเพื่อตรวจจำนวนแถวหลังส่ง) ส่งค่ามาใช้ต่อได้เลย
    ไม่ต้องยิง Target Sun ซ้ำสองรอบ — ไม่ระบุ (ค่าเริ่มต้น) จึงอ่านเองตามเดิม
    ระบุเป็น None ตรงๆ หมายถึง "อ่านมาแล้วแต่ไม่สำเร็จ" ก็จะไม่บล็อกเหมือนอ่านเองไม่ได้
    """
    snapshot = _sup_target_boxes_by_sku(sup_id, month, year)
    if not snapshot:
        return
    if live_by_sku is _UNSET:
        codes = list(emp_codes) if emp_codes is not None else team_emp_codes_from_grain(sup_id, month, year)
        live = _live_target_boxes_by_sku(sup_id, month, year, codes)
    else:
        live = live_by_sku
    if live is None:
        return

    drifts = []
    for sku in sorted(set(snapshot) | set(live)):
        was = int(snapshot.get(sku, 0))
        now = int(live.get(sku, 0))
        if was != now:
            drifts.append(
                {
                    "sku": sku,
                    "loaded_boxes": was,
                    "current_boxes": now,
                    "diff": now - was,
                    # สินค้าที่เพิ่งมีเป้า — ยังไม่อยู่ในตารางผลกระจาย หน้าจอพาไปดูไม่ได้
                    "new_sku": sku not in snapshot,
                    # สินค้าที่เป้าถูกเอาออก (เหลือ 0)
                    "removed_sku": sku not in live,
                }
            )
    if drifts:
        # ค่าที่เปลี่ยนเพราะ "เราส่งเอง" ไม่ใช่เป้าเปลี่ยน (ผลตรวจ 1 ต.ค. 2026 ข1 ทางที่ 1) — ส่งรวมภาคล้มกลางทาง
        # ทีมที่ลงแล้วถือยอดหลังย้ายหีบข้ามทีม (เช่น 60 → 70) ส่งทั้งชุดซ้ำต้องผ่าน ไม่งั้นผู้ใช้ค้าง
        # และถ้าหันไปโหลดขั้นที่ 1 ใหม่ เป้ารวมภาคจะกลายเป็นยอดที่ผิด (ทีมที่ลงแล้ว + ทีมที่ยังค้างค่าเก่า)
        # ยอมรับเฉพาะ SKU ที่ยอดสดเท่ากับที่ ledger จดว่าเราส่งครั้งล่าสุดพอดี — คนอื่นแก้เป้า = ยังบล็อก
        #
        # ต้องเป็น "ส่งรวมหลายทีมซ้ำ" ทั้งสองฝั่งเท่านั้น (ผลตรวจ 6 ต.ค. 2026 ก3) — เดิมไม่ดูว่ามาจากรอบไหน
        # หลังส่งรวมภาค A=70/B=30 ใครเปิดจอเก่าแล้วส่งทีม A เดี่ยว 60 ผ่านด่านนี้ ภาคเหลือ 90 จาก 100
        own = _own_sent_boxes_by_sku(sup_id, month, year) if str(send_batch_id or "").strip() else {}
        if own:
            drifts = [d for d in drifts if int(own.get(d["sku"], -1)) != int(d["current_boxes"])]
    if not drifts:
        return
    # เปลี่ยนมากก่อน — ผู้ใช้ไล่ดูตัวใหญ่ก่อน
    drifts.sort(key=lambda d: (-abs(int(d["diff"])), d["sku"]))

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
                "โหลดข้อมูลขั้นที่ 1 ใหม่ แล้วกระจายอีกครั้งก่อนส่ง — "
                "ยอดที่ส่งต้องเท่าเป้าล่าสุดใน Target Sun"
            ),
            "drifts": drifts[:200],
            "drift_count": len(drifts),
            "drift_boxes": diff_boxes,
            "sup_id": str(sup_id or "").strip().upper(),
        },
    )


def _reads_other_system_than_sends() -> bool:
    """อ่านกับส่งคนละระบบ (เช่น preset test) — ผลตรวจหลังส่งใช้ไม่ได้"""
    from .targetsun_endpoints import targetsun_endpoints_summary

    try:
        return str(targetsun_endpoints_summary().get("cross_env") or "") == "1"
    except Exception:
        return False


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
    # อ่านกับส่งคนละระบบ = ยอด "ลงจริง" ที่อ่านได้มาจากระบบที่ไม่ได้ถูกเขียน (ผลตรวจ §2.2)
    if _reads_other_system_than_sends():
        return {"checked": False, "reason": "cross_env"}
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
            return {"checked": True, "ok": True, "skus_checked": len(sent_by_sku),
                    # ยอดใน Target Sun หลังส่ง (เฉพาะ SKU ในไฟล์) — ไว้ใน log การส่ง (ผลตรวจ 7 ต.ค. 2026 ง)
                    "landed_boxes_total": int(sum(int(live.get(s, 0)) for s in sent_by_sku))}

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
            "landed_boxes_total": int(sum(int(live.get(s, 0)) for s in sent_by_sku)),
        }
    except Exception as e:
        logger.warning("ตรวจยอดหลังส่งไม่สำเร็จ (%s): %s", sup_id, e)
        return {"checked": False, "reason": "error"}


def unlanded_rows(file_qty_by_key: dict, live_qty_by_key: dict) -> list[dict]:
    """แถวในไฟล์ที่ Target Sun ไม่มี หรือมีแต่จำนวนไม่ตรงกับที่ส่ง"""
    out = []
    for k, q in file_qty_by_key.items():
        live = live_qty_by_key.get(k)
        if live is None and int(q) == 0:
            continue  # ส่ง 0 ไปที่คีย์ที่ไม่มีอยู่ = ไม่มีอะไรต้องล้าง ถือว่าลงแล้ว
        if live is None or int(live) != int(q):
            sku, emp, *_rest = k.split("|") + [""] * 7
            out.append({"key": k, "sku": sku, "emp_id": emp, "sent": int(q),
                        "in_targetsun": None if live is None else int(live)})
    return out


def parallel_rows_of_existing_pairs(
    before_keys: set, file_keys: set, live_qty_by_key: dict | None = None
) -> list[dict]:
    """
    แถวใหม่ในไฟล์ที่ "ซ้อน" คู่พนักงาน×สินค้าที่มีแถวอยู่แล้วใน Target Sun (ผู้ใช้ขอ 30 ก.ย. 2026)

    คีย์ upsert รวม WAREHOUSECODE — ถ้าคู่เดิมมีแถวคลัง A (หรือคลังว่าง) แล้วไฟล์ส่งคลัง B
    มา Target Sun จะสร้างแถวใหม่ ไม่ทับของเดิม คู่นั้นจึงมีเป้าสองก้อน (SL453/SL380)
    ตัวนับ "ส่วนเกิน" มองไม่เห็นเพราะแถวใหม่นี้อยู่ใน "คาดแถวใหม่" ด้วย

    นับเฉพาะเมื่อแถวเดิมของคู่นั้น **ไม่อยู่ในไฟล์** — ถ้าไฟล์ส่งแถวเดิมไปด้วย (เช่นกติกา
    บังคับคลังส่ง 0 ทับคลังเก่า) ยอดคุมได้ ไม่ใช่เป้าเบิ้ล
    """
    before_keys = set(before_keys or set())
    file_keys = set(file_keys or set())
    old_by_pair: dict[tuple[str, str], list[str]] = {}
    for k in before_keys - file_keys:
        sku, emp = (k.split("|") + ["", ""])[:2]
        old_by_pair.setdefault((sku, emp), []).append(k)
    out = []
    for k in sorted(file_keys - before_keys):
        sku, emp = (k.split("|") + ["", ""])[:2]
        olds = old_by_pair.get((sku, emp))
        if not olds:
            continue
        out.append({
            "key": k,
            "sku": sku,
            "emp_id": emp,
            "new_warehouse": k.split("|")[-1],
            "old_keys": sorted(olds),
            "old_warehouses": sorted({o.split("|")[-1] for o in olds}),
            "old_boxes": (
                sum(int(live_qty_by_key.get(o) or 0) for o in olds)
                if live_qty_by_key is not None else None
            ),
        })
    return out


def _pair_of_key(k: str) -> tuple[str, str]:
    sku, emp = (str(k).split("|") + ["", ""])[:2]
    return sku, emp


def warehouse_conflicts(live_qty_by_key: dict, file_qty_by_key: dict) -> list[dict]:
    """
    ด่านก่อนส่ง: คู่พนักงาน×สินค้าที่ไฟล์จะทำให้ Target Sun มีเป้าเบิ้ล (ผู้ใช้ขอ 30 ก.ย. 2026)

    คีย์ upsert รวมคลัง — แถวของคู่นี้ที่อยู่ใน Target Sun ตอนนี้แต่ **ไม่อยู่ในไฟล์** จะค้าง
    อยู่อย่างนั้น ยอดของคู่นี้จึงเป็น "ไฟล์ + แถวค้าง" · ต้นเหตุปกติคือ grain ขั้นที่ 1 เก่ากว่า
    Target Sun (มีคนแก้/เพิ่มแถวทีหลัง) หรือคลังในไฟล์ผิด

    นับเป็นปัญหาเมื่อแถวค้างยังมีหีบ หรือไฟล์จะสร้างแถวใหม่ให้คู่นี้ (แถวซ้อน) — แถวค้างที่เป็น
    0 และไฟล์ทับของเดิมครบไม่ทำให้อะไรเบิ้ล · คู่ใหม่ที่ Target Sun ไม่มีเลยไม่ใช่ปัญหา
    """
    live_by_pair: dict[tuple[str, str], dict[str, int]] = {}
    for k, q in (live_qty_by_key or {}).items():
        live_by_pair.setdefault(_pair_of_key(k), {})[k] = int(q or 0)
    file_by_pair: dict[tuple[str, str], dict[str, int]] = {}
    for k, q in (file_qty_by_key or {}).items():
        file_by_pair.setdefault(_pair_of_key(k), {})[k] = int(q or 0)

    out = []
    for pair in sorted(set(file_by_pair) & set(live_by_pair)):
        live, file = live_by_pair[pair], file_by_pair[pair]
        leftover = set(live) - set(file)
        if not leftover:
            continue
        leftover_boxes = sum(live[k] for k in leftover)
        new_keys = set(file) - set(live)
        if leftover_boxes <= 0 and not new_keys:
            continue
        sku, emp = pair
        out.append({
            "emp_id": emp,
            "sku": sku,
            "targetsun_rows": [
                {"warehouse": k.split("|")[-1], "boxes": live[k]} for k in sorted(live)
            ],
            "file_rows": [
                {"warehouse": k.split("|")[-1], "boxes": file[k]} for k in sorted(file)
            ],
            "leftover_boxes": leftover_boxes,
        })
    return out


def live_grain_for_pairs(live_qty_by_key: dict, pairs: set) -> pd.DataFrame:
    """
    grain ของคู่ที่ระบุ สร้างจากแถวใน Target Sun ตอนนี้ — ใช้แทน grain ขั้นที่ 1 ของคู่นั้น
    ตอนผู้ใช้เลือก「ใช้คลังตาม Target Sun」: หีบของคู่ไม่เปลี่ยน แค่แตกลงแถว (คลัง/เขต/จังหวัด)
    ที่มีอยู่จริงตอนนี้ ไม่ต้องโหลดขั้นที่ 1 หรือกระจายใหม่ (ค่าที่แก้มือไว้ไม่หาย)
    """
    rows = []
    for k, q in (live_qty_by_key or {}).items():
        parts = (str(k).split("|") + [""] * 7)[:7]
        sku, emp = parts[0], parts[1]
        if (sku, emp) not in pairs:
            continue
        rows.append({
            "emp_id": emp, "sku": sku, "qty": float(q or 0),
            "salestype": parts[2], "divisioncode": parts[3], "areacode": parts[4],
            "provincecode": parts[5], "warehouse_code": parts[6],
        })
    if not rows:
        return pd.DataFrame()
    return _normalize_grain_dtype(pd.DataFrame(rows))


def verify_row_count_after_send(
    sup_id: str,
    month: int,
    year: int,
    *,
    emp_codes: list[str],
    before_snapshot: dict | None,
    file_keys: set,
    file_qty_by_key: dict | None = None,
) -> dict:
    """
    ตรวจ "จำนวนแถวจริง" ก่อน/หลังส่ง — จับแถวซ้ำคนละคลัง (11.3 / ปริศนา SL453) ที่
    verify_after_send (ยอดหีบรวมต่อ SKU) มองไม่เห็น เพราะสองแถวคนละคลังบวกยอดกันแล้ว
    ยังเท่าไฟล์ที่ส่งไปพอดี

    expected_new_rows_from_file คือคีย์ในไฟล์ที่ "ก่อนส่ง" ยังไม่มีอยู่จริงใน Target Sun
    (ไม่ใช่ new_rows_count เดิมที่นับแค่ระดับคู่พนักงาน×สินค้าจาก local cache — คนละ
    ความหมาย และคนละความละเอียด)

    ห้าม raise เด็ดขาด — เหมือน verify_after_send: ส่งไปแล้วย้อนไม่ได้ ตรงนี้คือรายงาน
    ล้วน ๆ ไม่ใช่ประตู
    """
    try:
        # อ่านกับส่งคนละระบบ (เช่น preset test: อ่าน Prod ส่ง UAT) — ตัวเลขก่อน/หลัง
        # มาจากระบบที่ไม่ได้ถูกเขียน ผลเทียบจึงไม่มีความหมาย ต้องรายงานว่าตรวจไม่ได้
        from .targetsun_endpoints import targetsun_endpoints_summary

        if str(targetsun_endpoints_summary().get("cross_env") or "") == "1":
            return {"checked": False, "reason": "cross_env"}
        if before_snapshot is None:
            return {"checked": False, "reason": "before_unavailable"}
        after_snapshot = _live_target_snapshot(sup_id, month, year, emp_codes)
        if after_snapshot is None:
            return {"checked": False, "reason": "after_unavailable"}

        before_count = int(before_snapshot.get("row_count") or 0)
        after_count = int(after_snapshot.get("row_count") or 0)
        before_keys = before_snapshot.get("keys") or set()
        expected_new_rows = len(set(file_keys or set()) - set(before_keys))
        actual_new_rows = after_count - before_count
        unexpected_extra_rows = actual_new_rows - expected_new_rows

        # แถวในไฟล์ที่ลงไม่ครบ/ไม่ตรง — ใช้ส่งซ้ำเฉพาะแถวนั้น (upsert ทับได้ ลบไม่ได้)
        # ไม่พึ่ง errors[] ของ Target Sun เพราะคืนมาแค่ 50 แถวแรก
        unlanded = unlanded_rows(file_qty_by_key or {}, after_snapshot.get("qty_by_key") or {})
        parallel = parallel_rows_of_existing_pairs(
            before_keys, file_keys, after_snapshot.get("qty_by_key") or {}
        )
        result = {
            "checked": True,
            "ok": unexpected_extra_rows == 0 and not unlanded and not parallel,
            "unlanded_count": len(unlanded),
            "unlanded_sample": unlanded[:20],
            "parallel_rows_count": len(parallel),
            "parallel_rows_sample": parallel[:20],
            "before_count": before_count,
            "after_count": after_count,
            "actual_new_rows": actual_new_rows,
            "expected_new_rows": expected_new_rows,
            "unexpected_extra_rows": unexpected_extra_rows,
        }
        if unexpected_extra_rows != 0:
            logger.error(
                "จำนวนแถวใน Target Sun หลังส่งไม่ตรงที่คาด %s %s-%02d: "
                "ก่อน=%d หลัง=%d (%+d) คาดแถวใหม่=%d ส่วนเกิน=%+d",
                str(sup_id or "").strip().upper(),
                year,
                month,
                before_count,
                after_count,
                actual_new_rows,
                expected_new_rows,
                unexpected_extra_rows,
            )
        if parallel:
            logger.error(
                "แถวใหม่ซ้อนคู่เดิมใน Target Sun %s %s-%02d: %d แถว (คลังไม่ตรงแถวเดิม เป้าอาจเบิ้ล) "
                "ตัวอย่าง %s",
                str(sup_id or "").strip().upper(),
                year,
                month,
                len(parallel),
                [(p["emp_id"], p["sku"], p["old_warehouses"], p["new_warehouse"]) for p in parallel[:5]],
            )
        return result
    except Exception as e:
        logger.warning("ตรวจจำนวนแถวหลังส่งไม่สำเร็จ (%s): %s", sup_id, e)
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

    เทียบ SKU ที่ชุดนี้กำลังส่ง — ถ้าทุกทีมส่งทุกแบรนด์ทุกสินค้า (full_send) เทียบ
    SKU ที่มีเป้าแต่ไม่อยู่ในไฟล์ด้วย ส่วนการส่งแยกแบรนด์ SKU แบรนด์อื่นไม่อยู่ในไฟล์เป็นปกติ

    ตรวจไม่ได้ (ไม่มียอดในไฟล์ / อ่านเป้าไม่ได้) = 409 ห้ามส่ง — ไม่มีการตอบ 200
    แบบ verified:false ให้หน้าเว็บส่งต่ออีกแล้ว ผ่านแล้วผู้เรียกต้อง mark_batch_verified
    ให้ทุก token ในชุด (import_prepared_targetsun ไม่ส่ง token ของชุดที่ยังไม่ผ่าน)
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

    # ทีมเดียวกันมีไฟล์ได้ใบเดียวต่อชุด — เดิมทีมซ้ำทำให้เป้าทีมนั้นถูกบวกสองรอบ ไฟล์คู่
    # 「200 + 0」จึงผ่าน "ยอดรวมเท่าเป้า" ได้ แล้วส่งแค่ใบแรก = เป้าเบิ้ลลง Target Sun
    # (ผลตรวจ 5 ต.ค. 2026 ข้อ 4) · หน้าเว็บสร้างใบละทีมอยู่แล้ว ทางปกติจึงไม่โดนด่านนี้
    seen_sup: set[str] = set()
    dup_sup: set[str] = set()
    for m in metas:
        sid = str(m.get("sup_id") or "").strip().upper()
        (dup_sup if sid in seen_sup else seen_sup).add(sid)
    if dup_sup:
        logger.error("ชุดส่งมีทีมซ้ำ: %s", sorted(dup_sup))
        raise HTTPException(
            status_code=409,
            detail={
                "code": "send_batch_duplicate_team",
                "message": (
                    f"ยังไม่ได้ส่ง — ทีม {', '.join(sorted(dup_sup))} มีไฟล์มากกว่าหนึ่งใบในชุดเดียวกัน"
                ),
                "hint_th": "กดส่งใหม่อีกครั้งเพื่อให้ระบบเตรียมไฟล์ใหม่ทีมละหนึ่งใบ",
                "sup_ids": sorted(dup_sup),
            },
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

    # (0) ไฟล์เป้าต่างรุ่นกันในชุดส่งซ้ำ (ผลตรวจ 7 ต.ค. 2026 ก2) — ต้องอยู่ก่อนเทียบยอด เพราะยอดเทียบผ่านได้
    if len(periods) == 1 and len(sup_ids) > 1:
        _y, _m = next(iter(periods))
        assert_batch_snapshots_same_generation(sup_ids, int(_m), int(_y))

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

    batch_ids = {str(m.get("send_batch_id") or "").strip() for m in metas}
    if len(batch_ids) > 1:
        # ไฟล์จากคนละรอบส่งปนกัน — ตรวจรวมกันไม่ได้ความหมาย
        raise HTTPException(
            400,
            detail="ไฟล์ที่เตรียมไว้มาจากคนละรอบการส่ง — กรุณากดส่งใหม่อีกครั้ง",
        )

    if missing_totals:
        # ตรวจไม่ได้ = ห้ามส่ง (เดิมตอบ 200 verified:false แล้วหน้าเว็บส่งต่อ)
        logger.error("ตรวจยอดรวมทั้งชุดไม่ได้: ไฟล์ที่เตรียมไว้บางใบไม่มียอดต่อ SKU (%s)", sup_ids)
        raise HTTPException(
            status_code=409,
            detail={
                "code": "send_batch_unverifiable",
                "message": "ยังไม่ได้ส่ง — ไฟล์ที่เตรียมไว้บางทีมไม่มียอดต่อสินค้าให้ตรวจ",
                "hint_th": "กดส่งใหม่อีกครั้งเพื่อให้ระบบเตรียมไฟล์ใหม่",
                "sup_ids": sup_ids,
            },
        )

    # ทีมเดียวก็ตรวจด้วยสูตรเดียวกัน — ยอดรวมของ "ทีมในชุด" เทียบเป้ารวมของทีมเหล่านั้น
    # (ทีมเดียว = เทียบเป้าทีม) เดิมข้ามไปเพราะผู้ใช้อาจกดยืนยันความต่างไว้ ตอนนี้
    # ไม่มีการยืนยันข้ามแล้ว ยอดต้องตรงเสมอ
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
        logger.error("ตรวจยอดรวมทั้งชุดไม่ได้: อ่านเป้าไม่ได้ %s", unreadable)
        raise HTTPException(
            status_code=409,
            detail={
                "code": "send_batch_unverifiable",
                "message": (
                    f"ยังไม่ได้ส่ง — อ่านเป้าของทีม {', '.join(unreadable)} ไม่ได้ "
                    "จึงยืนยันไม่ได้ว่ายอดรวมตรงเป้า"
                ),
                "hint_th": "โหลดข้อมูลขั้นที่ 1 ใหม่เพื่อดึงเป้าเข้ามาเก็บอีกครั้ง แล้วค่อยส่ง",
                "sup_ids": sup_ids,
                "unreadable_sup_ids": unreadable,
            },
        )

    # ส่งทุกแบรนด์ทุกสินค้าทุกทีม = ต้องครบทุก SKU ที่มีเป้า (SKU ที่ไม่อยู่ในไฟล์เลยคือหีบหาย)
    full_send = bool(metas) and all(bool(m.get("full_send")) for m in metas)
    keys = set(file_by_sku) | (set(targets_total) if full_send else set())

    diffs = []
    for sku in sorted(keys):
        if sku in excluded:
            continue
        # ไม่มีเป้าในงวดนี้ = เป้า 0 — มีหีบเมื่อไรคือหีบงอก
        tgt = int(targets_total.get(sku, 0))
        got = int(file_by_sku.get(sku, 0))
        if got != tgt:
            diffs.append(
                {"sku": sku, "sending_boxes": got, "expected_boxes": tgt, "diff": got - tgt}
            )

    if diffs:
        diff_boxes = sum(int(d["diff"]) for d in diffs)
        logger.error("ยอดรวมทั้งชุดไม่ตรงเป้ารวม %s: %s", sup_ids, diffs[:10])
        scope = f"ทั้ง {len(per_team)} ทีม" if len(per_team) > 1 else f"ทีม {sup_ids[0]}"
        raise HTTPException(
            status_code=409,
            detail={
                "code": "send_batch_total_mismatch",
                "message": (
                    f"ยังไม่ได้ส่ง — ยอดรวมของ{scope}ไม่เท่าเป้ารวม "
                    f"{len(diffs)} SKU (ต่างรวม {diff_boxes:+,} หีบ)"
                ),
                "hint_th": (
                    "ย้ายหีบข้ามทีมได้ แต่ยอดรวมต้องเท่าเดิมพอดี — "
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
        "scope": "batch" if len(per_team) > 1 else "single_team",
        "sup_ids": sup_ids,
        "skus_checked": len([s for s in keys if s not in excluded]),
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
        return _apply_wh_hints(df, rows_raw, trust_existing=skip_emp_sku_dim_merge)

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
        # เลิกเรียก fabric.get_warehouse_by_emp (เดาจากประวัติขาย 2 ปี, cross_sold_history_2y_qu)
        # แล้วโดยตั้งใจ (24 ก.ย. 2026) — เป็นต้นตอบั๊กแถวซ้ำ SL380/SL530/SL525 มาแล้ว
        # คลังที่เหลือใช้ได้มีแค่ 2 แหล่ง: get_tga_lakehouse_dims_by_emp (MAX จาก
        # tga_target_salesman_next เอง — ของจริงจาก Target Sun แค่หยาบกว่า) กับ wh_hint
        # (ค่าที่ resolve จาก grain รายบรรทัดมาก่อนแล้ว) ไม่มีแหล่งไหนเป็นการเดาอีกต่อไป
        # ไม่มีคลังจริงให้เชื่อ = ปล่อยว่างไว้ชัดเจน (WAREHOUSECODE ว่างส่งได้ปกติ)
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

    # มี grain (ทางส่งจริง): เติมจาก Fabric ได้เฉพาะ "คู่ใหม่" ที่ไม่มีแถวใน Target Sun (มีคอลัมน์ dims_inferred)
    # แถวที่มาจาก grain = ค่าจริงของ Target Sun ทุกคอลัมน์ รวมค่าว่างจริง — ห้ามเติมทับ
    # (ผลตรวจ 7 ต.ค. 2026 ก4: เดิมเติม PROVINCECODE ว่างจริงด้วย MAX ต่อคนจาก Fabric
    #  ทันทีที่มีแถวใดในไฟล์ขาด dim → คีย์ upsert เปลี่ยน = แถวใหม่ซ้อนแถวเดิม เป้าเบิ้ล แบบ SL380)
    if skip_emp_sku_dim_merge:
        _fillable = (
            df["dims_inferred"].notna() if "dims_inferred" in df.columns
            else pd.Series(False, index=df.index)
        )
    else:
        _fillable = pd.Series(True, index=df.index)

    def _emp_fb_series(col: str) -> pd.Series:
        if not emp_fb:
            return pd.Series([""] * len(df), index=df.index)
        fb = df["emp_id"].map(lambda e: _cell_str((emp_fb.get(str(e).strip()) or {}).get(col)))
        # แถวที่ห้ามเติม: ให้ "ค่าสำรอง" เป็นค่าเดิมของแถว (ว่างก็ว่างต่อ)
        cur = df[col].map(_cell_str) if col in df.columns else pd.Series([""] * len(df), index=df.index)
        return fb.where(_fillable, cur)

    df["salestype"] = _coalesce_col(df, "salestype", _emp_fb_series("salestype"))
    df["divisioncode"] = _coalesce_col(df, "divisioncode", _emp_fb_series("divisioncode"))
    df["areacode"] = _coalesce_col(df, "areacode", _emp_fb_series("areacode"))
    df["provincecode"] = _coalesce_col(df, "provincecode", _emp_fb_series("provincecode"))

    if skip_emp_sku_dim_merge:
        # แถวจาก grain resolve คลังมาแล้ว (รวมคลังว่างจริง) ฟังก์ชันนี้ถูกเรียกเพราะ dim
        # อื่น (เช่น divisioncode) ยังขาด ไม่ใช่เพราะคลังไม่รู้ — ห้ามเอา wh_hint มาทับ
        # คลังที่ resolve มาแล้ว (ดู SL380/SL530/SL525)
        if "warehouse_code" not in df.columns:
            df["warehouse_code"] = ""
    else:
        # ไม่มีการเดาจากประวัติขาย 2 ปีอีกต่อไป (24 ก.ย. 2026) — เหลือแค่ wh_hint (คลังที่
        # resolve จาก grain รายบรรทัดมาก่อนแล้ว) กับ _emp_fb_series (MAX จาก
        # tga_target_salesman_next เอง ผ่าน get_tga_lakehouse_dims_by_emp) ทั้งคู่เป็น
        # ข้อมูลจริงจาก Target Sun ไม่ใช่การเดา ไม่มีทั้งคู่ = ว่างไว้ชัดเจน
        if "warehouse_code" not in df.columns:
            df["warehouse_code"] = ""
        df["warehouse_code"] = df.apply(
            lambda row: _cell_str(row.get("warehouse_code"))
            or wh_hint.get(str(row["emp_id"]).strip(), ""),
            axis=1,
        )
        df["warehouse_code"] = _coalesce_col(df, "warehouse_code", _emp_fb_series("warehouse_code"))

    df["areacode"] = df["areacode"].map(_areacode_str)
    df["warehouse_code"] = df["warehouse_code"].map(_cell_str)
    return df


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


# คอลัมน์ที่เป็น "จำนวนสะสม" ต่อแถว (เหมือน allocated_boxes) — ต้องรวมข้ามกลุ่มไปด้วย ไม่ใช่
# ก็อปแค่แถวแรก ไม่งั้นแถวใหม่ที่ backend/services/optimize.py::_apply_wh_pin_preview ส่งมา
# (มีคอลัมน์ประวัติเทียบเคียงติดมาด้วย) จะโชว์ประวัติของ "แค่คลังหนึ่งในกลุ่ม" แทนที่จะเป็น
# ผลรวมจริงของคนคนนั้น — ผู้เรียกเดิม (ตอนส่งจริง) ไม่มีคอลัมน์พวกนี้อยู่แล้วจึงไม่กระทบ
_WH_PIN_SUM_ACROSS_GROUP_COLS = (
    "hist_avg",
    "hist_ly_same_month",
    "hist_prev_month",
    "baseline_boxes",
)


def _apply_warehouse_pin_rules(
    df: pd.DataFrame, sup_id: str, month: int, year: int,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """
    บังคับคลังเดียวสำหรับ "ชุด SKU ที่แอดมินเลือกไว้" × AREACODE × DIVISIONCODE ตามกติกา
    ที่ตั้งไว้ (warehouse_pin_rules_store) — ใช้แก้ปัญหาที่บางกลุ่มสินค้ากระจายเป้าปน
    หลายคลังตามประวัติขาย ทั้งที่ธุรกิจต้องการคลังเดียว

    จับคู่กติกาด้วย SKU ตรง ๆ (rules_by_key คืน {(sku, areacode, divisioncode): rule})
    ไม่ผ่าน Dim_Product[Section] เลย — Section เป็นแค่ตัวช่วยกรองตอนแอดมินเลือกสินค้าใน
    หน้าเว็บ (เลือกกลุ่มแล้วเลือกเฉพาะบางตัวผ่านโมดัล) จุดนี้จึงไม่ต้องพึ่งไฟล์เป้าราย sup
    หรือคอลัมน์ section เลย — ไม่มีทางเกิดกรณี "ไม่มีข้อมูลให้จับคู่" แบบเดิมอีกต่อไป

    ต้องรันหลังคลัง/areacode/divisioncode ของทุกแถว resolve ครบแล้ว (หลัง
    _apply_wh_hints/_enrich_emp_dimensions) — จับคู่กติกาด้วย 4 ทูเพิล
    (emp_id, sku, areacode, divisioncode) ไม่ใช่แค่ (emp_id, sku) เพราะพนักงานคนเดียว
    อาจมีแถวคนละ area/division ปนกันได้ (ดู emp_dims_from_own_grain) กติกาต้องแตะเฉพาะ
    ขาที่ area/division ตรงกับกติกาจริง ๆ

    เมื่อกติกาแมตช์: รวมหีบทั้งกลุ่มเป็นแถวใหม่ 1 แถวที่คลังปักหมุด + ตั้ง
    allocated_boxes=0 ให้ทุกแถวเดิมที่ไม่ใช่คลังปักหมุด (**ตั้งเป็น 0 ไม่ลบแถว** — ทำให้
    คลังเก่าถูกส่ง 0 ทับจริงที่ Target Sun กันปัญหาเดียวกับ SL380/SL530/SL525:
    WAREHOUSECODE อยู่ในคีย์ upsert ตั้งแต่ 7 ก.ย. 2026 คลังใหม่ที่ปลายทางไม่เคยมี =
    insert ซ้อนไม่ใช่ update ทับ ดู docs/ALLOCATION_INVARIANTS.md)

    ปล่อยให้ _merge_duplicate_import_keys (เรียกทีหลังใน _build_tga_upload_dataframe)
    ยุบ+บวกหีบของแถวที่คีย์ตรงกันเป๊ะให้เอง — ไม่ต้องเขียน merge เอง
    """
    empty_stats = {"matched_groups": 0, "boxes_moved": 0, "rows_zeroed": 0, "new_warehouse_legs": 0}
    if df is None or df.empty:
        return df, empty_stats

    rules = warehouse_pin_rules_store.rules_by_key()
    if not rules:
        return df, empty_stats

    d = df.copy()
    d["_wh_pin_area"] = d.get("areacode", "").map(_areacode_str)
    d["_wh_pin_div"] = d.get("divisioncode", "").map(_cell_str)

    extra_rows: list[dict] = []
    zero_idx: list[int] = []
    stats = dict(empty_stats)

    for (emp_id, sku, area, div), grp in d.groupby(
        ["emp_id", "sku", "_wh_pin_area", "_wh_pin_div"], sort=False
    ):
        rule = rules.get((str(sku).strip(), area, div))
        if not rule:
            continue

        target_wh = _cell_str(rule["warehouse_code"])
        wh_series = grp["warehouse_code"].map(_cell_str)
        if wh_series.eq(target_wh).all():
            continue  # ทุกแถวอยู่คลังปักหมุดอยู่แล้ว ไม่ต้องทำอะไร

        total = int(pd.to_numeric(grp["allocated_boxes"], errors="coerce").fillna(0).sum())
        if total == 0:
            # ไม่มีหีบให้รวม (ทุกแถวในกลุ่มเป็น 0 อยู่แล้ว) — สร้างแถวใหม่ที่คลังปักหมุดไปก็ได้
            # แค่แถวเปล่าไร้ประโยชน์ คนละจุดกับ ค9 (ที่ตัดหลังสร้างแล้ว) แต่หลักการเดียวกัน
            continue

        already_pinned = wh_series.eq(target_wh)
        non_pinned_idx = grp.index[~already_pinned].tolist()
        moved = int(
            pd.to_numeric(grp.loc[~already_pinned, "allocated_boxes"], errors="coerce")
            .fillna(0)
            .sum()
        )

        if already_pinned.any():
            # คลังปักหมุดมี "แถวอยู่แล้ว" ปนอยู่ในกลุ่มนี้ (เช่น พนักงานคลังแตกที่บังเอิญมี
            # คลังปักหมุดเป็นหนึ่งในคลังเดิมของเขาอยู่แล้ว) — ห้ามสร้างแถวใหม่ซ้อนแบบเดิม
            # เพราะ total ข้างบนรวมหีบของแถวที่ pinned อยู่แล้วเข้าไปด้วย ถ้าสร้างแถวใหม่อีก
            # หีบของแถว pinned เดิมจะถูกนับซ้ำสอง (ครั้งแรกในแถวเดิมที่ยังไม่ถูกแตะ ครั้งที่
            # สองในแถวใหม่ที่รวม total ทั้งกลุ่ม) ยอดรวมต่อ SKU จะพองเกินเป้าและไปชนด่าน
            # I1/_assert_send_matches_sup_targets ทีหลัง — เจอบั๊กจริงจากเคส SL225/S543/
            # SKU 411140 ที่คลังปักหมุด G010 มีแถวเดิมอยู่แล้วคู่กับ G080 (22 ก.ย. 2026)
            # ทางแก้คือแก้ "แถวที่ pinned อยู่แล้ว" ให้เป็นยอดรวมทั้งกลุ่มตรง ๆ แทน
            pinned_idx = grp.index[already_pinned].tolist()
            d.loc[pinned_idx[0], "allocated_boxes"] = total
            # ปกติจะมีแถว pinned ซ้ำได้แค่ 1 แถวต่อกลุ่มอยู่แล้ว แต่กันไว้เผื่อข้อมูลเพี้ยน
            zero_idx.extend(pinned_idx[1:])
            zero_idx.extend(non_pinned_idx)
            stats["matched_groups"] += 1
            stats["boxes_moved"] += moved
            stats["rows_zeroed"] += len(non_pinned_idx) + len(pinned_idx[1:])
            continue

        zero_idx.extend(non_pinned_idx)

        # ก็อปทุกคอลัมน์จากแถวแรกของกลุ่มไว้ก่อน (เผื่อผู้เรียกมีคอลัมน์เสริมนอกเหนือชุด
        # หลักที่นี่รู้จัก เช่น optimize.py::_apply_wh_pin_preview ที่มี hist_avg/ราคา/ชื่อ
        # แบรนด์ ฯลฯ ติดมาด้วยสำหรับโชว์ผลตอนคำนวณ Step 3) แล้วทับเฉพาะคีย์กลุ่ม + ฟิลด์ที่
        # ต้องรวม/เปลี่ยนจริง — ผู้เรียกเดิม (ตอนส่งจริง) มีแค่ 8 คอลัมน์นี้อยู่แล้วจึงไม่ต่าง
        row0 = grp.iloc[0]
        extra_row = row0.to_dict()
        for col in _WH_PIN_SUM_ACROSS_GROUP_COLS:
            if col in grp.columns:
                extra_row[col] = pd.to_numeric(grp[col], errors="coerce").fillna(0).sum()
        extra_row.update(
            {
                "emp_id": emp_id,
                "sku": sku,
                "allocated_boxes": total,
                "salestype": _cell_str(row0.get("salestype", "")),
                "divisioncode": div,
                "areacode": area,
                "provincecode": _cell_str(row0.get("provincecode", "")),
                "warehouse_code": target_wh,
            }
        )
        extra_rows.append(extra_row)
        stats["matched_groups"] += 1
        stats["boxes_moved"] += moved
        stats["rows_zeroed"] += len(non_pinned_idx)
        stats["new_warehouse_legs"] += 1

    if zero_idx:
        d.loc[zero_idx, "allocated_boxes"] = 0
    d = d.drop(columns=["_wh_pin_area", "_wh_pin_div"])
    if extra_rows:
        d = pd.concat([d, pd.DataFrame(extra_rows)], ignore_index=True)
        logger.warning(
            "กติกาบังคับคลัง %s: %d กลุ่มพนักงาน×สินค้า×เขต×ดิวิชันถูกรวมคลัง — "
            "ย้าย %d หีบ, ล้าง %d แถวคลังอื่นเป็น 0, %d เป็นคลังใหม่ของคู่นั้น",
            str(sup_id or "").strip().upper(),
            stats["matched_groups"], stats["boxes_moved"], stats["rows_zeroed"],
            stats["new_warehouse_legs"],
        )
    return d, stats


def _assert_send_matches_sup_targets(
    df: pd.DataFrame,
    sup_id: str,
    month: int,
    year: int,
    *,
    check_missing_skus: bool = False,
    defer_to_batch: bool = False,
) -> list[dict]:
    """
    ประตูสุดท้ายก่อนส่งเข้า Target Sun — ผลรวมหีบต่อ SKU ของทีมนี้ต้องตรงเป้าของทีมนี้

    กติกา (ผู้ใช้ยืนยัน 29 ก.ย. 2026): ยอดหีบรวมหลังกระจายต้องเท่าเป้าที่เข้ามา
    **ห้ามขาดหรือเกินแม้แต่หีบเดียว** และไม่มีปุ่มยืนยันข้าม — เดิมมี
    confirm_target_mismatch / confirm_unverifiable_target / ALLOC_ALLOW_MISMATCH
    ให้กดข้ามได้ ตอนนี้ถอดออกทั้งหมด

    ทำไมต้องตรวจซ้ำทั้งที่ /optimize ตรวจแล้ว:
      ระหว่าง optimize -> ส่ง ยังมีอีกหลายก้าวที่ไม่มีใครตรวจเลย
        - แก้มือในตาราง
        - PUT /data/allocations บันทึกร่างโดยไม่ตรวจผลรวม (ตั้งใจ — ร่างยังไม่ตรงได้)
        - โหลด snapshot เก่ากลับมาส่ง ทั้งที่เป้า TGA เปลี่ยนไปแล้ว

    defer_to_batch — ทีมนี้เป็นส่วนหนึ่งของการส่งรวมภาค (มี send_batch_id)
      โหมดรวมภาคย้ายหีบข้ามทีมได้ตามกติกา I7 ยอดรายทีมจึงต่างจากเป้าทีมได้
      สิ่งที่ต้องเท่าเดิมคือ **ยอดรวมทั้งภาคต่อ SKU** ซึ่ง verify_send_batch ตรวจ
      และ import_prepared_targetsun ไม่ยอมส่ง token ที่ชุดยังไม่ผ่านการตรวจ
      ตรงนี้จึงคืนรายการที่ต่างไว้เฉย ๆ ไม่บล็อก — แต่ "อ่านเป้าไม่ได้" ยังบล็อกเสมอ
      เพราะด่านระดับชุดก็ต้องใช้เป้าของทุกทีม

    check_missing_skus — ตรวจ "SKU ที่มีเป้าแต่ไม่มีใน payload เลย" ด้วย
      เปิดเฉพาะตอนส่งทุกแบรนด์ เพราะตอนนั้น payload ต้องครอบคลุมทุก SKU ที่มีเป้า

    คืนรายการที่ต่าง (ว่าง = ตรงทุก SKU) · ไม่ defer แล้วมีรายการ = 409
    """
    if df is None or df.empty:
        return []
    sid = str(sup_id or "").strip().upper()
    targets = _sup_target_boxes_by_sku(sid, month, year)
    if targets is None:
        # อ่านเป้าไม่ได้ = ตรวจไม่ได้ = ห้ามส่ง ไม่มีทางยืนยันข้าม
        # ทางแก้ที่ถูกคือโหลดขั้นที่ 1 ใหม่ให้ระบบดึงเป้ามาเก็บอีกรอบ
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
                    "กลับไปโหลดข้อมูลขั้นที่ 1 ใหม่เพื่อดึงเป้าเข้ามาเก็บอีกครั้ง แล้วค่อยส่ง"
                ),
                "sup_id": sid,
                "target_month": int(month),
                "target_year": int(year),
            },
        )

    boxes = pd.to_numeric(df["allocated_boxes"], errors="coerce").fillna(0)
    got_raw = boxes.groupby(df["sku"].astype(str).str.strip()).sum().to_dict()
    got: dict[str, int] = {}
    for k, v in got_raw.items():
        fv = float(v)
        if fv != int(fv):
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "send_boxes_not_integer",
                    "message": f"จำนวนหีบของ SKU {k} ไม่ใช่จำนวนเต็ม ({fv}) — ระบบไม่ส่ง",
                    "sup_id": sid,
                },
            )
        got[str(k)] = int(fv)

    # วนจาก "SKU ที่มีเป้า" ด้วยเมื่อ payload ควรครบ — ไม่งั้น SKU ที่หายไปทั้งตัว
    # จะไม่เคยถูกหยิบมาเทียบ
    keys = sorted(set(targets) | set(got)) if check_missing_skus else sorted(got)

    problems = []
    for sku in keys:
        # SKU ที่ไม่มีเป้าของทีมนี้ = เป้า 0 — มีหีบเมื่อไรคือหีบงอก
        tgt = int(targets.get(sku, 0))
        total = got.get(sku, 0)
        if total != tgt:
            problems.append(
                {
                    "sku": sku,
                    "sending_boxes": int(total),
                    "expected_boxes": tgt,
                    # ไม่มีแถวเลย ต่างจากส่งมาแต่จำนวนไม่ตรง — หน้าเว็บใช้แยกข้อความ
                    "missing_from_payload": sku not in got,
                    "no_target": sku not in targets,
                }
            )

    if not problems:
        return []

    if defer_to_batch:
        logger.info(
            "ส่งรวมภาค: ทีม %s ต่างจากเป้าทีม %d SKU — ให้ด่านยอดรวมทั้งภาคตัดสิน",
            sid, len(problems),
        )
        return problems

    logger.error(
        "ส่ง Target Sun ไม่ตรงเป้าทีม %s %s-%02d: %s", sid, year, month, problems[:20]
    )
    missing = [p for p in problems if p.get("missing_from_payload")]
    hint = (
        "ยอดหีบต่อสินค้าต้องเท่าเป้าของทีมพอดี — ตรวจช่องที่แก้มือไว้ "
        "หรือกด「คำนวณใหม่」แล้วส่งอีกครั้ง"
    )
    if missing:
        # SKU ที่ไม่มีในสิ่งที่ส่งเลย = หน้าเว็บยังไม่รู้จักมัน (เป้าเพิ่มมาหลังโหลดขั้นที่ 1)
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
        },
    )


#: ช่องที่ Target Sun ข้ามทั้งแถวถ้าว่าง ("Missing required fields: …") — ยึดจากพฤติกรรมจริง
#: ไม่ใช่ตามสเปก: สเปกบอก PROVINCECODE บังคับ แต่ข้อมูลจริงว่างทุกแถว (117,560 แถว) และรับได้
#: สเปกบอกรหัสพนักงาน 5 ตัว แต่ของจริง 4 ตัว (เช่น C442) — จึงไม่ตรวจความยาว
_IMPORT_REQUIRED_COLUMNS = (
    "PRODUCTCODE", "SALESTYPE", "DIVISIONCODE", "SALESMANCODE", "AREACODE",
    "QUANTITYCASE", "EFFECTIVEDATE", "USERCODE",
)
_IMPORT_KEY_COLUMNS = (
    "PRODUCTCODE", "SALESMANCODE", "SALESTYPE", "DIVISIONCODE", "AREACODE",
    "PROVINCECODE", "WAREHOUSECODE",
)


def import_row_key_series(df: pd.DataFrame) -> pd.Series:
    """คีย์เต็มของแต่ละแถวในไฟล์ส่ง — ลำดับเดียวกับ _live_target_row_key"""
    parts = [df[c].astype(str).str.strip() if c == "PRODUCTCODE" else df[c].astype(str) for c in _IMPORT_KEY_COLUMNS]
    out = parts[0]
    for p in parts[1:]:
        out = out + "|" + p
    return out


def assert_rows_importable(df: pd.DataFrame, sup_id: str = "") -> None:
    """
    ตรวจทุกแถวก่อนส่งด้วยกติกาที่ Target Sun ใช้ข้ามแถว — ผิดแถวเดียว = ไม่ส่งทั้งไฟล์

    ผู้ใช้ขอ 29 ก.ย. 2026: Target Sun ข้ามแถวที่ผิดทีละแถวแต่ยังบันทึกแถวอื่น แล้วเราลบแถว
    ในนั้นไม่ได้ ส่งไปครึ่ง ๆ กลาง ๆ จึงแก้ยากมาก · ตามปกติด่านนี้ไม่ควรเจออะไรเลย เพราะ
    แถวที่ขาดเขต/พื้นที่ถูกตัดตั้งแต่ขั้นก่อนหน้า — นี่คือตาข่ายชั้นสุดท้าย
    """
    if df is None or df.empty:
        return
    problems: list[dict] = []
    for i, r in enumerate(df[list(_IMPORT_REQUIRED_COLUMNS)].itertuples(index=False), start=2):
        missing = [c for c, v in zip(_IMPORT_REQUIRED_COLUMNS, r) if str(v if v is not None else "").strip() in ("", "nan", "None")]
        if missing:
            problems.append({"row": i, "reason": "ขาดช่อง " + ", ".join(missing)})
    qty = pd.to_numeric(df["QUANTITYCASE"], errors="coerce")
    for i in df.index[(qty.isna()) | (qty < 0) | (qty != qty.round())]:
        problems.append({"row": int(df.index.get_loc(i)) + 2, "reason": f"จำนวนหีบไม่ถูกต้อง ({df.at[i, 'QUANTITYCASE']})"})
    keys = import_row_key_series(df)
    dup = keys.duplicated(keep=False)
    for pos in [int(p) for p, d in enumerate(dup.tolist()) if d][:50]:
        problems.append({"row": pos + 2, "reason": "คีย์ซ้ำกับแถวอื่นในไฟล์ (Target Sun จะข้าม)"})
    if not problems:
        return
    problems.sort(key=lambda p: p["row"])
    sample = []
    for p in problems[:20]:
        row = df.iloc[p["row"] - 2]
        sample.append({
            **p,
            "sku": str(row.get("PRODUCTCODE") or ""),
            "emp_id": str(row.get("SALESMANCODE") or ""),
            "warehouse_code": str(row.get("WAREHOUSECODE") or ""),
        })
    logger.error("ไฟล์ส่ง Target Sun มีแถวที่จะถูกข้าม %s: %d แถว %s", sup_id, len(problems), sample[:5])
    raise HTTPException(
        status_code=409,
        detail={
            "code": "send_rows_not_importable",
            "message": (
                f"ยังไม่ได้ส่ง — มี {len(problems):,} แถวที่ Target Sun จะไม่รับ "
                "ถ้าส่งไปตอนนี้จะลงไม่ครบ ระบบจึงไม่ส่งทั้งไฟล์"
            ),
            "hint_th": "แจ้ง dev พร้อมรายการนี้ — มักเกิดจากข้อมูลเขต/พื้นที่ขายของพนักงานไม่ครบ",
            "rows": sample,
            "row_count": len(problems),
            "sup_id": str(sup_id or "").strip().upper(),
        },
    )


def _build_tga_upload_dataframe(
    req: LakehouseUploadRequest,
    *,
    drop_incomplete_rows: bool = False,
    enforce_targets: bool = False,
    live_grain: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, int, list[dict], list[dict]]:
    """
    enforce_targets — ตรวจว่าผลรวมหีบต่อ SKU ตรงเป้าทีมหรือไม่ (409 ถ้าไม่ตรง)

    เปิดเฉพาะ "เส้นทางส่งจริง" เท่านั้น ห้ามเปิดกับการสร้างไฟล์เพื่อดาวน์โหลด
    เพราะผู้ใช้ต้องโหลด Excel มาตรวจได้แม้ตัวเลขยังไม่ตรง — ถ้าบล็อกตรงนั้นด้วย
    จะกลายเป็นว่ายิ่งมีปัญหายิ่งตรวจไม่ได้

    live_grain — grain ของบางคู่ที่อ่านสดจาก Target Sun (live_grain_for_pairs) ใช้แทน grain
    ขั้นที่ 1 ของคู่นั้นเท่านั้น ตัวสร้างไฟล์ยังออฟไลน์ ผู้เรียก (prepare) เป็นคนอ่านมาให้
    """
    t0 = time.perf_counter()
    rows_raw = [a.model_dump() for a in req.allocations]
    rows_raw, moved_out = _drop_rows_of_reassigned_employees(rows_raw, req.sup_id)
    df = pd.DataFrame(rows_raw)
    if df.empty:
        if moved_out:
            raise HTTPException(
                400,
                detail={
                    "message": (
                        "ทุกคนในแผนนี้ถูกย้ายไปให้ทีมอื่นเกลี่ยเป้าแล้ว จึงไม่มีอะไรให้ส่ง"
                    ),
                    "reassigned_employees": sorted(moved_out),
                    "hint_th": "โหลดข้อมูลขั้นที่ 1 ใหม่แล้วกระจายอีกครั้ง",
                },
            )
        raise HTTPException(400, detail="ไม่มีข้อมูล allocations สำหรับส่งออก")
    df["allocated_boxes"] = pd.to_numeric(df["allocated_boxes"], errors="coerce").fillna(0).astype(int)

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
    team_target_mismatches: list[dict] = []
    if enforce_targets:
        team_target_mismatches = _assert_send_matches_sup_targets(
            df,
            req.sup_id,
            int(req.target_month),
            int(req.target_year),
            # ส่งทุกแบรนด์ครบทุกสินค้าเท่านั้นที่ payload ควรครอบคลุมทุก SKU ที่มีเป้า
            check_missing_skus=(brand_filter or "ALL").upper() == "ALL" and not sku_filter,
            # ส่งรวมภาค: ยอดรายทีมต่างได้ (I7) — ด่านยอดรวมทั้งภาคตัดสินแทน
            defer_to_batch=bool(str(getattr(req, "send_batch_id", "") or "").strip()),
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
    if live_grain is not None and not live_grain.empty:
        # ผู้ใช้เลือก「ใช้คลังตาม Target Sun」— แถวของคู่เหล่านี้ยึดของจริงตอนนี้ทั้งชุด
        _live_pairs = set(zip(live_grain["emp_id"], live_grain["sku"]))
        if not grain_dg.empty:
            _keep = [
                (e, s) not in _live_pairs
                for e, s in zip(grain_dg["emp_id"], grain_dg["sku"].astype(str).str.strip())
            ]
            grain_dg = pd.concat([grain_dg[_keep], live_grain], ignore_index=True)
        else:
            grain_dg = live_grain.copy()
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
    if drop_incomplete_rows and not grain_ok:
        # ไม่มี grain = ไม่รู้ว่าแต่ละแถวใน Target Sun ตอนนี้ใช้คลังอะไร (หรือว่าง) — ทางที่เหลือ
        # คือเดาคลังรายคน (คลังจากแถวอื่นของคนเดียวกัน / MAX จาก tga_target_salesman_next)
        # ซึ่งผิดกติกา "ว่างมาว่างไป มีรหัสไหนมาส่งรหัสนั้น" (ผู้ใช้ย้ำ 30 ก.ย. 2026) และคีย์
        # upsert รวมคลัง เดาผิด = แถวใหม่ซ้อนแถวเดิม เป้าเบิ้ล (SL453/SL380) จึงไม่ส่ง
        raise HTTPException(
            409,
            detail={
                "code": "grain_missing",
                "message": "ไม่พบข้อมูลคลังของเป้าปัจจุบันจากขั้นที่ 1 จึงยังส่งไม่ได้ (กันเป้าเบิ้ลจากคลังไม่ตรง)",
                "hint_th": "โหลดข้อมูลขั้นที่ 1 ใหม่ แล้วกดส่งอีกครั้ง",
            },
        )
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
    # เฉพาะเส้นทางส่งจริง: แถว 0 ที่ใช้ "ล้าง" เป้าคนไม่ต้องตั้งเป้า / คู่ที่หลุดจากรอบนี้ (ค8)
    # ไฟล์ Excel ที่ดาวน์โหลดจึงไม่มีสองชุดนี้ — แต่ยังมีแถว 0 จากการแตกตาม grain ของ Target Sun
    # (_align_zero_allocations_to_tga_grain / _ensure_zero_pairs_have_rows ข้างบน) เหมือนไฟล์ที่ส่ง
    # (คอมเมนต์เดิมบอกว่าไฟล์ดาวน์โหลด "ไม่มีแถวล้าง" ซึ่งไม่จริงทั้งหมด · ผลตรวจ §7)
    stale_rows_cleared = 0
    if drop_incomplete_rows:
        # ล้างแถวที่หลุดจากรอบนี้ (ค8) ต้องเป็นการส่งแบบเต็ม — brand_filter=="ALL" และ
        # ไม่มี sku_filter — ไม่งั้นจะเข้าใจผิดว่า "ตั้งใจไม่เลือกส่ง" คือ "หมดเป้าแล้ว"
        # แล้วไปล้างเป้าที่ยังถูกต้องของ SKU/แบรนด์อื่นที่ไม่ได้อยู่ในรอบส่งนี้
        _full_send = (brand_filter or "ALL").upper() == "ALL" and not sku_filter
        # คนไม่ต้องตั้งเป้า: ส่งไม่เต็ม = ล้างเฉพาะ SKU ในรอบนี้ (df ถูกกรองแบรนด์/สินค้าแล้ว)
        df, _no_target_cleared = _clear_no_target_employees_in_tga(
            df,
            req.sup_id,
            dg=grain_dg,
            only_skus=None if _full_send else set(df["sku"].astype(str).str.strip()),
        )
        df, stale_rows_cleared = _clear_stale_employee_sku_rows_in_tga(
            df, req.sup_id, dg=grain_dg, full_send=_full_send
        )
    t_expand = time.perf_counter()

    # grain จากขั้นที่ 1 ครบทุกแถว → ไม่ยิง Fabric ซ้ำ (เร็วขึ้น ~2–3s)
    # ทุกแถวผ่าน _expand_allocations_with_tga_grain/_align_zero_allocations_to_tga_grain
    # มาแล้ว คลัง (รวมคลังว่างจริง) resolve จากปลายทางครบแล้ว — trust_existing=True
    # กันไม่ให้ payload (ซึ่งอาจปนค่าเดาจากประวัติขาย 2 ปี) มาทับคลังว่างจริงของปลายทาง
    # อีกที ไม่งั้นตั้งแต่คลังเข้าคีย์ upsert แถวคลังว่างจะกลายเป็น "แถวใหม่" ที่ปลายทาง
    # แทนการทับของเดิม (ของจริง: SL380/SL530/SL525) — ตัวนับ dims_inferred ด้านล่างฟ้อง
    # กรณีที่เหลือ (คู่ใหม่จริงๆ ที่ไม่มีใน grain เลย)
    if grain_ok and not df.empty and bool(_import_key_mask(df).all()):
        logger.info(
            "lakehouse enrich: skip Fabric (grain_ok + SALESTYPE/DIVISION/AREACODE ครบ %d แถว)",
            len(df),
        )
        df = _apply_wh_hints(df, rows_raw, trust_existing=True)
    else:
        df = _enrich_emp_dimensions(
            df, rows_raw, skip_emp_sku_dim_merge=bool(grain_ok)
        )
    t_enrich = time.perf_counter()

    # ทุกแถวมี areacode/divisioncode/warehouse_code resolve ครบแล้วถึงจุดนี้ — บังคับกติกา
    # คลังเดียว (ถ้ามีตั้งไว้) ก่อนตัดแถว/ยุบคีย์ซ้ำ ให้ _merge_duplicate_import_keys
    # (ท้ายฟังก์ชัน) ยุบ+บวกหีบของแถวที่คลังปักหมุดชนกับของเดิม (ถ้ามี) ให้เอง
    df, wh_pin_stats = _apply_warehouse_pin_rules(
        df, req.sup_id, int(req.target_month), int(req.target_year)
    )

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
        _now_by_sku = (
            _targetsun_boxes_now_by_sku(req.sup_id, int(req.target_month), int(req.target_year))
            if (_from_batch & _payload_skus)
            else {}
        )
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
                    "current_targetsun_boxes": _now_by_sku.get(_sku),
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
        # ตัดแถวเป้า 0 ที่ "สร้างแถวใหม่เปล่า ๆ" — ปลายทางไม่เคยมีคู่นี้มาก่อน
        # (dims_inferred == True แปลว่าเดา dim จากแถวอื่นของคนคนนั้นแล้วผ่านด่านบนมาได้)
        # และหีบ = 0 จึงไม่มีอะไรให้ทับ/ล้าง สร้างแถวเปล่าไปก็ไม่มีประโยชน์
        #
        # ห้ามแตะแถวหีบ 0 ที่ dims_inferred เป็น NaN — พวกนั้นคือคู่ที่ Target Sun
        # "มีอยู่แล้ว" ต้องส่ง 0 ไปทับเพื่อล้างเป้างวดก่อน ไม่ตัดจะเหลือเลขเก่าค้างเป็นยอดเกิน
        # ไม่กระทบยอดรวมต่อ SKU เลย (ตัดแถวที่มีค่า 0 ผลรวมเท่าเดิม) — ดู
        # docs/ALLOCATION_INVARIANTS.md หัวข้อ "เริ่มงาน ค9" และ docs/next-plan-2026-09.md 11.2
        if "dims_inferred" in df.columns and not df.empty:
            # dims_inferred มีค่า (True/False) = คู่ใหม่ที่ไม่มีแถวใน Target Sun · NaN = แถวจาก grain
            # (ผลตรวจ 7 ต.ค. 2026 ก4: เดิมดูแค่ True — คู่ใหม่ที่ได้ dim จาก Fabric (False) กลายเป็นแถว 0 ใหม่)
            _empty_new_mask = df["dims_inferred"].notna() & (
                pd.to_numeric(df["allocated_boxes"], errors="coerce").fillna(0).astype(int) == 0
            )
            _cut = int(_empty_new_mask.sum())
            if _cut:
                df = df[~_empty_new_mask].copy()
                logger.info(
                    "ตัดแถวเป้า 0 ที่สร้างแถวใหม่เปล่า ๆ %s: %d แถว (ปลายทางไม่เคยมีคู่นี้ + หีบ=0)",
                    str(req.sup_id or "").strip().upper(),
                    _cut,
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

    # เฉพาะเส้นทางส่งจริง (เหมือนตัวล้างแถวค้างอื่น) — ทำหลังตัดแถวใหม่เปล่า/ตัด SKU ที่ส่งไม่ครบแล้ว
    # จึงเห็น "คู่ที่หายไปจากไฟล์" จริง ๆ (ผลตรวจ 6 ต.ค. 2026 ก1/ก2)
    leftover_cleared = 0
    if drop_incomplete_rows:
        df, leftover_cleared = _clear_leftover_rows_outside_round(
            df, req.sup_id, int(req.target_month), int(req.target_year)
        )
        stale_rows_cleared += leftover_cleared

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
    #
    # ทำไมต้องรู้จำนวนนี้: ถ้าปลายทางมีคู่นี้อยู่แล้วที่คลังอื่น (คีย์ upsert รวม
    # WAREHOUSECODE) แถวที่ "สร้างใหม่" ตรงนี้จะไม่ทับของเดิม แต่ไปตั้งเป็นแถวคู่ขนาน
    # ที่คลังคนละอัน — คู่เดียวกันจึงมีเป้าสองก้อนพร้อมกัน (ดู docs/next-plan-2026-09.md
    # หัวข้อ 11.3 และปริศนา SL453) ยิ่งสำคัญมากสำหรับงวดที่เพิ่งเริ่มมีข้อมูล เพราะ
    # แคชในเครื่องยังไม่มีคลังของใครเลยสักคน ตัวเดา (emp_dims_from_own_grain) จึง
    # พลาดสูงเป็นพิเศษ
    new_rows = 0
    new_rows_with_boxes = 0
    if "dims_inferred" in df.columns:
        # คอลัมน์นี้เป็น object (แถวจากเส้นทางอื่นไม่มีค่า) — เทียบตรง ๆ เลี่ยง
        # การ downcast ที่ pandas เตือนว่าจะเปลี่ยนพฤติกรรมในอนาคต
        _new_mask = df["dims_inferred"] == True  # noqa: E712
        new_rows = int(_new_mask.sum())
        if new_rows:
            new_rows_with_boxes = int(
                (_new_mask & (pd.to_numeric(df["allocated_boxes"], errors="coerce").fillna(0) > 0)).sum()
            )
            logger.warning(
                "จะสร้างเป้าใหม่ใน Target Sun %s: %d แถว (%d แถวมีหีบ > 0) "
                "(เติมเขต/พื้นที่จากแถวอื่นของพนักงานคนเดียวกัน)",
                str(req.sup_id or "").strip().upper(),
                new_rows,
                new_rows_with_boxes,
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
    final = out[LAKEHOUSE_CSV_COLUMNS]
    # เมทาดาทาเสริม ไม่ใช่คอลัมน์ข้อมูล — ผู้เรียกที่สนใจ (เตรียม/ส่งจริง) อ่านผ่าน
    # .attrs ได้โดยไม่ต้องเปลี่ยน signature ของฟังก์ชันนี้ (ผู้เรียกเดิม 9+ จุด
    # unpack เป็น 4-tuple ตายตัวอยู่แล้ว เพิ่มค่าคืนที่ 5 จะพังของเดิมทั้งหมด)
    final.attrs["new_rows_count"] = new_rows
    final.attrs["new_rows_with_boxes_count"] = new_rows_with_boxes
    final.attrs["stale_rows_cleared_count"] = stale_rows_cleared
    # คีย์เต็มของทุกแถวที่กำลังจะส่ง (sku + คีย์ upsert 6 ตัว) — ใช้เทียบกับ "ก่อนส่ง"
    # ตอน verify_row_count_after_send หาว่ากี่แถวที่ Target Sun ยังไม่เคยมี (คีย์เดียว
    # กับ _live_target_row_key ทุกประการ ไม่งั้นสองฝั่ง drift แล้วฟ้องเท็จ)
    final.attrs["import_row_keys"] = import_row_key_series(final).tolist()
    if enforce_targets:
        # เส้นทางส่งจริง: ผิดแถวเดียว = ไม่ส่งทั้งไฟล์ (ผู้ใช้ขอ 29 ก.ย. 2026)
        assert_rows_importable(final, req.sup_id)
    final.attrs["wh_pin_matched_groups"] = wh_pin_stats["matched_groups"]
    final.attrs["wh_pin_boxes_moved"] = wh_pin_stats["boxes_moved"]
    final.attrs["wh_pin_rows_zeroed"] = wh_pin_stats["rows_zeroed"]
    final.attrs["wh_pin_new_warehouse_legs"] = wh_pin_stats["new_warehouse_legs"]
    # ส่งรวมภาค: ยอดรายทีมที่ต่างจากเป้าทีม (ไม่บล็อกที่ด่านนี้) — ให้ bundle จดไว้
    final.attrs["team_target_mismatches"] = team_target_mismatches
    # ส่งทุกแบรนด์ทุกสินค้า = ด่านยอดรวมทั้งภาคต้องตรวจ SKU ที่มีเป้าแต่ไม่อยู่ในไฟล์ด้วย
    final.attrs["full_send"] = (brand_filter or "ALL").upper() == "ALL" and not sku_filter
    return final, dropped_dims, not_in_ts, shortfall


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
    live_grain: pd.DataFrame | None = None,
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
        live_grain=live_grain,
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
