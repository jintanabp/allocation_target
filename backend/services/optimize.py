import logging
import os
from typing import Any

import pandas as pd
from fastapi import HTTPException

from ..OR_engine import (
    LockedEditsExceedTarget,
    _CAP_MULTIPLIER,
    _DEFAULT_HIST_BAND_PCT,
    _TIER_FLEX_BAND_PCT,
    _TIER_STRICT_BAND_PCT,
    _baseline_map_from_df,
    _flex_skus_by_target_value,
    _greedy_revenue_balancer,
    _norm_sku,
    _proportional,
    _revenue_scale_factor,
    _skus_with_target_boxes,
    allocate_boxes,
)
from ..core.allocation_checks import (
    detect_new_product_skus,
    missing_employee_alloc_keys,
    skus_zero_team_hist_window,
    validate_allocation_vs_targets,
    zero_fill_missing_employees,
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
    target_boxes_cache_path,
    target_boxes_union_cache_path,
    tga_grain_cache_path,
)
from ..core.atomic_io import atomic_write_csv
from ..core.targets import (
    load_summed_target_boxes,
    load_target_csv_for,
    target_boxes_source_path,
)
from ..generate_excel import create_target_excel
from ..schemas import OptimizeRequest
from ..fabric_dax_connector import FabricDAXConnector
from . import no_target_store
from .sku_link_store import collapse_hist_to_canonical
from .wh_split import (
    _norm_wh,
    alloc_key,
    prepare_optimize_targets,
    restore_allocation_emp_ids,
    split_hist_dataframe,
    tga_value_by_emp_wh,
    value_shares_for_reverse_map,
)

logger = logging.getLogger("target_allocation")


def _allow_allocation_mismatch() -> bool:
    """
    ทางออกฉุกเฉิน: ปล่อยผลที่ผลรวมไม่ตรงเป้าให้ผ่าน (ค่าเริ่มต้น = ปิด)

    เปิดเฉพาะตอนต้องกู้สถานการณ์หน้างานจริง ๆ และควรปิดกลับทันที
    เพราะนี่คือกฎ I1 ที่ทั้งระบบยึดอยู่ (ดู docs/ALLOCATION_INVARIANTS.md)
    """
    return (os.environ.get("ALLOC_ALLOW_MISMATCH") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _drop_no_target_employees(df_all_targets, sup_id: str, req) -> tuple[Any, list[str]]:
    """
    ตัดพนักงานในรายชื่อ「ไม่ต้องตั้งเป้า」ออกจากคำขอ — คืน (frame ที่เหลือ, รายชื่อที่ตัด)

    ทีมของแต่ละแถวเอาจาก `supervisor_code` ที่หน้าเว็บส่งมา ถ้าแถวไหนไม่บอกทีม
    (หน้าเว็บรุ่นเก่า) จะตกไปใช้ชุดรวมของทุกทีมที่เกี่ยวกับคำขอนี้ — กันเกินดีกว่ากันขาด
    เพราะกันเกินเห็นทันทีบนหน้าจอและปลดได้ ส่วนกันขาดคือหีบไหลไปหาคนที่ไม่ควรได้
    แล้วถูกส่งขึ้นระบบจริง

    เป้าหีบของทีมไม่ลดตาม (I1) — คนที่เหลือรับส่วนนั้นไป ซึ่งเป็นสิ่งที่ผู้ใช้ต้องการ
    """
    if df_all_targets.empty:
        return df_all_targets, []
    try:
        entries = no_target_store.read_entries()
    except Exception as e:
        logger.error("อ่านรายชื่อไม่ต้องตั้งเป้าไม่ได้ — ไม่ตัดใครออกรอบนี้: %s", e)
        return df_all_targets, []
    if not entries:
        return df_all_targets, []

    by_sup = no_target_store.no_target_map(entries)
    involved = {no_target_store.norm_sup(sup_id)}
    for attr in ("peer_sup_ids", "target_sup_ids"):
        for code in getattr(req, attr, None) or []:
            involved.add(no_target_store.norm_sup(code))
    involved.discard("")
    fallback = no_target_store.no_target_emp_ids_for_sups(involved, entries)

    has_sup_col = "supervisor_code" in df_all_targets.columns

    def _blocked(row) -> bool:
        emp = no_target_store.norm_emp(row.get("emp_id"))
        if not emp:
            return False
        row_sup = no_target_store.norm_sup(row.get("supervisor_code")) if has_sup_col else ""
        if row_sup:
            return emp in by_sup.get(row_sup, set())
        return emp in fallback

    mask = df_all_targets.apply(_blocked, axis=1)
    if not mask.any():
        return df_all_targets, []
    dropped = sorted(
        {
            no_target_store.norm_emp(e)
            for e in df_all_targets.loc[mask, "emp_id"]
            if no_target_store.norm_emp(e)
        }
    )
    logger.info(
        "optimize %s: ตัดพนักงานที่ไม่ต้องตั้งเป้า %d คน — %s",
        sup_id, len(dropped), ", ".join(dropped[:10]),
    )
    return df_all_targets.loc[~mask].copy(), dropped


def _requested_alloc_keys(df_all_targets) -> list[dict]:
    """
    รายชื่อ (emp_id, warehouse_code, yellow_target) ที่ผู้เรียกขอมา — ก่อนกรองเป้าเงิน

    ใช้เป็นฐานของด่าน I8 ("ส่งเข้ามากี่คน ต้องได้กลับครบเท่านั้น") จึงต้องอ่านจาก frame
    ที่ยังไม่ถูกกรอง เพราะคนที่หายไปคือคนที่ถูกกรองออกนั่นเอง
    """
    has_wh = "warehouse_code" in df_all_targets.columns
    out: list[dict] = []
    for _, r in df_all_targets.iterrows():
        emp = str(r.get("emp_id") or "").strip()
        if not emp:
            continue
        out.append(
            {
                "emp_id": emp,
                "warehouse_code": _norm_wh(r.get("warehouse_code")) if has_wh else "",
                "yellow_target": float(r.get("yellow_target") or 0.0),
            }
        )
    return out


def _lock_or_emp_id(emp_id: str, warehouse_code: str | None) -> str:
    em = str(emp_id or "").strip()
    wh = str(warehouse_code or "").strip()
    if wh:
        return alloc_key(em, wh, wh_split=True)
    return em


def _wh_value_shares(
    reverse_map: dict[str, tuple[str, str]],
    sup_id: str,
    target_month: int,
    target_year: int,
    df_sku: pd.DataFrame,
) -> dict[tuple[str, str], float]:
    path = tga_grain_cache_path(sup_id, target_month, target_year)
    if not os.path.exists(path):
        return value_shares_for_reverse_map(reverse_map, {})
    try:
        dg = pd.read_csv(path, dtype={"sku": str, "emp_id": str})
        price_map = dict(
            zip(
                df_sku["sku"].astype(str).str.strip(),
                pd.to_numeric(df_sku["price_per_box"], errors="coerce").fillna(0.0),
            )
        )
        return value_shares_for_reverse_map(
            reverse_map, tga_value_by_emp_wh(dg, price_map)
        )
    except Exception as e:
        logger.warning("wh value shares from tga grain: %s", e)
        return value_shares_for_reverse_map(reverse_map, {})


def _maybe_split_hist(
    df: pd.DataFrame,
    reverse_map: dict[str, tuple[str, str]],
    value_shares: dict[tuple[str, str], float],
) -> pd.DataFrame:
    if not reverse_map or not any("|" in k for k in reverse_map):
        return df
    return split_hist_dataframe(df, reverse_map, value_shares)


def _read_hist_cache(path: str, emp_list: list[str]) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame(columns=["emp_id", "sku", "hist_boxes"])
    df = pd.read_csv(path, dtype={"sku": str, "emp_id": str})
    df = df[df["emp_id"].isin(emp_list)]
    return collapse_hist_to_canonical(df)


def _read_hist_cache_across_teams(
    path_fn,
    sup_ids: list[str],
    emp_list: list[str],
) -> pd.DataFrame:
    """
    อ่านประวัติขายจาก cache ของหลายทีมแล้วต่อกัน

    ตอนกระจายรวมทั้งหน่วย พนักงานมาจากหลายทีม แต่ cache ประวัติแยกไฟล์ตามทีม
    ถ้าอ่านแค่ไฟล์ของทีมเจ้าของเป้า คนทีมอื่นจะถูกมองว่าไม่มีประวัติ แล้วได้
    น้ำหนักขั้นต่ำ (0.01) — กระจายออกมาเบี้ยวจนใช้ไม่ได้

    ตัดคู่ (emp, sku) ซ้ำทิ้ง (ปกติไม่ควรซ้ำเพราะพนักงานหนึ่งคนอยู่ทีมเดียว)
    เพื่อกันประวัติถูกนับสองรอบถ้าไฟล์ทีมทับซ้อนกัน
    """
    frames = []
    for sid in sup_ids:
        df = _read_hist_cache(path_fn(sid), emp_list)
        if not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["emp_id", "sku", "hist_boxes"])
    out = pd.concat(frames, ignore_index=True)
    if {"emp_id", "sku"} <= set(out.columns):
        out = out.drop_duplicates(subset=["emp_id", "sku"], keep="first").reset_index(drop=True)
    return out


def _hist_input_for_strategy(
    strategy_u: str,
    df_hist_3: pd.DataFrame,
    df_hist_6: pd.DataFrame,
    df_hist_lysm: pd.DataFrame,
    *,
    sup_id: str,
    target_month: int,
    target_year: int,
) -> tuple[pd.DataFrame, int]:
    """เลือก cache ประวัติ + จำนวนเดือนสำหรับกลยุทธ์นั้น (แยก 3M / 6M / LY)."""
    strategy_u = strategy_u.upper()
    want_6m = strategy_u == "L6M"
    cache_6 = hist_cache_path(sup_id, target_month, target_year, n_months=6)

    if strategy_u == "LY":
        if not df_hist_lysm.empty:
            return df_hist_lysm.copy(), 1
        logger.warning(
            "กลยุทธ์ LY: ไม่พบ cache เดือนเดียวกันปีที่แล้ว — ใช้ประวัติ 3M/6M แทน "
            "(แนะนำให้โหลดหน้า Dashboard ใหม่เพื่อสร้าง hist_lysm)"
        )
        if want_6m and os.path.exists(cache_6) and not df_hist_6.empty:
            return df_hist_6, 6
        return (df_hist_3 if not df_hist_3.empty else df_hist_6), (
            6 if (want_6m and os.path.exists(cache_6) and not df_hist_6.empty) else 3
        )

    if want_6m:
        if not df_hist_6.empty:
            return df_hist_6, 6
        if not df_hist_3.empty:
            logger.warning(
                "ไม่พบ hist 6M cache — ใช้ cache 3M แทนสำหรับ L6M (โหลดหน้า Dashboard ใหม่เพื่อสร้าง 6M cache)"
            )
            return df_hist_3, 3
        return pd.DataFrame(columns=["emp_id", "sku", "hist_boxes"]), 6

    return (df_hist_3 if not df_hist_3.empty else df_hist_6), 3


def _build_multi_strategy_base_map(
    df_emp_targets: pd.DataFrame,
    df_sku: pd.DataFrame,
    sku_strategy: dict[str, str],
    hist_by_strategy: dict[str, pd.DataFrame],
    *,
    force_min_one: bool,
    locked_map: dict,
    cap_multiplier: float | None,
    even_skus: frozenset[str],
) -> dict[tuple[str, str], int]:
    """baseline ต่อ (emp, sku) ตามกลยุทธ์ของแต่ละ SKU — ใช้รั้ว greedy ปลายทาง."""
    effective_cap = cap_multiplier if cap_multiplier is not None else _CAP_MULTIPLIER
    combined: dict[tuple[str, str], int] = {}
    for strat in sorted(set(sku_strategy.values())):
        strat_u = str(strat).upper()
        sku_set = frozenset(s for s, st in sku_strategy.items() if str(st).upper() == strat_u)
        if not sku_set:
            continue
        df_sku_grp = df_sku[
            df_sku["sku"].astype(str).str.strip().isin(sku_set)
        ].copy()
        if df_sku_grp.empty:
            continue
        df_hist = hist_by_strategy.get(strat_u, pd.DataFrame())
        baseline = strat_u if strat_u in ("L3M", "L6M", "LY") else "L3M"
        even_grp = frozenset(s for s in even_skus if s in sku_set)
        df_base = _proportional(
            df_emp_targets,
            df_sku_grp,
            df_hist,
            baseline,
            force_min_one,
            locked_map,
            effective_cap,
            even_skus=even_grp,
        )
        combined.update(_baseline_map_from_df(df_base, df_emp_targets, df_sku_grp))
    return combined


def _post_merge_revenue_balance(
    df_allocation: pd.DataFrame,
    df_emp_targets: pd.DataFrame,
    df_sku: pd.DataFrame,
    *,
    sku_strategy: dict[str, str],
    hist_by_strategy: dict[str, pd.DataFrame],
    locked_edits_data: list,
    force_min_one: bool,
    cap_multiplier: float | None,
    even_skus: frozenset[str],
    tiered_allocation: bool,
    tier_pct: float,
    revenue_tolerance_baht: float,
) -> pd.DataFrame:
    """ปรับมูลค่ารายคนรวมทั้งตะกร้า — โยนหีบได้เฉพาะ SKU หลัก."""
    if df_allocation.empty or not tiered_allocation:
        return df_allocation

    flex_skus = _flex_skus_by_target_value(df_sku, float(tier_pct)) - even_skus
    if not flex_skus:
        return df_allocation

    # คีย์ต้อง normalize แบบเดียวกับที่ OR_engine ทำใน _normalize_engine_inputs (I2)
    # เดิมใช้ string ดิบจาก request ตรง ๆ ขณะที่ base_map / flex_skus / even_skus
    # ใช้ _norm_sku() — ต่างกันแค่ช่องว่างหน้า-หลังก็ทำให้ล็อกของผู้ใช้ถูกเมินเงียบ ๆ
    # แล้วเซลล์ที่กดล็อกไว้ก็ถูกขยับในโหมดหลายกลยุทธ์ + tiered โดยไม่มีสัญญาณอะไรเลย
    locked_map: dict[tuple[str, str], int] = {}
    for le in (locked_edits_data or []):
        try:
            locked_map[(str(le["emp_id"]).strip(), _norm_sku(le["sku"]))] = int(
                le["locked_boxes"]
            )
        except (KeyError, TypeError, ValueError):
            logger.warning("locked_edit รูปแบบไม่ถูกต้อง — ข้าม: %r", le)
    base_map = _build_multi_strategy_base_map(
        df_emp_targets,
        df_sku,
        sku_strategy,
        hist_by_strategy,
        force_min_one=force_min_one,
        locked_map=locked_map,
        cap_multiplier=cap_multiplier,
        even_skus=even_skus,
    )
    if not base_map:
        return df_allocation

    strict_keys = frozenset(
        _norm_sku(s)
        for s in _skus_with_target_boxes(df_sku)
        if _norm_sku(s) not in flex_skus and _norm_sku(s) not in even_skus
    )
    skip_for_greedy = strict_keys | even_skus
    rev_tol = max(0.0, float(revenue_tolerance_baht if revenue_tolerance_baht is not None else 1000.0))

    df_sparse = df_allocation[df_allocation["allocated_boxes"] > 0].copy()
    if df_sparse.empty:
        df_sparse = df_allocation.copy()

    df_balanced = _greedy_revenue_balancer(
        df_sparse,
        df_emp_targets,
        df_sku,
        locked_map=locked_map,
        force_min_one=force_min_one,
        skip_balance_skus=skip_for_greedy,
        tolerance_baht=rev_tol,
        base_map=base_map,
        tiered_allocation=True,
        flex_skus=flex_skus,
        flex_band_pct=_TIER_FLEX_BAND_PCT,
        strict_band_pct=_TIER_STRICT_BAND_PCT,
        default_band_pct=_DEFAULT_HIST_BAND_PCT,
        even_skus=even_skus,
    )

    alloc_idx = {
        (str(r["emp_id"]).strip(), _norm_sku(r["sku"])): int(r["allocated_boxes"])
        for _, r in df_balanced.iterrows()
    }
    df_out = df_allocation.copy()
    df_out["allocated_boxes"] = df_out.apply(
        lambda r: alloc_idx.get(
            (str(r["emp_id"]).strip(), _norm_sku(r["sku"])),
            int(r["allocated_boxes"]),
        ),
        axis=1,
    )
    return df_out


def _merge_partial_result(
    result_csv_path: str, df_new: pd.DataFrame, only_skus: list[str]
) -> pd.DataFrame:
    """
    รวมผลกระจายบางส่วนเข้ากับผลเดิมทั้งงวด — แทนที่เฉพาะ SKU ที่เพิ่งกระจายใหม่

    อ่านไฟล์เดิมไม่ได้/ยังไม่มีไฟล์ ก็คืนของใหม่ไปตรง ๆ (พฤติกรรมเดิม)
    """
    try:
        if not os.path.exists(result_csv_path):
            return df_new
        df_old = pd.read_csv(result_csv_path, dtype={"emp_id": str, "sku": str})
    except Exception as e:
        logger.warning("อ่านผลเดิมเพื่อรวมกับผลบางส่วนไม่ได้ (%s) — เขียนทับตามเดิม", e)
        return df_new
    if df_old.empty or "sku" not in df_old.columns:
        return df_new
    df_old["sku"] = df_old["sku"].astype(str).str.strip()
    keep = df_old[~df_old["sku"].isin({str(x).strip() for x in only_skus})]
    if keep.empty:
        return df_new
    merged = pd.concat([keep, df_new], ignore_index=True)
    logger.info(
        "กระจายเฉพาะ %d SKU — รวมกับผลเดิมอีก %d แถวก่อนเขียนไฟล์",
        len(only_skus), len(keep),
    )
    return merged


def reject_mixed_sales_units(
    target_sup_ids: list[str], target_month: int, target_year: int
) -> None:
    """
    กระจายรวมภาคที่ปนทั้งทีมเครดิตและทีมรถเงินสดไม่ได้ — ต้องกั้นก่อนคำนวณ

    สองหน่วยขายใช้ราคาคนละชุด (CREDITUNITPRICE / CASHUNITPRICE) เอาเป้าหีบมาบวก
    รวมกันแล้วกระจายด้วยกัน มูลค่าที่ได้จึงไม่มีความหมาย และ revenue_scale
    (OR_engine._revenue_scale_factor) จะดันเป้าเงินรายคนทั้งภาคเพี้ยนตามส่วนต่างราคา
    งานจริงก็ไม่เคยกระจายข้ามหน่วยขายอยู่แล้ว การเจอสภาพนี้แปลว่าขอบเขตถูกเลือกผิด

    ทีมที่ยังไม่รู้หน่วยขาย (acc_unit ว่าง และยังไม่เคยสร้าง payload) ไม่นับเป็นหน่วย
    — ข้อมูลไม่ครบต้องไม่กลายเป็นตัวบล็อกงานของผู้ใช้
    """
    from .employees import sales_units_of_sups

    units = sales_units_of_sups(target_sup_ids, target_month, target_year)
    if len({u for u in units.values() if u}) <= 1:
        return
    label = {"C": "รถเงินสด", "S": "เครดิต"}
    by_unit: dict[str, list[str]] = {}
    for sid, u in sorted(units.items()):
        if u:
            by_unit.setdefault(u, []).append(sid)
    raise HTTPException(
        400,
        detail=(
            "กระจายรวมทั้งภาคไม่ได้ — ขอบเขตนี้มีทั้งทีมเครดิตและทีมรถเงินสด ("
            + " · ".join(
                f"{label.get(u, u)}: {', '.join(sids)}"
                for u, sids in sorted(by_unit.items())
            )
            + ") สองหน่วยขายใช้ราคาคนละชุด กรุณาเลือกขอบเขตให้เหลือหน่วยขายเดียว"
        ),
    )


def _resolve_target_sup_ids(sup_id: str, raw: list[str] | None) -> list[str]:
    """
    ทีมที่เอาเป้ามาบวกรวมกัน — ทีมที่ยิง request ต้องอยู่ในกองเสมอและมาก่อน

    ถ้าหลุดออกไป เป้าของทีมตัวเองจะหายจากผลรวม แล้วประตู I1 จะบังคับให้
    ผลกระจายน้อยกว่าที่ควรเป็นทั้งภาค
    """
    own = str(sup_id or "").strip().upper()
    out = [own] if own else []
    for x in raw or []:
        sid = str(x or "").strip().upper()
        if sid and sid not in out:
            out.append(sid)
    return out


def _excel_target_boxes_path(
    sup_id: str,
    target_month: int,
    target_year: int,
    df_summed: pd.DataFrame | None,
) -> str:
    """
    แหล่งของแถว "เป้าหีบ (หัวหน้า)" ใน Excel ผลกระจาย

    โหมดรวมเป้าทั้งภาคต้องเขียนไฟล์ยอดรวมแยกไว้ก่อน ถ้าชี้ไปที่ไฟล์ของทีมเดียว
    Excel จะโชว์เป้าของทีมเดียวคู่กับหีบของทั้งภาค — ดูเหมือนกระจายเกินเป้ามหาศาล
    """
    if df_summed is None:
        return target_boxes_source_path(sup_id, target_month, target_year)
    path = target_boxes_union_cache_path(sup_id, target_month, target_year)
    try:
        atomic_write_csv(path, df_summed, index=False)
        return path
    except Exception as e:
        logger.warning("เขียนไฟล์เป้ารวมภาคไม่สำเร็จ (%s) — Excel จะใช้เป้าของทีมเดียว", e)
        return target_boxes_source_path(sup_id, target_month, target_year)


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

    target_sup_ids = _resolve_target_sup_ids(sup_id, req.target_sup_ids)
    summed_target = len(target_sup_ids) > 1
    if summed_target:
        reject_mixed_sales_units(target_sup_ids, target_month, target_year)
        df_sku, missing_target_sups = load_summed_target_boxes(
            target_sup_ids, target_month, target_year
        )
        if missing_target_sups:
            raise HTTPException(
                400,
                detail=(
                    "กระจายรวมทั้งภาคไม่ได้ — ยังไม่มีไฟล์เป้าหีบของทีม "
                    + ", ".join(missing_target_sups)
                    + " งวดนี้ กรุณาโหลดข้อมูลขั้นที่ 1 ใหม่แล้วลองอีกครั้ง"
                ),
            )
        logger.info(
            "optimize: รวมเป้าทั้งภาค %d ทีม (%s)",
            len(target_sup_ids),
            ", ".join(target_sup_ids[:8]),
        )
    else:
        df_sku, _ = load_target_csv_for(sup_id, target_month, target_year)
    if df_sku is None:
        raise HTTPException(500, detail="ไม่พบเป้าหีบของทีมนี้ กรุณาโหลดหน้า Dashboard ก่อน")

    df_sku = df_sku.copy()
    df_sku["sku"] = df_sku["sku"].astype(str).str.strip()
    df_sku["supervisor_target_boxes"] = pd.to_numeric(
        df_sku["supervisor_target_boxes"], errors="coerce"
    ).fillna(0)
    df_sku = df_sku[df_sku["supervisor_target_boxes"] > 0].copy()
    # ยุบ SKU ซ้ำ "ก่อน" แบ่งกลุ่มตามแบรนด์ (I6)
    #
    # allocate_boxes ยุบให้อยู่แล้ว แต่โหมดหลายกลยุทธ์แบ่ง df_sku ตามแบรนด์
    # ก่อนเรียก — SKU รหัสเดียวที่ติดสองแบรนด์จะไปอยู่คนละกลุ่ม แล้วถูกกระจาย
    # ครบเป้าในทั้งสองกลุ่ม = ได้หีบสองเท่าของเป้าจริง
    if df_sku["sku"].duplicated().any():
        dups = sorted(set(df_sku.loc[df_sku["sku"].duplicated(), "sku"]))
        logger.warning(
            "target_boxes ของ %s %s-%02d มี SKU ซ้ำ %d รหัส — ยุบเหลือแถวเดียว: %s",
            sup_id, target_year, target_month, len(dups), dups[:10],
        )
        df_sku = df_sku.drop_duplicates(subset=["sku"], keep="last").reset_index(drop=True)
    if df_sku.empty:
        raise HTTPException(
            400,
            detail=(
                "ไม่มี SKU ที่มีเป้าหีบใน Target Sun งวดนี้ — "
                "กรุณาโหลดข้อมูล Dashboard ใหม่"
            ),
        )

    # กระจายเฉพาะบางสินค้า (ปุ่ม "กระจายเฉพาะสินค้าที่เป้าเพิ่ม") — SKU อื่นไม่ถูกแตะ
    # I1 ยังบังคับเต็มบนเซ็ตที่เลือก: ทุก SKU ที่กระจายรอบนี้ต้องตรงเป้าเป๊ะ
    only_skus = [str(s).strip() for s in (req.only_skus or []) if str(s).strip()]
    if only_skus:
        df_sku = df_sku[df_sku["sku"].isin(set(only_skus))].copy()
        if df_sku.empty:
            raise HTTPException(
                400,
                detail=(
                    "ไม่พบสินค้าที่เลือกกระจายในเป้าหีบงวดนี้ — "
                    "กรุณาโหลดข้อมูลขั้นที่ 1 ใหม่แล้วลองอีกครั้ง"
                ),
            )
        logger.info("optimize: กระจายเฉพาะ %d SKU ที่เลือก", len(df_sku))

    df_all_targets = pd.DataFrame([t.model_dump() for t in req.yellowTargets])
    if df_all_targets.empty:
        raise HTTPException(400, detail="ไม่มีเป้าเหลือง (yellowTargets) — โหลดข้อมูล Dashboard ก่อน")
    df_all_targets["yellow_target"] = pd.to_numeric(
        df_all_targets["yellow_target"], errors="coerce"
    ).fillna(0.0)
    # ตัดพนักงานที่ "ไม่ต้องตั้งเป้า" ออกก่อนทุกอย่าง — /optimize ไม่เคยกรองพนักงานเลย
    # เชื่อรายชื่อจากหน้าเว็บล้วน หน้าเว็บรุ่นเก่าที่ค้างในเบราว์เซอร์จึงส่งคนเหล่านี้มาได้
    # ต้องตัดก่อน _requested_alloc_keys ด้วย ไม่งั้นด่าน I8 จะเติมแถว 0 พาเขากลับเข้ามา
    df_all_targets, dropped_no_target = _drop_no_target_employees(
        df_all_targets, sup_id, req
    )
    if df_all_targets.empty:
        raise HTTPException(
            400,
            detail=(
                "ไม่มีพนักงานให้กระจายหีบ — ทุกคนที่ส่งมาอยู่ในรายชื่อ "
                "「พนักงานที่ไม่ต้องตั้งเป้า」 กรุณาปลดอย่างน้อยหนึ่งคนในหน้าแอดมิน"
            ),
        )
    # บันทึก "ใครถูกขอมาบ้าง" ก่อนกรองเป้าเงิน — ด่าน I8 ท้ายฟังก์ชันใช้ชุดนี้เทียบ
    # ต้องเก็บจาก frame ที่ยังไม่กรอง ไม่งั้นคนที่ถูกกรองออกจะไม่มีวันถูกตรวจ = ด่านหลอก
    requested_alloc_keys = _requested_alloc_keys(df_all_targets)
    df_emp_targets = df_all_targets[df_all_targets["yellow_target"] > 0].copy()
    if df_emp_targets.empty:
        raise HTTPException(
            400,
            detail=(
                "ไม่มีพนักงานที่มีเป้าเงิน > 0 — ไม่สามารถเกลี่ยหีบได้ "
                "(ทุกคนเป้า 0 ในงวดนี้ / ตรวจสอบ Target Sun)"
            ),
        )
    df_prepared, reverse_map = prepare_optimize_targets(df_emp_targets)
    value_shares = _wh_value_shares(
        reverse_map, sup_id, target_month, target_year, df_sku
    )
    real_emp_list = list(
        {str(t.emp_id).strip() for t in req.yellowTargets if str(t.emp_id).strip()}
    )
    emp_list = df_prepared["or_emp_id"].astype(str).str.strip().tolist()
    eligible_set = set(emp_list)
    df_emp_targets = df_prepared[["or_emp_id", "yellow_target"]].rename(
        columns={"or_emp_id": "emp_id"}
    )

    strategy_u = req.strategy.upper()
    # กระจายรวมทั้งหน่วย: พนักงานมาจากหลายทีม ต้องอ่านประวัติจาก cache ของทุกทีมที่เกี่ยว
    # (ทีมเจ้าของเป้าอยู่ในลิสต์เสมอ และเรียงมาก่อนเพื่อให้ชนะตอนตัดคู่ซ้ำ)
    hist_sup_ids = [sup_id] + [
        s for s in (
            str(x).strip().upper() for x in (getattr(req, "peer_sup_ids", None) or [])
        )
        if s and s != str(sup_id).strip().upper()
    ]
    df_hist_3 = _read_hist_cache_across_teams(
        lambda sid: hist_cache_path(sid, target_month, target_year, n_months=3),
        hist_sup_ids,
        real_emp_list,
    )
    df_hist_6 = _read_hist_cache_across_teams(
        lambda sid: hist_cache_path(sid, target_month, target_year, n_months=6),
        hist_sup_ids,
        real_emp_list,
    )
    if len(hist_sup_ids) > 1:
        logger.info(
            "optimize: กระจายรวมทั้งหน่วย อ่านประวัติจาก %d ทีม (%s)",
            len(hist_sup_ids),
            ", ".join(hist_sup_ids[:6]),
        )
    if df_hist_3.empty and df_hist_6.empty:
        logger.warning("ไม่พบ hist cache → ใช้ตารางเปล่า")
    else:
        loaded = []
        if not df_hist_3.empty:
            loaded.append(f"3M={len(df_hist_3)}")
        if not df_hist_6.empty:
            loaded.append(f"6M={len(df_hist_6)}")
        logger.info("hist cache loaded (%s)", ", ".join(loaded))

    try:
        df_hist_lysm = _read_hist_cache_across_teams(
            lambda sid: hist_ly_same_month_cache_path(sid, target_month, target_year),
            hist_sup_ids,
            real_emp_list,
        )
        if not df_hist_lysm.empty:
            logger.info(
                "hist LY same-month loaded: %d rows (blend weight env ALLOC_HIST_LYM_WEIGHT, default 0.5)",
                len(df_hist_lysm),
            )
    except Exception as e:
        logger.warning("hist LY same-month cache read failed: %s", e)
        df_hist_lysm = pd.DataFrame()

    df_hist_3 = _maybe_split_hist(df_hist_3, reverse_map, value_shares)
    df_hist_6 = _maybe_split_hist(df_hist_6, reverse_map, value_shares)
    df_hist_lysm = _maybe_split_hist(df_hist_lysm, reverse_map, value_shares)

    df_hist_input, hist_months = _hist_input_for_strategy(
        strategy_u,
        df_hist_3,
        df_hist_6,
        df_hist_lysm,
        sup_id=sup_id,
        target_month=target_month,
        target_year=target_year,
    )
    df_hist = df_hist_3 if not df_hist_3.empty else df_hist_6

    try:
        df_hist_prev = _read_hist_cache_across_teams(
            lambda sid: hist_prev_month_cache_path(sid, target_month, target_year),
            hist_sup_ids,
            real_emp_list,
        )
        if not df_hist_prev.empty:
            logger.info("hist prev-month loaded: %d rows", len(df_hist_prev))
    except Exception as e:
        logger.warning("hist prev-month cache read failed: %s", e)
        df_hist_prev = pd.DataFrame()

    df_hist_prev = _maybe_split_hist(df_hist_prev, reverse_map, value_shares)

    logger.info(
        "Running strategy=%s for sup=%s (eligible emps for boxes: %d)",
        req.strategy,
        sup_id,
        len(emp_list),
    )
    locked_edits_data = [
        {
            "emp_id": _lock_or_emp_id(le.emp_id, le.warehouse_code),
            "sku": le.sku,
            "locked_boxes": le.locked_boxes,
        }
        for le in req.locked_edits
        if _lock_or_emp_id(le.emp_id, le.warehouse_code) in eligible_set
    ]
    # ล็อกรวมต้องไม่เกินเป้าของ SKU — ตรวจตั้งแต่รับ request (I2)
    # ถ้าปล่อยเข้าเครื่องคำนวณ ของเดิมจะกลบส่วนเกินทิ้งแล้วปล่อยผลที่เกินเป้าออกไป
    # ส่วนฝั่ง LP จะกลายเป็นโจทย์ที่แก้ไม่ได้แล้วตกไป proportional เงียบ ๆ
    if locked_edits_data:
        _target_by_sku = {
            str(r["sku"]).strip(): int(round(float(r["supervisor_target_boxes"] or 0)))
            for _, r in df_sku.iterrows()
        }
        _locked_by_sku: dict[str, int] = {}
        for _le in locked_edits_data:
            _s = str(_le["sku"]).strip()
            _locked_by_sku[_s] = _locked_by_sku.get(_s, 0) + int(_le["locked_boxes"])
        _over = {
            s: (tot, _target_by_sku[s])
            for s, tot in _locked_by_sku.items()
            if s in _target_by_sku and tot > _target_by_sku[s]
        }
        if _over:
            detail = "หีบที่ล็อกไว้รวมกันเกินเป้าหีบ — " + " | ".join(
                f"SKU {s}: ล็อกไว้ {tot} หีบ แต่เป้ามี {tgt} หีบ"
                for s, (tot, tgt) in sorted(_over.items())
            )
            logger.warning("optimize ปฏิเสธ: %s", detail)
            raise HTTPException(400, detail=detail)

    sku_ids_opt = df_sku["sku"].astype(str).str.strip().tolist()
    new_product_skus_used, detection_mode = detect_new_product_skus(
        sup_id, target_year, sku_ids_opt, df_hist_3 if not df_hist_3.empty else df_hist_6
    )
    new_products_even_mode = detection_mode if new_product_skus_used else "off"
    new_skus_cy_ly: set[str] | None = set()

    if req.new_products_even:
        if detection_mode == "cy_ly":
            new_skus_cy_ly = set(new_product_skus_used)
            new_products_even_mode = "cy_ly"
        elif detection_mode == "fallback_hist_window":
            new_skus_cy_ly = None
            new_products_even_mode = "fallback_hist_window"
        else:
            cy_ok = os.path.exists(hist_calendar_year_cache_path(sup_id, target_year))
            ly_ok = os.path.exists(hist_calendar_year_cache_path(sup_id, target_year - 1))
            if not cy_ok or not ly_ok:
                logger.warning(
                    "new_products_even เปิดอยู่ แต่ไม่พบ cache ปีปฏิทิน (hist_cy_) — "
                    "จะ fallback ใช้เงื่อนไขยอด 3M/6M = 0 (ชั่วคราว) — "
                    "แนะนำให้โหลดหน้า Dashboard ใหม่เพื่อสร้างไฟล์ CY/LY"
                )
            new_skus_cy_ly = None
            new_product_skus_used = sorted(skus_zero_team_hist_window(df_hist, sku_ids_opt))
            new_products_even_mode = (
                "fallback_hist_window" if new_product_skus_used else "off"
            )
        logger.info(
            "new_products_even: แบ่งเท่า %d SKU (mode=%s)",
            len(new_product_skus_used),
            new_products_even_mode,
        )
    elif new_product_skus_used:
        logger.info(
            "สินค้าใหม่ %d SKU (mode=%s) — แสดงป้าย UI (ยังไม่ติ๊กแบ่งเท่า)",
            len(new_product_skus_used),
            detection_mode,
        )

    # ──────────────────────────────────────────────────────────────
    # MULTI-STRATEGY: ผู้ใช้เลือกหลายวิธี + กำหนดแบรนด์ไหนใช้วิธีไหน
    # ──────────────────────────────────────────────────────────────
    brand_map = req.brand_strategy_map or {}
    distinct_strategies = {s for s in brand_map.values() if s}
    multi_strategy_run = False
    optimization_fallback = False
    sku_strategy_map: dict[str, str] = {}
    hist_by_strategy: dict[str, pd.DataFrame] = {}
    if brand_map and len(distinct_strategies) > 1 and not df_sku.empty:
        logger.info(
            "multi-strategy run: %d distinct strategies across %d brands",
            len(distinct_strategies), len(brand_map),
        )
        multi_strategy_run = True
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

        even_skus_global = (
            frozenset(_norm_sku(s) for s in new_product_skus_used)
            if req.new_products_even and new_product_skus_used
            else frozenset()
        )

        for _, row in df_sku_local.iterrows():
            sku_key = str(row["sku"]).strip()
            strat_key = str(row["_strategy_resolved"] or req.strategy).upper()
            sku_strategy_map[sku_key] = strat_key
            if strat_key not in hist_by_strategy:
                df_hist_grp, _ = _hist_input_for_strategy(
                    strat_key,
                    df_hist_3,
                    df_hist_6,
                    df_hist_lysm,
                    sup_id=sup_id,
                    target_month=target_month,
                    target_year=target_year,
                )
                hist_by_strategy[strat_key] = df_hist_grp

        alloc_parts = []
        for strat in sorted(df_sku_local["_strategy_resolved"].unique()):
            strat_u = str(strat).upper()
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
            if req.new_products_even and new_product_skus_used:
                new_skus_grp = {s for s in new_product_skus_used if s in sku_in_grp}

            df_hist_grp = hist_by_strategy.get(strat_u, df_hist_input)

            df_alloc_grp = allocate_boxes(
                df_targets_grp,
                df_sku_grp.drop(columns=["_brand_key", "_strategy_resolved", "_value"], errors="ignore"),
                df_hist_grp,
                strategy=strat,
                force_min_one=req.force_min_one,
                locked_edits=locked_grp if locked_grp else None,
                cap_multiplier=req.cap_multiplier,
                even_new_products=bool(req.new_products_even),
                new_product_skus=new_skus_grp if (req.new_products_even and new_skus_grp) else None,
                hist_balance=float(req.hist_balance),
                revenue_tolerance_baht=float(req.revenue_tolerance_baht),
                tiered_allocation=bool(req.tiered_allocation),
                tier_pct=float(req.tier_pct),
            )
            if df_alloc_grp.attrs.get("optimization_fallback"):
                optimization_fallback = True
            alloc_parts.append(df_alloc_grp)
        df_allocation = (
            pd.concat(alloc_parts, ignore_index=True)
            if alloc_parts
            else pd.DataFrame(columns=["emp_id", "sku", "allocated_boxes"])
        )
        if not df_allocation.empty and req.tiered_allocation:
            df_allocation = _post_merge_revenue_balance(
                df_allocation,
                df_emp_targets,
                df_sku,
                sku_strategy=sku_strategy_map,
                hist_by_strategy=hist_by_strategy,
                locked_edits_data=locked_edits_data,
                force_min_one=bool(req.force_min_one),
                cap_multiplier=req.cap_multiplier,
                even_skus=even_skus_global,
                tiered_allocation=bool(req.tiered_allocation),
                tier_pct=float(req.tier_pct),
                revenue_tolerance_baht=float(req.revenue_tolerance_baht),
            )
            logger.info("multi-strategy: post-merge revenue balance applied")
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
            new_product_skus=(
                frozenset(new_product_skus_used) if req.new_products_even and new_product_skus_used else None
            ),
            hist_balance=float(req.hist_balance),
            revenue_tolerance_baht=float(req.revenue_tolerance_baht),
            tiered_allocation=bool(req.tiered_allocation),
            tier_pct=float(req.tier_pct),
        )
        optimization_fallback = bool(df_allocation.attrs.get("optimization_fallback"))

    tier_flex_skus: list[str] = []
    if req.tiered_allocation:
        tier_flex_skus = sorted(_flex_skus_by_target_value(df_sku, float(req.tier_pct)))

    # log meta จาก Step 2
    if req.bui_deductions:
        logger.info("bui_deductions provided: %d emps", len(req.bui_deductions))
    if req.neg_growth_reason:
        logger.info("neg_growth_reason: %s", req.neg_growth_reason[:200])

    if not multi_strategy_run:
        if not df_hist_input.empty:
            df_hist_avg = df_hist_input.groupby(["emp_id", "sku"])["hist_boxes"].sum().reset_index()
            df_hist_avg["hist_avg"] = (df_hist_avg["hist_boxes"] / float(hist_months)).round(1)
        else:
            df_hist_avg = pd.DataFrame(columns=["emp_id", "sku", "hist_avg"])
    else:
        hist_avg_frames: list[pd.DataFrame] = []
        strat_months: dict[str, int] = {}
        for strat_u in sorted(hist_by_strategy.keys()):
            df_hist_grp = hist_by_strategy[strat_u]
            skus_for_strat = [s for s, st in sku_strategy_map.items() if st == strat_u]
            if df_hist_grp.empty or not skus_for_strat:
                continue
            _, months = _hist_input_for_strategy(
                strat_u,
                df_hist_3,
                df_hist_6,
                df_hist_lysm,
                sup_id=sup_id,
                target_month=target_month,
                target_year=target_year,
            )
            strat_months[strat_u] = months
            sub = df_hist_grp[df_hist_grp["sku"].astype(str).str.strip().isin(skus_for_strat)]
            if sub.empty:
                continue
            g = sub.groupby(["emp_id", "sku"], as_index=False)["hist_boxes"].sum()
            g["hist_avg"] = (g["hist_boxes"] / float(months)).round(1)
            hist_avg_frames.append(g[["emp_id", "sku", "hist_avg"]])
        df_hist_avg = (
            pd.concat(hist_avg_frames, ignore_index=True)
            if hist_avg_frames
            else pd.DataFrame(columns=["emp_id", "sku", "hist_avg"])
        )
        if strat_months:
            hist_months = max(strat_months.values())

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

    df_final = restore_allocation_emp_ids(df_final, reverse_map)

    # ── I8: พนักงานที่ส่งเข้ามาต้องมีแถวกลับออกไปเสมอ (หีบ 0 ได้) ──────────
    #
    # "ไม่มีแถว" กับ "มีแถวแต่เป็น 0" ต่างกันมากสำหรับทุกอย่างปลายน้ำ: ตารางขั้นที่ 3
    # สร้างแถวจากผลลัพธ์ ถ้าไม่มีแถวก็ไม่มีคนคนนั้นบนจอ แล้วตัวเกลี่ยอัตโนมัติจะมองว่า
    # ยอดขาดและยกหีบไปให้เพื่อนทันที โดยที่ยอดต่อ SKU ยังตรงเป้า ด่าน I1 จึงมองไม่เห็น
    #
    # ต้องอยู่ "หลัง" restore_allocation_emp_ids (คีย์เป็น emp+คลังแล้ว) และ "ก่อน" ด่าน I1
    # เพื่อให้ I1 ตรวจกับ frame เดียวกับที่เขียนไฟล์/สร้าง Excel/ตอบกลับจริง
    # การเติมมีแต่แถวหีบ 0 จึงเปลี่ยนผลรวมต่อ SKU ไม่ได้เลย (มีเทสพิสูจน์)
    emp_missing = missing_employee_alloc_keys(df_final, requested_alloc_keys)
    if emp_missing:
        lost_with_money = [m for m in emp_missing if m["yellow_target"] > 0]
        if lost_with_money:
            # เป้าเงิน > 0 แต่ไม่มีแถว = ท่อแปลงข้อมูลพัง ไม่ใช่การตัดสินใจของผู้ใช้
            logger.error(
                "I8: พนักงานหายจากผลกระจาย %d ราย (มีเป้าเงิน) — เติมแถว 0 ให้แล้ว: %s",
                len(lost_with_money),
                [f"{m['emp_id']}|{m['warehouse_code']}" for m in lost_with_money[:10]],
            )
        else:
            # เป้าเงิน 0 = ไม่เข้าเครื่องคำนวณตามกติกา แต่ยังต้องมีแถวไว้ให้เห็นบนจอ
            logger.info("I8: เติมแถวหีบ 0 ให้พนักงานเป้าเงิน 0 จำนวน %d ราย", len(emp_missing))
        df_final = zero_fill_missing_employees(df_final, emp_missing)

    # ── ประตูสุดท้าย: ผลรวมหีบต่อ SKU ต้องตรงเป้า (I1) ──────────────────
    # ต้องตรวจ "ก่อน" เขียนไฟล์และสร้าง Excel
    # เดิมตรวจแล้วแค่ logger.warning จากนั้นก็เขียนไฟล์ สร้าง Excel และตอบ 200 ตามปกติ
    # ผลที่ผิดจึงถึงมือผู้ใช้ในสภาพที่ดูเหมือนสำเร็จทุกประการ
    sku_checks = validate_allocation_vs_targets(df_final, df_sku)
    if sku_checks:
        logger.error("allocation vs target mismatch: %s", sku_checks)
        if not _allow_allocation_mismatch():
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "allocation_mismatch",
                    "message": (
                        "ผลกระจายไม่ตรงเป้าหีบ — ระบบไม่บันทึกผลนี้ "
                        "กรุณาตรวจตัวเลขที่ล็อกไว้แล้วกระจายใหม่"
                    ),
                    "sku_total_checks": sku_checks[:20],
                    "mismatch_count": len(sku_checks),
                },
            )
        logger.warning(
            "ALLOC_ALLOW_MISMATCH เปิดอยู่ — ปล่อยผลที่ไม่ตรงเป้าผ่าน (%d SKU)",
            len(sku_checks),
        )

    # ผูกชื่อไฟล์กับงวด — กันสองงวดของซุปเดียวกันเขียนทับกันแล้ว Excel อ่านผิดงวด
    result_csv_path = result_path(sup_id, target_month, target_year)
    df_to_write = df_final
    if only_skus:
        # "กระจายเฉพาะสินค้าที่เป้าเปลี่ยน" — df_final มีเฉพาะ SKU ชุดนั้น
        # ถ้าเขียนทับทั้งไฟล์ ผลของ SKU อื่นทั้งงวดหายไปจากไฟล์และจาก Excel ฝั่ง
        # เซิร์ฟเวอร์ทันที (หน้าจอไม่ฟ้องเพราะ merge ฝั่งเบราว์เซอร์เอง) แล้วใครที่
        # กดดาวน์โหลดทีหลังจะได้ไฟล์ที่มีสินค้าไม่กี่ตัว
        df_to_write = _merge_partial_result(result_csv_path, df_final, only_skus)
    atomic_write_csv(result_csv_path, df_to_write, index=False)

    yellow_map: dict[str, float] = {}
    for y in req.yellowTargets:
        em = str(y.emp_id).strip()
        yellow_map[em] = yellow_map.get(em, 0.0) + float(y.yellow_target or 0)

    create_target_excel(
        result_csv=result_csv_path,
        output_path=excel_path(sup_id, target_month, target_year),
        brand_filter="ALL",
        yellow_map=yellow_map,
        sup_id=sup_id,
        # ต้องเช็คว่ามีไฟล์จริง: df_sku ข้างบนอาจมาจาก fallback ไฟล์ global ช่วงเปลี่ยนผ่าน
        # ซึ่งแปลว่าไฟล์ราย sup ยังไม่มี — generate_excel เจอ path ไม่มีไฟล์แล้วคืน {} เงียบ ๆ
        # จะได้ Excel ที่แถว "เป้าหีบ (หัวหน้า)" ว่างทั้งที่ตัวเลขกระจายมีค่า
        target_boxes_csv=_excel_target_boxes_path(
            sup_id, target_month, target_year, df_sku if summed_target else None
        ),
        scope_sup_ids=target_sup_ids if summed_target else [],
    )

    return {
        "allocations": df_final.to_dict(orient="records"),
        "sku_total_checks": sku_checks,
        "hist_window_months": hist_months,
        "new_products_even_mode": new_products_even_mode,
        "new_product_skus": new_product_skus_used,
        "tiered_allocation": bool(req.tiered_allocation),
        "tier_pct": float(req.tier_pct),
        "tier_flex_skus": tier_flex_skus,
        "tier_strict_sku_count": max(0, len(df_sku) - len(tier_flex_skus)) if req.tiered_allocation else 0,
        "revenue_scale": round(_revenue_scale_factor(df_emp_targets, df_sku), 6),
        "optimization_fallback": optimization_fallback,
        # ใครถูกตัดเพราะอยู่ในรายชื่อ「ไม่ต้องตั้งเป้า」— หน้าเว็บเอาไปบอกผู้ใช้
        # ไม่งั้นจะเห็นแค่ว่าคนหายไปจากตาราง แล้วเข้าใจว่าเป็นบั๊กแบบเดียวกับ C442
        "no_target_excluded": dropped_no_target,
    }
