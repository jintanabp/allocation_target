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
<<<<<<< Updated upstream
from .wh_split import expand_employee_rows, warehouses_per_emp_from_tga
=======
from .wh_split import (
    expand_employee_rows,
    tga_value_by_emp_wh,
    warehouses_per_emp_from_tga,
)
>>>>>>> Stashed changes
from .employee_payload_cache import (
    read_cached_employee_payload,
    write_cached_employee_payload,
)

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


def _clean(df: pd.DataFrame) -> list:
    """แปลง NaN → None ก่อน serialize เพื่อกัน JSON invalid"""
    return df.where(pd.notna(df), None).to_dict(orient="records")


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
        enforce_tga_selection_matches_effective_window(
            fabric, target_month, target_year
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
            logger.warning("ไม่มี SKU ที่มีเป้า TGA ในงวดที่เลือก")
            enforce_tga_has_targets_for_period(
                fabric,
                target_month,
                target_year,
                df_tga,
                0,
            )

        df_sku_base = pd.DataFrame()
        try:
            df_sku_base = fabric.get_product_info(sku_list=sku_union)
        except Exception as e:
            logger.warning("get_product_info error: %s", e)
            df_sku_base = pd.DataFrame({"sku": sku_union})

        if df_sku_base.empty:
            df_sku_base = pd.DataFrame({"sku": sku_union})

        price_latest = {}
        try:
            df_price = fabric.get_latest_price_per_box_by_sku(
                target_month, target_year, sku_union
            )
            if df_price is not None and not df_price.empty:
                price_latest = dict(
                    zip(
                        df_price["sku"].astype(str),
                        df_price["price_per_box"].astype(float),
                    )
                )
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
    emp_list = df_emp["emp_id"].astype(str).str.strip().tolist()
    excluded_from_allocation = int((df_emp["target_sun"] <= 0).sum())
    if excluded_from_allocation > 0:
        logger.info(
            "Excluded from allocation (target_sun <= 0): %d คน (ยังแสดงใน Dashboard)",
            excluded_from_allocation,
        )

    # ── Step 4: History caches (3M/6M + LY same-month + prev-month) ──
    sku_warnings: list[dict] = []
    df_hist = pd.DataFrame(columns=["emp_id", "sku", "hist_boxes", "hist_amount"])
    df_lysm = pd.DataFrame(columns=["emp_id", "sku", "hist_boxes", "hist_amount"])
    try:
        df_hist = fabric.get_historical_sales(
            target_month,
            target_year,
            sku_list=sku_list,
            emp_list=emp_list,
            n_months=3,
        )
        if df_hist is not None and not df_hist.empty:
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
            sku_list=sku_list,
            emp_list=emp_list,
            n_months=6,
        )
        if df_hist6 is not None and not df_hist6.empty:
            df_hist6.to_csv(
                hist_cache_path(sup_id, target_month, target_year, n_months=6),
                index=False,
            )
            logger.info("historical 6M cache saved: %d rows", len(df_hist6))
    except Exception as e:
        logger.warning("historical 6M skipped: %s", e)

    try:
        df_lysm = fabric.get_same_month_prior_year_by_emp_sku(
            target_month, target_year, sku_list=sku_list, emp_list=emp_list
        )
        if df_lysm is not None and not df_lysm.empty:
            p_lysm = hist_ly_same_month_cache_path(sup_id, target_month, target_year)
            df_lysm.to_csv(p_lysm, index=False)
            logger.info("historical LY same-month cache saved: %d rows → %s", len(df_lysm), p_lysm)
    except Exception as e:
        logger.warning("historical LY same month (emp×sku) skipped: %s", e)

    try:
        df_prev = fabric.get_prev_month_by_emp_sku(
            target_month, target_year, sku_list=sku_list, emp_list=emp_list
        )
        if df_prev is not None and not df_prev.empty:
            p_prev = hist_prev_month_cache_path(sup_id, target_month, target_year)
            df_prev.to_csv(p_prev, index=False)
            logger.info("historical prev-month cache saved: %d rows → %s", len(df_prev), p_prev)
    except Exception as e:
        logger.warning("historical prev month (emp×sku) skipped: %s", e)

    # ── Step 5c: calendar-year caches (CY + LY) — ใช้ตรวจสินค้าใหม่ตอน optimize ──
    try:
        for cy in (int(target_year), int(target_year) - 1):
            df_cy = fabric.get_calendar_year_sales_by_emp_sku(
                cy, sku_list=sku_list, emp_list=emp_list
            )
            pcy = hist_calendar_year_cache_path(sup_id, cy)
            if df_cy is not None and not df_cy.empty:
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
    df_emp["ly_sales"] = 0.0
    df_emp["hist_avg_3m"] = 0.0

    try:
        if df_lysm is not None and not df_lysm.empty:
            ly_by_emp = (
                df_lysm.groupby("emp_id", as_index=True)["hist_amount"]
                .sum()
                .astype(float)
                .to_dict()
            )
            df_emp["ly_sales"] = (
                df_emp["emp_id"].astype(str).str.strip().map(ly_by_emp).fillna(0.0)
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

    if excluded_from_allocation > 0:
        sku_warnings.append(
            {
                "type": "employees_excluded_no_tga",
                "sku": "",
                "brand": "",
                "message": (
                    f"พนักงาน {excluded_from_allocation} คนมีเป้าเงิน (Target Sun) เป็น 0 — "
                    "ยังแสดงใน Dashboard แต่จะไม่ถูกนำไปเกลี่ยหีบเมื่อกดปุ่มนั้น"
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

    sku_ids_list = df_sku["sku"].astype(str).str.strip().tolist()
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
<<<<<<< Updated upstream
                    (str(r["emp_id"]).strip(), str(r.get("warehouse_code") or "").strip()): float(
                        r.get("hist_amount") or 0.0
                    )
=======
                    (
                        str(r["emp_id"]).strip(),
                        str(r.get("warehouse_code") or "").strip(),
                    ): float(r.get("hist_amount") or 0.0)
>>>>>>> Stashed changes
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
<<<<<<< Updated upstream
                    (str(r["emp_id"]).strip(), str(r.get("warehouse_code") or "").strip()): float(
                        r.get("hist_amount") or 0.0
                    )
                    / 3.0
=======
                    (
                        str(r["emp_id"]).strip(),
                        str(r.get("warehouse_code") or "").strip(),
                    ): float(r.get("hist_amount") or 0.0) / 3.0
>>>>>>> Stashed changes
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
<<<<<<< Updated upstream
    if wh_split_emps:
=======
    wh_split_rows = sum(1 for e in emp_records if e.get("wh_split"))
    if wh_split_rows:
        logger.info(
            "WH split: %d alloc rows (%d พนักงานมีหลายคลัง)",
            len(emp_records),
            len(wh_split_emps),
        )
>>>>>>> Stashed changes
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
            warnings.append(dict(w))
        for np in p.get("new_product_skus") or []:
            new_products.add(str(np).strip())

    employees.sort(key=lambda e: (str(e.get("supervisor_code") or ""), str(e.get("emp_id") or "")))
    skus = sorted(sku_map.values(), key=lambda s: str(s.get("sku") or ""))

    warnings.insert(
        0,
        {
            "type": "aggregate_view",
            "sku": "",
            "brand": "",
            "message": (
                f"โหมดดูรวม ({aggregate_label}) — {len(aggregate_sup_ids)} ซุป, "
                f"{len(employees)} พนักงาน · ไม่สามารถกระจายหีบในโหมดนี้"
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

