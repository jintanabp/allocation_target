import logging
import os
from typing import Any

import pandas as pd
from fastapi import HTTPException

from ..core.allocation_checks import detect_new_product_skus
from ..core.constants import PRICE_FALLBACK
from ..core.paths import (
    emp_cache_path,
    hist_cache_path,
    hist_calendar_year_cache_path,
    hist_ly_same_month_cache_path,
    hist_prev_month_cache_path,
    tga_grain_cache_path,
)
from ..core.employee_filter import (
    drop_van_employees,
    filter_employees_for_display,
    is_allocation_eligible,
)
from ..core.targets import load_target_csv
from ..core.tga_period import (
    enforce_tga_has_targets_for_period,
    enforce_tga_selection_matches_effective_window,
)

_SKU_OUTPUT_COLUMNS = [
    "sku",
    "price_per_box",
    "price_missing",
    "price_from_sales_history",
    "supervisor_target_boxes",
    "brand_name_thai",
    "brand_name_english",
    "section",
    "product_name_thai",
    "product_name_english",
]
from ..fabric_dax_connector import FabricDAXConnector
from .sku_link_store import (
    collapse_hist_to_canonical,
    expand_skus_for_dax,
    extra_aliases_for_canonical,
    read_links,
)
from .wh_split import expand_employee_rows, warehouses_per_emp_from_tga
from .employee_payload_cache import (
    read_cached_employee_payload,
    write_cached_employee_payload,
)
from . import targetsun_read

logger = logging.getLogger("target_allocation")


def _build_sku_and_sun_from_tga(
    df_tga: pd.DataFrame,
    df_product: pd.DataFrame,
    emp_list: list,
    sku_list: list,
    price_latest_by_sku: dict[str, float] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, set[str]]:
    """
    จาก TGA (จำนวนหีบ = QUANTITYCASE ต่อคู่ emp×sku):
    - supervisor_target_boxes ต่อ SKU = SUM หีบของทีมต่อ SKU
    - target_sun ต่อคน = SUM(หีบ × ราคา/หีบ) รายพนักงาน
      ราคา: หลัก cfm_product_characteristic[CREDITUNITPRICE] (PRODUCTSIZE=0, PRODUCTCODE);
      ไม่มี → Amount÷Qty ประวัติ (ไฮไลต์ฟ้า); ไม่มีเลย → 0 + เหลือง
    """
    sku_list = [str(s).strip() for s in sku_list if str(s).strip()]
    team_set = set(str(e).strip() for e in emp_list)

    if not sku_list:
        df_sku = pd.DataFrame(columns=_SKU_OUTPUT_COLUMNS)
        df_sun = pd.DataFrame(
            [{"emp_id": str(e).strip(), "target_sun": 0.0} for e in emp_list]
        )
        return df_sku, df_sun, set()

    df_p = (
        df_product.copy()
        if df_product is not None and not df_product.empty
        else pd.DataFrame()
    )
    if not df_p.empty:
        df_p["sku"] = df_p["sku"].astype(str).str.strip()

    sum_dict: dict[str, float] = {}
    emp_with_tga: set[str] = set()
    if df_tga is not None and not df_tga.empty:
        d = df_tga.copy()
        d["emp_id"] = d["emp_id"].astype(str).str.strip()
        d["sku"] = d["sku"].astype(str).str.strip()
        sub = d[d["emp_id"].isin(team_set)]
        emp_with_tga = set(sub["emp_id"].unique())
        sum_dict = sub.groupby("sku")["qty"].sum().to_dict()

    rows_sku: list[dict] = []
    for sku in sku_list:
        row_p = df_p[df_p["sku"] == sku] if not df_p.empty else pd.DataFrame()
        price = 0.0
        price_missing = True
        price_from_sales_history = False
        brand_th = brand_en =         pname_th = pname_en = ""
        section = ""
        credit_unit_price = 0.0
        if not row_p.empty:
            r0 = row_p.iloc[0]
            brand_th = str(r0.get("brand_name_thai", "") or "")
            brand_en = str(r0.get("brand_name_english", "") or "")
            pname_th = str(r0.get("product_name_thai", "") or "")
            pname_en = str(r0.get("product_name_english", "") or "")
            section = str(r0.get("section", "") or "").strip()
            credit_unit_price = float(r0.get("credit_unit_price", 0) or 0)
        sk = str(sku).strip()
        sales_price: float | None = None
        if price_latest_by_sku is not None and sk in price_latest_by_sku:
            sales_price = float(price_latest_by_sku.get(sk) or 0.0)
        # หลัก: CREDITUNITPRICE (PRODUCTSIZE=0); สำรอง: Amount÷Qty ประวัติ (ฟ้า); ไม่มีเลย: เหลือง
        if credit_unit_price > 0:
            price = credit_unit_price
            price_missing = False
            price_from_sales_history = False
        elif sales_price is not None and sales_price > 0:
            price = sales_price
            price_missing = False
            price_from_sales_history = True
        else:
            price = 0.0
            price_missing = True
            price_from_sales_history = False
        sup_boxes = int(round(float(sum_dict.get(sku, 0))))
        rows_sku.append(
            {
                "sku": sku,
                "price_per_box": price,
                "price_missing": bool(price_missing),
                "price_from_sales_history": bool(price_from_sales_history),
                "supervisor_target_boxes": max(0, sup_boxes),
                "brand_name_thai": brand_th,
                "brand_name_english": brand_en,
                "section": section,
                "product_name_thai": pname_th,
                "product_name_english": pname_en,
            }
        )

    df_sku = pd.DataFrame(rows_sku)
    price_by_sku = dict(zip(df_sku["sku"].astype(str), df_sku["price_per_box"]))

    sun_map: dict[str, float] = {str(e).strip(): 0.0 for e in emp_list}
    if df_tga is not None and not df_tga.empty:
        d = df_tga.copy()
        d["emp_id"] = d["emp_id"].astype(str).str.strip()
        d["sku"] = d["sku"].astype(str).str.strip()
        d["price"] = d["sku"].map(
            lambda s: float(price_by_sku.get(str(s).strip(), 0.0))
        )
        d["line_value"] = d["qty"] * d["price"]
        g = d.groupby("emp_id", as_index=True)["line_value"].sum()
        for emp in sun_map:
            if emp in g.index:
                sun_map[emp] = round(float(g[emp]), 2)

    df_sun = pd.DataFrame([{"emp_id": k, "target_sun": v} for k, v in sun_map.items()])
    return df_sku, df_sun, emp_with_tga


def _build_allocations_preview_from_grain(
    df_granular: pd.DataFrame | None,
    df_sku: pd.DataFrame,
    emp_list: list[str],
) -> list[dict[str, Any]]:
    """แถว emp×sku (และคลังถ้ามี) สำหรับแสดงตารางก่อนกระจายหีบ"""
    team_set = {str(e).strip() for e in emp_list if str(e).strip()}
    if not team_set or df_granular is None or df_granular.empty:
        return []

    dg = df_granular.copy()
    dg["emp_id"] = dg["emp_id"].astype(str).str.strip()
    dg["sku"] = dg["sku"].astype(str).str.strip()
    dg = dg[dg["emp_id"].isin(team_set)]
    dg["qty"] = pd.to_numeric(dg["qty"], errors="coerce").fillna(0)
    dg = dg[dg["qty"] != 0]
    if dg.empty:
        return []

    if "warehouse_code" not in dg.columns:
        dg["warehouse_code"] = ""
    else:
        dg["warehouse_code"] = dg["warehouse_code"].fillna("").astype(str).str.strip()

    grouped = (
        dg.groupby(["emp_id", "sku", "warehouse_code"], as_index=False)["qty"]
        .sum()
    )

    sku_meta: dict[str, dict[str, Any]] = {}
    if df_sku is not None and not df_sku.empty:
        for row in df_sku.to_dict(orient="records"):
            sku_meta[str(row.get("sku") or "").strip()] = row

    out: list[dict[str, Any]] = []
    for row in grouped.to_dict(orient="records"):
        sku = str(row.get("sku") or "").strip()
        emp_id = str(row.get("emp_id") or "").strip()
        if not sku or not emp_id:
            continue
        boxes = int(round(float(row.get("qty") or 0)))
        if boxes <= 0:
            continue
        meta = sku_meta.get(sku, {})
        out.append(
            {
                "emp_id": emp_id,
                "sku": sku,
                "warehouse_code": str(row.get("warehouse_code") or "").strip(),
                "allocated_boxes": boxes,
                "price_per_box": float(meta.get("price_per_box") or 0),
                "brand_name_thai": str(meta.get("brand_name_thai") or ""),
                "brand_name_english": str(meta.get("brand_name_english") or ""),
                "product_name_thai": str(meta.get("product_name_thai") or ""),
                "baseline_boxes": boxes,
            }
        )
    return out


def _preview_from_grain_cache(
    sup_id: str,
    target_month: int,
    target_year: int,
    skus: list[dict[str, Any]],
    emp_list: list[str],
) -> list[dict[str, Any]]:
    p_grain = tga_grain_cache_path(sup_id, target_month, target_year)
    if not os.path.isfile(p_grain):
        return []
    try:
        dg = pd.read_csv(p_grain, dtype={"emp_id": str, "sku": str})
    except Exception as e:
        logger.warning("read grain cache for preview %s: %s", p_grain, e)
        return []
    df_sku = pd.DataFrame(skus) if skus else pd.DataFrame()
    return _build_allocations_preview_from_grain(dg, df_sku, emp_list)


def _clean(df: pd.DataFrame) -> list:
    """แปลง NaN → None ก่อน serialize เพื่อกัน JSON invalid"""
    return df.where(pd.notna(df), None).to_dict(orient="records")


def _enrich_employee_allocation_flags(
    emp_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """คำนวณ allocation_eligible / include_in_allocation ทุกครั้ง (รวม cache เก่า)"""
    for rec in emp_records:
        eligible = is_allocation_eligible(
            bool(rec.get("has_tga_rows")),
            float(rec.get("target_sun") or 0),
        )
        rec["allocation_eligible"] = eligible
        rec["include_in_allocation"] = eligible
        rec["view_only"] = not eligible
    return emp_records


def load_employees_payload(
    sup_id: str,
    target_month: int,
    target_year: int,
    regen_target: bool = False,
    refresh: bool = False,
) -> dict:
    """
    Logic ของ GET /data/employees (ย้ายออกจาก router เพื่อให้อ่านง่าย)
    ต้องคง behavior เดิม: เขียน cache ที่ data/, สร้าง target_boxes/target_sun, สร้าง history caches

    refresh=True หรือ regen_target=True → ข้าม JSON cache แล้วยิง DAX ใหม่
    """
    if not regen_target and not refresh:
        cached = read_cached_employee_payload(sup_id, target_month, target_year)
        if cached is not None:
            current_src = targetsun_read.get_target_read_source()
            cached_src = str(cached.get("target_read_source") or "fabric").strip().lower()
            if cached_src != current_src:
                logger.info(
                    "payload cache skip %s — source %s != %s",
                    sup_id,
                    cached_src,
                    current_src,
                )
            else:
                emps = cached.get("employees")
                if isinstance(emps, list):
                    cached["employees"] = _enrich_employee_allocation_flags(
                        [dict(e) for e in emps]
                    )
                return cached

    os.makedirs("data", exist_ok=True)

    # ── Step 1: ดึงพนักงาน ───────────────────────────────
    fabric = None
    df_emp_fabric = pd.DataFrame()
    sup_name = ""
    try:
        fabric = FabricDAXConnector()
        df_emp_fabric = fabric.get_employees_by_manager(sup_id)
        try:
            sup_name = fabric.get_supervisor_name(sup_id)
        except Exception:
            sup_name = ""
    except Exception as e:
        cp = emp_cache_path(sup_id, target_month, target_year)
        if os.path.exists(cp):
            logger.warning("Fabric error → emp cache: %s", e)
            df_emp_fabric = pd.read_csv(cp, dtype={"emp_id": str})
        else:
            raise HTTPException(503, detail=f"ไม่สามารถดึงพนักงานได้ และไม่มี cache: {e}")

    if df_emp_fabric.empty:
        raise HTTPException(404, detail=f"ไม่พบพนักงานใต้ SuperCode '{sup_id}'")

    df_emp_fabric, van_excluded = drop_van_employees(df_emp_fabric)
    if van_excluded:
        logger.info(
            "ตัดรหัส V (Van — ไม่แสดง/ไม่คำนวณ): %d คน ใต้ %s",
            van_excluded,
            sup_id,
        )
    if df_emp_fabric.empty:
        raise HTTPException(
            404,
            detail=f"ไม่พบพนักงานใต้ SuperCode '{sup_id}' (หลังตัดรหัส V)",
        )

    emp_list = df_emp_fabric["emp_id"].tolist()
    df_emp_fabric.to_csv(emp_cache_path(sup_id, target_month, target_year), index=False)
    logger.info("Employees: %d คน %s", len(emp_list), emp_list)

    # ── Step 2: เป้าหมาย — ค่าเริ่มต้นจาก Fabric (tga_target_salesman_next) ─────
    use_legacy = os.environ.get("USE_LEGACY_TARGET_CSV", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    df_sku_csv, _df_sun_loaded = load_target_csv()
    emp_with_tga_set: set[str] | None = None
    df_tga_granular: pd.DataFrame | None = None
    df_tga: pd.DataFrame | None = None
    sold_only_excluded = 0

    if use_legacy and df_sku_csv is not None and not regen_target:
        logger.info("ใช้ target_boxes.csv / target_sun.csv (USE_LEGACY_TARGET_CSV)")
        try:
            _gc = [
                "emp_id",
                "sku",
                "qty",
                "salestype",
                "divisioncode",
                "areacode",
                "provincecode",
                "warehouse_code",
            ]
            pd.DataFrame(columns=_gc).to_csv(
                tga_grain_cache_path(sup_id, target_month, target_year),
                index=False,
            )
        except Exception:
            pass
        df_sku = df_sku_csv
        sku_list = df_sku["sku"].tolist()
        df_sun_csv = _df_sun_loaded
        if df_sun_csv is None and os.path.exists("data/target_sun.csv"):
            df_sun_csv = pd.read_csv("data/target_sun.csv", dtype={"emp_id": str}).fillna(0)
            df_sun_csv["emp_id"] = df_sun_csv["emp_id"].astype(str).str.strip()
    else:
        if fabric is None:
            try:
                fabric = FabricDAXConnector()
            except Exception as e:
                raise HTTPException(
                    503,
                    detail=f"ไม่สามารถเชื่อมต่อ Fabric สำหรับดึงเป้าและประวัติ: {e}",
                )
        ts_div = ts_st = None
        ts_max_effective: dict | None = None
        if targetsun_read.is_enabled():
            ts_div, ts_st = targetsun_read.resolve_targetsun_scope(sup_id, fabric=fabric)
            try:
                ts_max_effective = targetsun_read.fetch_max_effective_date(ts_div, ts_st)
            except HTTPException:
                if not targetsun_read.fallback_to_fabric():
                    raise
                logger.warning(
                    "TargetSun maxEffectiveDate failed — skip period pre-check"
                )
                ts_div = ts_st = None
            except Exception as e:
                logger.warning("TargetSun maxEffectiveDate error: %s", e)
                ts_div = ts_st = None

        enforce_tga_selection_matches_effective_window(
            fabric,
            target_month,
            target_year,
            division_code=ts_div if targetsun_read.is_enabled() else None,
            sales_type=ts_st if targetsun_read.is_enabled() else None,
        )
        sold_skus: list[str] = []
        try:
            sold_skus = fabric.get_skus_sold_by_team(
                emp_list, target_month, target_year, n_months=6
            )
        except Exception as e:
            logger.warning("get_skus_sold_by_team error: %s", e)
            sold_skus = []

        df_tga = pd.DataFrame()
        df_tga_granular = pd.DataFrame()
        if targetsun_read.is_enabled():
            df_ts = targetsun_read.try_granular_df_for_team(
                emp_list,
                target_month,
                target_year,
                sup_id=sup_id,
                fabric=fabric,
            )
            if df_ts is not None:
                df_tga_granular = df_ts
                logger.info(
                    "เป้า TGA จาก Target Sun: sup=%s period=%02d/%d rows=%d",
                    sup_id,
                    target_month,
                    target_year,
                    len(df_tga_granular),
                )
            else:
                try:
                    df_tga_granular = fabric.get_tga_target_salesman_granular(
                        emp_list, target_month, target_year
                    )
                except Exception as e:
                    logger.warning(
                        "Fabric TGA fallback error: %s — เป้าจะเป็น 0 ทั้งหมด", e
                    )
        else:
            try:
                df_tga_granular = fabric.get_tga_target_salesman_granular(
                    emp_list, target_month, target_year
                )
            except Exception as e:
                logger.warning(
                    "get_tga_target_salesman_granular error: %s — เป้าจะเป็น 0 ทั้งหมด",
                    e,
                )

        grain_cols = [
            "emp_id",
            "sku",
            "qty",
            "salestype",
            "divisioncode",
            "areacode",
            "provincecode",
            "warehouse_code",
        ]

        try:
            p_grain = tga_grain_cache_path(sup_id, target_month, target_year)
            if df_tga_granular is None or df_tga_granular.empty:
                pd.DataFrame(columns=grain_cols).to_csv(p_grain, index=False)
            else:
                df_tga_granular.to_csv(p_grain, index=False)
            logger.info(
                "tga grain cache: %s (%d rows)", p_grain, len(df_tga_granular)
            )
        except Exception as e:
            logger.warning("tga grain cache write failed: %s", e)

        if df_tga_granular is not None and not df_tga_granular.empty:
            df_tga = (
                df_tga_granular.groupby(["emp_id", "sku"], as_index=False)["qty"]
                .sum()
            )
            df_tga = df_tga[df_tga["qty"] != 0]
        else:
            df_tga = pd.DataFrame(columns=["emp_id", "sku", "qty"])

        tga_skus: list[str] = []
        if df_tga is not None and not df_tga.empty:
            tga_skus = (
                df_tga["sku"].dropna().astype(str).str.strip().unique().tolist()
            )
        # แสดง/เกลี่ย/ส่ง Target Sun เฉพาะ SKU ที่มีเป้า TGA งวดนี้
        # ประวัติขาย (sold_skus) ใช้แค่เป็นน้ำหนักกระจายหีบ — ไม่รวมใน sku_union
        sku_union = list(dict.fromkeys(str(s).strip() for s in tga_skus if str(s).strip()))
        sold_only_excluded = len(set(sold_skus) - set(sku_union)) if sold_skus else 0
        if sold_only_excluded:
            logger.info(
                "SKU ที่เคยขายแต่ไม่มีเป้า TGA งวดนี้ (ไม่แสดง/ไม่เกลี่ย): %d",
                sold_only_excluded,
            )
        if not sku_union:
            logger.warning(
                "SuperCode=%s period=%02d/%d | team=%d | tga_granular=%d | ไม่มี SKU เป้า",
                sup_id,
                target_month,
                target_year,
                len(emp_list),
                len(df_tga_granular) if df_tga_granular is not None else 0,
            )
            enforce_tga_has_targets_for_period(
                fabric,
                target_month,
                target_year,
                df_tga,
                0,
                debug={
                    "supervisor_code": sup_id,
                    "team_size": len(emp_list),
                    "tga_granular_rows": int(
                        len(df_tga_granular) if df_tga_granular is not None else 0
                    ),
                },
                max_effective=ts_max_effective,
            )

        df_sku_base = pd.DataFrame()
        from . import fabric_cache as fc

        if sku_union:
            cached_product = fc.read_product_info_df(target_year, target_month)
            sku_set = (
                set(cached_product["sku"].astype(str))
                if cached_product is not None and not cached_product.empty
                else set()
            )
            if sku_set and all(str(s) in sku_set for s in sku_union):
                df_sku_base = cached_product[cached_product["sku"].astype(str).isin(sku_union)].copy()
            else:
                try:
                    df_fresh = fabric.get_product_info(sku_list=sku_union)
                    if df_fresh is not None and not df_fresh.empty:
                        if cached_product is not None and not cached_product.empty:
                            merged = pd.concat([cached_product, df_fresh]).drop_duplicates(
                                subset=["sku"], keep="last"
                            )
                        else:
                            merged = df_fresh
                        fc.write_product_info_df(target_year, target_month, merged)
                        df_sku_base = merged[merged["sku"].astype(str).isin(sku_union)].copy()
                except Exception as e:
                    logger.warning("get_product_info error: %s", e)

        if df_sku_base.empty and sku_union:
            df_sku_base = pd.DataFrame({"sku": sku_union})

        price_latest = fc.read_price_map(target_year, target_month) or {}
        missing_price_skus = [s for s in sku_union if str(s) not in price_latest]
        if missing_price_skus:
            try:
                df_price = fabric.get_latest_price_per_box_by_sku(
                    target_month, target_year, sku_union
                )
                if df_price is not None and not df_price.empty:
                    fetched = dict(
                        zip(
                            df_price["sku"].astype(str),
                            df_price["price_per_box"].astype(float),
                        )
                    )
                    price_latest = {**price_latest, **fetched}
                    fc.write_price_map(target_year, target_month, price_latest)
            except Exception as e:
                logger.warning(
                    "get_latest_price_per_box_by_sku error: %s (price จะเป็น 0 + flag missing)",
                    e,
                )

        df_sku, df_sun_csv, emp_with_tga = _build_sku_and_sun_from_tga(
            df_tga, df_sku_base, emp_list, sku_union, price_latest_by_sku=price_latest
        )
        emp_with_tga_set = emp_with_tga

        df_sku.to_csv("data/target_boxes.csv", index=False)
        df_sun_csv.to_csv("data/target_sun.csv", index=False)
        logger.info(
            "บันทึกเป้าจาก Fabric (TGA): %d SKU, พนักงาน %d คน, มีแถว TGA %d คน",
            len(df_sku),
            len(df_sun_csv),
            len(emp_with_tga_set),
        )

    if df_sun_csv is None and os.path.exists("data/target_sun.csv"):
        df_sun_csv = pd.read_csv("data/target_sun.csv", dtype={"emp_id": str}).fillna(0)
        df_sun_csv["emp_id"] = df_sun_csv["emp_id"].astype(str).str.strip()

    sku_list = df_sku["sku"].tolist()

    # ── Step 3: merge target_sun ──────────────────────────
    df_emp = df_emp_fabric.copy()
    if df_sun_csv is not None and not df_sun_csv.empty:
        df_emp = pd.merge(
            df_emp, df_sun_csv[["emp_id", "target_sun"]], on="emp_id", how="left"
        )
    if "target_sun" not in df_emp.columns:
        df_emp["target_sun"] = 0.0
    df_emp["target_sun"] = df_emp["target_sun"].fillna(0.0)

    if emp_with_tga_set is not None:
        df_emp["has_tga_rows"] = (
            df_emp["emp_id"].astype(str).str.strip().isin(emp_with_tga_set)
        )
    else:
        df_emp["has_tga_rows"] = True

    df_emp["target_sun"] = pd.to_numeric(df_emp["target_sun"], errors="coerce").fillna(0.0)
    team_size_fabric = len(df_emp)

    ly_sales_by_emp: dict[str, float] = {}
    team_ids_for_ly = df_emp["emp_id"].astype(str).str.strip().tolist()
    if team_ids_for_ly:
        if fabric is None:
            try:
                fabric = FabricDAXConnector()
            except Exception as e:
                logger.warning("Fabric unavailable for LY visibility: %s", e)
        if fabric is not None:
            try:
                df_ly_team = fabric.get_ly_sales(
                    target_month, target_year, emp_list=team_ids_for_ly
                )
                if df_ly_team is not None and not df_ly_team.empty:
                    ly_sales_by_emp = {
                        str(r["emp_id"]).strip(): float(r["ly_sales"] or 0.0)
                        for _, r in df_ly_team.iterrows()
                        if str(r.get("emp_id") or "").strip()
                    }
            except Exception as e:
                logger.warning("get_ly_sales (employee visibility) failed: %s", e)

    df_emp, hidden_no_target, ly_only_shown = filter_employees_for_display(
        df_emp, ly_sales_by_emp
    )
    df_emp["allocation_eligible"] = (
        df_emp["has_tga_rows"].astype(bool) & (df_emp["target_sun"] > 0)
    )
    if hidden_no_target > 0 or ly_only_shown > 0:
        logger.info(
            "กรองพนักงานงวด %02d/%d: ซ่อน %d | แสดงจากยอด LY ไม่มีเป้า %d | เหลือ %d จาก %d คน",
            target_month,
            target_year,
            hidden_no_target,
            ly_only_shown,
            len(df_emp),
            team_size_fabric,
        )
    if df_emp.empty:
        logger.warning(
            "SuperCode=%s | team=%d | ไม่มีพนักงานที่มีเป้าในงวด %02d/%d",
            sup_id,
            team_size_fabric,
            target_month,
            target_year,
        )
    emp_list = df_emp["emp_id"].astype(str).str.strip().tolist()
    excluded_from_allocation = 0

    # ── Step 4: History caches (3M/6M + LY same-month + prev-month) ──
    sku_warnings: list[dict] = []
    sku_links = read_links()
    dax_sku_list = expand_skus_for_dax(sku_list, sku_links)
    if len(dax_sku_list) > len(sku_list):
        logger.info(
            "sku_links: ขยาย SKU สำหรับ DAX %d → %d รหัส",
            len(sku_list),
            len(dax_sku_list),
        )
    df_hist = pd.DataFrame(columns=["emp_id", "sku", "hist_boxes", "hist_amount"])
    df_lysm = pd.DataFrame(columns=["emp_id", "sku", "hist_boxes", "hist_amount"])
    try:
        df_hist = fabric.get_historical_sales(
            target_month,
            target_year,
            sku_list=dax_sku_list,
            emp_list=emp_list,
            n_months=3,
        )
        if df_hist is not None and not df_hist.empty:
            df_hist = collapse_hist_to_canonical(df_hist, sku_links)
            df_hist.to_csv(
                hist_cache_path(sup_id, target_month, target_year, n_months=3),
                index=False,
            )
            logger.info("historical 3M cache saved: %d rows", len(df_hist))
    except Exception as e:
        logger.warning("historical 3M skipped: %s", e)

    try:
        df_hist6 = fabric.get_historical_sales(
            target_month,
            target_year,
            sku_list=dax_sku_list,
            emp_list=emp_list,
            n_months=6,
        )
        if df_hist6 is not None and not df_hist6.empty:
            df_hist6 = collapse_hist_to_canonical(df_hist6, sku_links)
            df_hist6.to_csv(
                hist_cache_path(sup_id, target_month, target_year, n_months=6),
                index=False,
            )
            logger.info("historical 6M cache saved: %d rows", len(df_hist6))
    except Exception as e:
        logger.warning("historical 6M skipped: %s", e)

    try:
        df_lysm = fabric.get_same_month_prior_year_by_emp_sku(
            target_month, target_year, sku_list=dax_sku_list, emp_list=emp_list
        )
        if df_lysm is not None and not df_lysm.empty:
            df_lysm = collapse_hist_to_canonical(df_lysm, sku_links)
            p_lysm = hist_ly_same_month_cache_path(sup_id, target_month, target_year)
            df_lysm.to_csv(p_lysm, index=False)
            logger.info("historical LY same-month cache saved: %d rows → %s", len(df_lysm), p_lysm)
    except Exception as e:
        logger.warning("historical LY same month (emp×sku) skipped: %s", e)

    try:
        df_prev = fabric.get_prev_month_by_emp_sku(
            target_month, target_year, sku_list=dax_sku_list, emp_list=emp_list
        )
        if df_prev is not None and not df_prev.empty:
            df_prev = collapse_hist_to_canonical(df_prev, sku_links)
            p_prev = hist_prev_month_cache_path(sup_id, target_month, target_year)
            df_prev.to_csv(p_prev, index=False)
            logger.info("historical prev-month cache saved: %d rows → %s", len(df_prev), p_prev)
    except Exception as e:
        logger.warning("historical prev month (emp×sku) skipped: %s", e)

    # ── Step 5c: calendar-year caches (CY + LY) — ใช้ตรวจสินค้าใหม่ตอน optimize ──
    try:
        for cy in (int(target_year), int(target_year) - 1):
            df_cy = fabric.get_calendar_year_sales_by_emp_sku(
                cy, sku_list=dax_sku_list, emp_list=emp_list
            )
            pcy = hist_calendar_year_cache_path(sup_id, cy)
            if df_cy is not None and not df_cy.empty:
                df_cy = collapse_hist_to_canonical(df_cy, sku_links)
                df_cy.to_csv(pcy, index=False)
                logger.info(
                    "historical calendar-year %d cache: %d rows → %s",
                    cy,
                    len(df_cy),
                    pcy,
                )
            else:
                pd.DataFrame(
                    columns=["emp_id", "sku", "hist_boxes", "hist_amount"]
                ).to_csv(pcy, index=False)
                logger.info("historical calendar-year %d: empty → %s", cy, pcy)
    except Exception as e:
        logger.warning("historical calendar-year caches skipped: %s", e)

    # ── Step 5b: เติมตัวเลขสรุปให้หน้า Step1 (LY ยอดขาย / เฉลี่ย 3M) ─────────
    # Frontend ใช้ฟิลด์ชื่อ: ly_sales, hist_avg_3m
    df_emp["ly_sales"] = (
        df_emp["emp_id"]
        .astype(str)
        .str.strip()
        .map(lambda e: float(ly_sales_by_emp.get(e, 0.0)))
        .fillna(0.0)
    )
    df_emp["hist_avg_3m"] = 0.0

    try:
        if df_lysm is not None and not df_lysm.empty:
            ly_by_emp_sku = (
                df_lysm.groupby("emp_id", as_index=True)["hist_amount"]
                .sum()
                .astype(float)
                .to_dict()
            )
            for emp_id, amt in ly_by_emp_sku.items():
                eid = str(emp_id).strip()
                cur = float(ly_sales_by_emp.get(eid, 0.0) or 0.0)
                if float(amt or 0.0) > cur:
                    ly_sales_by_emp[eid] = float(amt)
            df_emp["ly_sales"] = (
                df_emp["emp_id"]
                .astype(str)
                .str.strip()
                .map(lambda e: float(ly_sales_by_emp.get(e, 0.0)))
                .fillna(0.0)
            )
            df_emp["ly_sales"] = pd.to_numeric(df_emp["ly_sales"], errors="coerce").fillna(0.0)
    except Exception as e:
        logger.warning("compute ly_sales failed: %s", e)

    try:
        if df_hist is not None and not df_hist.empty:
            avg3_by_emp = (
                (df_hist.groupby("emp_id", as_index=True)["hist_amount"].sum().astype(float) / 3.0)
                .to_dict()
            )
            df_emp["hist_avg_3m"] = (
                df_emp["emp_id"].astype(str).str.strip().map(avg3_by_emp).fillna(0.0)
            )
            df_emp["hist_avg_3m"] = pd.to_numeric(df_emp["hist_avg_3m"], errors="coerce").fillna(0.0)
    except Exception as e:
        logger.warning("compute hist_avg_3m failed: %s", e)

    # ── Step 6: Warehouse ─────────────────────────────────
    try:
        df_wh = fabric.get_warehouse_by_emp(emp_list)
        if not df_wh.empty:
            df_emp = pd.merge(
                df_emp, df_wh[["emp_id", "warehouse_code"]], on="emp_id", how="left"
            )
    except Exception as e:
        logger.warning("warehouse: %s", e)
    if "warehouse_code" not in df_emp.columns:
        df_emp["warehouse_code"] = ""
    df_emp["warehouse_code"] = df_emp["warehouse_code"].fillna("")

    numeric_cols = df_emp.select_dtypes(include=["number"]).columns
    df_emp[numeric_cols] = df_emp[numeric_cols].fillna(0)
    for col in ["emp_name", "manager_code", "warehouse_code"]:
        if col in df_emp.columns:
            df_emp[col] = df_emp[col].fillna("")

    logger.info("Response: %d emp, %d sku", len(df_emp), len(df_sku))

    if van_excluded > 0:
        sku_warnings.append(
            {
                "type": "employees_excluded_van_code",
                "sku": "",
                "brand": "",
                "message": (
                    f"ตัดพนักงานรหัส V (Van) {van_excluded} คน — ไม่แสดงและไม่นำมาคำนวณ"
                ),
            }
        )

    if ly_only_shown > 0:
        sku_warnings.append(
            {
                "type": "employees_shown_ly_no_target",
                "sku": "",
                "brand": "",
                "message": (
                    f"แสดงใน Step 1 เท่านั้น {ly_only_shown} คน "
                    f"(เคยขายเดือนเดียวกันปีที่แล้ว แต่ไม่มีเป้างวดนี้) "
                    f"— ไม่เข้าขั้นกำหนดเป้าและไม่กระจายหีบ"
                ),
            }
        )

    if hidden_no_target > 0:
        sku_warnings.append(
            {
                "type": "employees_hidden_no_target",
                "sku": "",
                "brand": "",
                "message": (
                    f"ซ่อนพนักงาน {hidden_no_target} คนที่ไม่มีเป้าและไม่มียอดขายปีที่แล้ว "
                    f"(แสดง {len(df_emp)} คน)"
                ),
            }
        )

    if not use_legacy and sold_only_excluded > 0:
        sku_warnings.append(
            {
                "type": "sold_only_skus_excluded",
                "sku": "",
                "brand": "",
                "message": (
                    f"มี {sold_only_excluded} SKU ที่ทีมเคยขายใน 6 เดือนย้อนหลัง "
                    "แต่ไม่มีเป้าใน Target Sun งวดนี้ — ไม่แสดงใน Dashboard และไม่ส่งเข้า Target Sun "
                    "(ใช้ประวัติขายเป็นน้ำหนักกระจายหีบเท่านั้น)"
                ),
            }
        )

    if use_legacy and df_sun_csv is not None and not df_sun_csv.empty:
        sun_emp_ids = set(df_sun_csv["emp_id"].astype(str).str.strip())
        fabric_emp_ids = set(str(e) for e in emp_list)
        unmatched = sun_emp_ids - fabric_emp_ids
        if unmatched:
            logger.warning("target_sun emp_id ไม่ตรงกับ Fabric: %s", unmatched)
            sku_warnings.append(
                {
                    "type": "emp_mismatch",
                    "sku": "",
                    "brand": "",
                    "message": f"มี emp_id ใน target_sun.csv ไม่พบใน Fabric: {sorted(list(unmatched))[:20]}",
                }
            )

    if df_hist is None or df_hist.empty:
        sku_warnings.append(
            {
                "type": "no_history",
                "sku": "",
                "brand": "",
                "message": "⚠️ ไม่สามารถดึงประวัติขายจาก Fabric ได้ — การกระจายหีบจะใช้ EVEN แทนประวัติ",
            }
        )

    tga_period_status = "ok"
    if not use_legacy and fabric is not None:
        total_sup_boxes = 0
        if "supervisor_target_boxes" in df_sku.columns:
            total_sup_boxes = int(
                pd.to_numeric(df_sku["supervisor_target_boxes"], errors="coerce")
                .fillna(0)
                .sum()
            )
        enforce_tga_has_targets_for_period(
            fabric,
            target_month,
            target_year,
            df_tga,
            total_sup_boxes,
            debug={
                "supervisor_code": sup_id,
                "team_with_targets": int(df_emp["allocation_eligible"].sum()),
                "sku_count": len(df_sku),
            },
            max_effective=ts_max_effective if targetsun_read.is_enabled() else None,
        )

    if sku_warnings:
        logger.info("reconciliation warnings: %d รายการ", len(sku_warnings))

    # CSV เดิมอาจไม่มี flag ราคา — เติมให้ครบก่อนส่ง JSON
    df_sku = df_sku.copy()
    if "price_from_sales_history" not in df_sku.columns:
        df_sku["price_from_sales_history"] = (
            df_sku["price_from_cfm_cost"].astype(bool)
            if "price_from_cfm_cost" in df_sku.columns
            else False
        )
    if "price_from_cfm_cost" in df_sku.columns:
        df_sku.drop(columns=["price_from_cfm_cost"], inplace=True, errors="ignore")
    if "price_missing" not in df_sku.columns:
        df_sku["price_missing"] = (
            pd.to_numeric(df_sku.get("price_per_box", 0), errors="coerce").fillna(0.0)
            <= 0
        )

    linked_skus = {
        row["canonical_sku"]: extra_aliases_for_canonical(row["canonical_sku"], sku_links)
        for row in sku_links
        if extra_aliases_for_canonical(row["canonical_sku"], sku_links)
    }
    sku_ids_list = df_sku["sku"].astype(str).str.strip().tolist()
    if linked_skus:
        df_sku["linked_history_skus"] = df_sku["sku"].astype(str).str.strip().map(
            lambda s: linked_skus.get(s, [])
        )
        sku_id_set = set(sku_ids_list)
        for canon, aliases in linked_skus.items():
            if canon in sku_id_set:
                sku_warnings.append(
                    {
                        "type": "sku_linked_history",
                        "sku": canon,
                        "brand": "",
                        "message": (
                            f"SKU {canon} ผูกประวัติรหัสเก่า: {', '.join(aliases)}"
                        ),
                    }
                )

    new_product_skus, new_products_detection_mode = detect_new_product_skus(
        sup_id, target_year, sku_ids_list, df_hist
    )

    price_by_sku = dict(
        zip(
            df_sku["sku"].astype(str).str.strip(),
            pd.to_numeric(df_sku["price_per_box"], errors="coerce").fillna(0.0),
        )
    )
    ly_amount_by_emp_wh: dict[tuple[str, str], float] | None = None
    avg3_amount_by_emp_wh: dict[tuple[str, str], float] | None = None
    wh_split_emps = [
        e
        for e, whs in warehouses_per_emp_from_tga(df_tga_granular).items()
        if len(set(whs)) >= 2
    ]
    if wh_split_emps and fabric is not None:
        try:
            df_ly_wh = fabric.get_ly_same_month_amount_by_emp_wh(
                target_month, target_year, wh_split_emps
            )
            if not df_ly_wh.empty:
                ly_amount_by_emp_wh = {
                    (str(r["emp_id"]).strip(), str(r.get("warehouse_code") or "").strip()): float(
                        r.get("hist_amount") or 0.0
                    )
                    for _, r in df_ly_wh.iterrows()
                }
        except Exception as e:
            logger.warning("ly amount by emp×wh skipped: %s", e)
        try:
            df_3m_wh = fabric.get_sales_amount_by_emp_wh(
                target_month, target_year, wh_split_emps, n_months=3
            )
            if not df_3m_wh.empty:
                avg3_amount_by_emp_wh = {
                    (str(r["emp_id"]).strip(), str(r.get("warehouse_code") or "").strip()): float(
                        r.get("hist_amount") or 0.0
                    )
                    / 3.0
                    for _, r in df_3m_wh.iterrows()
                }
        except Exception as e:
            logger.warning("3M amount by emp×wh skipped: %s", e)

    emp_records = expand_employee_rows(
        _clean(df_emp),
        df_tga_granular,
        price_by_sku,
        ly_amount_by_emp_wh=ly_amount_by_emp_wh,
        avg3_amount_by_emp_wh=avg3_amount_by_emp_wh,
    )
    emp_records = _enrich_employee_allocation_flags(emp_records)
    if wh_split_emps:
        sku_warnings.append(
            {
                "type": "wh_split_active",
                "sku": "",
                "brand": "",
                "message": (
                    f"พนักงาน {len(wh_split_emps)} คนมีหลายคลัง — "
                    "แสดงแยกตาม W/H ใน Dashboard"
                ),
            }
        )

    payload = {
        "employees": emp_records,
        "skus": _clean(df_sku),
        "sku_warnings": sku_warnings,
        "tga_period_status": tga_period_status,
        "supervisor_name": sup_name,
        "new_product_skus": new_product_skus,
        "new_products_detection_mode": new_products_detection_mode,
        "target_read_source": targetsun_read.get_target_read_source(),
        "data_from_cache": False,
        "data_cached_at": None,
    }
    write_cached_employee_payload(sup_id, target_month, target_year, payload)
    return payload


def merge_employees_payloads(
    payloads: list[dict[str, Any]],
    *,
    aggregate_label: str,
    aggregate_sup_ids: list[str],
) -> dict[str, Any]:
    """รวมหลาย supervisor payload เป็นมุมมองเดียว (read-only overview)"""
    if not payloads:
        raise HTTPException(404, detail="ไม่มีข้อมูลจาก Supervisor ที่เลือก")

    employees: list[dict[str, Any]] = []
    sku_map: dict[str, dict[str, Any]] = {}
    warnings: list[dict[str, Any]] = []
    new_products: set[str] = set()
    skipped: list[dict[str, str]] = []

    for p in payloads:
        sid = str(p.get("_source_sup_id") or "").strip().upper()
        for emp in p.get("employees") or []:
            row = dict(emp)
            row["supervisor_code"] = sid
            employees.append(row)
        for s in p.get("skus") or []:
            sku = str(s.get("sku") or "").strip()
            if not sku:
                continue
            boxes = float(s.get("supervisor_target_boxes") or 0)
            if sku in sku_map:
                sku_map[sku]["supervisor_target_boxes"] = (
                    float(sku_map[sku].get("supervisor_target_boxes") or 0) + boxes
                )
            else:
                sku_map[sku] = dict(s)
                sku_map[sku]["supervisor_target_boxes"] = boxes
        for w in p.get("sku_warnings") or []:
            row = dict(w)
            if sid and str(row.get("type") or "") != "aggregate_view":
                row["sup_id"] = sid
            warnings.append(row)
        for np in p.get("new_product_skus") or []:
            new_products.add(str(np).strip())

    employees.sort(key=lambda e: (str(e.get("supervisor_code") or ""), str(e.get("emp_id") or "")))
    employees = _enrich_employee_allocation_flags(employees)
    skus = sorted(sku_map.values(), key=lambda s: str(s.get("sku") or ""))

    warnings.insert(
        0,
        {
            "type": "aggregate_view",
            "sku": "",
            "brand": "",
            "message": (
                f"โหมดดูรวม ({aggregate_label}) — {len(aggregate_sup_ids)} ซุป, "
                f"{len(employees)} พนักงาน · ผู้จัดการกระจายหีบทั้งภาคได้ · ซุปดูอย่างเดียว"
            ),
        },
    )

    return {
        "employees": employees,
        "skus": skus,
        "sku_warnings": warnings,
        "tga_period_status": "ok",
        "supervisor_name": aggregate_label,
        "new_product_skus": sorted(new_products),
        "new_products_detection_mode": "aggregate",
        "aggregate_mode": True,
        "aggregate_sup_ids": aggregate_sup_ids,
        "skipped_supervisors": skipped,
    }


def load_employees_bulk(
    sup_ids: list[str],
    target_month: int,
    target_year: int,
    *,
    aggregate_label: str,
    refresh: bool = False,
) -> dict[str, Any]:
    ids = sorted({str(x).strip().upper() for x in sup_ids if str(x).strip()})
    if not ids:
        raise HTTPException(400, detail="ไม่มีรหัส Supervisor สำหรับโหลดแบบรวม")

    payloads: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for sid in ids:
        try:
            p = load_employees_payload(
                sid, target_month, target_year, refresh=refresh
            )
            p["_source_sup_id"] = sid
            payloads.append(p)
        except HTTPException as ex:
            skipped.append({"sup_id": sid, "detail": str(ex.detail)})
            logger.warning("bulk skip %s: %s", sid, ex.detail)
        except Exception as ex:
            skipped.append({"sup_id": sid, "detail": str(ex)})
            logger.warning("bulk skip %s: %s", sid, ex)

    if not payloads:
        raise HTTPException(
            404,
            detail=f"ไม่สามารถโหลดข้อมูลจาก Supervisor ที่เลือก ({len(skipped)} รายการล้มเหลว)",
        )

    merged = merge_employees_payloads(
        payloads,
        aggregate_label=aggregate_label,
        aggregate_sup_ids=[p["_source_sup_id"] for p in payloads],
    )
    merged["skipped_supervisors"] = skipped
    return merged


def load_live_targets_payload(
    sup_id: str,
    target_month: int,
    target_year: int,
    *,
    refresh: bool = False,
) -> dict[str, Any]:
    """ดึงเฉพาะเป้าหีบล่าสุดจาก Target Sun — สำหรับ refresh ใน Step 3"""
    if not targetsun_read.is_enabled():
        raise HTTPException(
            status_code=400,
            detail="ยังไม่ได้เปิด Target Sun Read API (TARGETSUN_READ_ENABLED)",
        )

    sid = str(sup_id or "").strip().upper()
    if not refresh:
        cached = targetsun_read.read_live_cache(sid, target_month, target_year)
        if cached is not None:
            if not cached.get("allocations_preview"):
                emp_ids = [
                    str(e.get("emp_id") or "").strip()
                    for e in (cached.get("employees") or [])
                    if str(e.get("emp_id") or "").strip()
                ]
                cached = dict(cached)
                cached["allocations_preview"] = _preview_from_grain_cache(
                    sid,
                    target_month,
                    target_year,
                    cached.get("skus") or [],
                    emp_ids,
                )
            return cached

    cached_emp = read_cached_employee_payload(sid, target_month, target_year)
    emp_list: list[str] = []
    if cached_emp and cached_emp.get("employees"):
        emp_list = sorted(
            {
                str(e.get("emp_id") or "").strip()
                for e in cached_emp["employees"]
                if str(e.get("emp_id") or "").strip()
            }
        )

    if not emp_list:
        emp_path = emp_cache_path(sid, target_month, target_year)
        if os.path.exists(emp_path):
            try:
                df_cached = pd.read_csv(emp_path, dtype={"emp_id": str})
                emp_list = df_cached["emp_id"].astype(str).str.strip().tolist()
            except Exception as e:
                logger.warning("read emp cache for live targets: %s", e)

    if not emp_list:
        raise HTTPException(
            status_code=404,
            detail="ยังไม่มีรายชื่อพนักงาน — โหลด Dashboard ก่อน",
        )

    fabric = None
    try:
        fabric = FabricDAXConnector()
    except Exception as e:
        logger.warning("Fabric connector for live targets scope: %s", e)

    df_granular = targetsun_read.granular_df_for_team(
        emp_list,
        target_month,
        target_year,
        sup_id=sid,
        fabric=fabric,
    )

    grain_cols = [
        "emp_id",
        "sku",
        "qty",
        "salestype",
        "divisioncode",
        "areacode",
        "provincecode",
        "warehouse_code",
    ]
    try:
        p_grain = tga_grain_cache_path(sid, target_month, target_year)
        if df_granular is None or df_granular.empty:
            pd.DataFrame(columns=grain_cols).to_csv(p_grain, index=False)
        else:
            df_granular.to_csv(p_grain, index=False)
    except Exception as e:
        logger.warning("live targets grain cache write: %s", e)

    if df_granular is not None and not df_granular.empty:
        df_tga = (
            df_granular.groupby(["emp_id", "sku"], as_index=False)["qty"].sum()
        )
        df_tga = df_tga[df_tga["qty"] != 0]
    else:
        df_tga = pd.DataFrame(columns=["emp_id", "sku", "qty"])

    sku_union = (
        df_tga["sku"].dropna().astype(str).str.strip().unique().tolist()
        if not df_tga.empty
        else []
    )

    df_product = pd.DataFrame()
    price_latest: dict[str, float] = {}
    if cached_emp and cached_emp.get("skus"):
        df_product = pd.DataFrame(cached_emp["skus"])
        if not df_product.empty and "sku" in df_product.columns:
            df_product = df_product.copy()
            df_product["sku"] = df_product["sku"].astype(str).str.strip()
            price_latest = dict(
                zip(
                    df_product["sku"].astype(str),
                    pd.to_numeric(df_product["price_per_box"], errors="coerce").fillna(
                        0.0
                    ),
                )
            )
    if sku_union and df_product.empty:
        df_product = pd.DataFrame({"sku": sku_union})

    df_sku, df_sun, emp_with_tga = _build_sku_and_sun_from_tga(
        df_tga,
        df_product,
        emp_list,
        list(sku_union),
        price_latest_by_sku=price_latest,
    )

    sun_map = {
        str(r["emp_id"]).strip(): float(r.get("target_sun") or 0)
        for r in df_sun.to_dict(orient="records")
    }
    employees_out = [
        {
            "emp_id": eid,
            "target_sun": sun_map.get(eid, 0.0),
            "has_tga_rows": eid in emp_with_tga,
        }
        for eid in emp_list
    ]

    allocations_preview = _build_allocations_preview_from_grain(
        df_granular,
        df_sku,
        emp_list,
    )

    payload: dict[str, Any] = {
        "source": "targetsun",
        "sup_id": sid,
        "target_year": int(target_year),
        "target_month": int(target_month),
        "row_count": int(len(df_granular)),
        "tga_grain_rows": int(len(df_granular)),
        "skus": _clean(df_sku),
        "employees": employees_out,
        "allocations_preview": allocations_preview,
    }
    targetsun_read.write_live_cache(sid, target_month, target_year, payload)
    return payload

