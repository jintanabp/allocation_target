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


class LockedEditsExceedTarget(ValueError):
    """หีบที่ล็อกไว้รวมกันเกินเป้าของ SKU — ต้องให้ผู้ใช้แก้ ไม่ใช่กลบส่วนเกินทิ้ง"""

    def __init__(self, message: str, skus: list[str] | None = None):
        super().__init__(message)
        self.skus = skus or []

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
# ยอมรับคำตอบ LP ที่ห่างจากดีที่สุดที่พิสูจน์ได้ไม่เกิน 0.1% (ผู้ใช้อนุมัติ 30 ก.ย. 2026)
# เดิมไม่ตั้ง CBC ไล่พิสูจน์จนชนเพดานเวลา (60 วิ) เกือบทุกทีม แล้วคืนคำตอบที่เจอ ณ ตอนนั้น
# ผลจึงต่างกันทุกครั้งที่กดตามภาระเครื่อง · วัดจาก export 10/2026: SL418 เหลือ ~7 วิ
# ได้ผลเท่าให้เวลา 300 วิ (ห่างเป้าเงินรวม 2,881 บาท คนห่างสุด 660)
_LP_GAP_REL = 0.001


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
    # ต้อง guard คอลัมน์เหมือน allocation_checks.skus_zero_team_hist_window
    # ไม่งั้น cache ประวัติที่ไม่มีคอลัมน์ครบทำให้ KeyError กลางการกระจาย
    if "sku" not in df_hist.columns or "hist_boxes" not in df_hist.columns:
        return frozenset(sku_list)
    df = df_hist.copy()
    df["sku"] = df["sku"].map(_norm_sku)
    g = df.groupby("sku")["hist_boxes"].sum()
    return frozenset(s for s in sku_list if float(g.get(s, 0) or 0) <= 0)


def _normalize_engine_inputs(
    df_emp_targets: pd.DataFrame,
    df_sku: pd.DataFrame,
    df_hist: pd.DataFrame,
    locked_edits: list | None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list]:
    """
    ทำให้ทุกฝั่งใช้คีย์รูปเดียวกันก่อนเข้าเครื่องคำนวณ (แก้ F6 + F8)

    ก่อนหน้านี้ locked_map ใช้ string ดิบจาก request (`s == sku`) แต่ even_skus /
    base_map / flex_skus ใช้ _norm_sku() ต่างกันแค่ช่องว่างหน้า-หลังก็ทำให้
    "ล็อกถูกเมินเงียบ ๆ" การแก้มือของผู้ใช้จึงหายไปโดยไม่มีสัญญาณอะไรเลย

    supervisor_target_boxes ปัดเป็น int ที่นี่ครั้งเดียว เพราะ LP ใช้ int() (ตัดทศนิยม)
    แต่ _proportional และตัวตรวจใช้ int(round()) — เป้า 10.7 จึงกลายเป็น 10 กับ 11
    คนละที่ แล้วตัวตรวจก็ฟ้อง mismatch ที่ไม่ได้เกิดจากการกระจายจริง
    """
    emp = df_emp_targets.copy()
    emp["emp_id"] = emp["emp_id"].astype(str).str.strip()

    sku = df_sku.copy()
    sku["sku"] = sku["sku"].astype(str).str.strip()
    if "supervisor_target_boxes" in sku.columns:
        sku["supervisor_target_boxes"] = (
            pd.to_numeric(sku["supervisor_target_boxes"], errors="coerce")
            .fillna(0)
            .round()
            .astype(int)
        )
    # ราคาต้องเป็นตัวเลขเสมอ (ออดิต 26 ส.ค. 2026) — เดิมคอลัมน์หาย = KeyError, เป็นข้อความ = TypeError ตอนคูณ
    # ทั้งสองแบบได้ 500 กลางการกระจาย · ราคาที่อ่านไม่ได้ถือเป็น 0 เหมือนสินค้าที่ราคาหาย (กลุ่มมูลค่า 0)
    if "price_per_box" not in sku.columns:
        logger.warning("ข้อมูลสินค้าไม่มีคอลัมน์ price_per_box — ถือราคาเป็น 0 ทุกตัว")
        sku["price_per_box"] = 0.0
    else:
        _raw_price = sku["price_per_box"]
        sku["price_per_box"] = pd.to_numeric(_raw_price, errors="coerce")
        _bad = sku["price_per_box"].isna() & _raw_price.notna()
        if _bad.any():
            logger.warning("ราคาอ่านไม่ได้ %d SKU — ถือเป็น 0: %s", int(_bad.sum()),
                           sorted(sku.loc[_bad, "sku"].tolist())[:10])
        sku["price_per_box"] = sku["price_per_box"].fillna(0.0).astype(float)

    # SKU รหัสเดียวต้องมีแถวเดียว (I6)
    # dict(zip(...)) ที่ใช้ทั่วไฟล์เก็บแค่แถวสุดท้ายอยู่แล้ว จึงยุบแบบ keep="last"
    # ให้ตรงกับพฤติกรรมเดิม — แต่ที่สำคัญคือโหมดหลายกลยุทธ์ที่แบ่งกลุ่มตามแบรนด์
    # SKU เดียวกันจะไปอยู่สองกลุ่มแล้วถูกกระจายซ้ำสองรอบ
    if sku["sku"].duplicated().any():
        dup_ids = sorted(set(sku.loc[sku["sku"].duplicated(), "sku"]))
        conflicting = []
        for s in dup_ids:
            rows = sku[sku["sku"] == s]
            for col in ("supervisor_target_boxes", "price_per_box"):
                if col in rows.columns and rows[col].nunique(dropna=False) > 1:
                    conflicting.append(f"{s}:{col}")
        logger.warning(
            "พบ SKU ซ้ำ %d รหัส — ยุบเหลือแถวเดียว (keep=last)%s",
            len(dup_ids),
            f" | ค่าไม่ตรงกัน: {conflicting}" if conflicting else "",
        )
        sku = sku.drop_duplicates(subset=["sku"], keep="last").reset_index(drop=True)

    hist = df_hist
    if hist is not None and not hist.empty:
        hist = hist.copy()
        for col in ("emp_id", "sku"):
            if col in hist.columns:
                hist[col] = hist[col].astype(str).str.strip()

    locks: list[dict] = []
    for le in locked_edits or []:
        try:
            locks.append(
                {
                    "emp_id": str(le["emp_id"]).strip(),
                    "sku": _norm_sku(le["sku"]),
                    "locked_boxes": int(le["locked_boxes"]),
                }
            )
        except (KeyError, TypeError, ValueError):
            logger.warning("locked_edit รูปแบบไม่ถูกต้อง — ข้าม: %r", le)
    # ช่องเดียวกันซ้ำ = ล็อกเดียว ใช้ค่าสุดท้าย (ตรงกับ locked_map) — ไม่งั้นด่าน I2 บวกซ้ำแล้วฟ้องเกินเป้า
    _by_key = {(lk["emp_id"], lk["sku"]): lk for lk in locks}
    if len(_by_key) < len(locks):
        logger.warning("พบล็อกซ้ำ %d รายการ — ใช้ค่าสุดท้ายของแต่ละช่อง", len(locks) - len(_by_key))
        locks = list(_by_key.values())

    # ล็อกรวมต้องไม่เกินเป้าของ SKU นั้น (I2)
    # ของเดิมใช้ max(0, total - locked_sum) กลบส่วนเกินทิ้ง แต่ยังใส่เซลล์ที่ล็อกเต็มจำนวน
    # ผลรวมจึงเกินเป้าเงียบ ๆ และฝั่ง LP ก็กลายเป็นโจทย์ที่แก้ไม่ได้ (lowBound=upBound
    # ชนกับสมการผลรวม) แล้วตกไปใช้ proportional โดยผู้ใช้ไม่รู้
    if locks and "supervisor_target_boxes" in sku.columns:
        target_by_sku = dict(zip(sku["sku"], sku["supervisor_target_boxes"]))
        locked_sum_by_sku: dict[str, int] = {}
        for lk in locks:
            locked_sum_by_sku[lk["sku"]] = locked_sum_by_sku.get(lk["sku"], 0) + lk["locked_boxes"]
        over = {
            s: (tot, int(target_by_sku[s]))
            for s, tot in locked_sum_by_sku.items()
            if s in target_by_sku and tot > int(target_by_sku[s])
        }
        if over:
            detail = " | ".join(
                f"SKU {s}: ล็อกไว้รวม {tot} หีบ แต่เป้าหีบมีแค่ {tgt} หีบ"
                for s, (tot, tgt) in sorted(over.items())
            )
            raise LockedEditsExceedTarget(detail, skus=sorted(over))

    return emp, sku, hist, locks


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
    df_sold_12m: pd.DataFrame | None = None,
    push_multiple: float = 5.0,
    history_only: bool = False,
    never_sold_known_emps: set | frozenset | None = None,
    rotate_small_in_brand: bool = True,
    wh_blocked_pairs: set | frozenset | None = None,
) -> pd.DataFrame:
    strategy = strategy.upper()
    valid = ("L3M", "L6M", "LY", "EVEN", "PUSH", "LP")
    if strategy not in valid:
        strategy = "L3M"

    # ทำคีย์ให้อยู่รูปเดียวกันทั้งหมดตั้งแต่ประตูทางเข้า — ทุกอย่างหลังจากนี้
    # จึงเทียบ emp_id/sku ได้ตรง ๆ โดยไม่ต้องเดาว่าฝั่งไหน normalize มาแล้วบ้าง
    df_emp_targets, df_sku, df_hist, locked_edits = _normalize_engine_inputs(
        df_emp_targets, df_sku, df_hist, locked_edits
    )

    locked_map = {}
    for le in locked_edits:
        locked_map[(le["emp_id"], le["sku"])] = le["locked_boxes"]

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
        "allocate_boxes: strategy=%s emp=%d sku=%d force_min_one=%s locked=%d even_new_products=%s even_skus=%d (cy_ly=%d zero_hist=%d) history_only=%s",
        strategy,
        len(df_emp_targets),
        len(df_sku),
        force_min_one,
        len(locked_map),
        even_new_products,
        len(even_skus),
        len(cy_ly_skus),
        len(zero_hist_skus),
        history_only,
    )

    # ── กติกา "หน่วยไม่เคยขายสินค้านั้น = เป้า 0" ─────────────────────────
    #
    # ต้องคิดก่อน _proportional เพราะ baseline ที่ใช้เป็นรั้ว ±% ของ LP มาจากรอบนั้น
    # ถ้าเอากติกาไปใส่เฉพาะ LP รั้วจะอ้างอิง baseline คนละชุดกับคำตอบที่ต้องการ
    # แล้วโจทย์กลายเป็นแก้ไม่ได้ ตกกลับไป fallback ทั้งทีม
    never_sold_zero_pairs, never_sold_even, never_sold_summary = _never_sold_plan(
        df_sold_12m, df_emp_targets, df_sku, locked_map, float(push_multiple or 5.0),
        known_emps=never_sold_known_emps,
    )
    if never_sold_even:
        # SKU ที่ทีมไม่เคยขาย/ถูกดันเป้า ใช้กลไก "แบ่งเท่า" ตัวเดียวกับสินค้าใหม่
        # ซึ่งบังคับได้ครบทั้ง LP · proportional · ตัวเกลี่ยเงิน อยู่แล้ว
        even_skus = frozenset(even_skus) | never_sold_even
    if never_sold_summary:
        logger.info(
            "กติกาไม่เคยขาย=เป้า 0: ตัด %d คู่ · เฉลี่ยแทน %d SKU (ไม่มีคนเคยขาย %d · ดันเป้า %d)",
            len(never_sold_zero_pairs),
            len(never_sold_even),
            sum(1 for v in never_sold_summary.values() if v["reason"] == "no_seller"),
            sum(1 for v in never_sold_summary.values() if v["reason"] == "push_target"),
        )

    # คู่ที่ห้ามรับหีบ = กติกาไม่เคยขาย + คู่ "คน×คลัง × สินค้า" ที่ Target Sun ไม่มีแถวที่คลังนั้น
    # (wh_blocked_pairs — ผู้ใช้ตัดสิน 8 ต.ค. 2026: C442 มีสินค้า X แค่ที่ R493 เป้า 0 → แถว R408 ต้องไม่ได้ X)
    # รวมกันใช้ทุกชั้น แต่เก็บแยกใน attrs — หน้าจอติดป้าย "ไม่เคยขาย" จาก never_sold_zero_pairs เท่านั้น
    alloc_zero_pairs = never_sold_zero_pairs
    if wh_blocked_pairs:
        alloc_zero_pairs = set(never_sold_zero_pairs or ()) | {
            (str(e).strip(), _norm_sku(s)) for e, s in wh_blocked_pairs
        }

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
    opt_meta: dict[str, bool] = {"optimization_fallback": False, "lp_time_limited": False}
    # history_only=True: ข้ามชั้นเงินทั้งหมด (LP + greedy revenue balancer ด้านล่าง —
    # ตัวนั้นถูก gate ด้วย "if ... base_map" อยู่แล้ว จึงข้ามอัตโนมัติเมื่อ base_map
    # ไม่ถูกตั้งตรงนี้) กระจายด้วย _proportional ตรง ๆ ตามสัดส่วนประวัติล้วน ๆ
    # ไม่แตะ yellow_target เลยไม่ว่าค่าที่ส่งมาจะเป็นอะไร — ใช้เมื่อไม่มีการตั้งเป้าเงิน
    if strategy in _LP_STRATEGIES and not history_only:
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
            zero_pairs=alloc_zero_pairs,
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
            zero_pairs=alloc_zero_pairs,
            _meta=opt_meta,
        )
    else:
        # strategy == LP/L3M/L6M/LY ตอน history_only=True ไม่มีความหมายเป็น "วิธีแก้ LP"
        # อีกต่อไป — ใช้เป็นแค่ชื่อหน้าต่างประวัติ ตกไป L3M เหมือน baseline ของ LP ปกติ
        prop_strategy = strategy if strategy in ("L3M", "L6M", "LY", "EVEN", "PUSH") else "L3M"
        df_out = _proportional(
            df_emp_targets,
            df_sku,
            df_hist,
            prop_strategy,
            force_min_one,
            locked_map,
            effective_cap,
            even_skus=even_skus,
            zero_pairs=alloc_zero_pairs,
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
            cap_multiplier=effective_cap,
            zero_pairs=alloc_zero_pairs,
        )

    if even_skus:
        df_out = _enforce_even_skus_on_df(
            df_out,
            even_skus,
            df_emp_targets,
            df_sku,
            locked_map,
            force_min_one,
            df_hist=df_hist,
            zero_pairs=alloc_zero_pairs,
        )
        # ห้ามเอาผลสุดท้ายไปทับ base_map (ผลตรวจ 28 ก.ย. 2026 §4.1-1) — เดิมทับตรงนี้
        # ป้ายเทียบประวัติจึงเทียบผลกับตัวเอง ทุกช่องได้ "ok" ทุกครั้งที่มี SKU แบ่งเท่า
        # (งวด 09/2026 เกิดครบทั้ง 55 ทีม) · SKU แบ่งเท่าไม่ติดป้ายอยู่แล้วใน _annotate_hist_deviation

    # หมุนผู้รับ SKU เป้าน้อยกว่าจำนวนคนภายในแบรนด์ — ทำหลังสุด (หลัง LP/ตัวเกลี่ยเงิน/แบ่งเท่า)
    # เพราะทุกชั้นก่อนหน้าตัดสิน "ใครได้" ทีละ SKU แยกกัน จึงเลือกคนเดิมซ้ำทุก SKU
    rotation_stats = {"brands": 0, "skus": 0, "moved_boxes": 0}
    if rotate_small_in_brand:
        df_out, rotation_stats = _rotate_small_skus_in_brand(
            df_out,
            df_emp_targets,
            df_sku,
            hist_lookup=_hist_lookup(df_hist),
            locked_map=locked_map,
            zero_pairs=alloc_zero_pairs,
            even_skus=even_skus,
        )

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
            zero_pairs=alloc_zero_pairs,
        )
        base_map = _baseline_map_from_df(df_base, df_emp_targets, df_sku)
    if base_map:
        df_expanded = _annotate_hist_deviation(
            df_expanded,
            base_map,
            band_pct=_DEFAULT_HIST_BAND_PCT,
            even_skus=even_skus,
        )
    df_expanded.attrs["optimization_fallback"] = opt_meta.get("optimization_fallback", False)
    df_expanded.attrs["lp_time_limited"] = opt_meta.get("lp_time_limited", False)
    # ส่งแผนกติกาไม่เคยขายกลับไปด้วย — โหมดหลายกลยุทธ์มีรอบเกลี่ยเงินอีกรอบหลังรวมผล
    # (`_post_merge_revenue_balance`) ถ้าไม่บอกมัน มันจะยกหีบกลับเข้าช่องที่เพิ่งตัดไป
    df_expanded.attrs["never_sold_zero_pairs"] = never_sold_zero_pairs
    df_expanded.attrs["never_sold_summary"] = never_sold_summary
    df_expanded.attrs["brand_rotation"] = rotation_stats
    df_expanded.attrs["wh_blocked_pairs"] = frozenset(
        (str(e).strip(), _norm_sku(s)) for e, s in (wh_blocked_pairs or ())
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


def _never_sold_plan(
    df_sold_12m,
    df_emp_targets,
    df_sku,
    locked_map: dict,
    push_multiple: float,
    known_emps: set | frozenset | None = None,
) -> tuple[set, frozenset, dict]:
    """
    วางแผนกติกา "หน่วยไม่เคยขายสินค้านั้น = เป้า 0" — คืน (คู่ที่ต้องเป็น 0, SKU ที่ให้เฉลี่ย, สรุป)

    กติกาที่ผู้ใช้เคาะไว้ 10 ก.ย. 2026:
      1. คู่ (พนักงาน × สินค้า) ที่ไม่เคยขายกันเลยใน 12 เดือนล่าสุด -> ไม่ได้รับหีบ
      2. ถ้าทั้งทีมไม่มีใครเคยขาย SKU นั้นเลย -> **เฉลี่ยทุกคน** ไม่ใช่กองที่คนเดียว
         (วัดจากงวด 09/2026: เจอเคสนี้ครบทั้ง 55 ทีม จึงไม่ใช่กรณียกเว้นหายาก)
      3. ถ้าเป้าของ SKU ใหญ่เกิน `push_multiple` เท่าของประวัติรวมของคนที่เคยขาย ->
         ถือว่าเป็น "สินค้าดันเป้า" ที่ประวัติใช้ตัดสินไม่ได้ -> เฉลี่ยทุกคนเช่นกัน

    ข้อ 3 มาจากของจริง: SL531 สินค้า 351320 เป้า 2,052 หีบ ทีม 5 คน มีคนเคยขายคนเดียว
    ประวัติ 1 หีบ — ถ้าไม่มีข้อนี้ หีบทั้งก้อนจะตกที่คนเดียว ซึ่งแย่กว่าเดิม

    ตัวหารของข้อ 3 ใช้ **สเกลรายเดือน** ให้เทียบกับเป้ารายเดือนได้: ยอด 12 เดือนหารด้วย 12
    เทียบกับเป้าตรง ๆ ไม่ได้ เพราะเป้าเป็นของเดือนเดียว

    ช่องที่ผู้ใช้ล็อกไว้ไม่ถูกแตะ (กฎ I2) — การล็อกคือเจตนาที่ชัดเจนกว่ากติกาอัตโนมัติ

    `known_emps` — พนักงานที่ "รู้" ประวัติ 12 เดือนจริง (ทีมมีไฟล์ประวัติ) · None = รู้ทุกคน
    **ไม่มีข้อมูล ≠ ไม่เคยขาย** (ผลตรวจ §4.1-4): รอบรวมภาค/หน่วยที่บางทีมไม่มีไฟล์ประวัติ
    คนทีมนั้นเคยถูกนับว่าไม่เคยขายทุกสินค้าแล้วถูกตัดเป็น 0 ทั้งทีม · ตอนนี้คนที่ไม่รู้ไม่ถูกตัด
    และถ้าข้อ 2/3 ต้องอาศัยว่า "ทั้งทีมไม่มีใครขาย" หรือ "ความจุของคนที่เคยขาย" ซึ่งคนที่ไม่รู้
    อาจเปลี่ยนคำตอบได้ → ไม่ใช้กติกากับ SKU นั้นเลย (reason "hist_unknown")
    """
    if df_sold_12m is None or df_sold_12m.empty:
        return set(), frozenset(), {}
    if not {"emp_id", "sku"} <= set(df_sold_12m.columns):
        return set(), frozenset(), {}

    employees = [str(e).strip() for e in df_emp_targets["emp_id"].tolist() if str(e).strip()]
    if not employees:
        return set(), frozenset(), {}
    emp_set = set(employees)
    known = emp_set if known_emps is None else emp_set & {str(e).strip() for e in known_emps}
    unknown = emp_set - known
    if not known:
        return set(), frozenset(), {}
    skus = _skus_with_target_boxes(df_sku)
    target_boxes = dict(zip(df_sku["sku"], df_sku["supervisor_target_boxes"]))

    # เดือนละกี่หีบโดยเฉลี่ยใน 12 เดือน — คู่ที่ไม่อยู่ในตารางนี้คือ "ไม่เคยขาย"
    monthly: dict[tuple[str, str], float] = {}
    boxes_col = "hist_boxes" if "hist_boxes" in df_sold_12m.columns else None
    for _, r in df_sold_12m.iterrows():
        e = str(r.get("emp_id") or "").strip()
        s = _norm_sku(r.get("sku"))
        if not e or not s or e not in emp_set:
            continue
        try:
            tot = float(r.get(boxes_col, 0) or 0) if boxes_col else 1.0
        except (TypeError, ValueError):
            tot = 0.0
        if tot <= 0:
            continue
        monthly[(e, s)] = monthly.get((e, s), 0.0) + tot / 12.0

    zero_pairs: set[tuple[str, str]] = set()
    even_skus: set[str] = set()
    summary: dict[str, dict] = {}

    for sku in skus:
        sku_key = _norm_sku(sku)
        sellers = [e for e in employees if (e, sku_key) in monthly]
        try:
            tgt = max(0, int(round(float(target_boxes.get(sku, 0) or 0))))
        except (TypeError, ValueError):
            tgt = 0
        if tgt <= 0:
            continue

        capacity = sum(monthly[(e, sku_key)] for e in sellers)
        if unknown and (not sellers or (capacity > 0 and tgt > push_multiple * capacity)):
            summary[sku_key] = {
                "reason": "hist_unknown",
                "sellers": len(sellers),
                "team": len(employees),
                "unknown": len(unknown),
            }
            continue
        if not sellers:
            even_skus.add(sku_key)
            summary[sku_key] = {"reason": "no_seller", "sellers": 0, "team": len(employees)}
            continue
        if capacity > 0 and tgt > push_multiple * capacity:
            even_skus.add(sku_key)
            summary[sku_key] = {
                "reason": "push_target",
                "sellers": len(sellers),
                "team": len(employees),
                "target_boxes": tgt,
                "seller_capacity": round(capacity, 1),
                "multiple": round(tgt / capacity, 1),
            }
            continue

        blocked = [
            e for e in employees
            if e in known and (e, sku_key) not in monthly and (e, sku_key) not in locked_map
        ]
        if not blocked:
            continue

        # ทางหลุดที่ 3: คนที่เคยขายถูกล็อกไว้ "หมดทุกคน" แต่ยังเหลือหีบให้แบ่ง — หีบที่
        # เหลือไปได้แค่คนที่ไม่เคยขาย (ห้ามขาดเป้า I1 ชนะ) ถ้ายังตัดพวกเขาเป็น 0 ไว้ LP
        # จะหาคำตอบไม่ได้ "ทั้งทีม" แล้วถอยไปแบ่งตามสัดส่วนทุก SKU (เสียการเกลี่ยเงิน
        # หมดเพราะ SKU เดียว) — ยกเว้นกติกาเฉพาะ SKU นี้ บอกผู้ใช้ผ่าน summary ให้รู้
        free_sellers = [e for e in sellers if (e, sku_key) not in locked_map]
        if not free_sellers:
            locked_sum = 0
            for (le, ls), lv in locked_map.items():
                if _norm_sku(ls) == sku_key and str(le).strip() in emp_set:
                    try:
                        locked_sum += max(0, int(lv))
                    except (TypeError, ValueError):
                        pass
            if tgt - locked_sum > 0:
                summary[sku_key] = {
                    "reason": "sellers_all_locked",
                    "sellers": len(sellers),
                    "team": len(employees),
                    "target_boxes": tgt,
                    "left_for_non_sellers": tgt - locked_sum,
                }
                continue

        zero_pairs.update((e, sku_key) for e in blocked)
        summary[sku_key] = {
            "reason": "zeroed",
            "sellers": len(sellers),
            "team": len(employees),
            "blocked": len(blocked),
            "target_boxes": tgt,
        }

    return zero_pairs, frozenset(even_skus), summary


def _fair_rank(
    employees, hist_by_emp: dict | None = None, tie_by_emp: dict | None = None
) -> dict:
    """
    ลำดับ "ใครควรได้เศษหีบก่อน" — คนที่ขายสินค้านั้นได้มากกว่ามาก่อน
    เท่ากันให้คนที่เป้าเงินสูงกว่า (tie_by_emp) ก่อน แล้วค่อยเรียงตามรหัส

    tie_by_emp (ผู้ใช้ตัดสิน 29 ก.ย. 2026): สินค้าใหม่/สินค้าที่ไม่มีใครเคยขาย ทุกคนเสมอกัน
    ถ้าตัดสินด้วยรหัสอย่างเดียว คนรหัสน้อยได้เศษทุกงวด — ให้คนเป้าเงินสูงกว่าได้ก่อน

    เดิมเศษหีบตกที่คนแถวบนสุดของตารางเสมอ เพราะ `sorted()` ของ Python เป็น stable
    พอน้ำหนักเท่ากัน (ไม่มีใครมีประวัติ SKU นั้น หรือกลยุทธ์ EVEN) ลำดับที่เหลือจึงเป็น
    ลำดับในทะเบียนพนักงาน — คนเดิมได้เศษทุกงวด คนท้ายตารางไม่เคยได้เลย
    SL384 รายงานเรื่องนี้ และผลตรวจรอบ 0 นับได้ 3,820 คู่ (19.5% ของคู่สินค้า×ทีม)
    """
    hist_by_emp = hist_by_emp or {}
    tie_by_emp = tie_by_emp or {}
    ordered = sorted(
        employees,
        key=lambda e: (
            -float(hist_by_emp.get(e, 0.0) or 0.0),
            -float(tie_by_emp.get(e, 0.0) or 0.0),
            str(e),
        ),
    )
    return {e: i for i, e in enumerate(ordered)}


def _hist_lookup(df_hist) -> dict[tuple[str, str], float]:
    """
    ประวัติหีบต่อ (emp, sku) เป็น dict ครั้งเดียว แทนการ scan ทั้งตารางต่อทุกคู่
    ของเดิมสร้าง boolean mask 2 ชุด + .map(_norm_sku) ใหม่ทุกรอบในลูปซ้อน
    ทีมจริง 638 SKU x 6 คน x 2,311 แถว = ~8.8 ล้าน row-ops ต่อการเรียกหนึ่งครั้ง
    """
    if df_hist is None or df_hist.empty or not {"emp_id", "sku", "hist_boxes"} <= set(df_hist.columns):
        return {}
    g = (
        df_hist.assign(
            _e=df_hist["emp_id"].astype(str).str.strip(),
            _s=df_hist["sku"].map(_norm_sku),
            _b=pd.to_numeric(df_hist["hist_boxes"], errors="coerce").fillna(0.0),
        )
        .groupby(["_e", "_s"], sort=False)["_b"]
        .sum()
    )
    return {(e, s): float(v) for (e, s), v in g.items()}


def _yellow_by_emp(df_emp_targets) -> dict:
    """เป้าเงินรายคน — ใช้ตัดสินเศษหีบเมื่อประวัติเสมอกัน (_fair_rank)"""
    if df_emp_targets is None or df_emp_targets.empty or "yellow_target" not in df_emp_targets.columns:
        return {}
    vals = pd.to_numeric(df_emp_targets["yellow_target"], errors="coerce").fillna(0.0)
    return {str(e).strip(): float(v) for e, v in zip(df_emp_targets["emp_id"], vals)}


def _even_split_by_rank(total: int, employees, rank: dict) -> dict:
    """
    แบ่งเท่าที่สุด (ต่างกันไม่เกิน 1) — เศษตกกับคนต้น _fair_rank ไม่ใช่คนต้นรายชื่อ

    ผลตรวจ 28 ก.ย. 2026 §4.1-9: _distribute_even_integers ให้เศษกับช่องแรกของลิสต์เสมอ
    ลิสต์คือลำดับในทะเบียน → คนเดิมได้เศษของ SKU ที่แบ่งเท่าทุกงวด (SL384/SL540)
    ยอดรวมเท่าเดิมทุกหีบ เปลี่ยนแค่ว่าใครได้เศษ
    """
    emps = list(employees)
    parts = _distribute_even_integers(total, len(emps))
    ordered = sorted(emps, key=lambda e: rank.get(e, len(emps)))
    return {e: parts[i] for i, e in enumerate(ordered)}


def _spread_one_each(total_target, n_emps) -> bool:
    """
    เป้ารวมของ SKU น้อยกว่าจำนวนคนในทีม → ต้องกระจายให้ทั่ว คนละไม่เกิน 1 หีบ

    เช่น SL540 สินค้า 111336 เป้ารวม 4 หีบ ทีมมี 11 คน — ของเดิมมีสิทธิ์ตกที่คนเดียว
    4 หีบ (หรือกองที่คนแถวบน) ทั้งที่งานจริงคือ "ให้ 4 คนไปขายคนละหีบ"
    ใครได้ตัดสินด้วยประวัติการขายผ่าน _fair_rank ไม่ใช่ตำแหน่งในตาราง
    """
    try:
        return 0 < int(total_target) < int(n_emps)
    except (TypeError, ValueError):
        return False


def _sku_brand_map(df_sku: pd.DataFrame) -> dict[str, str]:
    """แบรนด์ของแต่ละ SKU (ชื่อไทยก่อน แล้วอังกฤษ — เหมือน optimize._sku_brand_key) · ไม่มีแบรนด์ = ไม่อยู่ในแผนที่"""
    if df_sku is None or df_sku.empty:
        return {}
    cols = [c for c in ("brand_name_thai", "brand_name_english") if c in df_sku.columns]
    if not cols:
        return {}
    out: dict[str, str] = {}
    for _, r in df_sku.iterrows():
        for c in cols:
            v = r.get(c)
            if v is not None and not (isinstance(v, float) and pd.isna(v)) and str(v).strip():
                out[_norm_sku(r.get("sku"))] = str(v).strip()
                break
    return out


def _rotate_small_skus_in_brand(
    df_out: pd.DataFrame,
    df_emp_targets: pd.DataFrame,
    df_sku: pd.DataFrame,
    *,
    hist_lookup: dict | None = None,
    locked_map: dict | None = None,
    zero_pairs: set | frozenset | None = None,
    even_skus: frozenset | None = None,
) -> tuple[pd.DataFrame, dict]:
    """
    หมุนหีบของ SKU "เป้าน้อยกว่าจำนวนคน" ภายในแบรนด์เดียวกัน ให้ทุกคนได้ขายแบรนด์นั้น (ผู้ใช้เลือก 7 ต.ค. 2026)

    ปัญหาเดิม: SKU ที่เป้าน้อยกว่าคน (เช่น 6 คน 3 หีบ) แจกคนละไม่เกิน 1 หีบ แต่ "ใครได้" ตัดสินแยกทีละ SKU
    ด้วยประวัติ/เงิน/รหัสเหมือนกันทุกตัว — แบรนด์ที่ทุก SKU เป้าน้อย (เช่น กู๊ดเอจ) คนเดิม 3 คนได้ทุก SKU
    อีก 3 คนไม่ได้ขายแบรนด์นั้นเลย (จำลองผ่านตัวกระจายจริง: E1–E3 ได้ทุก SKU · E4–E6 ได้ 0 ทุกวิธี)

    วิธี: ต่อแบรนด์ที่มี SKU แบบนี้ตั้งแต่ 2 ตัว ไล่ทีละ SKU แล้วให้หีบกับคนที่ "ได้ SKU ของแบรนด์นี้ไปน้อยที่สุด"
    ก่อน (นับจำนวน SKU ของแบรนด์ที่คนนั้นได้ > 0 รวม SKU ใหญ่ของแบรนด์ด้วย) เสมอกันให้คนที่ตัวกระจายเลือกไว้แล้ว
    ก่อน (เงินขยับน้อยที่สุด) แล้วประวัติ SKU นั้น → ประวัติทั้งแบรนด์ → เป้าเงิน → รหัส

    กติกาที่ห้ามพัง: ยอดต่อ SKU เท่าเดิม (ย้ายแค่ "ใครได้" ของหีบละ 1) · ช่องที่ล็อกไม่แตะ · คนที่กติกาไม่เคยขาย
    ตัดเป็น 0 ไม่ได้รับ · SKU แบ่งเท่า (สินค้าใหม่/ไม่มีใครเคยขาย) ไม่แตะ · SKU ที่มีช่องใดได้เกิน 1 หีบไม่แตะ
    """
    stats = {"brands": 0, "skus": 0, "moved_boxes": 0}
    if df_out is None or df_out.empty or df_emp_targets is None or df_emp_targets.empty:
        return df_out, stats
    brand_of = _sku_brand_map(df_sku)
    if not brand_of:
        return df_out, stats
    hist_lookup = hist_lookup or {}
    locked_map = locked_map or {}
    zero_pairs = zero_pairs or frozenset()
    even_skus = even_skus or frozenset()
    employees = [str(e).strip() for e in df_emp_targets["emp_id"].tolist() if str(e).strip()]
    yellow = _yellow_by_emp(df_emp_targets)
    target = {
        _norm_sku(s): int(round(float(t or 0)))
        for s, t in zip(df_sku["sku"], df_sku["supervisor_target_boxes"])
    }
    locked = {(str(e).strip(), _norm_sku(s)): int(v) for (e, s), v in locked_map.items()}

    cur: dict[tuple[str, str], int] = {}
    for e, s, b in zip(df_out["emp_id"], df_out["sku"], df_out["allocated_boxes"]):
        k = (str(e).strip(), _norm_sku(s))
        cur[k] = cur.get(k, 0) + int(pd.to_numeric(b, errors="coerce") or 0)

    by_brand: dict[str, list[str]] = {}
    for s, b in brand_of.items():
        if s in target:
            by_brand.setdefault(b, []).append(s)

    new_rows: dict[str, list[str]] = {}   # sku -> ผู้รับ 1 หีบ (เฉพาะช่องที่ไม่ล็อก)
    for brand, skus in sorted(by_brand.items()):
        small: list[tuple[str, list[str], int]] = []
        for s in sorted(skus):
            if s in even_skus:
                continue
            eligible = [e for e in employees if (e, s) not in zero_pairs]
            if not _spread_one_each(target.get(s, 0), len(eligible)):
                continue
            free = [e for e in eligible if (e, s) not in locked]
            vals = [cur.get((e, s), 0) for e in free]
            if any(v > 1 for v in vals):
                continue
            k = sum(vals)
            # คนที่ไม่ได้สิทธิ์ (zero_pairs) แต่มีหีบอยู่ = มีอะไรผิดปกติ — ไม่แตะ SKU นี้
            if k <= 0 or any(cur.get((e, s), 0) for e in employees if (e, s) in zero_pairs and (e, s) not in locked):
                continue
            small.append((s, free, k))
        if len(small) < 2:
            continue
        small_set = {s for s, _f, _k in small}
        # นับจาก SKU ของแบรนด์ที่ไม่ถูกหมุน + ช่องที่ล็อกของ SKU ที่หมุน
        got = {e: 0 for e in employees}
        for s in skus:
            for e in employees:
                if s in small_set and (e, s) not in locked:
                    continue
                if cur.get((e, s), 0) > 0:
                    got[e] += 1
        brand_hist = {e: sum(hist_lookup.get((e, s), 0.0) for s in skus) for e in employees}
        # SKU ที่คนมีสิทธิ์น้อยกว่าเลือกก่อน — กันคนที่รับได้หลาย SKU ถูกใช้หมดก่อนถึง SKU ที่ทางเลือกน้อย
        for s, free, k in sorted(small, key=lambda x: (len(x[1]), x[0])):
            pick = sorted(
                free,
                key=lambda e: (
                    got[e],
                    0 if cur.get((e, s), 0) > 0 else 1,
                    -float(hist_lookup.get((e, s), 0.0)),
                    -float(brand_hist.get(e, 0.0)),
                    -float(yellow.get(e, 0.0)),
                    e,
                ),
            )[:k]
            for e in pick:
                got[e] += 1
            new_rows[s] = pick
            before = {e for e in free if cur.get((e, s), 0) > 0}
            stats["moved_boxes"] += len(set(pick) - before)
        stats["brands"] += 1
        stats["skus"] += len(small)

    if not new_rows or not stats["moved_boxes"]:
        return df_out, stats
    keep = []
    for e, s in zip(df_out["emp_id"], df_out["sku"]):
        k = (str(e).strip(), _norm_sku(s))
        keep.append(not (k[1] in new_rows and k not in locked))
    out = df_out[keep].copy()
    add = [{"emp_id": e, "sku": s, "allocated_boxes": 1} for s, emps in new_rows.items() for e in emps]
    out = pd.concat([out, pd.DataFrame(add)], ignore_index=True)
    logger.info(
        "หมุนหีบ SKU เป้าน้อยในแบรนด์: %d แบรนด์ %d SKU ย้ายผู้รับ %d หีบ",
        stats["brands"], stats["skus"], stats["moved_boxes"],
    )
    return out, stats


def _eligible_emp_count(employees, sku, zero_pairs) -> int:
    """
    จำนวนคนที่มีสิทธิ์รับหีบของ SKU นี้ — ตัวหารของ _spread_one_each

    ต้องหักคนที่กติกาไม่เคยขายตัดเป็น 0 ออก: ทีม 6 คน เป้า 3 หีบ มีคนเคยขายคนเดียว
    ถ้านับ 6 คน เพดาน "คนละไม่เกิน 1" จะให้คนเคยขายได้ 1 หีบ อีก 2 หีบไม่มีที่ลง
    ยอดขาดเป้า (I1) แล้ว LP แก้ไม่ได้ ถอยไป fallback ทั้งทีม
    (พบตอนเทียบ §4.1-2 — ไฟล์ export งวด 10/2026 เกิด 30 จาก 38 ทีมเมื่อเปิดกติกา)
    ไม่มีกติกา = นับทุกคนเท่าเดิม ผลจึงไม่เปลี่ยน
    """
    if not zero_pairs:
        return len(employees)
    sku_key = _norm_sku(sku)
    return sum(1 for e in employees if (str(e).strip(), sku_key) not in zero_pairs)


def _min_one_floor(force_min_one: bool, total_target, employees, locked_map, sku) -> int:
    """
    ขั้นต่ำต่อคนของ SKU นี้เมื่อติ๊ก "ทุกคนอย่างน้อย 1 หีบ" (I4) — กติกาเดียวทุกชั้น

    บังคับ 1 หีบเมื่อ (ก) เป้า >= จำนวนคนทั้งทีม ตามเอกสาร **และ** (ข) หีบที่เหลือหลัง
    หักช่องที่ล็อกยังพอแจกคนที่ไม่ได้ล็อกได้คนละ 1 จริง — ข้อ (ข) เคยมีแค่ใน _proportional
    ส่วน LP/ตัวเกลี่ยเงินดูแค่ (ก) พอผู้ใช้ล็อกช่องจนแจกคนละ 1 ไม่พอ LP จึงหาคำตอบไม่ได้
    "ทั้งทีม" แล้วถอยไปแบ่งตามสัดส่วนทุก SKU (เสียการเกลี่ยเงินหมด เพราะ SKU เดียว)
    I1 (ห้ามเกินเป้า) ชนะ I4 เสมอเมื่อขัดกัน
    """
    if not force_min_one:
        return 0
    emps = [str(e).strip() for e in (employees or [])]
    try:
        t = int(round(float(total_target or 0)))
    except (TypeError, ValueError):
        return 0
    if not emps or t < len(emps):
        return 0
    lm = locked_map or {}
    locked_sum = 0
    free_n = 0
    for e, e_raw in zip(emps, employees):
        v = lm.get((e_raw, sku), lm.get((e, sku)))
        if v is None:
            free_n += 1
        else:
            locked_sum += max(0, int(v))
    return 1 if (t - locked_sum) >= free_n else 0


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
    df_hist: pd.DataFrame | None = None,
    zero_pairs: set | None = None,
) -> pd.DataFrame:
    """
    บังคับ SKU สินค้าใหม่ให้แบ่งเท่าทุกคน (หลัง LP/greedy — กันโหมดหลัก/รองดึงไปปรับเงิน)

    เศษตกกับคนต้น _fair_rank (ขายสินค้านั้นมาก → เป้าเงินสูง → รหัส) ไม่ใช่คนต้นรายชื่อ (§4.1-9)

    zero_pairs (I9, ผลตรวจ §4.1-8): สินค้าใหม่ที่มีคนเคยขายใน 12 เดือน — คนที่ไม่เคยขายได้ 0
    แบ่งเท่าเฉพาะคนที่เหลือ เหมือนที่ _proportional/LP ทำไว้แล้ว · เดิมขั้นนี้แบ่งใหม่ให้ทุกคน
    ทับผลนั้นทิ้ง · force_min_one ยังชนะ (คนไม่เคยขายได้ 1 หีบ เหมือน _proportional)
    """
    locked_map = locked_map or {}
    zero_pairs = zero_pairs or set()
    if not even_skus:
        return df_out
    hist_lookup = _hist_lookup(df_hist)
    yellow_by_emp = _yellow_by_emp(df_emp_targets)

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

        # โควตาที่เหลือหลังหักของที่ล็อกไว้ — ทุกอย่างต่อจากนี้ต้องอยู่ในนี้
        avail = max(0, total_target - locked_sum)
        free_n = len(free_emps)
        # base_box ต้องคิดจาก "คนที่ยังแบ่งได้" ไม่ใช่คนทั้งหมด
        # ของเดิมเช็ค total_target >= n_emps แล้วเอาไปคูณกับ len(free_emps)
        # พอ max(0, ...) ตัดส่วนเกิน ผลรวมจึงกลายเป็น locked_sum + free_n ซึ่งเกินเป้า
        # เช่น 10 คน เป้า 10 ล็อก 3 คน คนละ 3 หีบ -> 9 + 7 = 16
        base_box = 1 if (force_min_one and total_target >= n_emps and avail >= free_n) else 0
        remaining = avail - base_box * free_n
        # คนที่กติกาไม่เคยขายตัดทิ้งไม่ร่วมแบ่ง — ตัดจนไม่เหลือใครแปลว่าแผนผิด ปล่อยตามเดิม
        share_emps = [e for e in free_emps if (e, sku_key) not in zero_pairs] or free_emps
        rank = _fair_rank(
            share_emps, {e: hist_lookup.get((e, sku_key), 0.0) for e in share_emps}, yellow_by_emp
        )
        parts = _even_split_by_rank(remaining, share_emps, rank)

        for e, boxes in locked_by_emp.items():
            if boxes > 0:
                rows.append({"emp_id": e, "sku": sku_key, "allocated_boxes": boxes})

        for e in free_emps:
            boxes = base_box + parts.get(e, 0)
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


def _zero_baseline_cap_enabled() -> bool:
    """
    เพดานของเซลล์ที่ baseline = 0 — **ค่าเริ่มต้นคือปิด**

    ทำไมถึงปิดไว้: วัดกับข้อมูลจริง 8 ทีมแล้ว เปิดแล้วหีบย้ายที่ราว 2-3.5% ของทีม
    (ผลรวมต่อ SKU ยังตรงเป้าทุกกรณี — แค่ย้ายจากคนที่ไม่มีประวัติขาย SKU นั้น
    ไปหาคนที่มีประวัติ) ซึ่งเป็นการเปลี่ยนที่หัวหน้าทีมเห็นตัวเลขต่างทันที
    จึงเป็น "การตัดสินใจเชิงนโยบาย" ไม่ใช่บั๊กที่ต้องรีบปิด

    ตัวคุมชั้นแรกยังทำงานอยู่แล้วทั้งหมด: _cap_and_redistribute (เพดาน mean x cap),
    anchor term ที่ถ่วงด้วยราคา, และสมการผลรวมต่อ SKU

    ALLOC_ZERO_BASELINE_CAP=1 เพื่อเปิด (บังคับกฎ I5 เต็มรูปแบบ)
    """
    import os

    raw = (os.environ.get("ALLOC_ZERO_BASELINE_CAP") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _zero_baseline_cap(total_target: int, n_emps: int, cap_multiplier: float | None) -> int:
    """
    เพดานของเซลล์ที่ baseline = 0 (คนที่ไม่เคยขาย SKU นั้น)

    เดิมไม่มีขอบบนเลย (`if base <= 0: continue`) เหลือแค่สมการผลรวมต่อ SKU เป็นตัวคุม
    ในทางปฏิบัติ _cap_and_redistribute + anchor term คุมไว้อยู่แล้ว เพดานนี้จึงเป็น
    ตัวกันชั้นสอง — ให้เจตนา "ห้ามกองไว้ที่คนที่ไม่มีประวัติ" อยู่ในรูปข้อจำกัดจริง ๆ
    ไม่ใช่ผลข้างเคียงของ objective ที่แก้เมื่อไหร่ก็หลุดเมื่อนั้น
    """
    import math

    n = max(1, int(n_emps))
    mult = float(cap_multiplier if cap_multiplier is not None else _CAP_MULTIPLIER)
    return max(1, int(math.ceil(max(0, int(total_target)) / n * max(1.0, mult))))


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

def _cap_and_redistribute(
    raw: dict, total: int, cap_multiplier: float = None, tie_rank: dict | None = None
) -> dict:
    """
    จำกัด weight outlier: ถ้าใครได้ > mean * CAP ให้ cap แล้วกระจายส่วนเกินให้คนที่เหลือ
    ทำซ้ำจนไม่มีคนเกิน cap (max 10 รอบ)
    cap_multiplier: override _CAP_MULTIPLIER ถ้า Custom strategy ส่งมา
    tie_rank: ลำดับตัดสินเมื่อเศษทศนิยมเท่ากัน (ดู _fair_rank) — ไม่ส่งมา = ลำดับเดิมในลิสต์
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
    # เศษเท่ากันตัดสินด้วยประวัติ ไม่ใช่ลำดับแถวในทะเบียน (ดู _fair_rank)
    rank = tie_rank or {}
    order = sorted(emps, key=lambda e: (-(allocated[e] - floored[e]), rank.get(e, 0)))
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
    zero_pairs: set | frozenset | None = None,
):
    locked_map = locked_map or {}
    even_skus = even_skus or frozenset()
    zero_pairs = zero_pairs or frozenset()
    employees = df_emp_targets["emp_id"].tolist()
    target_boxes = dict(zip(df_sku["sku"], df_sku["supervisor_target_boxes"]))
    hist_lookup = _hist_lookup(df_hist)
    yellow_by_emp = _yellow_by_emp(df_emp_targets)

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

        # กติกาไม่เคยขาย: คนที่ไม่เคยขาย SKU นี้ไม่เข้าร่วมแบ่งเลย
        # (SKU ที่ทีมไม่เคยขาย/ถูกดันเป้า ไม่เข้ามาทางนี้ — อยู่ในสาขา even_skus)
        if zero_pairs:
            sku_key_zp = _norm_sku(sku)
            eligible = [e for e in active_employees if (e, sku_key_zp) not in zero_pairs]
            # ตัดจนไม่เหลือใครแปลว่าแผนคำนวณผิด — ปล่อยตามเดิมดีกว่าคืนผลว่าง
            if eligible:
                active_employees = eligible

        if total <= 0 or not active_employees:
            continue

        # force_min_one: กระจายอย่างน้อย 1 หีบ/คน — กติกาเดียวกับ LP/ตัวเกลี่ยเงิน
        # (_min_one_floor) คนไม่เคยขายที่ถูกตัดออกจาก active ก็ได้ 1 หีบด้วย เพราะคำสั่ง
        # ของผู้ใช้ชนะกติกาอัตโนมัติ (I9) — เดิมทางนี้ให้ 0 แต่ LP ให้ 1 ผลจึงต่างกัน
        # ตามว่าไปจบที่ LP หรือถอยมาทางนี้
        base_box = _min_one_floor(force_min_one, total_orig, employees, locked_map, sku)
        if base_box:
            for e in employees:
                if e not in locked_emps and e not in active_employees:
                    results.append({"emp_id": e, "sku": sku, "allocated_boxes": 1})
                    total -= 1
            total -= len(active_employees)

        # ── คำนวณ hist weight ──
        sku_key = _norm_sku(sku)
        hist_by_emp = {
            emp: max(hist_lookup.get((str(emp).strip(), sku_key), 0.0), 0.0)
            for emp in active_employees
        }

        hist_sum = sum(hist_by_emp.values())
        # ลำดับทั่วไป (เป้าน้อยกว่าจำนวนคน / เศษทศนิยมเสมอกัน): ประวัติ → รหัส ตามเดิม
        rank = _fair_rank(active_employees, hist_by_emp)

        # สินค้าใหม่: แบ่งเท่าโดยไม่ผ่าน cap (กันหีบเบี้ยวในโหมดหลัก/รอง)
        # เศษตัดสินด้วยประวัติ → เป้าเงิน → รหัส (ผู้ใช้เลือก 29 ก.ย. 2026 ทางเลือก B —
        # ใช้เป้าเงินเฉพาะ SKU ที่แบ่งเท่า จุดอื่นคงเดิม ผลเปลี่ยนน้อยกว่าใช้ทุกจุด)
        if sku_key in even_skus:
            floored = _even_split_by_rank(
                total, active_employees, _fair_rank(active_employees, hist_by_emp, yellow_by_emp)
            )
        elif (_spread_one_each(total_orig, _eligible_emp_count(employees, sku, zero_pairs))
              and total <= len(active_employees)):
            # ต้องพอที่ลงด้วย (ผลตรวจ 1 ต.ค. 2026 ก3): ล็อกบางคนไว้ที่ 0 แล้วหีบที่เหลือมากกว่าคนที่ยังรับได้
            # ถ้าคงเพดาน 1 หีบ หีบส่วนเกินไม่มีที่ลง ยอดขาดเป้า → 409 ทั้งที่ล็อกถูกต้อง · กรณีนั้นไปทางสัดส่วนแทน
            # เป้าน้อยกว่าจำนวนคน → คนละไม่เกิน 1 หีบ ให้ทั่วถึงตามลำดับประวัติการขาย
            # (เทียบกับ "จำนวนคนทั้งทีม" ไม่ใช่คนที่เหลือหลังหักล็อก — นิยามเดียวกับที่
            #  ผลตรวจรอบ 0 นับ และไม่แกว่งตามว่าผู้ใช้ล็อกช่องไปแล้วกี่ช่อง)
            picked = set(sorted(active_employees, key=lambda e: rank[e])[:total])
            floored = {e: (1 if e in picked else 0) for e in active_employees}
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
                floored = _cap_and_redistribute(
                    raw, total, cap_multiplier=cap_multiplier, tie_rank=rank
                )
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
    cap_multiplier: float | None = None,
    zero_pairs: set | frozenset | None = None,
) -> pd.DataFrame:
    if df_out.empty:
        return df_out
    locked_map = locked_map or {}
    skip_balance_skus = skip_balance_skus or set()
    even_skus = even_skus or frozenset()
    zero_pairs = zero_pairs or frozenset()
    target_rev = dict(zip(df_emp_targets["emp_id"], df_emp_targets["yellow_target"]))
    sku_prices = dict(zip(df_sku["sku"], df_sku["price_per_box"]))
    target_boxes = dict(zip(df_sku["sku"], df_sku["supervisor_target_boxes"]))
    emps = df_emp_targets["emp_id"].tolist()
    n_emps = len(emps)

    _bounds_cache: dict[tuple[str, str], tuple[int, int] | None] = {}

    # รั้วประวัติผ่อนเป็นขั้น เมื่อเงินยังห่างเป้าและขยับต่อไม่ได้แล้ว
    #
    # ทุกเซลล์ถูกล็อกให้อยู่ในกรอบ +-12% (strict) / +-35% (flex) รอบประวัติของคนนั้น
    # กระจายทีมเดียวไม่มีปัญหา เพราะ baseline มาจากการเกลี่ยเป้าของทีมนั้นเองอยู่แล้ว
    # แต่ "รวมเป้าทั้งภาคเป็นก้อนเดียว" baseline มาจากประวัติทั้งภาค ขณะที่เป้าเงิน
    # รายคนยังเป็นของทีมตัวเอง สองอย่างนี้ไม่ตรงรูปกัน พอถูกล็อกไว้แคบ ๆ เครื่องจึง
    # ย้ายหีบไม่พอ แล้วทุกคนค้างห่างเป้าหลักแสนพร้อมกัน (จำลอง 120 คน: 462,169 บาท)
    #
    # ผู้ใช้ยึดวิธีกระจายเป็นหลักก็จริง แต่ยอมรับได้แค่ระดับหลักพัน จึงคลายรั้วให้
    # เฉพาะเมื่อจำเป็น: ลองกรอบเดิมก่อนเสมอ ถ้ายังไม่เข้าเกณฑ์ค่อยขยาย และหยุดทันที
    # ที่ทุกคนเข้าเกณฑ์ — เคสที่เดิมผ่านอยู่แล้วจึงได้ผลเหมือนเดิมเป๊ะ
    _BAND_SCALES = (1.0, 2.5, 6.0, None)      # None = ปล่อยอิสระ (ยังคุมด้วยเป้าราย SKU)
    _band_idx = 0

    def _band_scale() -> float | None:
        return _BAND_SCALES[_band_idx]

    def _cell_bounds(emp: str, sku: str) -> tuple[int, int] | None:
        # memoize: ถูกเรียกซ้ำหลักแสนครั้งในลูป แต่ผลขึ้นกับ (emp, sku) ล้วน
        ck = (emp, sku)
        if ck in _bounds_cache:
            return _bounds_cache[ck]
        _bounds_cache[ck] = _v = _cell_bounds_uncached(emp, sku)
        return _v

    def _cell_bounds_uncached(emp: str, sku: str) -> tuple[int, int] | None:
        """รั้วประวัติ + เพดาน "เป้าน้อยกว่าจำนวนคน" — ตัวหลังต้องมีผลแม้ไม่มี base_map"""
        if (emp, sku) in locked_map:
            return None
        if _norm_sku(sku) in even_skus:
            return None
        # กติกาไม่เคยขาย — ตรึงไว้ที่ขอบล่าง ไม่ให้ตัวเกลี่ยเงินยกหีบกลับเข้ามา
        if zero_pairs and (emp, _norm_sku(sku)) in zero_pairs:
            floor_zero = _min_floor_boxes(sku)
            return (floor_zero, floor_zero)
        bounds = _cell_bounds_by_history(emp, sku)
        if not _spread_one_each(target_boxes.get(sku, 0), _eligible_emp_count(emps, sku, zero_pairs)):
            return bounds
        # ตัวเกลี่ยเงินย้ายหีบทีละใบ ถ้าไม่กั้นตรงนี้มันจะยกหีบไปกองคืนที่คนเดียวได้
        # ทั้งที่ LP/ตัวกระจายตั้งใจแบ่งให้คนละใบ
        if bounds is None:
            floor_min = _min_floor_boxes(sku)
            return (floor_min, max(floor_min, 1))
        lo, hi = bounds
        return (lo, max(lo, min(hi, 1)))

    def _cell_bounds_by_history(emp: str, sku: str) -> tuple[int, int] | None:
        if not base_map:
            return None
        if (emp, sku) in locked_map:
            return None
        sku_key = _norm_sku(sku)
        if sku_key in even_skus:
            return None
        base = int(base_map.get((str(emp).strip(), sku_key), 0))
        min_box = _min_floor_boxes(sku)
        if base <= 0:
            # baseline 0 — ใช้เพดานสัมบูรณ์แทนรั้ว % ให้ตรงกับฝั่ง LP (I5)
            if not _zero_baseline_cap_enabled():
                return None
            try:
                tgt = int(round(float(target_boxes.get(sku, 0) or 0)))
            except (TypeError, ValueError):
                tgt = 0
            return (min_box, max(min_box, _zero_baseline_cap(tgt, n_emps, cap_multiplier)))
        scale = _band_scale()
        if scale is None:
            # ผ่อนเต็มที่ = เลิกใช้รั้ว "ตามประวัติ" แต่ไม่ใช่เลิกมีเพดานเลย
            # ถ้าคืน None ที่นี่ ช่องหนึ่งช่องรับหีบได้ไม่จำกัด พนักงานคนเดียวจึงกิน
            # SKU นั้นเกือบทั้งก้อนได้ทั้งที่ยอดรวมต่อ SKU ยังตรงเป้า (ด่านไหนก็ไม่จับ)
            # ใช้เพดานสัมบูรณ์ชุดเดียวกับกรณี baseline 0 แทน — ฝั่ง LP ก็ใช้ตัวนี้ (I5)
            if not _zero_baseline_cap_enabled():
                return None
            try:
                tgt_free = int(round(float(target_boxes.get(sku, 0) or 0)))
            except (TypeError, ValueError):
                tgt_free = 0
            return (
                min_box,
                max(min_box, _zero_baseline_cap(tgt_free, n_emps, cap_multiplier)),
            )
        cell_band = _tier_cell_band_pct(
            sku_key,
            tiered_allocation=tiered_allocation,
            flex_skus=flex_skus,
            default_band_pct=default_band_pct,
            flex_band_pct=flex_band_pct,
            strict_band_pct=strict_band_pct,
        )
        return _hist_band_int_bounds(base, cell_band * scale, min_box)

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

    _floor_cache: dict = {}

    def _min_floor_boxes(sku: str) -> int:
        """กติกาเดียวกับ _proportional / LP (_min_one_floor)"""
        if sku not in _floor_cache:
            _floor_cache[sku] = _min_one_floor(
                force_min_one, target_boxes.get(sku, 0), emps, locked_map, sku
            )
        return _floor_cache[sku]
    
    alloc = {}
    for emp in emps: alloc[emp] = {s: 0 for s in sku_prices.keys()}
    for _, r in df_out.iterrows():
        if r["emp_id"] in alloc and r["sku"] in sku_prices:
            alloc[r["emp_id"]][r["sku"]] = r["allocated_boxes"]

    # รายได้ปัจจุบันต่อคน — คิดครั้งเดียวแล้วปรับทีละก้าวตอนย้ายหีบ
    # ของเดิมเรียก get_current_rev() ใหม่ทั้งตารางทุกรอบ (สูงสุด 50,000 รอบ
    # x จำนวนคน x จำนวน SKU) ทั้งที่การย้าย 1 หีบเปลี่ยนค่าแค่สองคน
    rev = {e: sum(alloc[e][s] * sku_prices[s] for s in sku_prices) for e in emps}

    # ค่าที่ไม่เปลี่ยนระหว่างลูป — ยกออกมาคำนวณครั้งเดียว
    # ลำดับต้องคงเดิมเป๊ะ เพราะการเลือก best ใช้ ">" (ตัวแรกที่ดีที่สุดชนะ)
    candidates = [
        (sku, float(price or 0))
        for sku, price in sku_prices.items()
        if _norm_sku(sku) not in skip_balance_skus
    ]
    floors = {sku: _min_floor_boxes(sku) for sku, _ in candidates}

    # โหมดหลายวิธีล็อกหนักกว่าอีกชั้น: SKU ที่ไม่ใช่ตัวหลัก (strict) ถูกห้ามแตะเลย
    # ไม่ใช่แค่จำกัดกรอบ · ถ้าอิสระที่เหลือในกลุ่มตัวหลักไม่พอ เงินก็เข้าเป้าไม่ได้
    # และการผ่อนรั้วอย่างเดียวช่วยไม่ได้ เพราะ SKU พวกนั้นไม่อยู่ในรายการให้เลือกด้วยซ้ำ
    # จึงปลดเป็นขั้นสุดท้าย หลังผ่อนรั้วจนสุดแล้วยังไม่เข้าเกณฑ์
    # (สินค้าใหม่ที่ตั้งใจเกลี่ยเท่ากันยังห้ามแตะตลอด — เจตนาคนละเรื่อง)
    _strict_locked = bool(skip_balance_skus - set(even_skus))

    def _relax() -> bool:
        """ผ่อนข้อจำกัดทีละขั้น คืน True ถ้ายังผ่อนต่อได้"""
        nonlocal _band_idx, _strict_locked, candidates, floors
        nonlocal stall_count, prev_total_error
        if _band_idx + 1 < len(_BAND_SCALES):
            _band_idx += 1
        elif _strict_locked:
            _strict_locked = False
            keep_out = set(even_skus)
            candidates = [
                (sku, float(price or 0))
                for sku, price in sku_prices.items()
                if _norm_sku(sku) not in keep_out
            ]
            floors = {sku: _min_floor_boxes(sku) for sku, _ in candidates}
        else:
            return False
        _bounds_cache.clear()
        stall_count = 0
        prev_total_error = float("inf")
        return True

    prev_total_error = float("inf")
    stall_count = 0
    for _ in range(int(max_iters)):
        diffs = {e: rev[e] - target_rev.get(e, 0) for e in emps}
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

        # ความคลาดเคลื่อนรวมของ "ทุกคน" ไม่ใช่แค่คู่ที่กำลังจับอยู่รอบนี้
        #
        # ของเดิมวัดจาก |รวย| + |จน| ของคู่ปัจจุบัน ซึ่งกระโดดขึ้นทุกครั้งที่คนจน
        # คนหนึ่งเต็มแล้วเลื่อนไปหาคนจนรายถัดไป (คนถัดไปห่างเป้ามากกว่าคนที่เพิ่งเสร็จ)
        # ตัวนับ stall จึงเพิ่มขึ้นเรื่อย ๆ ทั้งที่งานเดินหน้าอยู่ พอครบ 20 ก็เลิกกลางคัน
        # ทีมเดียว ~15-25 คนไม่เคยเห็นปัญหาเพราะ 20 ครั้งพอปิดจ๊อบทุกคนอยู่แล้ว
        # แต่รวมภาคมีเป็นร้อยคน มันจึงหยุดตั้งแต่เพิ่งเกลี่ยไปได้ไม่ถึงยี่สิบคน
        # เหลือที่เหลือห่างเป้าหลักแสน · ผลรวมทั้งก้อนลดลงจริงทุกครั้งที่ย้ายได้ผล
        # ค่านี้จึงไม่กระโดด และ "ไม่ลดลง" ก็แปลว่าติดจริง
        total_error = sum(abs(v) for v in diffs.values())
        # กันติด: ถ้าไม่ดีขึ้นต่อเนื่องให้หยุด แต่ให้โอกาสมากขึ้น
        if total_error >= prev_total_error - 1e-6:
            stall_count += 1
            if stall_count >= 20:
                if _relax():
                    continue                 # ลองใหม่ด้วยข้อจำกัดที่ผ่อนแล้ว
                break
        else:
            stall_count = 0
        prev_total_error = total_error
            
        best_sku_to_move = None
        best_improvement = 0
        
        d_rich = diffs[rich_emp]
        d_poor = diffs[poor_emp]
        current_error = abs(d_rich) + abs(d_poor)
        alloc_rich = alloc[rich_emp]
        # ข้ามได้ 4 เหตุ แต่มีเหตุเดียวที่ "ผ่อนรั้วแล้วช่วยได้" คือติดกรอบประวัติ
        # ถ้าไม่แยกให้ออก เคสที่ช่องว่างเล็กกว่าราคากล่องถูกที่สุด (ย้ายยังไงก็ไม่ดีขึ้น)
        # จะไล่ผ่อนจนสุดแล้วปลดล็อก SKU ที่ตั้งใจคุมไว้ทั้งกระดาน เพื่อไล่ตามเงิน
        # ไม่กี่ร้อยบาทที่ไปไม่ถึงอยู่แล้ว — ทรงการกระจายของทีมที่ปกติดีก็เพี้ยนตาม
        band_blocked = False
        for sku, price in candidates:
            # 🔴 ข้ามการสลับหีบที่คนพิมพ์แก้ไขไว้แล้ว (ห้ามยุ่งเด็ดขาด)
            if (rich_emp, sku) in locked_map or (poor_emp, sku) in locked_map:
                continue

            # ห้ามดึงหีบจนเหลือต่ำกว่า floor (กัน force_min_one ถูกทำลายหลัง _proportional)
            if alloc_rich[sku] <= floors[sku]:
                continue
            if not _can_move_box(rich_emp, poor_emp, sku):
                band_blocked = True
                continue
            new_error = abs(d_rich - price) + abs(d_poor + price)

            improvement = current_error - new_error
            if improvement > best_improvement:
                best_improvement = improvement
                best_sku_to_move = sku

        if best_sku_to_move is None:
            # ย้ายอะไรไม่ได้แล้ว — ผ่อนต่อเมื่อการผ่อนมีทางช่วยจริงเท่านั้น:
            # ติดกรอบประวัติ (band_blocked) หรือยังมี SKU ที่ถูกกันออกจากรายการอยู่
            if (band_blocked or _strict_locked) and _relax():
                continue
            break

        moved_price = float(sku_prices.get(best_sku_to_move, 0) or 0)
        # ย้ายทีละหลายหีบเมื่อช่องว่างยังกว้าง — ของเดิมย้ายรอบละ 1 หีบเสมอ
        #
        # ทีมเดียวไม่เคยเห็นปัญหา เพราะช่องว่างรวมปิดได้ในไม่กี่พันหีบ แต่รวมภาค
        # มีเป็นร้อยคนและช่องว่างตั้งต้นหลักสิบล้านบาท ต้องย้ายหลายหมื่นใบ พอชน
        # เพดาน max_iters ก่อนก็เลิกกลางคัน ทุกคนเลยค้างห่างเป้าหลักแสนพร้อมกัน
        # (จำลอง 150 คน/600 SKU: ดิฟมัธยฐาน 175,956 บาท ทั้งที่ยอมรับได้แค่หลักพัน)
        #
        # จำนวนที่ย้ายถูกจำกัดด้วยสามอย่าง ไม่ให้เลยเป้าฝั่งไหน และไม่ทะลุรั้วของเซลล์
        steps = 1
        if moved_price > 0:
            room = [int(min(d_rich, -d_poor) // moved_price)]      # ไม่ให้ข้ามเป้า
            take = alloc[rich_emp][best_sku_to_move] - floors[best_sku_to_move]
            b_from = _cell_bounds(rich_emp, best_sku_to_move)
            if b_from is not None:
                take = min(take, alloc[rich_emp][best_sku_to_move] - b_from[0])
            room.append(take)                                      # ดึงออกได้เท่าไร
            b_to = _cell_bounds(poor_emp, best_sku_to_move)
            if b_to is not None:
                room.append(b_to[1] - alloc[poor_emp][best_sku_to_move])   # ใส่เพิ่มได้เท่าไร
            # อย่างน้อย 1 ใบเสมอ — _can_move_box ผ่านแล้วว่าย้ายใบแรกได้จริง
            steps = max(1, min(room))
        alloc[rich_emp][best_sku_to_move] = max(
            floors[best_sku_to_move], alloc[rich_emp][best_sku_to_move] - steps
        )
        alloc[poor_emp][best_sku_to_move] += steps
        # ปรับรายได้แบบก้าวเดียว แทนการรวมใหม่ทั้งตาราง
        rev[rich_emp] -= moved_price * steps
        rev[poor_emp] += moved_price * steps

    # ต้องคืน "ทุกเซลล์" รวมที่เป็น 0 ด้วย
    #
    # ของเดิมกรอง `if boxes > 0` ทิ้ง เซลล์ที่ถูกดึงหีบออกจนเหลือ 0 จึงหายไปจากผลลัพธ์
    # ฝั่ง _post_merge_revenue_balance ที่ merge กลับด้วย .get(key, ค่าเดิม)
    # เลยดึงค่าก่อนปรับกลับมา = คนให้ไม่ได้ลด แต่คนรับได้เพิ่ม -> หีบงอกเกินเป้า (I1)
    final_results = [
        {"emp_id": emp, "sku": sku, "allocated_boxes": boxes}
        for emp in emps
        for sku, boxes in alloc[emp].items()
    ]
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
    zero_pairs: set | frozenset | None = None,
    _meta: dict | None = None,
):
    locked_map = locked_map or {}
    even_skus = even_skus or frozenset()
    zero_pairs = zero_pairs or frozenset()
    baseline_strategy = (baseline_strategy or "L3M").upper()
    if baseline_strategy not in ("L3M", "L6M", "LY", "EVEN", "PUSH"):
        baseline_strategy = "L3M"

    def _fallback_prop():
        if _meta is not None:
            _meta["optimization_fallback"] = True
        return _proportional(
            df_emp_targets,
            df_sku,
            df_hist,
            baseline_strategy,
            force_min_one,
            locked_map,
            cap_multiplier,
            even_skus=even_skus,
            zero_pairs=zero_pairs,
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

    # ── ชื่อตัวแปร LP ต้องเป็นเลขล้วน ห้ามเอา emp_id/sku ไปต่อเป็นชื่อ
    #
    # PuLP ล้างเฉพาะอักขระใน LpElement.illegal_chars = "-+[] ->/" เท่านั้น จึงมี 2 ปัญหา:
    #   1. "|" ไม่อยู่ในชุดนั้น — พนักงานหลายคลังมี id เป็น "emp|WH" (ดู wh_split.alloc_key)
    #      กลายเป็นชื่อ x_E001|WH12_111111 เขียนลงไฟล์ LP ที่ CBC อ่านไม่ได้
    #   2. "-" ถูกแปลงเป็น "_" → ("A-B","C") กับ ("A","B_C") ได้ชื่อเดียวกัน
    #      และ PuLP อ่านคำตอบกลับ "โดยอิงชื่อ" ตัวแปรชนกัน = หีบไปตกกับคนผิด
    # ทั้งสองเคสพังเงียบ (ตกไป fallback proportional) จึงต้องตัดปัญหาที่ต้นทาง
    _emp_ix = {e: i for i, e in enumerate(employees)}
    _sku_ix = {s: j for j, s in enumerate(skus)}

    def _vname(prefix: str, emp=None, sku=None) -> str:
        parts = [prefix]
        if emp is not None:
            parts.append(str(_emp_ix[emp]))
        if sku is not None:
            parts.append(str(_sku_ix[sku]))
        return "_".join(parts)

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

    # ขั้นต่ำ force_min_one ต่อ SKU — กติกาเดียวกับ _proportional/ตัวเกลี่ยเงิน (I4)
    min_box_by_sku = {
        sku: _min_one_floor(force_min_one, target_boxes[sku], employees, locked_map, sku)
        for sku in skus
    }

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
                        _vname("x", emp, sku), lowBound=val, upBound=val, cat="Integer"
                    )
                    continue
                min_box = min_box_by_sku[sku]
                even_base = None
                if sku_key in even_skus and base_map:
                    even_base = int(base_map.get((str(emp).strip(), sku_key), 0))
                if even_base is not None:
                    # SKU ใหม่แบ่งเท่า — ล็อกตาม baseline เกลี่ยเท่า ไม่ให้ LP/ทดลอง 80/20 ดึงไปปรับเงิน
                    x[(emp, sku)] = pulp.LpVariable(
                        _vname("x", emp, sku), lowBound=even_base, upBound=even_base, cat="Integer"
                    )
                    continue
                if (emp, sku_key) in zero_pairs:
                    # กติกาไม่เคยขาย — ล็อกไว้ที่ขอบล่างเลย
                    # ปกติ min_box = 0 · แต่ถ้าผู้ใช้ติ๊ก "ทุกคนอย่างน้อย 1 หีบ" ไว้
                    # เขาสั่งชัดกว่ากติกาอัตโนมัติ คนไม่เคยขายจึงได้พอดี 1 หีบ ไม่ใช่ 0
                    x[(emp, sku)] = pulp.LpVariable(
                        _vname("x", emp, sku), lowBound=min_box, upBound=min_box, cat="Integer"
                    )
                    continue
                x[(emp, sku)] = pulp.LpVariable(
                    _vname("x", emp, sku), lowBound=min_box, cat="Integer"
                )
                dpos[(emp, sku)] = pulp.LpVariable(_vname("dp", emp, sku), lowBound=0, cat="Continuous")
                dneg[(emp, sku)] = pulp.LpVariable(_vname("dn", emp, sku), lowBound=0, cat="Continuous")

        # ขอบล่างที่ถูกบังคับไว้จริงต่อเซลล์ — เพดาน "เป้าน้อยกว่าจำนวนคน" ด้านล่างต้องรู้
        # ไม่งั้นจะไปทับขอบล่างของรั้วประวัติแล้วโจทย์กลายเป็น infeasible ทั้งทีม
        cell_lo: dict[tuple, int] = {}
        if band_pct > 0 and base_map:
            for emp in employees:
                for sku in skus:
                    if (emp, sku) in locked_map:
                        continue
                    sku_key = _norm_sku(sku)
                    if sku_key in even_skus:
                        continue
                    base = int(base_map.get((str(emp).strip(), sku_key), 0))
                    min_box = min_box_by_sku[sku]
                    if base <= 0:
                        # baseline 0 ไม่มีรั้ว % ให้อ้างอิง — ใช้เพดานสัมบูรณ์แทน (I5)
                        if _zero_baseline_cap_enabled():
                            hi0 = max(min_box, _zero_baseline_cap(
                                int(target_boxes[sku]), len(employees), cap_multiplier
                            ))
                            prob += x[(emp, sku)] <= hi0
                        continue
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
                    cell_lo[(emp, sku)] = lo

        # เป้ารวมของ SKU น้อยกว่าจำนวนคน → คนละไม่เกิน 1 หีบ (ดู _spread_one_each)
        #
        # LP ตัดสินด้วย "เงินรายคนห่างเป้าเท่าไร" ล้วน ๆ เป้า 4 หีบในทีม 11 คนจึงลงที่
        # คนเดียวได้ถ้าบังเอิญช่วยให้เงินเข้าเป้ากว่า · ใช้ max(1, ขอบล่างของเซลล์) เสมอ
        # เพื่อไม่ให้ชนกับรั้วประวัติ (เช่นคนที่ baseline 3 ถูกบังคับ >= 2 อยู่แล้ว)
        # ซึ่งจะทำให้โจทย์แก้ไม่ได้แล้วตกไป fallback ทั้งทีม
        for sku in skus:
            if not _spread_one_each(target_boxes[sku], _eligible_emp_count(employees, sku, zero_pairs)):
                continue
            if _norm_sku(sku) in even_skus:
                continue
            # เพดาน 1 หีบใช้ได้เฉพาะเมื่อหีบที่เหลือหลังหักล็อก ไม่เกินจำนวนคนที่ยังรับได้ (ผลตรวจ 1 ต.ค. 2026 ก3)
            # ไม่งั้นโจทย์แก้ไม่ได้ แล้ว LP ตกไป fallback ทั้งทีม
            _locked_sum = sum(int(v) for (e, s_), v in locked_map.items() if s_ == sku and e in employees)
            _free = sum(
                1 for e in employees
                if (e, sku) not in locked_map
                and (str(e).strip(), _norm_sku(sku)) not in (zero_pairs or ())
            )
            if int(target_boxes[sku]) - _locked_sum > _free:
                continue
            for emp in employees:
                if (emp, sku) in locked_map:
                    continue
                prob += x[(emp, sku)] <= max(1, cell_lo.get((emp, sku), 0))

        # LpVariable.dicts จะเอา emp_id ไปต่อเป็นชื่อ ("sf_E001|WH2") — มีปัญหาเดียวกับ x
        # จึงสร้างเองด้วยชื่อเลขล้วน แต่ยังคีย์ด้วย emp เหมือนเดิม
        shortfall = {e: pulp.LpVariable(_vname("sf", e), lowBound=0, cat="Continuous") for e in employees}
        excess = {e: pulp.LpVariable(_vname("ex", e), lowBound=0, cat="Continuous") for e in employees}
        sf_pen = {e: pulp.LpVariable(_vname("sfp", e), lowBound=0, cat="Continuous") for e in employees}
        ex_pen = {e: pulp.LpVariable(_vname("exp", e), lowBound=0, cat="Continuous") for e in employees}

        anchor_term = 0
        if lp_anchor > 0 and dpos:
            # สินค้าราคา 0 (หาราคาไม่ได้): เทอม "อยู่ใกล้ประวัติ" เคยถูกคูณด้วย 0 จน LP ไม่สนว่าหีบไปตกที่ใคร
            # → ไปตกคนที่ไม่เคยขายได้ (ผลตรวจ 5 ต.ค. 2026 ข้อ 7.5) · ใช้น้ำหนักขั้นต่ำ 1 บาท/หีบแทน
            # ไม่กระทบเงิน (ราคาจริงยังเป็น 0 ในเทอมเป้าเงิน) แค่ให้หีบอยู่ตามสัดส่วนประวัติ
            anchor_term = pulp.lpSum(
                (dpos[(e, s)] + dneg[(e, s)])
                * max(float(sku_prices.get(s, 0) or 0), 1.0)
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
            prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit, gapRel=_LP_GAP_REL))
        except Exception as e:
            logger.warning("LP solver error: %s → fallback proportional %s", e, baseline_strategy)
            return _fallback_prop()

        last_status = pulp.LpStatus[prob.status]
        if last_status == "Optimal":
            break
        # ลองรอบถัดไป (ขยายกรอบ) เฉพาะตอนโจทย์ "เป็นไปไม่ได้" เท่านั้น — ถ้าหมดเวลา (Not Solved)
        # รอบใหม่ใหญ่กว่าเดิมก็จะหมดเวลาอีก ผู้ใช้รอ 2 เท่าฟรี → ไป fallback เลย (ผลตรวจ 5 ต.ค. 2026 ข้อ 7.6)
        if last_status != "Infeasible":
            break

    # PuLP ติดป้าย "Optimal" ให้ผลที่ CBC หยุดเพราะหมดเวลาด้วย (sol_status = 2 "Solution Found")
    # ผลใช้ได้ (ผ่านทุกเงื่อนไข ยอดหีบตรงเป้า) แต่ยังไม่ใช่คำตอบที่ดีที่สุด กดใหม่อาจได้ตัวเลขต่างเล็กน้อย
    # — บอกผู้ใช้ (แบบสำรวจ R9 / OPEN_ITEMS 6.2) แทนที่จะเงียบแล้วให้เข้าใจว่าระบบสุ่ม
    if last_status == "Optimal" and getattr(prob, "sol_status", None) == pulp.LpSolutionIntegerFeasible:
        logger.warning("LP หยุดที่เวลา %ds — ใช้คำตอบที่ดีที่สุดที่หาได้ (ยังไม่พิสูจน์ว่าดีที่สุด)", time_limit)
        if _meta is not None:
            _meta["lp_time_limited"] = True

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