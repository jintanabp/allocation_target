"""
OR_engine.py — Target Box Allocation Engine
────────────────────────────────────────────
Strategies:
  L3M   — LP: baseline 3M + ปรับมูลค่ารายคน (tolerance ±1,000 บ.) ภายในรั้ว ±20% ต่อ SKU
  L6M   — LP: baseline 6M + ปรับมูลค่ารายคน (รั้ว ±20%)
  LY    — LP: baseline LY + ปรับมูลค่ารายคน (รั้ว ±20%)
  EVEN  — เกลี่ยเท่ากันทุกคน (proportional อย่างเดียว)
  PUSH  — ผลักดันคนขายน้อย (proportional อย่างเดียว)
  LP    — LP ตาม yellow_target (baseline L3M, ปรับได้ด้วย hist_balance)
"""

import pandas as pd
import logging

logger = logging.getLogger("target_allocation.OR")

# ── Cap / band constants
_CAP_MULTIPLIER = 3.0
_DEFAULT_REVENUE_TOLERANCE_BAHT = 1000.0
_DEFAULT_HIST_BAND_PCT = 0.20
_TIER_DEFAULT_PCT = 0.80
_TIER_FLEX_BAND_PCT = 0.35
_TIER_STRICT_BAND_PCT = 0.12
_TIER_FLEX_ANCHOR_MULT = 0.35
_TIER_STRICT_ANCHOR_MULT = 3.5
_TIER_LP_HIST_BALANCE = 0.35


def _revenue_scale_factor(
    df_emp_targets: pd.DataFrame,
    df_sku: pd.DataFrame,
) -> float:
    """สเกลเป้าเงินให้ sum(yellow) สอดคล้องมูลค่าหีบรวมที่จัดสรรได้"""
    try:
        prices = pd.to_numeric(df_sku.get("price_per_box", 0), errors="coerce").fillna(0)
        boxes = pd.to_numeric(df_sku.get("supervisor_target_boxes", 0), errors="coerce").fillna(0)
        total_possible = float((prices * boxes).sum())
    except Exception:
        total_possible = 0.0
    try:
        total_yellow = float(
            pd.to_numeric(df_emp_targets.get("yellow_target", 0), errors="coerce").fillna(0).sum()
        )
    except Exception:
        total_yellow = 0.0
    if total_possible > 0 and total_yellow > 0:
        return total_possible / total_yellow
    return 1.0


def _norm_sku(s) -> str:
    return str(s).strip() if s is not None else ""


def _skus_with_target_boxes(df_sku: pd.DataFrame) -> list[str]:
    """SKU ที่มีเป้าหีบหัวหน้า > 0 — ใช้เกลี่ยและส่ง Target Sun"""
    if df_sku is None or df_sku.empty or "sku" not in df_sku.columns:
        return []
    boxes = pd.to_numeric(
        df_sku.get("supervisor_target_boxes", 0), errors="coerce"
    ).fillna(0)
    return [
        _norm_sku(s)
        for s, b in zip(df_sku["sku"].tolist(), boxes.tolist())
        if _norm_sku(s) and int(b) > 0
    ]


def _expand_full_allocation_matrix(
    df_out: pd.DataFrame,
    df_emp_targets: pd.DataFrame,
    df_sku: pd.DataFrame,
) -> pd.DataFrame:
    """เติมคู่ emp×sku ที่หีบ = 0 — เฉพาะ SKU ที่มีเป้า TGA (ส่งทับเป้าเดิมใน DB)"""
    emps = [
        str(e).strip()
        for e in df_emp_targets["emp_id"].tolist()
        if str(e).strip()
    ]
    skus = _skus_with_target_boxes(df_sku)
    if not skus and df_sku is not None and not df_sku.empty:
        skus = [_norm_sku(s) for s in df_sku["sku"].tolist() if _norm_sku(s)]
    if not emps or not skus:
        return df_out

    full = pd.MultiIndex.from_product([emps, skus], names=["emp_id", "sku"]).to_frame(
        index=False
    )
    if df_out is None or df_out.empty:
        full["allocated_boxes"] = 0
        return full

    out = df_out.copy()
    out["emp_id"] = out["emp_id"].astype(str).str.strip()
    out["sku"] = out["sku"].map(_norm_sku)
    extra_cols = [c for c in out.columns if c not in ("emp_id", "sku", "allocated_boxes")]
    merge_cols = ["emp_id", "sku", "allocated_boxes"] + extra_cols
    merged = full.merge(out[merge_cols], on=["emp_id", "sku"], how="left")
    merged["allocated_boxes"] = (
        pd.to_numeric(merged["allocated_boxes"], errors="coerce").fillna(0).astype(int)
    )
    for col in extra_cols:
        if col in merged.columns:
            if merged[col].dtype == object or col == "hist_dev_status":
                merged[col] = merged[col].fillna("")
            else:
                merged[col] = pd.to_numeric(merged[col], errors="coerce").fillna(0)
    return merged


def _skus_zero_team_hist(df_hist: pd.DataFrame, sku_list: list) -> frozenset[str]:
    """
    SKU ที่รวมยอดหีบในประวัติช่วงที่ใช้เกลี่ย (df_hist) = 0 ทั้งทีม
    ใช้เสริมเมื่อติ๊กสินค้าใหม่ — กันกรณีไม่มี/ไม่ครบ hist_cy หรือคีย์ SKU ไม่ตรง CY/LY
    """
    sku_list = [_norm_sku(s) for s in sku_list if _norm_sku(s)]
    if df_hist is None or df_hist.empty:
        return frozenset(sku_list)
    df = df_hist.copy()
    df["sku"] = df["sku"].map(_norm_sku)
    g = df.groupby("sku")["hist_boxes"].sum()
    return frozenset(s for s in sku_list if float(g.get(s, 0) or 0) <= 0)


def allocate_boxes(
    df_emp_targets: pd.DataFrame,
    df_sku: pd.DataFrame,
    df_hist: pd.DataFrame,
    strategy: str = "L3M",
    force_min_one: bool = False,
    locked_edits: list = None,
    cap_multiplier: float = None,  # override _CAP_MULTIPLIER (Custom strategy)
    even_new_products: bool = False,
    new_product_skus: set | frozenset | None = None,
    hist_balance: float = 0.85,
    revenue_tolerance_baht: float = 1000.0,
    tiered_allocation: bool = True,
    tier_pct: float = _TIER_DEFAULT_PCT,
) -> pd.DataFrame:
    strategy = strategy.upper()
    valid = ("L3M", "L6M", "LY", "EVEN", "PUSH", "LP")
    if strategy not in valid:
        strategy = "L3M"

    locked_map = {}
    if locked_edits:
        for le in locked_edits:
            locked_map[(le["emp_id"], le["sku"])] = int(le["locked_boxes"])

    cy_ly_skus: frozenset[str] = frozenset()
    zero_hist_skus: frozenset[str] = frozenset()
    even_skus: frozenset[str] = frozenset()
    if even_new_products:
        # ถ้า backend ส่งชุด SKU จาก CY/LY มา (ไม่ None) ให้ใช้ "เฉพาะชุดนั้น" ตามนิยามสินค้าใหม่
        # fallback ไปใช้ยอด 3M/6M = 0 เฉพาะตอน cache CY/LY ไม่พร้อมเท่านั้น (backend จะส่ง None)
        if new_product_skus is None:
            zero_hist_skus = _skus_zero_team_hist(df_hist, df_sku["sku"].tolist())
            even_skus = zero_hist_skus
        else:
            even_skus = frozenset(_norm_sku(s) for s in (new_product_skus or []))

    logger.info(
        "allocate_boxes: strategy=%s emp=%d sku=%d force_min_one=%s locked=%d even_new_products=%s even_skus=%d (cy_ly=%d zero_hist=%d)",
        strategy,
        len(df_emp_targets),
        len(df_sku),
        force_min_one,
        len(locked_map),
        even_new_products,
        len(even_skus),
        len(cy_ly_skus),
        len(zero_hist_skus),
    )

    # ถ้า custom strategy ส่ง cap_multiplier มา ให้ใช้ค่านั้นแทน default
    effective_cap = cap_multiplier if cap_multiplier is not None else _CAP_MULTIPLIER
    hb = max(0.0, min(1.0, float(hist_balance if hist_balance is not None else 0.85)))
    if tiered_allocation:
        hb = min(hb, _TIER_LP_HIST_BALANCE)
    rev_tol = max(0.0, float(revenue_tolerance_baht if revenue_tolerance_baht is not None else 1000.0))

    flex_skus: frozenset[str] | None = None
    if tiered_allocation:
        flex_skus = _flex_skus_by_target_value(df_sku, tier_pct) - even_skus
        logger.info(
            "tiered_allocation: flex_skus=%d strict_skus=%d even_skus=%d tier_pct=%.0f%%",
            len(flex_skus),
            max(0, len(_skus_with_target_boxes(df_sku)) - len(flex_skus) - len(even_skus)),
            len(even_skus),
            tier_pct * 100,
        )

    _LP_STRATEGIES = frozenset({"L3M", "L6M", "LY", "LP"})
    base_map: dict[tuple[str, str], int] = {}
    if strategy in _LP_STRATEGIES:
        baseline = strategy if strategy in ("L3M", "L6M", "LY") else "L3M"
        df_base = _proportional(
            df_emp_targets,
            df_sku,
            df_hist,
            baseline,
            force_min_one,
            locked_map,
            effective_cap,
            even_skus=even_skus,
        )
        base_map = _baseline_map_from_df(df_base, df_emp_targets, df_sku)
        df_out = _lp_optimize(
            df_emp_targets,
            df_sku,
            df_hist,
            force_min_one,
            locked_map,
            even_skus=even_skus,
            baseline_strategy=baseline,
            hist_balance=hb,
            revenue_tolerance_baht=rev_tol,
            cap_multiplier=effective_cap,
            base_map=base_map,
            hist_band_pct=_DEFAULT_HIST_BAND_PCT,
            tiered_allocation=bool(tiered_allocation),
            flex_skus=flex_skus,
            flex_band_pct=_TIER_FLEX_BAND_PCT,
            strict_band_pct=_TIER_STRICT_BAND_PCT,
        )
    else:
        df_out = _proportional(
            df_emp_targets,
            df_sku,
            df_hist,
            strategy,
            force_min_one,
            locked_map,
            effective_cap,
            even_skus=even_skus,
        )

    if tiered_allocation and flex_skus and base_map:
        strict_keys = frozenset(
            _norm_sku(s)
            for s in _skus_with_target_boxes(df_sku)
            if _norm_sku(s) not in flex_skus and _norm_sku(s) not in even_skus
        )
        skip_for_greedy = strict_keys | even_skus
        df_out = _greedy_revenue_balancer(
            df_out,
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

    if even_skus:
        df_out = _enforce_even_skus_on_df(
            df_out,
            even_skus,
            df_emp_targets,
            df_sku,
            locked_map,
            force_min_one,
        )
        if base_map and strategy in _LP_STRATEGIES:
            base_map = _baseline_map_from_df(df_out, df_emp_targets, df_sku)

    df_expanded = _expand_full_allocation_matrix(df_out, df_emp_targets, df_sku)
    if not base_map:
        prop_strat = strategy if strategy in ("L3M", "L6M", "LY", "EVEN", "PUSH") else "L3M"
        df_base = _proportional(
            df_emp_targets,
            df_sku,
            df_hist,
            prop_strat,
            force_min_one,
            locked_map,
            effective_cap,
            even_skus=even_skus,
        )
        base_map = _baseline_map_from_df(df_base, df_emp_targets, df_sku)
    if base_map:
        df_expanded = _annotate_hist_deviation(
            df_expanded,
            base_map,
            band_pct=_DEFAULT_HIST_BAND_PCT,
            even_skus=even_skus,
        )
    return df_expanded


def _baseline_map_from_df(
    df_base: pd.DataFrame,
    df_emp_targets: pd.DataFrame,
    df_sku: pd.DataFrame,
) -> dict[tuple[str, str], int]:
    """baseline หีบต่อ (emp, sku) จาก proportional — ใช้รั้ว ±% และ flag UI"""
    emps = [str(e).strip() for e in df_emp_targets["emp_id"].tolist() if str(e).strip()]
    skus = _skus_with_target_boxes(df_sku)
    base_map: dict[tuple[str, str], int] = {(e, s): 0 for e in emps for s in skus}
    if df_base is not None and not df_base.empty:
        for _, r in df_base.iterrows():
            key = (str(r["emp_id"]).strip(), _norm_sku(r["sku"]))
            if key in base_map:
                base_map[key] = int(r["allocated_boxes"])
    return base_map


def _flex_skus_by_target_value(df_sku: pd.DataFrame, tier_pct: float = _TIER_DEFAULT_PCT) -> frozenset[str]:
    """SKU หลัก: สะสมมูลค่าเป้าหีบ (หีบ×ราคา) ถึง tier_pct ของทีม (Pareto)"""
    tier_pct = max(0.5, min(0.95, float(tier_pct)))
    skus = _skus_with_target_boxes(df_sku)
    if not skus:
        return frozenset()

    sku_to_val: dict[str, float] = {}
    for _, r in df_sku.iterrows():
        s = _norm_sku(r.get("sku"))
        if s not in skus:
            continue
        boxes = int(pd.to_numeric(r.get("supervisor_target_boxes", 0), errors="coerce") or 0)
        price = float(pd.to_numeric(r.get("price_per_box", 0), errors="coerce") or 0)
        sku_to_val[s] = sku_to_val.get(s, 0.0) + max(0, boxes) * max(0.0, price)

    if not sku_to_val:
        return frozenset()

    total = float(sum(sku_to_val.values()))
    if total <= 0:
        return frozenset(skus)

    ordered = sorted(sku_to_val.items(), key=lambda x: -x[1])
    cum = 0.0
    flex: list[str] = []
    for s, v in ordered:
        flex.append(s)
        cum += v
        if cum / total >= tier_pct:
            break
    if not flex:
        flex = [ordered[0][0]]
    return frozenset(flex)


def _distribute_even_integers(total: int, n_slots: int) -> list[int]:
    """แบ่งจำนวนเต็มให้เท่าที่สุด (ต่างกันได้ไม่เกิน 1)"""
    n = max(0, int(n_slots))
    total = max(0, int(total))
    if n <= 0:
        return []
    base = total // n
    rem = total % n
    return [base + (1 if i < rem else 0) for i in range(n)]


def _enforce_even_skus_on_df(
    df_out: pd.DataFrame,
    even_skus: frozenset[str],
    df_emp_targets: pd.DataFrame,
    df_sku: pd.DataFrame,
    locked_map: dict | None,
    force_min_one: bool,
) -> pd.DataFrame:
    """บังคับ SKU สินค้าใหม่ให้แบ่งเท่าทุกคน (หลัง LP/greedy — กันโหมดหลัก/รองดึงไปปรับเงิน)"""
    locked_map = locked_map or {}
    if not even_skus:
        return df_out

    employees = [
        str(e).strip()
        for e in df_emp_targets["emp_id"].tolist()
        if str(e).strip()
    ]
    if not employees:
        return df_out

    sku_targets: dict[str, int] = {}
    for _, r in df_sku.iterrows():
        s = _norm_sku(r.get("sku"))
        if s in even_skus:
            sku_targets[s] = max(
                0,
                int(pd.to_numeric(r.get("supervisor_target_boxes", 0), errors="coerce") or 0),
            )

    rows: list[dict] = []
    if df_out is not None and not df_out.empty:
        for _, r in df_out.iterrows():
            e = str(r["emp_id"]).strip()
            s = _norm_sku(r["sku"])
            if s not in even_skus and e in employees:
                rows.append(
                    {
                        "emp_id": e,
                        "sku": r["sku"],
                        "allocated_boxes": int(
                            pd.to_numeric(r.get("allocated_boxes", 0), errors="coerce") or 0
                        ),
                    }
                )

    n_emps = len(employees)
    for sku_key, total_target in sku_targets.items():
        if total_target <= 0:
            continue

        locked_by_emp: dict[str, int] = {}
        for e in employees:
            if (e, sku_key) in locked_map:
                locked_by_emp[e] = max(0, int(locked_map[(e, sku_key)]))

        locked_sum = sum(locked_by_emp.values())
        free_emps = [e for e in employees if e not in locked_by_emp]
        if not free_emps:
            for e, boxes in locked_by_emp.items():
                if boxes > 0:
                    rows.append({"emp_id": e, "sku": sku_key, "allocated_boxes": boxes})
            continue

        base_box = 1 if force_min_one and total_target >= n_emps else 0
        remaining = max(0, total_target - locked_sum - base_box * len(free_emps))
        parts = _distribute_even_integers(remaining, len(free_emps))

        for e, boxes in locked_by_emp.items():
            if boxes > 0:
                rows.append({"emp_id": e, "sku": sku_key, "allocated_boxes": boxes})

        for i, e in enumerate(free_emps):
            boxes = base_box + parts[i]
            if boxes > 0:
                rows.append({"emp_id": e, "sku": sku_key, "allocated_boxes": boxes})

    if not rows:
        return df_out
    return pd.DataFrame(rows)


def _tier_cell_band_pct(
    sku_key: str,
    *,
    tiered_allocation: bool,
    flex_skus: frozenset[str] | None,
    default_band_pct: float,
    flex_band_pct: float,
    strict_band_pct: float,
) -> float:
    if not tiered_allocation or not flex_skus:
        return default_band_pct
    return flex_band_pct if sku_key in flex_skus else strict_band_pct


def _tier_cell_anchor_mult(
    sku_key: str,
    *,
    tiered_allocation: bool,
    flex_skus: frozenset[str] | None,
) -> float:
    if not tiered_allocation or not flex_skus:
        return 1.0
    return _TIER_FLEX_ANCHOR_MULT if sku_key in flex_skus else _TIER_STRICT_ANCHOR_MULT


def _hist_band_int_bounds(base: int, band_pct: float, var_min: int = 0) -> tuple[int, int]:
    """คืน (lo, hi) หีบ integer ที่อนุญาต ไม่เกิน ±band_pct จาก baseline"""
    import math

    base = max(0, int(base))
    bp = max(0.0, min(1.0, float(band_pct)))
    if base <= 0:
        return max(var_min, 0), max(var_min, 0)
    lo = max(var_min, int(math.floor(base * (1.0 - bp))))
    hi = max(lo, int(math.ceil(base * (1.0 + bp))))
    return lo, hi


def _annotate_hist_deviation(
    df: pd.DataFrame,
    base_map: dict[tuple[str, str], int],
    *,
    band_pct: float = _DEFAULT_HIST_BAND_PCT,
    even_skus: frozenset | None = None,
) -> pd.DataFrame:
    """
    เพิ่ม baseline_boxes, hist_dev_pct, hist_dev_status
    status: ok | near | far | "" (ไม่ใช้ flag — baseline 0 / สินค้าเกลี่ย)
    """
    even_skus = even_skus or frozenset()
    band_pct = max(0.0, min(1.0, float(band_pct)))
    band_pct100 = band_pct * 100.0
    near_threshold = band_pct100 * 0.75

    out = df.copy()
    baselines = []
    pcts = []
    statuses = []
    for _, r in out.iterrows():
        emp = str(r["emp_id"]).strip()
        sku = _norm_sku(r["sku"])
        alloc = int(pd.to_numeric(r.get("allocated_boxes", 0), errors="coerce") or 0)
        base = int(base_map.get((emp, sku), 0))
        baselines.append(base)
        if sku in even_skus or base <= 0:
            pcts.append(None)
            statuses.append("")
            continue
        pct = round((alloc - base) / base * 100.0, 1)
        pcts.append(pct)
        abs_pct = abs(pct)
        if abs_pct > band_pct100 + 0.5:
            statuses.append("far")
        elif abs_pct >= near_threshold:
            statuses.append("near")
        else:
            statuses.append("ok")
    out["baseline_boxes"] = baselines
    out["hist_dev_pct"] = pd.array(pcts, dtype=object)
    out["hist_dev_status"] = statuses
    return out


def _lp_weights_from_balance(hist_balance: float) -> tuple[float, float, float]:
    """คืน (shortfall_weight, excess_weight, hist_anchor) จาก slider 0=เงิน … 1=ประวัติ"""
    hb = max(0.0, min(1.0, float(hist_balance)))
    hist_anchor = 0.2 + hb * 1.8
    shortfall_w = 2.5 - hb * 2.0
    excess_w = 1.875 - hb * 1.5
    return shortfall_w, excess_w, hist_anchor

def _cap_and_redistribute(raw: dict, total: int, cap_multiplier: float = None) -> dict:
    """
    จำกัด weight outlier: ถ้าใครได้ > mean * CAP ให้ cap แล้วกระจายส่วนเกินให้คนที่เหลือ
    ทำซ้ำจนไม่มีคนเกิน cap (max 10 รอบ)
    cap_multiplier: override _CAP_MULTIPLIER ถ้า Custom strategy ส่งมา
    """
    effective_cap_mult = cap_multiplier if cap_multiplier is not None else _CAP_MULTIPLIER
    emps = list(raw.keys())
    if not emps or total <= 0:
        return {e: 0 for e in emps}

    allocated = dict(raw)
    for _ in range(10):
        mean_alloc = total / len(emps)
        cap = mean_alloc * effective_cap_mult
        overflow = 0.0
        uncapped = []
        for e in emps:
            if allocated[e] > cap:
                overflow += allocated[e] - cap
                allocated[e] = cap
            else:
                uncapped.append(e)
        if overflow < 0.5 or not uncapped:
            break
        # กระจาย overflow ให้คนที่ยังไม่ถูก cap ตามสัดส่วนเดิม
        unc_sum = sum(allocated[e] for e in uncapped)
        if unc_sum <= 0:
            per = overflow / len(uncapped)
            for e in uncapped:
                allocated[e] += per
        else:
            for e in uncapped:
                allocated[e] += overflow * (allocated[e] / unc_sum)

    # floor + remainder distribution
    floored = {e: int(allocated[e]) for e in emps}
    remain = total - sum(floored.values())
    order = sorted(emps, key=lambda e: -(allocated[e] - floored[e]))
    for i in range(max(0, remain)):
        floored[order[i % len(order)]] += 1
    return floored


def _proportional(
    df_emp_targets,
    df_sku,
    df_hist,
    strategy,
    force_min_one=False,
    locked_map=None,
    cap_multiplier=None,
    even_skus: frozenset | None = None,
):
    locked_map = locked_map or {}
    even_skus = even_skus or frozenset()
    employees = df_emp_targets["emp_id"].tolist()
    target_boxes = dict(zip(df_sku["sku"], df_sku["supervisor_target_boxes"]))

    results = []

    for sku, total_orig in target_boxes.items():
        total_orig = max(0, int(round(float(total_orig))))

        # แยกคนที่โดน Lock ออกก่อน
        locked_emps = {e: boxes for (e, s), boxes in locked_map.items() if s == sku}
        locked_sum = sum(locked_emps.values())

        for e, b in locked_emps.items():
            if b > 0:
                results.append({"emp_id": e, "sku": sku, "allocated_boxes": b})

        total = max(0, total_orig - locked_sum)
        active_employees = [e for e in employees if e not in locked_emps]

        if total <= 0 or not active_employees:
            continue

        # force_min_one: กระจายอย่างน้อย 1 หีบ/คน เฉพาะเมื่อเป้าหีบ >= จำนวนพนักงาน
        base_box = 0
        if force_min_one and total >= len(active_employees):
            base_box = 1
            total -= len(active_employees)

        # ── คำนวณ hist weight ──
        sku_key = _norm_sku(sku)
        hist_by_emp = {}
        for emp in active_employees:
            if df_hist.empty:
                val = 0.0
            else:
                m_emp = df_hist["emp_id"].astype(str).str.strip() == str(emp).strip()
                m_sku = df_hist["sku"].map(_norm_sku) == sku_key
                val = df_hist.loc[m_emp & m_sku, "hist_boxes"].sum()
            hist_by_emp[emp] = max(float(val), 0.0)

        hist_sum = sum(hist_by_emp.values())

        # สินค้าใหม่: แบ่งเท่าโดยไม่ผ่าน cap (กันหีบเบี้ยวในโหมดหลัก/รอง)
        if sku_key in even_skus:
            parts = _distribute_even_integers(total, len(active_employees))
            floored = {e: parts[i] for i, e in enumerate(active_employees)}
        else:
            if strategy == "EVEN" or hist_sum == 0:
                weights = {e: 1.0 for e in active_employees}
            elif strategy in ("L3M", "L6M", "LY"):
                weights = {e: max(hist_by_emp[e], 0.01) for e in active_employees}
            elif strategy == "PUSH":
                max_h = max(hist_by_emp.values()) if hist_by_emp else 1.0
                weights = {e: max(max_h - hist_by_emp[e] + 0.1, 0.1) for e in active_employees}
            else:
                weights = {e: 1.0 for e in active_employees}

            total_w = sum(weights.values())
            if total_w > 0 and total > 0:
                raw = {e: total * weights[e] / total_w for e in active_employees}
                floored = _cap_and_redistribute(raw, total, cap_multiplier=cap_multiplier)
            else:
                floored = {e: 0 for e in active_employees}

        for emp in active_employees:
            boxes = floored[emp] + base_box
            if boxes > 0:
                results.append({"emp_id": emp, "sku": sku, "allocated_boxes": boxes})

    return pd.DataFrame(results) if results else pd.DataFrame(columns=["emp_id", "sku", "allocated_boxes"])

def _greedy_revenue_balancer(
    df_out: pd.DataFrame,
    df_emp_targets: pd.DataFrame,
    df_sku: pd.DataFrame,
    locked_map=None,
    force_min_one: bool = False,
    skip_balance_skus: frozenset | set | None = None,
    tolerance_baht: float = 1000.0,
    max_iters: int = 50000,
    *,
    base_map: dict[tuple[str, str], int] | None = None,
    tiered_allocation: bool = False,
    flex_skus: frozenset[str] | None = None,
    flex_band_pct: float = _TIER_FLEX_BAND_PCT,
    strict_band_pct: float = _TIER_STRICT_BAND_PCT,
    default_band_pct: float = _DEFAULT_HIST_BAND_PCT,
    even_skus: frozenset | None = None,
) -> pd.DataFrame:
    if df_out.empty:
        return df_out
    locked_map = locked_map or {}
    skip_balance_skus = skip_balance_skus or set()
    even_skus = even_skus or frozenset()
    target_rev = dict(zip(df_emp_targets["emp_id"], df_emp_targets["yellow_target"]))
    sku_prices = dict(zip(df_sku["sku"], df_sku["price_per_box"]))
    target_boxes = dict(zip(df_sku["sku"], df_sku["supervisor_target_boxes"]))
    emps = df_emp_targets["emp_id"].tolist()
    n_emps = len(emps)

    def _cell_bounds(emp: str, sku: str) -> tuple[int, int] | None:
        if not base_map:
            return None
        if (emp, sku) in locked_map:
            return None
        sku_key = _norm_sku(sku)
        if sku_key in even_skus:
            return None
        base = int(base_map.get((str(emp).strip(), sku_key), 0))
        if base <= 0:
            return None
        min_box = _min_floor_boxes(sku)
        cell_band = _tier_cell_band_pct(
            sku_key,
            tiered_allocation=tiered_allocation,
            flex_skus=flex_skus,
            default_band_pct=default_band_pct,
            flex_band_pct=flex_band_pct,
            strict_band_pct=strict_band_pct,
        )
        return _hist_band_int_bounds(base, cell_band, min_box)

    def _can_move_box(from_emp: str, to_emp: str, sku: str) -> bool:
        bounds_from = _cell_bounds(from_emp, sku)
        bounds_to = _cell_bounds(to_emp, sku)
        if bounds_from is not None and alloc[from_emp][sku] <= bounds_from[0]:
            return False
        if bounds_to is not None and alloc[to_emp][sku] >= bounds_to[1]:
            return False
        return True

    # ทำให้ "เป้าเงิน" อยู่ในสเกลที่เป็นไปได้จริง:
    # รายได้รวมที่จัดสรรได้ ถูกล็อคด้วยจำนวนหีบต่อ SKU (target_boxes × price)
    # ถ้า sum(yellow_target) ไม่เท่ากับรายได้รวมที่เป็นไปได้ จะไม่มีทางปรับให้ตรงเป๊ะได้
    # จึง normalize เป้าเงินต่อคนตามสัดส่วนเดิม ให้ sum(target_rev_scaled) == total_possible_rev
    try:
        total_possible_rev = float(
            sum(float(sku_prices.get(s, 0) or 0) * float(target_boxes.get(s, 0) or 0) for s in sku_prices)
        )
    except Exception:
        total_possible_rev = 0.0
    total_target_rev = float(sum(float(target_rev.get(e, 0) or 0) for e in emps))
    if total_possible_rev > 0 and total_target_rev > 0:
        scale = total_possible_rev / total_target_rev
        target_rev = {e: float(target_rev.get(e, 0) or 0) * scale for e in emps}

    def _min_floor_boxes(sku: str) -> int:
        """สอดคล้อง _proportional / LP: อย่างน้อย 1 หีบ/คนเมื่อเป้าหีบ SKU นั้น >= จำนวนพนักงาน"""
        if not force_min_one or n_emps <= 0:
            return 0
        try:
            t = int(round(float(target_boxes.get(sku, 0) or 0)))
        except (TypeError, ValueError):
            t = 0
        return 1 if t >= n_emps else 0
    
    alloc = {}
    for emp in emps: alloc[emp] = {s: 0 for s in sku_prices.keys()}
    for _, r in df_out.iterrows():
        if r["emp_id"] in alloc and r["sku"] in sku_prices: 
            alloc[r["emp_id"]][r["sku"]] = r["allocated_boxes"]

    def get_current_rev(emp): return sum(alloc[emp][s] * sku_prices[s] for s in sku_prices)

    prev_total_error = float("inf")
    stall_count = 0
    for _ in range(int(max_iters)):
        diffs = {e: get_current_rev(e) - target_rev.get(e, 0) for e in emps}
        # เป้าหมาย: ให้ทุกคนคลาดไม่เกิน tolerance
        max_abs = max((abs(v) for v in diffs.values()), default=0.0)
        if max_abs <= float(tolerance_baht or 0):
            break

        over = [e for e in emps if diffs[e] > 0]
        under = [e for e in emps if diffs[e] < 0]
        if not over or not under:
            break
        rich_emp = max(over, key=lambda e: diffs[e])
        poor_emp = min(under, key=lambda e: diffs[e])

        # หยุดเมื่อไม่มีใคร over และไม่มีใคร under พร้อมกัน
        # (หลัง normalize แล้วโดยทั่วไปควรมีทั้ง over/under แต่กันเคสขอบ)
        if diffs[rich_emp] <= 0 or diffs[poor_emp] >= 0:
            break

        total_error = abs(diffs[rich_emp]) + abs(diffs[poor_emp])
        # กันติด: ถ้าไม่ดีขึ้นต่อเนื่องให้หยุด แต่ให้โอกาสมากขึ้น
        if total_error >= prev_total_error - 1e-6:
            stall_count += 1
            if stall_count >= 20:
                break
        else:
            stall_count = 0
        prev_total_error = total_error
            
        best_sku_to_move = None
        best_improvement = 0
        
        for sku, price in sku_prices.items():
            if _norm_sku(sku) in skip_balance_skus:
                continue
            # 🔴 ข้ามการสลับหีบที่คนพิมพ์แก้ไขไว้แล้ว (ห้ามยุ่งเด็ดขาด)
            if (rich_emp, sku) in locked_map or (poor_emp, sku) in locked_map:
                continue

            floor = _min_floor_boxes(sku)
            # ห้ามดึงหีบจนเหลือต่ำกว่า floor (กัน force_min_one ถูกทำลายหลัง _proportional)
            if alloc[rich_emp][sku] <= floor:
                continue
            if not _can_move_box(rich_emp, poor_emp, sku):
                continue
            current_error = abs(diffs[rich_emp]) + abs(diffs[poor_emp])
            new_rich_diff = diffs[rich_emp] - price
            new_poor_diff = diffs[poor_emp] + price
            new_error = abs(new_rich_diff) + abs(new_poor_diff)

            improvement = current_error - new_error
            if improvement > best_improvement:
                best_improvement = improvement
                best_sku_to_move = sku
                    
        if best_sku_to_move is None:
            break

        fl = _min_floor_boxes(best_sku_to_move)
        alloc[rich_emp][best_sku_to_move] = max(
            fl, alloc[rich_emp][best_sku_to_move] - 1
        )
        alloc[poor_emp][best_sku_to_move] += 1

    final_results = [{"emp_id": emp, "sku": sku, "allocated_boxes": boxes} for emp in emps for sku, boxes in alloc[emp].items() if boxes > 0]
    return pd.DataFrame(final_results)

def _lp_optimize(
    df_emp_targets,
    df_sku,
    df_hist,
    force_min_one=False,
    locked_map=None,
    even_skus: frozenset | None = None,
    *,
    baseline_strategy: str = "L3M",
    hist_balance: float = 0.85,
    revenue_tolerance_baht: float = _DEFAULT_REVENUE_TOLERANCE_BAHT,
    cap_multiplier=None,
    base_map: dict[tuple[str, str], int] | None = None,
    hist_band_pct: float = _DEFAULT_HIST_BAND_PCT,
    tiered_allocation: bool = False,
    flex_skus: frozenset[str] | None = None,
    flex_band_pct: float = _TIER_FLEX_BAND_PCT,
    strict_band_pct: float = _TIER_STRICT_BAND_PCT,
):
    locked_map = locked_map or {}
    even_skus = even_skus or frozenset()
    baseline_strategy = (baseline_strategy or "L3M").upper()
    if baseline_strategy not in ("L3M", "L6M", "LY", "EVEN", "PUSH"):
        baseline_strategy = "L3M"

    def _fallback_prop():
        return _proportional(
            df_emp_targets,
            df_sku,
            df_hist,
            baseline_strategy,
            force_min_one,
            locked_map,
            cap_multiplier,
            even_skus=even_skus,
        )

    try:
        import pulp
    except ImportError:
        logger.warning("pulp not installed → fallback proportional %s", baseline_strategy)
        return _fallback_prop()

    employees = df_emp_targets["emp_id"].tolist()
    skus = df_sku["sku"].tolist()
    target_rev = dict(zip(df_emp_targets["emp_id"], df_emp_targets["yellow_target"]))
    target_boxes = dict(zip(df_sku["sku"], df_sku["supervisor_target_boxes"]))
    sku_prices = dict(zip(df_sku["sku"], df_sku["price_per_box"]))

    try:
        total_possible_rev = float(
            sum(
                float(sku_prices.get(s, 0) or 0) * float(target_boxes.get(s, 0) or 0)
                for s in sku_prices
            )
        )
    except Exception:
        total_possible_rev = 0.0
    total_target_rev = float(sum(float(target_rev.get(e, 0) or 0) for e in employees))
    if total_possible_rev > 0 and total_target_rev > 0:
        scale = total_possible_rev / total_target_rev
        target_rev = {e: float(target_rev.get(e, 0) or 0) * scale for e in employees}

    shortfall_w, excess_w, lp_anchor = _lp_weights_from_balance(hist_balance)
    tol = max(0.0, float(revenue_tolerance_baht or 0))
    band_pct = max(0.0, min(1.0, float(hist_band_pct if hist_band_pct is not None else _DEFAULT_HIST_BAND_PCT)))

    if base_map is None:
        df_base = _proportional(
            df_emp_targets,
            df_sku,
            df_hist,
            baseline_strategy,
            force_min_one,
            locked_map,
            cap_multiplier,
            even_skus=even_skus,
        )
        base_map = _baseline_map_from_df(df_base, df_emp_targets, df_sku)
    else:
        df_base = None

    logger.info(
        "LP optimize: baseline=%s hist_balance=%.2f anchor=%.2f rev_tol=%.0f sf_w=%.2f band=%.0f%% tiered=%s flex_skus=%s",
        baseline_strategy,
        hist_balance,
        lp_anchor,
        tol,
        shortfall_w,
        band_pct * 100,
        tiered_allocation,
        len(flex_skus) if flex_skus else 0,
    )

    strict_attempts: list[float] = [strict_band_pct]
    if tiered_allocation and strict_band_pct < band_pct - 1e-9:
        strict_attempts.append(band_pct)

    time_limit = min(60, max(15, (len(employees) * len(skus)) // 8))
    last_status = "Not Solved"
    x: dict = {}

    for attempt_idx, attempt_strict in enumerate(strict_attempts):
        if attempt_idx > 0:
            logger.warning(
                "tiered LP infeasible with strict ±%.0f%% → retry with ±%.0f%% on SKU รอง",
                strict_attempts[0] * 100,
                attempt_strict * 100,
            )

        prob = pulp.LpProblem("BoxAllocation_LP", pulp.LpMinimize)
        x = {}
        dpos = {}
        dneg = {}
        for emp in employees:
            for sku in skus:
                sku_key = _norm_sku(sku)
                if (emp, sku) in locked_map:
                    val = locked_map[(emp, sku)]
                    x[(emp, sku)] = pulp.LpVariable(
                        f"x_{emp}_{sku}", lowBound=val, upBound=val, cat="Integer"
                    )
                    continue
                min_box = 1 if force_min_one and int(target_boxes[sku]) >= len(employees) else 0
                even_base = None
                if sku_key in even_skus and base_map:
                    even_base = int(base_map.get((str(emp).strip(), sku_key), 0))
                if even_base is not None:
                    # SKU ใหม่แบ่งเท่า — ล็อกตาม baseline เกลี่ยเท่า ไม่ให้ LP/ทดลอง 80/20 ดึงไปปรับเงิน
                    x[(emp, sku)] = pulp.LpVariable(
                        f"x_{emp}_{sku}", lowBound=even_base, upBound=even_base, cat="Integer"
                    )
                    continue
                x[(emp, sku)] = pulp.LpVariable(
                    f"x_{emp}_{sku}", lowBound=min_box, cat="Integer"
                )
                dpos[(emp, sku)] = pulp.LpVariable(f"dp_{emp}_{sku}", lowBound=0, cat="Continuous")
                dneg[(emp, sku)] = pulp.LpVariable(f"dn_{emp}_{sku}", lowBound=0, cat="Continuous")

        if band_pct > 0 and base_map:
            for emp in employees:
                for sku in skus:
                    if (emp, sku) in locked_map:
                        continue
                    sku_key = _norm_sku(sku)
                    if sku_key in even_skus:
                        continue
                    base = int(base_map.get((str(emp).strip(), sku_key), 0))
                    if base <= 0:
                        continue
                    min_box = 1 if force_min_one and int(target_boxes[sku]) >= len(employees) else 0
                    cell_band = _tier_cell_band_pct(
                        sku_key,
                        tiered_allocation=tiered_allocation,
                        flex_skus=flex_skus,
                        default_band_pct=band_pct,
                        flex_band_pct=flex_band_pct,
                        strict_band_pct=attempt_strict,
                    )
                    lo, hi = _hist_band_int_bounds(base, cell_band, min_box)
                    prob += x[(emp, sku)] >= lo
                    prob += x[(emp, sku)] <= hi

        shortfall = pulp.LpVariable.dicts("sf", employees, lowBound=0, cat="Continuous")
        excess = pulp.LpVariable.dicts("ex", employees, lowBound=0, cat="Continuous")
        sf_pen = pulp.LpVariable.dicts("sfp", employees, lowBound=0, cat="Continuous")
        ex_pen = pulp.LpVariable.dicts("exp", employees, lowBound=0, cat="Continuous")

        anchor_term = 0
        if lp_anchor > 0 and dpos:
            anchor_term = pulp.lpSum(
                (dpos[(e, s)] + dneg[(e, s)])
                * float(sku_prices.get(s, 0) or 0)
                * lp_anchor
                * _tier_cell_anchor_mult(
                    _norm_sku(s),
                    tiered_allocation=tiered_allocation,
                    flex_skus=flex_skus,
                )
                for (e, s) in dpos.keys()
            )
        prob += (
            pulp.lpSum(shortfall_w * sf_pen[e] + excess_w * ex_pen[e] for e in employees)
            + anchor_term
        )

        for sku in skus:
            prob += pulp.lpSum(x[(e, sku)] for e in employees) == int(target_boxes[sku])

        for emp in employees:
            prob += (
                pulp.lpSum(x[(emp, s)] * sku_prices[s] for s in skus)
                + shortfall[emp]
                - excess[emp]
                == target_rev[emp]
            )
            prob += sf_pen[emp] >= shortfall[emp] - tol
            prob += ex_pen[emp] >= excess[emp] - tol

        if lp_anchor > 0 and dpos:
            for emp, sku in dpos.keys():
                base = int(base_map.get((str(emp).strip(), _norm_sku(sku)), 0))
                prob += x[(emp, sku)] - base == dpos[(emp, sku)] - dneg[(emp, sku)]

        try:
            prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit))
        except Exception as e:
            logger.warning("LP solver error: %s → fallback proportional %s", e, baseline_strategy)
            return _fallback_prop()

        last_status = pulp.LpStatus[prob.status]
        if last_status == "Optimal":
            break

    if last_status != "Optimal":
        logger.warning(
            "LP status=%s (hist band ±%.0f%%) → fallback proportional %s",
            last_status,
            band_pct * 100,
            baseline_strategy,
        )
        return _fallback_prop()

    results = [
        {
            "emp_id": emp,
            "sku": sku,
            "allocated_boxes": int(round(x[(emp, sku)].varValue)),
        }
        for emp in employees
        for sku in skus
        if x[(emp, sku)].varValue is not None and x[(emp, sku)].varValue > 0.5
    ]
    return pd.DataFrame(results) if results else _fallback_prop()