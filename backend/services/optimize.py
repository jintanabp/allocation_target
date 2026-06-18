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

    df_sku = df_sku.copy()
    df_sku["supervisor_target_boxes"] = pd.to_numeric(
        df_sku["supervisor_target_boxes"], errors="coerce"
    ).fillna(0)
    df_sku = df_sku[df_sku["supervisor_target_boxes"] > 0].copy()
    if df_sku.empty:
        raise HTTPException(
            400,
            detail=(
                "ไม่มี SKU ที่มีเป้าหีบใน Target Sun งวดนี้ — "
                "กรุณาโหลดข้อมูล Dashboard ใหม่"
            ),
        )

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

    df_hist_input = df_hist
    if strategy_u == "LY":
        if not df_hist_lysm.empty:
            df_hist_input = df_hist_lysm.copy()
            hist_months = 1
            logger.info("LY strategy: ใช้ cache เดือนเดียวกันปีที่แล้วเป็นฐานน้ำหนักกระจายหีบ")
        else:
            logger.warning(
                "กลยุทธ์ LY: ไม่พบ cache เดือนเดียวกันปีที่แล้ว — ใช้ประวัติ 3M/6M แทน "
                "(แนะนำให้โหลดหน้า Dashboard ใหม่เพื่อสร้าง hist_lysm)"
            )
            df_hist_input = df_hist
            hist_months = 6 if (want_6m and os.path.exists(cache_6)) else 3
    else:
        hist_months = 6 if (want_6m and os.path.exists(cache_6)) else 3

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

    # ──────────────────────────────────────────────────────────────
    # MULTI-STRATEGY: ผู้ใช้เลือกหลายวิธี + กำหนดแบรนด์ไหนใช้วิธีไหน
    # ──────────────────────────────────────────────────────────────
    brand_map = req.brand_strategy_map or {}
    distinct_strategies = {s for s in brand_map.values() if s}
    if brand_map and len(distinct_strategies) > 1 and not df_sku.empty:
        logger.info(
            "multi-strategy run: %d distinct strategies across %d brands",
            len(distinct_strategies), len(brand_map),
        )
        bcol_th = "brand_name_thai" if "brand_name_thai" in df_sku.columns else None
        bcol_en = "brand_name_english" if "brand_name_english" in df_sku.columns else None

        def _brand_key(row) -> str:
            if bcol_th and str(row.get(bcol_th, "") or "").strip():
                return str(row.get(bcol_th, "")).strip()
            if bcol_en and str(row.get(bcol_en, "") or "").strip():
                return str(row.get(bcol_en, "")).strip()
            return ""

        df_sku_local = df_sku.copy()
        df_sku_local["_brand_key"] = df_sku_local.apply(_brand_key, axis=1)
        df_sku_local["_strategy_resolved"] = df_sku_local["_brand_key"].map(
            lambda b: brand_map.get(b, req.strategy)
        )

        price_col = "price_per_box" if "price_per_box" in df_sku_local.columns else None
        box_col = "supervisor_target_boxes" if "supervisor_target_boxes" in df_sku_local.columns else None
        if price_col and box_col:
            df_sku_local["_value"] = (
                pd.to_numeric(df_sku_local[price_col], errors="coerce").fillna(0)
                * pd.to_numeric(df_sku_local[box_col], errors="coerce").fillna(0)
            )
        else:
            df_sku_local["_value"] = 1.0
        total_value = float(df_sku_local["_value"].sum()) or 1.0

        alloc_parts = []
        for strat in sorted(df_sku_local["_strategy_resolved"].unique()):
            df_sku_grp = df_sku_local[df_sku_local["_strategy_resolved"] == strat].copy()
            if df_sku_grp.empty:
                continue
            grp_value = float(df_sku_grp["_value"].sum())
            share = (grp_value / total_value) if total_value > 0 else 0.0
            df_targets_grp = df_emp_targets.copy()
            df_targets_grp["yellow_target"] = df_targets_grp["yellow_target"] * share
            df_targets_grp = df_targets_grp[df_targets_grp["yellow_target"] > 0]
            if df_targets_grp.empty:
                continue

            sku_in_grp = set(df_sku_grp["sku"].astype(str).str.strip().tolist())
            locked_grp = [le for le in (locked_edits_data or []) if str(le.get("sku", "")).strip() in sku_in_grp]

            new_skus_grp = None
            if req.new_products_even and new_skus_cy_ly is not None:
                new_skus_grp = {s for s in new_skus_cy_ly if s in sku_in_grp}

            df_alloc_grp = allocate_boxes(
                df_targets_grp,
                df_sku_grp.drop(columns=["_brand_key", "_strategy_resolved", "_value"], errors="ignore"),
                df_hist_input,
                strategy=strat,
                force_min_one=req.force_min_one,
                locked_edits=locked_grp if locked_grp else None,
                cap_multiplier=req.cap_multiplier,
                even_new_products=bool(req.new_products_even),
                new_product_skus=new_skus_grp if req.new_products_even else None,
                hist_balance=float(req.hist_balance),
                revenue_tolerance_baht=float(req.revenue_tolerance_baht),
            )
            alloc_parts.append(df_alloc_grp)
        df_allocation = (
            pd.concat(alloc_parts, ignore_index=True)
            if alloc_parts
            else pd.DataFrame(columns=["emp_id", "sku", "allocated_boxes"])
        )
    else:
        df_allocation = allocate_boxes(
            df_emp_targets,
            df_sku,
            df_hist_input,
            strategy=req.strategy,
            force_min_one=req.force_min_one,
            locked_edits=locked_edits_data if locked_edits_data else None,
            cap_multiplier=req.cap_multiplier,
            even_new_products=bool(req.new_products_even),
            new_product_skus=new_skus_cy_ly if req.new_products_even else None,
            hist_balance=float(req.hist_balance),
            revenue_tolerance_baht=float(req.revenue_tolerance_baht),
        )

    # log meta จาก Step 2
    if req.bui_deductions:
        logger.info("bui_deductions provided: %d emps", len(req.bui_deductions))
    if req.neg_growth_reason:
        logger.info("neg_growth_reason: %s", req.neg_growth_reason[:200])

    if not df_hist_input.empty:
        df_hist_avg = df_hist_input.groupby(["emp_id", "sku"])["hist_boxes"].sum().reset_index()
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
