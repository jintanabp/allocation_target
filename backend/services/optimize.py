import logging
import os

import pandas as pd
from fastapi import HTTPException

from ..OR_engine import allocate_boxes
from ..core.allocation_checks import (
    skus_no_sales_cy_ly,
    skus_zero_team_hist_window,
    validate_allocation_vs_targets,
)
from ..core.constants import VALID_STRATEGIES
from ..core.tga_period import enforce_tga_selection_matches_effective_window
from ..core.paths import (
    excel_path,
    hist_cache_path,
    hist_calendar_year_cache_path,
    hist_ly_same_month_cache_path,
    hist_prev_month_cache_path,
    result_path,
)
from ..core.targets import load_target_csv
from ..generate_excel import create_target_excel
from ..schemas import OptimizeRequest
from ..fabric_dax_connector import FabricDAXConnector

logger = logging.getLogger("target_allocation")


def run_optimization_service(
    req: OptimizeRequest,
    sup_id: str,
    target_month: int,
    target_year: int,
) -> dict:
    if req.strategy.upper() not in VALID_STRATEGIES:
        raise HTTPException(400, detail=f"strategy ไม่ถูกต้อง ต้องเป็น {VALID_STRATEGIES}")

    use_legacy = os.environ.get("USE_LEGACY_TARGET_CSV", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if not use_legacy:
        try:
            enforce_tga_selection_matches_effective_window(
                FabricDAXConnector(), target_month, target_year
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.warning("TGA EFFECTIVEDATE check skipped in optimize: %s", e)

    os.makedirs("data", exist_ok=True)

    df_sku, _ = load_target_csv()
    if df_sku is None:
        raise HTTPException(500, detail="ไม่พบ target_boxes.csv กรุณาโหลดหน้า Dashboard ก่อน")

    df_all_targets = pd.DataFrame([t.model_dump() for t in req.yellowTargets])
    if df_all_targets.empty:
        raise HTTPException(400, detail="ไม่มีเป้าเหลือง (yellowTargets) — โหลดข้อมูล Dashboard ก่อน")
    df_all_targets["yellow_target"] = pd.to_numeric(
        df_all_targets["yellow_target"], errors="coerce"
    ).fillna(0.0)
    df_emp_targets = df_all_targets[df_all_targets["yellow_target"] > 0].copy()
    if df_emp_targets.empty:
        raise HTTPException(
            400,
            detail=(
                "ไม่มีพนักงานที่มีเป้าเงิน > 0 — ไม่สามารถเกลี่ยหีบได้ "
                "(ทุกคนเป้า 0 ในงวดนี้ / ตรวจสอบ Target Sun)"
            ),
        )
    emp_list = df_emp_targets["emp_id"].astype(str).str.strip().tolist()
    eligible_set = set(emp_list)

    strategy_u = req.strategy.upper()
    want_6m = strategy_u == "L6M"
    cache_6 = hist_cache_path(sup_id, target_month, target_year, n_months=6)
    cache_3 = hist_cache_path(sup_id, target_month, target_year, n_months=3)
    cache_file = cache_6 if want_6m and os.path.exists(cache_6) else cache_3
    if want_6m and not os.path.exists(cache_6) and os.path.exists(cache_3):
        logger.warning(
            "ไม่พบ hist 6M cache — ใช้ cache 3M แทนสำหรับ L6M (โหลดหน้า Dashboard ใหม่เพื่อสร้าง 6M cache)"
        )
    if os.path.exists(cache_file):
        df_hist = pd.read_csv(cache_file, dtype={"sku": str, "emp_id": str})
        logger.info("hist cache loaded (%s): %d rows", os.path.basename(cache_file), len(df_hist))
    else:
        logger.warning("ไม่พบ hist cache → ใช้ตารางเปล่า")
        df_hist = pd.DataFrame(columns=["emp_id", "sku", "hist_boxes"])

    df_hist = df_hist[df_hist["emp_id"].isin(emp_list)]

    hist_months = 6 if (want_6m and os.path.exists(cache_6)) else 3

    lysm_path = hist_ly_same_month_cache_path(sup_id, target_month, target_year)
    df_hist_lysm = pd.DataFrame()
    if os.path.exists(lysm_path):
        try:
            df_hist_lysm = pd.read_csv(lysm_path, dtype={"sku": str, "emp_id": str})
            df_hist_lysm = df_hist_lysm[df_hist_lysm["emp_id"].isin(emp_list)]
            logger.info(
                "hist LY same-month loaded: %d rows (blend weight env ALLOC_HIST_LYM_WEIGHT, default 0.5)",
                len(df_hist_lysm),
            )
        except Exception as e:
            logger.warning("hist LY same-month cache read failed: %s", e)
            df_hist_lysm = pd.DataFrame()

    prev_path = hist_prev_month_cache_path(sup_id, target_month, target_year)
    df_hist_prev = pd.DataFrame()
    if os.path.exists(prev_path):
        try:
            df_hist_prev = pd.read_csv(prev_path, dtype={"sku": str, "emp_id": str})
            df_hist_prev = df_hist_prev[df_hist_prev["emp_id"].isin(emp_list)]
            logger.info("hist prev-month loaded: %d rows", len(df_hist_prev))
        except Exception as e:
            logger.warning("hist prev-month cache read failed: %s", e)
            df_hist_prev = pd.DataFrame()

    logger.info(
        "Running strategy=%s for sup=%s (eligible emps for boxes: %d)",
        req.strategy,
        sup_id,
        len(emp_list),
    )
    locked_edits_data = [
        {"emp_id": le.emp_id, "sku": le.sku, "locked_boxes": le.locked_boxes}
        for le in req.locked_edits
        if str(le.emp_id).strip() in eligible_set
    ]
    sku_ids_opt = df_sku["sku"].astype(str).str.strip().tolist()
    new_skus_cy_ly: set[str] | None = set()
    new_products_even_mode = "off"
    new_product_skus_used: list[str] = []
    if req.new_products_even:
        cy_ok = os.path.exists(hist_calendar_year_cache_path(sup_id, target_year))
        ly_ok = os.path.exists(hist_calendar_year_cache_path(sup_id, target_year - 1))
        if not cy_ok or not ly_ok:
            logger.warning(
                "new_products_even เปิดอยู่ แต่ไม่พบ cache ปีปฏิทิน (hist_cy_) — "
                "จะ fallback ใช้เงื่อนไขยอด 3M/6M = 0 (ชั่วคราว) — "
                "แนะนำให้โหลดหน้า Dashboard ใหม่เพื่อสร้างไฟล์ CY/LY"
            )
            new_skus_cy_ly = None
            new_products_even_mode = "fallback_hist_window"
            new_product_skus_used = sorted(skus_zero_team_hist_window(df_hist, sku_ids_opt))
        else:
            new_skus_cy_ly = skus_no_sales_cy_ly(sup_id, target_year, sku_ids_opt)
            new_products_even_mode = "cy_ly"
            new_product_skus_used = sorted(list(new_skus_cy_ly))
            logger.info(
                "new_products_even: SKU ไม่มียอดทั้งปีนี้และปีที่แล้ว (ปีปฏิทิน) = %d รายการ",
                len(new_skus_cy_ly),
            )

    df_allocation = allocate_boxes(
        df_emp_targets,
        df_sku,
        df_hist,
        strategy=req.strategy,
        force_min_one=req.force_min_one,
        locked_edits=locked_edits_data if locked_edits_data else None,
        cap_multiplier=req.cap_multiplier,
        even_new_products=bool(req.new_products_even),
        new_product_skus=new_skus_cy_ly if req.new_products_even else None,
    )

    if not df_hist.empty:
        df_hist_avg = df_hist.groupby(["emp_id", "sku"])["hist_boxes"].sum().reset_index()
        df_hist_avg["hist_avg"] = (df_hist_avg["hist_boxes"] / float(hist_months)).round(1)
    else:
        df_hist_avg = pd.DataFrame(columns=["emp_id", "sku", "hist_avg"])

    df_final = pd.merge(
        df_allocation,
        df_hist_avg[["emp_id", "sku", "hist_avg"]],
        on=["emp_id", "sku"],
        how="left",
    )
    df_final["hist_avg"] = df_final["hist_avg"].fillna(0)

    if not df_hist_lysm.empty:
        df_lym = (
            df_hist_lysm.groupby(["emp_id", "sku"], as_index=False)["hist_boxes"]
            .sum()
            .rename(columns={"hist_boxes": "hist_ly_same_month"})
        )
        df_final = pd.merge(df_final, df_lym, on=["emp_id", "sku"], how="left")
    else:
        df_final["hist_ly_same_month"] = 0.0
    df_final["hist_ly_same_month"] = (
        pd.to_numeric(df_final["hist_ly_same_month"], errors="coerce")
        .fillna(0.0)
        .round(1)
    )

    if not df_hist_prev.empty:
        df_pm = (
            df_hist_prev.groupby(["emp_id", "sku"], as_index=False)["hist_boxes"]
            .sum()
            .rename(columns={"hist_boxes": "hist_prev_month"})
        )
        df_final = pd.merge(df_final, df_pm, on=["emp_id", "sku"], how="left")
    else:
        df_final["hist_prev_month"] = 0.0
    df_final["hist_prev_month"] = (
        pd.to_numeric(df_final["hist_prev_month"], errors="coerce")
        .fillna(0.0)
        .round(1)
    )

    brand_cols = [
        c
        for c in [
            "brand_name_thai",
            "brand_name_english",
            "product_name_thai",
            "product_name_english",
            "price_per_box",
        ]
        if c in df_sku.columns
    ]
    if brand_cols:
        df_final = pd.merge(df_final, df_sku[["sku"] + brand_cols], on="sku", how="left")
        for c in brand_cols:
            df_final[c] = df_final[c].fillna("" if "name" in c else 0)

    df_final.to_csv(result_path(sup_id), index=False)

    yellow_map = {y.emp_id: y.yellow_target for y in req.yellowTargets}
    sku_checks = validate_allocation_vs_targets(df_final, df_sku)
    if sku_checks:
        logger.warning("allocation vs target mismatch: %s", sku_checks)

    create_target_excel(
        result_csv=result_path(sup_id),
        output_path=excel_path(sup_id),
        brand_filter="ALL",
        yellow_map=yellow_map,
        sup_id=sup_id,
        target_boxes_csv="data/target_boxes.csv",
    )

    return {
        "allocations": df_final.to_dict(orient="records"),
        "sku_total_checks": sku_checks,
        "hist_window_months": hist_months,
        "new_products_even_mode": new_products_even_mode,
        "new_product_skus": new_product_skus_used,
    }

