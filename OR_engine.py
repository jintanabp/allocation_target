"""
OR_engine.py — Target Box Allocation Engine
────────────────────────────────────────────
Strategies:
  L3M   — proportional ตามยอดขายเฉลี่ย 3 เดือนล่าสุด (fast, stable)
  L6M   — proportional ตามยอดขายเฉลี่ย 6 เดือนล่าสุด (smoother baseline)
  EVEN  — เกลี่ยเท่ากันทุกคน (fair distribution)
  PUSH  — ผลักดันคนขายน้อย (inverse ratio)
  LP    — Linear Programming ตาม yellow_target (revenue-optimal, slow)
"""

import pandas as pd
import logging

logger = logging.getLogger("target_allocation.OR")

def allocate_boxes(
    df_emp_targets: pd.DataFrame,
    df_sku: pd.DataFrame,
    df_hist: pd.DataFrame,
    strategy: str = "L3M",
    force_min_one: bool = False,
    locked_edits: list = None,
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

    if strategy == "LP":
        df_out = _lp_optimize(df_emp_targets, df_sku, df_hist, force_min_one, locked_map)
    else:
        df_out = _proportional(df_emp_targets, df_sku, df_hist, strategy, force_min_one, locked_map)
        df_out = _greedy_revenue_balancer(df_out, df_emp_targets, df_sku, locked_map)

    return df_out

# ── Cap multiplier: คนใดคนหนึ่งจะได้หีบสูงสุดไม่เกิน CAP_MULTIPLIER × ค่าเฉลี่ย
# ค่า 3.0 หมายความว่าถ้าค่าเฉลี่ยคือ 10 หีบ คนที่ประวัติสูงที่สุดจะได้ไม่เกิน 30 หีบ
# ป้องกัน outlier กองหีบใส่คนเดียวจนผิดสัดส่วนอย่างในรูปตัวอย่าง
_CAP_MULTIPLIER = 3.0

def _cap_and_redistribute(raw: dict, total: int) -> dict:
    """
    จำกัด weight outlier: ถ้าใครได้ > mean * CAP ให้ cap แล้วกระจายส่วนเกินให้คนที่เหลือ
    ทำซ้ำจนไม่มีคนเกิน cap (max 10 รอบ)
    """
    emps = list(raw.keys())
    if not emps or total <= 0:
        return {e: 0 for e in emps}

    allocated = dict(raw)
    for _ in range(10):
        mean_alloc = total / len(emps)
        cap = mean_alloc * _CAP_MULTIPLIER
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


def _proportional(df_emp_targets, df_sku, df_hist, strategy, force_min_one=False, locked_map=None):
    locked_map = locked_map or {}
    employees = df_emp_targets["emp_id"].tolist()
    target_boxes = dict(zip(df_sku["sku"], df_sku["supervisor_target_boxes"]))

    # Pre-compute brand map สำหรับ intra-brand fairness
    brand_of = dict(zip(df_sku["sku"], df_sku.get("brand_name_thai", df_sku.get("brand_name_english", ""))))

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
        hist_by_emp = {}
        for emp in active_employees:
            if df_hist.empty:
                val = 0.0
            else:
                mask = (df_hist["emp_id"] == emp) & (df_hist["sku"] == sku)
                val = df_hist.loc[mask, "hist_boxes"].sum()
            hist_by_emp[emp] = max(float(val), 0.0)

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
            floored = _cap_and_redistribute(raw, total)
        else:
            floored = {e: 0 for e in active_employees}

        for emp in active_employees:
            boxes = floored[emp] + base_box
            if boxes > 0:
                results.append({"emp_id": emp, "sku": sku, "allocated_boxes": boxes})

    return pd.DataFrame(results) if results else pd.DataFrame(columns=["emp_id", "sku", "allocated_boxes"])

def _greedy_revenue_balancer(df_out: pd.DataFrame, df_emp_targets: pd.DataFrame, df_sku: pd.DataFrame, locked_map=None) -> pd.DataFrame:
    if df_out.empty: return df_out
    locked_map = locked_map or {}
    target_rev = dict(zip(df_emp_targets["emp_id"], df_emp_targets["yellow_target"]))
    sku_prices = dict(zip(df_sku["sku"], df_sku["price_per_box"]))
    emps = df_emp_targets["emp_id"].tolist()
    
    alloc = {}
    for emp in emps: alloc[emp] = {s: 0 for s in sku_prices.keys()}
    for _, r in df_out.iterrows():
        if r["emp_id"] in alloc and r["sku"] in sku_prices: 
            alloc[r["emp_id"]][r["sku"]] = r["allocated_boxes"]
        
    def get_current_rev(emp): return sum(alloc[emp][s] * sku_prices[s] for s in sku_prices)

    prev_total_error = float("inf")
    stall_count = 0
    for _ in range(5000):
        diffs = {e: get_current_rev(e) - target_rev.get(e, 0) for e in emps}
        rich_emp = max(diffs, key=diffs.get)
        poor_emp = min(diffs, key=diffs.get)

        # หยุดเมื่อไม่มีใคร over และไม่มีใคร under พร้อมกัน
        if diffs[rich_emp] <= 0 or diffs[poor_emp] >= 0:
            break

        total_error = abs(diffs[rich_emp]) + abs(diffs[poor_emp])
        if total_error >= prev_total_error:
            stall_count += 1
            if stall_count >= 3:
                break
        else:
            stall_count = 0
        prev_total_error = total_error
            
        best_sku_to_move = None
        best_improvement = 0
        
        for sku, price in sku_prices.items():
            # 🔴 ข้ามการสลับหีบที่คนพิมพ์แก้ไขไว้แล้ว (ห้ามยุ่งเด็ดขาด)
            if (rich_emp, sku) in locked_map or (poor_emp, sku) in locked_map:
                continue

            if alloc[rich_emp][sku] > 0:
                current_error = abs(diffs[rich_emp]) + abs(diffs[poor_emp])
                new_rich_diff = diffs[rich_emp] - price
                new_poor_diff = diffs[poor_emp] + price
                new_error = abs(new_rich_diff) + abs(new_poor_diff)
                
                improvement = current_error - new_error
                if improvement > best_improvement:
                    best_improvement = improvement
                    best_sku_to_move = sku
                    
        if best_sku_to_move is None: break
            
        alloc[rich_emp][best_sku_to_move] = max(0, alloc[rich_emp][best_sku_to_move] - 1)
        alloc[poor_emp][best_sku_to_move] += 1

    final_results = [{"emp_id": emp, "sku": sku, "allocated_boxes": boxes} for emp in emps for sku, boxes in alloc[emp].items() if boxes > 0]
    return pd.DataFrame(final_results)

def _lp_optimize(df_emp_targets, df_sku, df_hist, force_min_one=False, locked_map=None):
    locked_map = locked_map or {}
    try: import pulp
    except ImportError: return _proportional(df_emp_targets, df_sku, df_hist, "L3M", force_min_one, locked_map)

    employees    = df_emp_targets["emp_id"].tolist()
    skus         = df_sku["sku"].tolist()
    target_rev   = dict(zip(df_emp_targets["emp_id"], df_emp_targets["yellow_target"]))
    target_boxes = dict(zip(df_sku["sku"], df_sku["supervisor_target_boxes"]))
    sku_prices   = dict(zip(df_sku["sku"], df_sku["price_per_box"]))

    prob = pulp.LpProblem("BoxAllocation_LP", pulp.LpMinimize)

    x = {}
    for emp in employees:
        for sku in skus:
            # 🔴 ถ้าช่องนี้โดนล็อกไว้ ให้ Fix ค่าไปเลย
            if (emp, sku) in locked_map:
                val = locked_map[(emp, sku)]
                x[(emp, sku)] = pulp.LpVariable(f"x_{emp}_{sku}", lowBound=val, upBound=val, cat="Integer")
            else:
                min_box = 1 if force_min_one and int(target_boxes[sku]) >= len(employees) else 0
                x[(emp, sku)] = pulp.LpVariable(f"x_{emp}_{sku}", lowBound=min_box, cat="Integer")

    shortfall = pulp.LpVariable.dicts("sf", employees, lowBound=0, cat="Continuous")
    excess    = pulp.LpVariable.dicts("ex", employees, lowBound=0, cat="Continuous")

    prob += pulp.lpSum(shortfall[e] * 2 + excess[e] * 1.5 for e in employees)

    for sku in skus:
        prob += pulp.lpSum(x[(e, sku)] for e in employees) == int(target_boxes[sku])

    for emp in employees:
        prob += pulp.lpSum(x[(emp, s)] * sku_prices[s] for s in skus) + shortfall[emp] - excess[emp] == target_rev[emp]

    time_limit = min(60, max(15, (len(employees) * len(skus)) // 8))
    try:
        prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit))
    except Exception as e:
        # CBC solver ไม่พร้อม (solver binary หาย, permission error ฯลฯ) — fallback ทันที
        logger.warning("LP solver error: %s → fallback to L3M", e)
        return _proportional(df_emp_targets, df_sku, df_hist, "L3M", force_min_one, locked_map)

    # "Optimal" เท่านั้นที่เชื่อถือได้ — "Not Solved" (time-limit hit) และสถานะอื่น fallback หมด
    if pulp.LpStatus[prob.status] != "Optimal":
        logger.warning("LP status=%s → fallback to L3M", pulp.LpStatus[prob.status])
        return _proportional(df_emp_targets, df_sku, df_hist, "L3M", force_min_one, locked_map)

    results = [{"emp_id": emp, "sku": sku, "allocated_boxes": int(round(x[(emp, sku)].varValue))} for emp in employees for sku in skus if x[(emp, sku)].varValue is not None and x[(emp, sku)].varValue > 0.5]
    return pd.DataFrame(results) if results else _proportional(df_emp_targets, df_sku, df_hist, "L3M", force_min_one, locked_map)