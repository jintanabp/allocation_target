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


def fallback_to_fabric() -> bool:
    return os.environ.get("TARGETSUN_READ_FALLBACK_FABRIC", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


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


def fetch_target_rows(
    target_year: int,
    target_month: int,
    salesman_codes: list[str],
    *,
    include_zero_quantity: bool = True,
    filter_by_effective_date: bool = True,
) -> dict:
    codes = sorted(
        {
            c
            for c in (_normalize_salesman_code(x) for x in salesman_codes)
            if c
        }
    )
    if not codes:
        raise HTTPException(400, detail="ไม่มีรหัสพนักงานสำหรับดึงเป้า")

    body = _request_json(
        "POST",
        "targetSalesmanNext/query",
        json_body={
            "targetYear": int(target_year),
            "targetMonth": int(target_month),
            "salesmanCodes": codes,
            "includeZeroQuantity": bool(include_zero_quantity),
            "filterByEffectiveDate": bool(filter_by_effective_date),
        },
    )
    result = body.get("result") or {}
    if not isinstance(result, dict):
        result = {}
    return result


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
