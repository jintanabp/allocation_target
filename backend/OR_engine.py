"""
OR_engine.py — Target Box Allocation Engine
────────────────────────────────────────────
Strategies:
  L3M   — proportional ตามยอดขายย้อนหลัง 3 เดือน (fast, stable) — ต้องส่ง df_hist จาก cache 3 เดือน
  L6M   — proportional ตามยอดขายย้อนหลัง 6 เดือน (smoother baseline) — ต้องส่ง df_hist จาก cache 6 เดือน
  EVEN  — เกลี่ยเท่ากันทุกคน (fair distribution)
  PUSH  — ผลักดันคนขายน้อย (inverse ratio)
  LP    — Linear Programming ตาม yellow_target (revenue-optimal, slow)

น้ำหนัก L3M / L6M / PUSH (ไม่รวม EVEN):
  ผสม (1−α)×ค่าเฉลี่ยหีบ/เดือนจากช่วงย้อนหลัง + α×หีบเดือนเดียวกันปีที่แล้ว (YoY)
  α จาก env ALLOC_HIST_LYM_WEIGHT (ค่าเริ่มต้น 0.5) — ถ้าไม่มีไฟล์ cache YoY จะใช้ α=0
"""

import pandas as pd
import logging
import os

logger = logging.getLogger("target_allocation.OR")

def allocate_boxes(
    df_emp_targets: pd.DataFrame,
    df_sku: pd.DataFrame,
    df_hist: pd.DataFrame,
    strategy: str = "L3M",
    force_min_one: bool = False,
    locked_edits: list = None,
    cap_multiplier: float = None,  # override _CAP_MULTIPLIER (Custom strategy)
    df_hist_ly_same_month: pd.DataFrame | None = None,
    hist_roll_months: int = 3,
) -> pd.DataFrame:
    strategy = strategy.upper()
    valid = ("L3M", "L6M", "EVEN", "PUSH", "LP")
    if strategy not in valid:
        strategy = "L3M"

    locked_map = {}
    if locked_edits:
        for le in locked_edits:
            locked_map[(le["emp_id"], le["sku"])] = int(le["locked_boxes"])

    logger.info("allocate_boxes: strategy=%s emp=%d sku=%d force_min_one=%s locked=%d",
                strategy, len(df_emp_targets), len(df_sku), force_min_one, len(locked_map))

    # ถ้า custom strategy ส่ง cap_multiplier มา ให้ใช้ค่านั้นแทน default
    effective_cap = cap_multiplier if cap_multiplier is not None else _CAP_MULTIPLIER

    ly_w = float(os.environ.get("ALLOC_HIST_LYM_WEIGHT", "0.5") or 0)
    ly_w = max(0.0, min(1.0, ly_w))
    if df_hist_ly_same_month is None or df_hist_ly_same_month.empty:
        ly_w = 0.0
    n_roll = max(1, int(hist_roll_months or 3))
    if ly_w > 0:
        logger.info(
            "allocate_boxes: blend YoY same-month (weight=%.2f) + rolling avg/%d months",
            ly_w,
            n_roll,
        )

    if strategy == "LP":
        df_out = _lp_optimize(
            df_emp_targets,
            df_sku,
            df_hist,
            force_min_one,
            locked_map,
            df_hist_ly_same_month=df_hist_ly_same_month,
            hist_roll_months=n_roll,
            ly_same_month_weight=ly_w,
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
            df_hist_ly_same_month=df_hist_ly_same_month,
            hist_roll_months=n_roll,
            ly_same_month_weight=ly_w,
        )

        # เป้าเงินสำหรับ greedy: ให้ความสำคัญกับข้อมูลจริง (YoY + rolling) มากกว่าเป้าเหลือง ถ้าตั้งค่าไว้
        # แนวคิด: อยากให้ทุกคนโตจากปีก่อนเท่า ๆ กัน (default +10%) ถ้าเป็นไปได้
        growth_pct = float(os.environ.get("ALLOC_GROWTH_PCT", "0.10") or 0.10)
        growth_pct = max(-0.5, min(1.0, growth_pct))  # กันค่าหลุด
        hist_target_weight = float(os.environ.get("ALLOC_TARGET_HIST_WEIGHT", "0.75") or 0.75)
        hist_target_weight = max(0.0, min(1.0, hist_target_weight))

        sku_prices = dict(zip(df_sku["sku"], df_sku["price_per_box"]))
        emps = df_emp_targets["emp_id"].tolist()

        # rolling value: (hist_boxes / n_roll) × price
        roll_val = {e: 0.0 for e in emps}
        if df_hist is not None and not df_hist.empty and "hist_boxes" in df_hist.columns:
            d = df_hist.copy()
            d["price_per_box"] = d["sku"].map(sku_prices).fillna(0.0)
            d["hist_boxes"] = pd.to_numeric(d["hist_boxes"], errors="coerce").fillna(0.0)
            d["v"] = (d["hist_boxes"] / float(n_roll)) * d["price_per_box"]
            g = d.groupby("emp_id", as_index=False)["v"].sum()
            roll_val.update({str(r["emp_id"]): float(r["v"] or 0) for _, r in g.iterrows()})

        # YoY same-month value: hist_boxes × price
        yoy_val = {e: 0.0 for e in emps}
        if df_hist_ly_same_month is not None and not df_hist_ly_same_month.empty and "hist_boxes" in df_hist_ly_same_month.columns:
            d2 = df_hist_ly_same_month.copy()
            d2["price_per_box"] = d2["sku"].map(sku_prices).fillna(0.0)
            d2["hist_boxes"] = pd.to_numeric(d2["hist_boxes"], errors="coerce").fillna(0.0)
            d2["v"] = d2["hist_boxes"] * d2["price_per_box"]
            g2 = d2.groupby("emp_id", as_index=False)["v"].sum()
            yoy_val.update({str(r["emp_id"]): float(r["v"] or 0) for _, r in g2.iterrows()})

        # blended baseline (value) แล้วคูณ growth
        baseline = {e: (1.0 - ly_w) * float(roll_val.get(e, 0.0) or 0.0) + ly_w * float(yoy_val.get(e, 0.0) or 0.0) for e in emps}
        desired = {e: max(0.0, baseline[e] * (1.0 + growth_pct)) for e in emps}

        # blend กับเป้าเหลืองเดิม (yellow_target) — ถ้า weight=1 จะยึด desired ล้วน
        y_in = dict(zip(df_emp_targets["emp_id"], df_emp_targets["yellow_target"]))
        target_override = {e: (1.0 - hist_target_weight) * float(y_in.get(e, 0.0) or 0.0) + hist_target_weight * float(desired.get(e, 0.0) or 0.0) for e in emps}

        df_out = _greedy_revenue_balancer(
            df_out,
            df_emp_targets,
            df_sku,
            locked_map,
            force_min_one=force_min_one,
            target_rev_override=target_override,
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
    df_hist_ly_same_month=None,
    hist_roll_months: int = 3,
    ly_same_month_weight: float = 0.0,
):
    locked_map = locked_map or {}
    employees = df_emp_targets["emp_id"].tolist()
    target_boxes = dict(zip(df_sku["sku"], df_sku["supervisor_target_boxes"]))
    n_roll = max(1, int(hist_roll_months or 3))
    w_ly = max(0.0, min(1.0, float(ly_same_month_weight or 0.0)))

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

        # ── คำนวณ hist weight (ผสม YoY เดือนเดียวกันปีที่แล้ว + ค่าเฉลี่ย/เดือนจากช่วง 3M/6M) ──
        hist_by_emp = {}
        for emp in active_employees:
            roll = 0.0
            if not df_hist.empty:
                mask = (df_hist["emp_id"] == emp) & (df_hist["sku"] == sku)
                roll = float(df_hist.loc[mask, "hist_boxes"].sum())
            roll_avg = roll / float(n_roll)

            lym = 0.0
            if df_hist_ly_same_month is not None and not df_hist_ly_same_month.empty:
                mask2 = (df_hist_ly_same_month["emp_id"] == emp) & (df_hist_ly_same_month["sku"] == sku)
                lym = float(df_hist_ly_same_month.loc[mask2, "hist_boxes"].sum())

            blended = (1.0 - w_ly) * roll_avg + w_ly * lym
            hist_by_emp[emp] = max(float(blended), 0.0)

        hist_sum = sum(hist_by_emp.values())

        if strategy == "EVEN" or hist_sum == 0:
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
    target_rev_override: dict | None = None,
    tolerance_baht: float = 1000.0,
    max_iters: int = 50000,
) -> pd.DataFrame:
    if df_out.empty:
        return df_out
    locked_map = locked_map or {}
    if target_rev_override is not None:
        target_rev = {str(k): float(v or 0) for k, v in dict(target_rev_override).items()}
    else:
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

    # Anchor กับ baseline จากผล _proportional (ก่อน greedy) เพื่อไม่ให้บิดรูปทรงที่อิงประวัติมากเกินไป
    # หน่วยเป็น "บาท" (คิดเป็น price_per_box × การเบี่ยงจาก baseline) — ยิ่งสูงยิ่งยึด baseline มากขึ้น
    try:
        hist_anchor = float(os.environ.get("ALLOC_GREEDY_HIST_ANCHOR", "0.15") or 0.15)
    except Exception:
        hist_anchor = 0.15
    hist_anchor = max(0.0, min(2.0, hist_anchor))

    baseline = {}
    for emp in emps:
        baseline[emp] = {s: 0 for s in sku_prices.keys()}
    for _, r in df_out.iterrows():
        e = r.get("emp_id")
        s = r.get("sku")
        if e in baseline and s in sku_prices:
            baseline[e][s] = int(r.get("allocated_boxes") or 0)
        
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
        best_score = 0
        
        for sku, price in sku_prices.items():
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

                # penalty: ถ้าย้ายแล้ว "ห่าง baseline" มากขึ้น ให้หักคะแนน
                if hist_anchor > 0:
                    br = baseline[rich_emp][sku]
                    bp = baseline[poor_emp][sku]
                    cr = alloc[rich_emp][sku]
                    cp = alloc[poor_emp][sku]
                    # หลัง move: rich-1, poor+1
                    before = abs(cr - br) + abs(cp - bp)
                    after = abs((cr - 1) - br) + abs((cp + 1) - bp)
                    delta_boxes = after - before
                    penalty = float(delta_boxes) * float(price) * float(hist_anchor)
                else:
                    penalty = 0.0

                score = improvement - penalty
                if score > best_score:
                    best_score = score
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
    df_hist_ly_same_month=None,
    hist_roll_months: int = 3,
    ly_same_month_weight: float = 0.0,
):
    locked_map = locked_map or {}
    try: import pulp
    except ImportError:
        return _proportional(
            df_emp_targets,
            df_sku,
            df_hist,
            "L3M",
            force_min_one,
            locked_map,
            None,
            df_hist_ly_same_month=df_hist_ly_same_month,
            hist_roll_months=hist_roll_months,
            ly_same_month_weight=ly_same_month_weight,
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
        df_emp_targets,
        df_sku,
        df_hist,
        "L3M",
        force_min_one,
        locked_map,
        None,
        df_hist_ly_same_month=df_hist_ly_same_month,
        hist_roll_months=hist_roll_months,
        ly_same_month_weight=ly_same_month_weight,
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
            df_emp_targets,
            df_sku,
            df_hist,
            "L3M",
            force_min_one,
            locked_map,
            None,
            df_hist_ly_same_month=df_hist_ly_same_month,
            hist_roll_months=hist_roll_months,
            ly_same_month_weight=ly_same_month_weight,
        )

    # "Optimal" เท่านั้นที่เชื่อถือได้ — "Not Solved" (time-limit hit) และสถานะอื่น fallback หมด
    if pulp.LpStatus[prob.status] != "Optimal":
        logger.warning("LP status=%s → fallback to L3M", pulp.LpStatus[prob.status])
        return _proportional(
            df_emp_targets,
            df_sku,
            df_hist,
            "L3M",
            force_min_one,
            locked_map,
            None,
            df_hist_ly_same_month=df_hist_ly_same_month,
            hist_roll_months=hist_roll_months,
            ly_same_month_weight=ly_same_month_weight,
        )

    results = [{"emp_id": emp, "sku": sku, "allocated_boxes": int(round(x[(emp, sku)].varValue))} for emp in employees for sku in skus if x[(emp, sku)].varValue is not None and x[(emp, sku)].varValue > 0.5]
    return pd.DataFrame(results) if results else _proportional(
        df_emp_targets,
        df_sku,
        df_hist,
        "L3M",
        force_min_one,
        locked_map,
        None,
        df_hist_ly_same_month=df_hist_ly_same_month,
        hist_roll_months=hist_roll_months,
        ly_same_month_weight=ly_same_month_weight,
    )