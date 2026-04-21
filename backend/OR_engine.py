"""
OR_engine.py — Target Box Allocation Engine
────────────────────────────────────────────
Strategies:
  L3M   — proportional ตามยอดขายย้อนหลัง 3 เดือน (fast, stable) — ต้องส่ง df_hist จาก cache 3 เดือน
  L6M   — proportional ตามยอดขายย้อนหลัง 6 เดือน (smoother baseline) — ต้องส่ง df_hist จาก cache 6 เดือน
  EVEN  — เกลี่ยเท่ากันทุกคน (fair distribution)
  PUSH  — ผลักดันคนขายน้อย (inverse ratio)
  LP    — Linear Programming ตาม yellow_target (revenue-optimal, slow)
"""

import pandas as pd
import logging
import os

logger = logging.getLogger("target_allocation.OR")


def _norm_sku(s) -> str:
    return str(s).strip() if s is not None else ""


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
) -> pd.DataFrame:
    strategy = strategy.upper()
    valid = ("L3M", "L6M", "EVEN", "PUSH", "LP")
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
            cy_ly_skus = frozenset(_norm_sku(s) for s in (new_product_skus or []))
            even_skus = cy_ly_skus

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

    # ห้าม greedy ย้ายหีบของ SKU เหล่านี้ — ไม่งั้นเป้าเงินจะดึงหีบไปมาแม้ตอนแรกแบ่งเท่าแล้ว
    skip_balance_skus = even_skus if even_new_products else None

    if strategy == "LP":
        df_out = _lp_optimize(
            df_emp_targets, df_sku, df_hist, force_min_one, locked_map, even_skus=even_skus
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
        df_out = _greedy_revenue_balancer(
            df_out,
            df_emp_targets,
            df_sku,
            locked_map,
            force_min_one=force_min_one,
            skip_balance_skus=skip_balance_skus,
        )

    return df_out

# ── Cap multiplier: คนใดคนหนึ่งจะได้หีบสูงสุดไม่เกิน CAP_MULTIPLIER × ค่าเฉลี่ย
# ค่า 3.0 หมายความว่าถ้าค่าเฉลี่ยคือ 10 หีบ คนที่ประวัติสูงที่สุดจะได้ไม่เกิน 30 หีบ
# ป้องกัน outlier กองหีบใส่คนเดียวจนผิดสัดส่วนอย่างในรูปตัวอย่าง
_CAP_MULTIPLIER = 3.0

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

        # สินค้าใหม่ (ไม่มียอดปีปฏิทินปีนี้+ปีที่แล้ว): เกลี่ยเท่ากัน แม้ strategy จะเป็น L3M/L6M/PUSH
        if strategy == "EVEN" or sku_key in even_skus or hist_sum == 0:
            weights = {e: 1.0 for e in active_employees}
        elif strategy in ("L3M", "L6M"):
            weights = {e: max(hist_by_emp[e], 0.01) for e in active_employees}
        elif strategy == "PUSH":
            max_h = max(hist_by_emp.values()) if hist_by_emp else 1.0
            weights = {e: max(max_h - hist_by_emp[e] + 0.1, 0.1) for e in active_employees}
        else:
            weights = {e: 1.0 for e in active_employees}

        total_w = sum(weights.values())

        if total_w > 0 and total > 0:
            raw = {e: total * weights[e] / total_w for e in active_employees}
            # ใช้ capped distribution แทน plain floor เพื่อป้องกัน outlier กองหีบ
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
) -> pd.DataFrame:
    if df_out.empty:
        return df_out
    locked_map = locked_map or {}
    skip_balance_skus = skip_balance_skus or set()
    target_rev = dict(zip(df_emp_targets["emp_id"], df_emp_targets["yellow_target"]))
    sku_prices = dict(zip(df_sku["sku"], df_sku["price_per_box"]))
    target_boxes = dict(zip(df_sku["sku"], df_sku["supervisor_target_boxes"]))
    emps = df_emp_targets["emp_id"].tolist()
    n_emps = len(emps)

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
            if alloc[rich_emp][sku] > floor:
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
):
    locked_map = locked_map or {}
    even_skus = even_skus or frozenset()
    try: import pulp
    except ImportError: return _proportional(
        df_emp_targets, df_sku, df_hist, "L3M", force_min_one, locked_map, even_skus=even_skus
    )

    employees    = df_emp_targets["emp_id"].tolist()
    skus         = df_sku["sku"].tolist()
    target_rev   = dict(zip(df_emp_targets["emp_id"], df_emp_targets["yellow_target"]))
    target_boxes = dict(zip(df_sku["sku"], df_sku["supervisor_target_boxes"]))
    sku_prices   = dict(zip(df_sku["sku"], df_sku["price_per_box"]))

    prob = pulp.LpProblem("BoxAllocation_LP", pulp.LpMinimize)

    # ── Anchor ให้ LP ใกล้ baseline จากประวัติขาย (ลดการกระจายแปลกๆ) ──
    # ค่า 0.0 = ปิด anchor (LP แบบเดิม), ค่า ~0.05–0.30 แนะนำ
    lp_anchor = float(os.environ.get("LP_HIST_ANCHOR", "0.15") or 0.15)
    df_base = _proportional(
        df_emp_targets, df_sku, df_hist, "L3M", force_min_one, locked_map, even_skus=even_skus
    )
    base_map = {}
    if df_base is not None and not df_base.empty:
        for _, r in df_base.iterrows():
            base_map[(str(r["emp_id"]), str(r["sku"]))] = int(r["allocated_boxes"])

    x = {}
    dpos = {}
    dneg = {}
    for emp in employees:
        for sku in skus:
            # 🔴 ถ้าช่องนี้โดนล็อกไว้ ให้ Fix ค่าไปเลย
            if (emp, sku) in locked_map:
                val = locked_map[(emp, sku)]
                x[(emp, sku)] = pulp.LpVariable(f"x_{emp}_{sku}", lowBound=val, upBound=val, cat="Integer")
            else:
                min_box = 1 if force_min_one and int(target_boxes[sku]) >= len(employees) else 0
                x[(emp, sku)] = pulp.LpVariable(f"x_{emp}_{sku}", lowBound=min_box, cat="Integer")
                # |x - base| linearization (ใช้เฉพาะที่ไม่ล็อก)
                dpos[(emp, sku)] = pulp.LpVariable(f"dp_{emp}_{sku}", lowBound=0, cat="Continuous")
                dneg[(emp, sku)] = pulp.LpVariable(f"dn_{emp}_{sku}", lowBound=0, cat="Continuous")

    shortfall = pulp.LpVariable.dicts("sf", employees, lowBound=0, cat="Continuous")
    excess    = pulp.LpVariable.dicts("ex", employees, lowBound=0, cat="Continuous")

    # Objective: เข้าเป้าเงิน + ไม่แกว่งจาก baseline
    anchor_term = 0
    if lp_anchor > 0 and dpos:
        anchor_term = pulp.lpSum(
            (dpos[(e, s)] + dneg[(e, s)]) * float(sku_prices.get(s, 0) or 0) * lp_anchor
            for (e, s) in dpos.keys()
        )
    prob += pulp.lpSum(shortfall[e] * 2 + excess[e] * 1.5 for e in employees) + anchor_term

    for sku in skus:
        prob += pulp.lpSum(x[(e, sku)] for e in employees) == int(target_boxes[sku])

    for emp in employees:
        prob += pulp.lpSum(x[(emp, s)] * sku_prices[s] for s in skus) + shortfall[emp] - excess[emp] == target_rev[emp]

    # Anchor constraints: x - base = dpos - dneg
    if lp_anchor > 0 and dpos:
        for emp in employees:
            for sku in skus:
                if (emp, sku) in locked_map:
                    continue
                base = int(base_map.get((str(emp), str(sku)), 0))
                prob += x[(emp, sku)] - base == dpos[(emp, sku)] - dneg[(emp, sku)]

    time_limit = min(60, max(15, (len(employees) * len(skus)) // 8))
    try:
        prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit))
    except Exception as e:
        # CBC solver ไม่พร้อม (solver binary หาย, permission error ฯลฯ) — fallback ทันที
        logger.warning("LP solver error: %s → fallback to L3M", e)
        return _proportional(
            df_emp_targets, df_sku, df_hist, "L3M", force_min_one, locked_map, even_skus=even_skus
        )

    # "Optimal" เท่านั้นที่เชื่อถือได้ — "Not Solved" (time-limit hit) และสถานะอื่น fallback หมด
    if pulp.LpStatus[prob.status] != "Optimal":
        logger.warning("LP status=%s → fallback to L3M", pulp.LpStatus[prob.status])
        return _proportional(
            df_emp_targets, df_sku, df_hist, "L3M", force_min_one, locked_map, even_skus=even_skus
        )

    results = [{"emp_id": emp, "sku": sku, "allocated_boxes": int(round(x[(emp, sku)].varValue))} for emp in employees for sku in skus if x[(emp, sku)].varValue is not None and x[(emp, sku)].varValue > 0.5]
    return pd.DataFrame(results) if results else _proportional(
        df_emp_targets, df_sku, df_hist, "L3M", force_min_one, locked_map, even_skus=even_skus
    )