"""
อ่านเป้าหีบจาก Target Sun Read API (Oracle ผ่าน SPC).

เอกสาร: docs/TARGETSUN_READ_API_SPEC.md
UAT: https://spcuatws.sahapat.com/spc/targetsun
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import pandas as pd
import requests
from fastapi import HTTPException
from requests import exceptions as req_exc

from .user_access_store import normalize_userpl, read_rows

from .targetsun_endpoints import targetsun_read_api_base

logger = logging.getLogger("target_allocation")

_GRAIN_COLS = [
    "emp_id",
    "sku",
    "qty",
    "salestype",
    "divisioncode",
    "areacode",
    "provincecode",
    "warehouse_code",
]

_LIVE_CACHE: dict[str, tuple[float, dict]] = {}
_LIVE_CACHE_TTL_SEC = 60


def is_enabled() -> bool:
    """ค่าเริ่มต้น = Target Sun; แอดมินสลับเป็น fabric ได้ใน app_runtime.json"""
    from .app_runtime_settings import get_target_read_source

    if get_target_read_source() == "fabric":
        return False
    env = os.environ.get("TARGETSUN_READ_ENABLED", "").strip().lower()
    if env in ("0", "false", "no", "off"):
        return False
    if env in ("1", "true", "yes", "on"):
        return True
    return True


def get_target_read_source() -> str:
    from .app_runtime_settings import get_target_read_source as _src

    return _src()


_FALLBACK_ENV_WARNED = False


def fallback_to_fabric() -> bool:
    """
    อ่านเป้าจาก Target Sun ไม่ได้ → หันไปดึงเป้าจาก Fabric แทน? — **ปิดถาวรแล้ว** (ผู้ใช้ตัดสิน 6 ต.ค. 2026)

    ไม่ใช้ Fabric อ่านเป้าแล้ว (ยอดขายย้อนหลัง/ราคายังมาจาก Fabric ตามเดิม) · เดิมค่าเริ่มต้นเปิด และ
    เอกสาร IT ให้ตั้ง TARGETSUN_READ_FALLBACK_FABRIC=1 ไว้ใน .env — Target Sun ล่มชั่วคราวเมื่อไร
    ระบบเอาเป้าจาก Fabric (ที่ไม่ได้ดูแลแล้ว) มาใช้เงียบ ๆ ผู้ใช้ไม่รู้ว่าตัวเลขมาจากไหน
    ตอนนี้: อ่าน Target Sun ไม่ได้ = บอกผู้ใช้ให้ลองใหม่ · ค่าใน .env ถูกเมิน (ไม่ต้องไปแก้ .env)
    """
    global _FALLBACK_ENV_WARNED
    if not _FALLBACK_ENV_WARNED and os.environ.get("TARGETSUN_READ_FALLBACK_FABRIC", "").strip().lower() in (
        "1", "true", "yes", "on",
    ):
        _FALLBACK_ENV_WARNED = True
        logger.info("TARGETSUN_READ_FALLBACK_FABRIC ใน .env ถูกเมินแล้ว — ไม่ดึงเป้าจาก Fabric อีก (6 ต.ค. 2026)")
    return False


def _read_base_url() -> str:
    return targetsun_read_api_base()


def _timeout_sec() -> int:
    try:
        t = int(os.environ.get("TARGETSUN_READ_TIMEOUT_SEC", "120"))
    except ValueError:
        t = 120
    return max(10, min(t, 600))


def _verify_ssl() -> bool:
    return os.environ.get("TARGETSUN_READ_VERIFY_SSL", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _auth_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    auth_raw = (os.environ.get("TARGETSUN_READ_AUTH_HEADER") or "").strip()
    if auth_raw:
        headers["Authorization"] = auth_raw if " " in auth_raw else f"Bearer {auth_raw}"
    return headers


def _normalize_salesman_code(code: str) -> str:
    s = str(code or "").strip().upper()
    if s.isdigit():
        return s.zfill(5)
    return s


def _acc_division_to_code(acc_division: str) -> str:
    div = str(acc_division or "").strip()
    if not div:
        return ""
    if div.upper().startswith("DIV."):
        return div.split(".", 1)[-1].strip().upper()[:1]
    return div.upper()[:1]


# ตัวย่อ salesType ของ Target Sun สลับกับที่ตัวอักษรชวนให้เดา:
# "C" คือ van (รถเงินสด) ไม่ใช่ credit · "S" คือ credit (เครดิต) ไม่ใช่ sales
# อ่านผิดกันมาแล้ว จึงมีตารางแปลงเป็นชื่อเต็มไว้ใช้ตอนแสดงผลโดยเฉพาะ
SALES_TYPE_LABEL_TH = {
    "C": "รถเงินสด (van)",
    "S": "เครดิต (credit)",
}


def sales_type_label(sales_type: str) -> str:
    """ชื่อหน่วยขายแบบเต็มสำหรับแสดงบนหน้าจอ — ไม่ใช้ตัดสินใจอะไรทั้งสิ้น"""
    st = str(sales_type or "").strip().upper()[:1]
    return SALES_TYPE_LABEL_TH.get(st, st or "—")


def _acc_unit_to_sales_type(acc_unit: str) -> str:
    u = str(acc_unit or "").strip().lower()
    if u == "credit":
        return "S"
    if u == "van":
        return "C"
    return ""


def _fabric_sales_type_to_code(sales_type: int | None) -> str:
    if sales_type == 0:
        return "S"
    if sales_type == 1:
        return "C"
    return ""


def sales_type_from_fabric_dim(sup_id: str, fabric) -> str:
    """
    หน่วยขาย (S/C) ของทีมจาก Dim ของ Fabric — "" ถ้าหาไม่ได้ · ไม่ raise

    ใช้ตอนอ่านเป้าจาก Fabric (ไม่ผ่าน resolve_targetsun_scope) และ acc_unit ในทะเบียนว่าง
    (ผลตรวจ 1 ต.ค. 2026 ข11) — เดิมตกไปราคาเครดิตเงียบ ๆ ทีมรถเงินสดได้เป้าเงินผิดราคา
    """
    if fabric is None:
        return ""
    upl = normalize_userpl(sup_id)
    try:
        for r in fabric.get_dim_salesman_supervisor_index() or []:
            sc = str(r.get("super_code") or r.get("SuperCode") or "").strip().upper()
            if sc == upl:
                st = _fabric_sales_type_to_code(r.get("sales_type"))
                if st:
                    return st
    except Exception as e:
        logger.warning("sales_type_from_fabric_dim failed (%s): %s", sup_id, e)
    return ""


def resolve_targetsun_scope(
    sup_id: str,
    *,
    fabric=None,
) -> tuple[str, str]:
    """
    คืน (divisionCode, salesType) สำหรับ maxEffectiveDate / context.
  จาก user_access.json ก่อน แล้ว fallback Fabric dim
    """
    upl = normalize_userpl(sup_id)
    division = ""
    sales_type = ""

    for row in read_rows():
        if normalize_userpl(row.get("userpl")) != upl:
            continue
        if not division:
            division = _acc_division_to_code(str(row.get("acc_division") or ""))
        if not sales_type:
            sales_type = _acc_unit_to_sales_type(str(row.get("acc_unit") or ""))
        if division and sales_type:
            break

    if (not division or not sales_type) and fabric is not None:
        try:
            dim_rows = fabric.get_dim_salesman_supervisor_index()
            for r in dim_rows or []:
                sc = str(r.get("super_code") or r.get("SuperCode") or "").strip().upper()
                if sc != upl:
                    continue
                if not sales_type:
                    sales_type = _fabric_sales_type_to_code(r.get("sales_type"))
                if division and sales_type:
                    break
        except Exception as e:
            logger.warning("resolve_targetsun_scope dim fallback failed: %s", e)

    if not division or not sales_type:
        raise HTTPException(
            status_code=400,
            detail=(
                f"ไม่ทราบ division/หน่วยขายของทีม {upl} "
                "— ตรวจ acc_division และ acc_unit ใน user_access.json"
            ),
        )
    return division, sales_type


def _request_json(
    method: str,
    path: str,
    *,
    params: dict | None = None,
    json_body: dict | None = None,
) -> dict:
    url = f"{_read_base_url()}/{path.lstrip('/')}"
    headers = {"Content-Type": "application/json", **_auth_headers()}
    timeout = _timeout_sec()
    verify = _verify_ssl()
    t0 = time.perf_counter()

    try:
        r = requests.request(
            method.upper(),
            url,
            params=params,
            json=json_body,
            headers=headers,
            timeout=timeout,
            verify=verify,
        )
    except req_exc.SSLError as e:
        logger.exception("TargetSun read SSL error: %s", e)
        raise HTTPException(502, detail="เชื่อม Target Sun ไม่สำเร็จ (SSL)") from e
    except (req_exc.ConnectTimeout, req_exc.ReadTimeout) as e:
        logger.exception("TargetSun read timeout: %s", e)
        raise HTTPException(504, detail="ดึงเป้าจาก Target Sun เกินเวลา — ลองใหม่อีกครั้ง") from e
    except req_exc.ConnectionError as e:
        logger.exception("TargetSun read connection error: %s", e)
        raise HTTPException(502, detail="เชื่อม Target Sun ไม่ได้ — ตรวจเครือข่ายหรือ URL") from e
    except requests.RequestException as e:
        logger.exception("TargetSun read request error: %s", e)
        raise HTTPException(502, detail="เรียก Target Sun Read API ไม่สำเร็จ") from e

    logger.info(
        "TargetSun read: %s %s http=%s elapsed=%.2fs",
        method.upper(),
        path,
        r.status_code,
        time.perf_counter() - t0,
    )

    try:
        body = r.json()
    except Exception as e:
        logger.warning("TargetSun read non-JSON HTTP %s: %s", r.status_code, (r.text or "")[:300])
        raise HTTPException(
            502,
            detail="Target Sun คืนค่าที่อ่านไม่ได้ — ติดต่อผู้ดูแลระบบ",
        ) from e

    if r.status_code == 400:
        msg = str(body.get("resultMsg") or "คำขอไม่ถูกต้อง")
        raise HTTPException(400, detail=msg)

    if r.status_code >= 500:
        msg = str(body.get("resultMsg") or f"HTTP {r.status_code}")
        raise HTTPException(502, detail=msg)

    if not body.get("success"):
        msg = str(body.get("resultMsg") or "Target Sun ปฏิเสธคำขอ")
        raise HTTPException(502, detail=msg)

    return body


def fetch_max_effective_date(division_code: str, sales_type: str) -> dict:
    div = str(division_code or "").strip().upper()[:1]
    st = str(sales_type or "").strip().upper()[:1]
    body = _request_json(
        "GET",
        "targetSalesmanNext/maxEffectiveDate",
        params={"divisionCode": div, "salesType": st},
    )
    result = body.get("result") or {}
    if not isinstance(result, dict):
        result = {}
    return result


#: เพดานรหัสพนักงานต่อคำขอ (TARGETSUN_READ_API_SPEC.md ข้อ 7)
_READ_MAX_CODES = 200
#: กันวนไม่รู้จบถ้าปลายทางตอบ hasMore ค้าง
_READ_MAX_PAGES = 50


def _int_or_none(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def fetch_target_rows(
    target_year: int,
    target_month: int,
    salesman_codes: list[str],
    *,
    include_zero_quantity: bool = True,
    filter_by_effective_date: bool = True,
) -> dict:
    """
    อ่านแถวเป้าของพนักงานชุดหนึ่ง — แบ่งคำขอชุดละ 200 รหัส และตามหน้าถัดไปถ้ามี

    result["complete"] = False เมื่ออ่านได้ไม่ครบ ได้แก่
      - ปลายทางบอกจำนวน (rowCount / totalRows) ไม่ตรงกับแถวที่ได้จริง
      - hasMore ค้างเกินเพดานหน้า หรือหน้าถัดไปซ้ำหน้าเดิม (ปลายทางไม่รองรับ page)
    ผู้เรียกที่ใช้นับแถว (ตรวจก่อน/หลังส่ง) ต้องถือว่า "ตรวจไม่ได้" ไม่ใช่นับจากของที่ขาด
    เดิมยิงคำขอเดียวแล้วใช้ rows ที่ได้เลย ถ้าปลายทางตัดหน้า ตัวเลขจะน้อยกว่าจริงแบบเงียบ ๆ
    """
    codes = sorted(
        {
            c
            for c in (_normalize_salesman_code(x) for x in salesman_codes)
            if c
        }
    )
    if not codes:
        raise HTTPException(400, detail="ไม่มีรหัสพนักงานสำหรับดึงเป้า")

    merged: dict[str, Any] = {}
    all_rows: list[Any] = []
    complete = True
    for i in range(0, len(codes), _READ_MAX_CODES):
        chunk = codes[i:i + _READ_MAX_CODES]
        chunk_rows: list[Any] = []
        reported: int | None = None
        prev_first: Any = None
        page = 1
        while True:
            payload: dict[str, Any] = {
                "targetYear": int(target_year),
                "targetMonth": int(target_month),
                "salesmanCodes": chunk,
                "includeZeroQuantity": bool(include_zero_quantity),
                "filterByEffectiveDate": bool(filter_by_effective_date),
            }
            if page > 1:
                payload["page"] = page
            body = _request_json("POST", "targetSalesmanNext/query", json_body=payload)
            result = body.get("result") or {}
            if not isinstance(result, dict):
                result = {}
            if not merged:
                merged = {k: v for k, v in result.items() if k != "rows"}
            rows = result.get("rows")
            rows = rows if isinstance(rows, list) else []
            if page > 1 and rows and rows[0] == prev_first:
                # ปลายทางไม่สนพารามิเตอร์ page แล้วส่งหน้าเดิมกลับมา — หยุด ไม่นับซ้ำ
                logger.warning("TargetSun read: หน้า %d ซ้ำหน้าเดิม — ถือว่าอ่านไม่ครบ", page)
                complete = False
                break
            prev_first = rows[0] if rows else None
            chunk_rows.extend(rows)
            total = _int_or_none(result.get("totalRows"))
            if total is None and page == 1 and not result.get("hasMore"):
                total = _int_or_none(result.get("rowCount"))
            if total is not None:
                reported = total
            if result.get("hasMore") and rows:
                page += 1
                if page > _READ_MAX_PAGES:
                    logger.warning("TargetSun read: hasMore เกิน %d หน้า — ถือว่าอ่านไม่ครบ", _READ_MAX_PAGES)
                    complete = False
                    break
                continue
            break
        if reported is not None and reported != len(chunk_rows):
            logger.warning(
                "TargetSun read: ปลายทางบอก %d แถว แต่ได้จริง %d แถว — ถือว่าอ่านไม่ครบ",
                reported, len(chunk_rows),
            )
            complete = False
        all_rows.extend(chunk_rows)

    merged["rows"] = all_rows
    merged["rowCount"] = len(all_rows)
    merged["complete"] = complete
    return merged


def rows_to_granular_df(rows: list[dict[str, Any]] | None) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=_GRAIN_COLS)

    out_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        wh = row.get("WAREHOUSECODE")
        out_rows.append(
            {
                "emp_id": _normalize_salesman_code(row.get("SALESMANCODE", "")),
                "sku": str(row.get("PRODUCTCODE") or "").strip(),
                "qty": int(float(row.get("QUANTITYCASE") or 0)),
                "salestype": str(row.get("SALESTYPE") or "").strip().upper(),
                "divisioncode": str(row.get("DIVISIONCODE") or "").strip().upper(),
                "areacode": str(row.get("AREACODE") or "").strip(),
                "provincecode": str(row.get("PROVINCECODE") or "").strip(),
                "warehouse_code": (
                    "" if wh is None else str(wh).strip()
                ),
            }
        )

    df = pd.DataFrame(out_rows)
    if df.empty:
        return pd.DataFrame(columns=_GRAIN_COLS)
    for c in _GRAIN_COLS:
        if c not in df.columns:
            df[c] = ""
    return df[_GRAIN_COLS]


def granular_df_for_team(
    emp_list: list,
    target_month: int,
    target_year: int,
    *,
    sup_id: str,
    fabric=None,
) -> pd.DataFrame:
    division, sales_type = resolve_targetsun_scope(sup_id, fabric=fabric)
    from ..core.tga_period import enforce_tga_selection_matches_effective_window_ts

    enforce_tga_selection_matches_effective_window_ts(
        division,
        sales_type,
        target_month,
        target_year,
    )

    codes = [_normalize_salesman_code(e) for e in emp_list if str(e).strip()]
    result = fetch_target_rows(target_year, target_month, codes)
    if result.get("complete") is False:
        # อ่านไม่ครบ (หน้าวน/ชนเพดานหน้า/จำนวนไม่ตรง) — ห้ามสร้างเป้าขั้นที่ 1 จากข้อมูลที่ขาด
        # (ผลตรวจ 7 ต.ค. 2026 ข13) เดิมใช้ต่อเงียบ ๆ เป้าทีมขาด และเป้าตั้งต้นที่จับครั้งแรกขาดถาวร
        logger.error(
            "TargetSun query ไม่ครบ: sup=%s period=%02d/%d rows=%d total_qty=%s",
            sup_id, target_month, target_year, len(result.get("rows") or []), result.get("totalQuantityCase"),
        )
        raise HTTPException(
            503,
            detail="อ่านเป้าจาก Target Sun ได้ไม่ครบ — ระบบยังไม่ใช้ข้อมูลชุดนี้ กรุณาลองโหลดใหม่อีกครั้งในอีกสักครู่",
        )
    rows = result.get("rows") or []
    df = rows_to_granular_df(rows if isinstance(rows, list) else [])
    logger.info(
        "TargetSun query: sup=%s period=%02d/%d rows=%d total_qty=%s",
        sup_id,
        target_month,
        target_year,
        len(df),
        result.get("totalQuantityCase"),
    )
    return df


def try_granular_df_for_team(
    emp_list: list,
    target_month: int,
    target_year: int,
    *,
    sup_id: str,
    fabric=None,
) -> pd.DataFrame | None:
    """คืน None เมื่อล้มเหลวแบบเครือข่าย/API และ fallback Fabric เปิดอยู่"""
    try:
        return granular_df_for_team(
            emp_list,
            target_month,
            target_year,
            sup_id=sup_id,
            fabric=fabric,
        )
    except HTTPException as ex:
        if ex.status_code in (409, 400):
            raise
        if fallback_to_fabric():
            logger.warning(
                "TargetSun read failed for %s (HTTP %s) — fallback to Fabric TGA",
                sup_id,
                ex.status_code,
            )
            return None
        raise
    except Exception as e:
        if fallback_to_fabric():
            logger.warning("TargetSun read error for %s: %s — fallback Fabric", sup_id, e)
            return None
        raise


def _live_cache_key(sup_id: str, target_month: int, target_year: int) -> str:
    return f"{sup_id.upper()}:{target_year}:{int(target_month)}"


def read_live_cache(
    sup_id: str,
    target_month: int,
    target_year: int,
) -> dict | None:
    key = _live_cache_key(sup_id, target_month, target_year)
    hit = _LIVE_CACHE.get(key)
    if not hit:
        return None
    ts, payload = hit
    if (time.time() - ts) > _LIVE_CACHE_TTL_SEC:
        _LIVE_CACHE.pop(key, None)
        return None
    return payload


def write_live_cache(
    sup_id: str,
    target_month: int,
    target_year: int,
    payload: dict,
) -> None:
    key = _live_cache_key(sup_id, target_month, target_year)
    _LIVE_CACHE[key] = (time.time(), payload)


def fetch_targetsun_periods_overview() -> list[dict[str, Any]]:
    """งวดล่าสุดที่มีข้อมูลเป้าใน Target Sun ต่อ division × salesType."""
    from backend.services.user_access_store import read_rows

    combos: set[tuple[str, str]] = set()
    for row in read_rows():
        div = _acc_division_to_code(row.get("acc_division"))
        st = _acc_unit_to_sales_type(row.get("acc_unit"))
        if div and st:
            combos.add((div, st))
    if not combos:
        combos = {("B", "S"), ("B", "C"), ("E", "S"), ("S", "C"), ("S", "S")}

    out: list[dict[str, Any]] = []
    for division_code, sales_type in sorted(combos):
        # เขียนชื่อหน่วยเต็ม ไม่ใช้ตัวย่อ C/S บนหน้าจอ — ตัวย่อสลับกับที่คนเดา
        label = f"Div.{division_code} · {sales_type_label(sales_type)}"
        entry: dict[str, Any] = {
            "division_code": division_code,
            "sales_type": sales_type,
            "sales_type_label": sales_type_label(sales_type),
            "label": label,
            "max_effective_date": None,
            "target_year": None,
            "target_month": None,
            "error": None,
        }
        try:
            result = fetch_max_effective_date(division_code, sales_type)
            entry["max_effective_date"] = result.get("maxEffectiveDate")
            entry["target_year"] = result.get("impliedTargetYear")
            entry["target_month"] = result.get("impliedTargetMonth")
        except Exception as exc:
            entry["error"] = str(exc)
        out.append(entry)
    return out
