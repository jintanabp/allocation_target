"""Admin — รายชื่อพนักงานใต้ Supervisor (cache + auto-refresh จาก Fabric)"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from fastapi import HTTPException

from ..core.paths import emp_cache_path
from .access_hierarchy import load_hierarchy_payload, parse_hierarchy_metadata

logger = logging.getLogger("target_allocation")

_EMP_COLS = ["emp_id", "emp_name", "super_code"]


def admin_team_cache_ttl_sec() -> int:
    raw = (os.environ.get("ADMIN_TEAM_CACHE_TTL_SEC") or "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    raw2 = (os.environ.get("EMPLOYEE_PAYLOAD_CACHE_TTL_SEC") or "900").strip()
    try:
        return int(raw2)
    except ValueError:
        return 900


def list_supervisor_codes() -> list[dict[str, str]]:
    mdata = load_hierarchy_payload()
    rows = mdata.get("rows") or []
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        sc = str(r.get("supervisor_code") or "").strip().upper()
        if not sc or sc in seen:
            continue
        seen.add(sc)
        mc = str(r.get("manager_code") or r.get("depend_on") or "").strip().upper()
        out.append({"supervisor_code": sc, "manager_code": mc})
    out.sort(key=lambda x: x["supervisor_code"])
    return out


def _df_to_employees(df: pd.DataFrame) -> list[dict[str, str]]:
    if df is None or df.empty:
        return []
    records: list[dict[str, str]] = []
    for _, row in df.iterrows():
        emp_id = str(row.get("emp_id") or row.get("SalesmanCode") or "").strip()
        if not emp_id:
            continue
        records.append(
            {
                "emp_id": emp_id,
                "emp_name": str(row.get("emp_name") or row.get("SalesmanName") or "").strip(),
                "super_code": str(row.get("super_code") or row.get("SuperCode") or "").strip(),
            }
        )
    return records


def _read_fresh_emp_cache(cache_path: str, ttl_sec: int) -> tuple[pd.DataFrame | None, str | None]:
    if ttl_sec <= 0 or not os.path.isfile(cache_path):
        return None, None
    try:
        mtime = os.path.getmtime(cache_path)
    except OSError:
        return None, None
    age_sec = datetime.now(timezone.utc).timestamp() - mtime
    if age_sec > ttl_sec:
        return None, datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
    try:
        df = pd.read_csv(cache_path, dtype={"emp_id": str})
        cached_at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
        return df, cached_at
    except (OSError, pd.errors.EmptyDataError, ValueError) as e:
        logger.warning("emp cache read failed %s: %s", cache_path, e)
        return None, None


def _fetch_from_fabric(super_code: str) -> tuple[pd.DataFrame, str]:
    from ..fabric_dax_connector import FabricDAXConnector

    fabric = FabricDAXConnector()
    df = fabric.get_employees_by_manager(super_code)
    try:
        sup_name = fabric.get_supervisor_name(super_code)
    except Exception:
        sup_name = ""
    return df, sup_name


def load_supervisor_team(
    super_code: str,
    *,
    target_year: int,
    target_month: int,
    force_refresh: bool = False,
) -> dict[str, Any]:
    sc = str(super_code or "").strip().upper()
    if not sc:
        raise HTTPException(status_code=400, detail="super_code ไม่ถูกต้อง")

    ttl = admin_team_cache_ttl_sec()
    cache_path = emp_cache_path(sc, target_month, target_year)
    now_iso = datetime.now(timezone.utc).isoformat()
    sup_name = ""

    if not force_refresh:
        df_cached, cached_at = _read_fresh_emp_cache(cache_path, ttl)
        if df_cached is not None and not df_cached.empty:
            return {
                "super_code": sc,
                "super_name": sup_name,
                "target_year": target_year,
                "target_month": target_month,
                "employees": _df_to_employees(df_cached),
                "employee_count": len(df_cached),
                "from_cache": True,
                "cached_at": cached_at,
                "fetched_at": None,
                "cache_path": cache_path,
            }

    try:
        df_fabric, sup_name = _fetch_from_fabric(sc)
    except Exception as e:
        df_stale, cached_at = _read_fresh_emp_cache(cache_path, ttl_sec=10**9)
        if df_stale is not None and not df_stale.empty:
            logger.warning("Fabric failed — stale emp cache %s: %s", cache_path, e)
            return {
                "super_code": sc,
                "super_name": sup_name,
                "target_year": target_year,
                "target_month": target_month,
                "employees": _df_to_employees(df_stale),
                "employee_count": len(df_stale),
                "from_cache": True,
                "cached_at": cached_at,
                "fetched_at": None,
                "cache_path": cache_path,
                "fabric_error": str(e),
            }
        raise HTTPException(status_code=503, detail=f"ไม่สามารถดึงพนักงานจาก Fabric: {e}") from e

    if df_fabric.empty:
        raise HTTPException(status_code=404, detail=f"ไม่พบพนักงานใต้ SuperCode '{sc}'")

    os.makedirs("data", exist_ok=True)
    df_fabric.to_csv(cache_path, index=False)

    return {
        "super_code": sc,
        "super_name": sup_name,
        "target_year": target_year,
        "target_month": target_month,
        "employees": _df_to_employees(df_fabric),
        "employee_count": len(df_fabric),
        "from_cache": False,
        "cached_at": now_iso,
        "fetched_at": now_iso,
        "cache_path": cache_path,
    }
