"""Rules for which employees appear on the allocation dashboard."""

from __future__ import annotations

import pandas as pd


def is_allocation_eligible(has_tga_rows: bool, target_sun: float) -> bool:
    """พนักงานที่มีเป้า Target Sun งวดนี้ — เข้าขั้นกำหนดเป้าและกระจายหีบได้"""
    return bool(has_tga_rows) and float(target_sun or 0) > 0


def is_van_employee_id(emp_id: str) -> bool:
    """Van salesman codes (prefix V) are excluded from display and allocation."""
    return str(emp_id or "").strip().upper().startswith("V")


def drop_van_employees(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Remove V-prefix employees. Returns (filtered df, excluded count)."""
    if df.empty or "emp_id" not in df.columns:
        return df, 0
    ids = df["emp_id"].astype(str).str.strip()
    keep = ~ids.str.upper().str.startswith("V")
    excluded = int((~keep).sum())
    return df.loc[keep].copy(), excluded


def filter_employees_for_display(
    df: pd.DataFrame,
    ly_sales_by_emp: dict[str, float] | None = None,
) -> tuple[pd.DataFrame, int, int]:
    """
    Keep employees with a TGA target this period OR same-month-last-year sales.

    Returns (visible_df, hidden_count, ly_only_without_target_count).
    """
    if df.empty:
        return df, 0, 0
    ly = ly_sales_by_emp or {}
    ids = df["emp_id"].astype(str).str.strip()
    ly_vals = ids.map(lambda e: float(ly.get(e, 0.0) or 0.0))
    target_sun = pd.to_numeric(df["target_sun"], errors="coerce").fillna(0.0)
    has_target = df["has_tga_rows"].astype(bool) & (target_sun > 0)
    has_ly = ly_vals > 0
    visible = has_target | has_ly
    hidden = int((~visible).sum())
    ly_only = int((~has_target & has_ly).sum())
    return df.loc[visible].copy(), hidden, ly_only
