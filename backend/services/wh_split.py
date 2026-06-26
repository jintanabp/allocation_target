"""
แยกพนักงานตาม warehouse จาก TGA grain — ใช้เมื่อพนักงานมี ≥ 2 คลังในงวดนั้น
"""

from __future__ import annotations

from typing import Any

import pandas as pd

_ALLOC_SEP = "|"


def alloc_key(emp_id: str, warehouse_code: str | None = None, *, wh_split: bool = False) -> str:
    emp = str(emp_id or "").strip()
    wh = str(warehouse_code or "").strip()
    if not wh_split or not wh:
        return emp
    return f"{emp}{_ALLOC_SEP}{wh}"


def parse_alloc_key(key: str) -> tuple[str, str]:
    raw = str(key or "").strip()
    if _ALLOC_SEP in raw:
        emp, wh = raw.split(_ALLOC_SEP, 1)
        return emp.strip(), wh.strip()
    return raw, ""


def _norm_wh(val: Any) -> str:
    return str(val or "").strip()


def warehouses_per_emp_from_tga(df_tga: pd.DataFrame | None) -> dict[str, list[str]]:
    """emp_id → รายการ WH ที่ไม่ซ้ำ (เรียง) จาก TGA grain"""
    if df_tga is None or df_tga.empty:
        return {}
    d = df_tga.copy()
    d["emp_id"] = d["emp_id"].astype(str).str.strip()
    if "warehouse_code" not in d.columns:
        return {}
    d["warehouse_code"] = d["warehouse_code"].map(_norm_wh)
    out: dict[str, set[str]] = {}
    for emp, wh in zip(d["emp_id"], d["warehouse_code"]):
        if not emp:
            continue
        out.setdefault(emp, set()).add(wh)
    return {e: sorted(ws, key=lambda x: (x == "", x)) for e, ws in out.items()}


def tga_value_by_emp_wh(
    df_tga: pd.DataFrame | None,
    price_by_sku: dict[str, float],
) -> dict[tuple[str, str], float]:
    """มูลค่าเป้า TGA รวมต่อ (emp_id, warehouse_code)"""
    if df_tga is None or df_tga.empty:
        return {}
    d = df_tga.copy()
    d["emp_id"] = d["emp_id"].astype(str).str.strip()
    d["sku"] = d["sku"].astype(str).str.strip()
    d["warehouse_code"] = d.get("warehouse_code", "").map(_norm_wh)
    d["qty"] = pd.to_numeric(d.get("qty", 0), errors="coerce").fillna(0.0)
    d["price"] = d["sku"].map(lambda s: float(price_by_sku.get(str(s).strip(), 0.0)))
    d["line_value"] = d["qty"] * d["price"]
    g = d.groupby(["emp_id", "warehouse_code"], as_index=False)["line_value"].sum()
    return {
        (str(r["emp_id"]).strip(), _norm_wh(r["warehouse_code"])): round(float(r["line_value"]), 2)
        for _, r in g.iterrows()
    }


def _split_amount(total: float, shares: dict[str, float]) -> dict[str, float]:
    if not shares:
        return {}
    tot = sum(max(0.0, v) for v in shares.values())
    if tot <= 0:
        n = len(shares)
        return {k: round(total / n, 2) if n else 0.0 for k in shares}
    out: dict[str, float] = {}
    distributed = 0.0
    keys = list(shares.keys())
    for i, k in enumerate(keys):
        if i == len(keys) - 1:
            out[k] = round(max(0.0, total - distributed), 2)
        else:
            part = round(total * max(0.0, shares[k]) / tot, 2)
            out[k] = part
            distributed += part
    return out


def expand_employee_rows(
    rows: list[dict[str, Any]],
    df_tga_granular: pd.DataFrame | None,
    price_by_sku: dict[str, float],
    *,
    ly_amount_by_emp_wh: dict[tuple[str, str], float] | None = None,
    avg3_amount_by_emp_wh: dict[tuple[str, str], float] | None = None,
) -> list[dict[str, Any]]:
    """
    ขยายแถวพนักงานตาม WH จาก TGA — พนักงานที่มีคลังเดียวไม่เปลี่ยนหน้าตา (wh_split=False)
    """
    wh_map = warehouses_per_emp_from_tga(df_tga_granular)
    value_map = tga_value_by_emp_wh(df_tga_granular, price_by_sku)
    out: list[dict[str, Any]] = []

    for row in rows:
        emp = str(row.get("emp_id") or "").strip()
        if not emp:
            continue
        whs = wh_map.get(emp) or []
<<<<<<< Updated upstream
        unique_whs = sorted(set(whs), key=lambda x: (x == "", x))
        if len(unique_whs) < 2:
            nr = dict(row)
            nr["warehouse_code"] = str(nr.get("warehouse_code") or (unique_whs[0] if unique_whs else "")).strip()
=======
        distinct = [w for w in whs if w != ""] if len(whs) > 1 else whs
        # แยกเมื่อมี ≥ 2 ค่า WH ที่ต่างกัน (รวมค่าว่างถ้ามี)
        unique_whs = sorted(set(whs), key=lambda x: (x == "", x))
        if len(unique_whs) < 2:
            nr = dict(row)
            nr["warehouse_code"] = str(nr.get("warehouse_code") or unique_whs[0] if unique_whs else "").strip()
>>>>>>> Stashed changes
            nr["wh_split"] = False
            nr["alloc_key"] = alloc_key(emp, nr.get("warehouse_code"), wh_split=False)
            out.append(nr)
            continue

        shares = {w: value_map.get((emp, w), 0.0) for w in unique_whs}
        ts_total = float(row.get("target_sun") or 0.0)
        ly_total = float(row.get("ly_sales") or 0.0)
        avg_total = float(row.get("hist_avg_3m") or 0.0)
        ts_parts = _split_amount(ts_total, shares)
<<<<<<< Updated upstream
        ly_weights = (
            {w: float(ly_amount_by_emp_wh.get((emp, w), 0.0)) for w in unique_whs}
            if ly_amount_by_emp_wh
            else shares
        )
        avg_weights = (
            {w: float(avg3_amount_by_emp_wh.get((emp, w), 0.0)) for w in unique_whs}
            if avg3_amount_by_emp_wh
            else shares
        )
=======
        ly_weights = {w: float(ly_amount_by_emp_wh.get((emp, w), 0.0)) for w in unique_whs} if ly_amount_by_emp_wh else shares
        avg_weights = {w: float(avg3_amount_by_emp_wh.get((emp, w), 0.0)) for w in unique_whs} if avg3_amount_by_emp_wh else shares
>>>>>>> Stashed changes
        ly_parts = _split_amount(ly_total, ly_weights)
        avg_parts = _split_amount(avg_total, avg_weights)

        for w in unique_whs:
            nr = dict(row)
            nr["warehouse_code"] = w
            nr["wh_split"] = True
            nr["wh_group_id"] = emp
            nr["target_sun"] = ts_parts.get(w, 0.0)
            nr["ly_sales"] = ly_parts.get(w, 0.0)
            nr["hist_avg_3m"] = avg_parts.get(w, 0.0)
            nr["alloc_key"] = alloc_key(emp, w, wh_split=True)
            out.append(nr)

    return out


def prepare_optimize_targets(df_targets: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, tuple[str, str]]]:
    """
    เตรียม df สำหรับ OR: คอลัมน์ or_emp_id = alloc_key, เก็บแมปกลับ real emp+wh
    คืน (df_prepared, reverse_map: or_emp_id → (emp_id, warehouse_code))
    """
    df = df_targets.copy()
    if "warehouse_code" not in df.columns:
        df["warehouse_code"] = ""
    df["warehouse_code"] = df["warehouse_code"].fillna("").astype(str).str.strip()
    reverse: dict[str, tuple[str, str]] = {}
    or_ids: list[str] = []
    for _, r in df.iterrows():
        emp = str(r.get("emp_id") or "").strip()
        wh = str(r.get("warehouse_code") or "").strip()
        wh_split = bool(wh)
        key = alloc_key(emp, wh, wh_split=wh_split)
        or_ids.append(key)
        reverse[key] = (emp, wh)
    df["or_emp_id"] = or_ids
    return df, reverse


def split_hist_dataframe(
    df_hist: pd.DataFrame,
    reverse_map: dict[str, tuple[str, str]],
    value_shares: dict[tuple[str, str], float],
) -> pd.DataFrame:
    """แตกประวัติ emp×sku ไปยัง alloc_key ตามสัดส่วนมูลค่า TGA ต่อ WH"""
    if df_hist is None or df_hist.empty or not reverse_map:
        return df_hist

    by_emp: dict[str, list[tuple[str, str, float]]] = {}
    for or_id, (emp, wh) in reverse_map.items():
        if _ALLOC_SEP not in or_id:
            continue
        share = float(value_shares.get((emp, wh), 0.0))
        by_emp.setdefault(emp, []).append((or_id, wh, share))

    if not by_emp:
        return df_hist

    parts: list[pd.DataFrame] = []
    untouched = df_hist[~df_hist["emp_id"].astype(str).str.strip().isin(by_emp.keys())].copy()
    if not untouched.empty:
        parts.append(untouched)

    amount_cols = [c for c in ("hist_boxes", "hist_amount") if c in df_hist.columns]
    for emp, splits in by_emp.items():
        sub = df_hist[df_hist["emp_id"].astype(str).str.strip() == emp].copy()
        if sub.empty:
            continue
        tot_share = sum(s for _, _, s in splits)
        for or_id, _wh, share in splits:
            row = sub.copy()
            row["emp_id"] = or_id
            if tot_share > 0 and share >= 0:
                ratio = share / tot_share
            else:
                ratio = 1.0 / len(splits)
            for c in amount_cols:
                row[c] = pd.to_numeric(row[c], errors="coerce").fillna(0.0) * ratio
            parts.append(row)

    if not parts:
        return df_hist
    return pd.concat(parts, ignore_index=True)


def restore_allocation_emp_ids(
    df_alloc: pd.DataFrame,
    reverse_map: dict[str, tuple[str, str]],
) -> pd.DataFrame:
    """แปลง or_emp_id กลับเป็น emp_id + warehouse_code"""
    if df_alloc is None or df_alloc.empty:
        return df_alloc
    out = df_alloc.copy()
    emps: list[str] = []
    whs: list[str] = []
    for e in out["emp_id"].astype(str).str.strip():
        emp, wh = reverse_map.get(e, parse_alloc_key(e))
        emps.append(emp)
        whs.append(wh)
    out["emp_id"] = emps
    out["warehouse_code"] = whs
    return out


def value_shares_for_reverse_map(
    reverse_map: dict[str, tuple[str, str]],
    value_map: dict[tuple[str, str], float],
) -> dict[tuple[str, str], float]:
    shares: dict[tuple[str, str], float] = {}
    for _or_id, (emp, wh) in reverse_map.items():
        if wh:
            shares[(emp, wh)] = float(value_map.get((emp, wh), 0.0))
    return shares
