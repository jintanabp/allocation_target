import os
import pandas as pd

from .paths import hist_calendar_year_cache_path


def skus_no_sales_cy_ly(sup_id: str, target_year: int, sku_list: list[str]) -> set[str]:
    """
    SKU ที่รวมยอดหีบทั้งทีม = 0 ทั้งปีปฏิทิน target_year และปีก่อน (อิงไฟล์ hist_cy_*)
    """
    cy_path = hist_calendar_year_cache_path(sup_id, target_year)
    ly_path = hist_calendar_year_cache_path(sup_id, target_year - 1)
    if not os.path.exists(cy_path) or not os.path.exists(ly_path):
        return set()
    try:
        df_cy = pd.read_csv(cy_path, dtype={"sku": str, "emp_id": str})
        df_ly = pd.read_csv(ly_path, dtype={"sku": str, "emp_id": str})
    except Exception:
        return set()
    for df in (df_cy, df_ly):
        if "hist_boxes" not in df.columns:
            df["hist_boxes"] = 0.0
    cy_sum = df_cy.groupby("sku")["hist_boxes"].sum()
    ly_sum = df_ly.groupby("sku")["hist_boxes"].sum()
    out: set[str] = set()
    for sku in sku_list:
        s = str(sku).strip()
        c = float(cy_sum.get(s, 0) or 0)
        l = float(ly_sum.get(s, 0) or 0)
        if c <= 0 and l <= 0:
            out.add(s)
    return out


def skus_zero_team_hist_window(df_hist: pd.DataFrame, sku_list: list[str]) -> set[str]:
    """
    SKU ที่รวมยอดหีบในประวัติช่วงที่ใช้เกลี่ย (df_hist: 3M/6M) = 0 ทั้งทีม
    ใช้เป็น fallback ของ "สินค้าใหม่กระจายเท่ากัน" เฉพาะเมื่อไม่มี cache CY/LY
    """
    sku_list = [str(s or "").strip() for s in (sku_list or []) if str(s or "").strip()]
    if not sku_list:
        return set()
    if df_hist is None or df_hist.empty or "sku" not in df_hist.columns:
        return set(sku_list)
    df = df_hist.copy()
    df["sku"] = df["sku"].astype(str).str.strip()
    if "hist_boxes" not in df.columns:
        return set(sku_list)
    sums = df.groupby("sku")["hist_boxes"].sum()
    out: set[str] = set()
    for s in sku_list:
        if float(sums.get(s, 0) or 0) <= 0:
            out.add(s)
    return out


def validate_allocation_vs_targets(df_alloc: pd.DataFrame, df_sku: pd.DataFrame) -> list[dict]:
    """ตรวจว่าผลรวมหีบที่กระจายแล้วต่อ SKU ตรงกับ supervisor_target_boxes หรือไม่"""
    if df_alloc.empty or df_sku is None or df_sku.empty:
        return []
    df_a = df_alloc.copy()
    df_a["sku"] = df_a["sku"].astype(str).str.strip()
    sums = df_a.groupby("sku", as_index=True)["allocated_boxes"].sum()
    out: list[dict] = []
    for _, row in df_sku.iterrows():
        sku = str(row["sku"]).strip()
        try:
            tgt = int(round(float(row.get("supervisor_target_boxes", 0) or 0)))
        except (TypeError, ValueError):
            tgt = 0
        got = int(sums[sku]) if sku in sums.index else 0
        if got != tgt:
            out.append(
                {
                    "sku": sku,
                    "expected_boxes": tgt,
                    "allocated_sum": got,
                    "message": f"SKU {sku}: กระจายรวม {got} หีบ แต่เป้าหีบจากหัวหน้า {tgt} หีบ",
                }
            )
    return out

