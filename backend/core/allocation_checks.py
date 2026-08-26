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


def detect_new_product_skus(
    sup_id: str,
    target_year: int,
    sku_list: list[str],
    df_hist: pd.DataFrame | None = None,
) -> tuple[list[str], str]:
    """
    ระบุ SKU สินค้าใหม่สำหรับแสดงป้าย UI (ไม่ขึ้นกับว่าติ๊กแบ่งเท่าหรือไม่)
    คืน (รายการ sku เรียงแล้ว, โหมด: cy_ly | fallback_hist_window | off)
    """
    sku_list = [str(s or "").strip() for s in (sku_list or []) if str(s or "").strip()]
    if not sku_list:
        return [], "off"
    cy_ok = os.path.exists(hist_calendar_year_cache_path(sup_id, target_year))
    ly_ok = os.path.exists(hist_calendar_year_cache_path(sup_id, target_year - 1))
    if cy_ok and ly_ok:
        found = skus_no_sales_cy_ly(sup_id, target_year, sku_list)
        return sorted(found), "cy_ly" if found else "off"
    if df_hist is not None and not df_hist.empty:
        found = skus_zero_team_hist_window(df_hist, sku_list)
        return sorted(found), "fallback_hist_window" if found else "off"
    return [], "off"


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
    """
    ตรวจว่าผลรวมหีบที่กระจายแล้วต่อ SKU ตรงกับ supervisor_target_boxes หรือไม่

    **ผลว่างเปล่าคือความผิดพลาดที่หนักที่สุด ไม่ใช่เคสที่ข้ามได้** — เดิมคืน [] ทันที
    เมื่อ df_alloc ว่าง ประตูสุดท้ายจึงเปิดให้ผ่าน แล้วระบบเขียนทับไฟล์ผลของงวดและ
    Excel ด้วยของว่าง แล้วตอบ 200 ตามปกติ · เกิดได้จริงตอนราคาทุกตัวเป็น 0
    (Fabric ดึงราคาไม่ได้) เพราะสัดส่วนแบ่งกลุ่มกลายเป็น 0 ทุกกลุ่มจนไม่เหลือแถวไหนเลย
    """
    if df_sku is None or df_sku.empty:
        return []
    if df_alloc is None or df_alloc.empty:
        sums = pd.Series(dtype="float64")
    else:
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


# ── I8: พนักงานที่ส่งเข้ามาต้องมีแถวกลับออกไปเสมอ (หีบ 0 ได้) ────────────────
#
# validate_allocation_vs_targets ข้างบนรวมยอด "ต่อ SKU อย่างเดียว" ไม่มีแกนพนักงาน
# ถ้าพนักงานคนหนึ่งหายไปจากผลลัพธ์ หีบของเขาจะถูกเกลี่ยไปคนอื่นแล้วยอดต่อ SKU ยังตรงเป้า
# → ด่าน I1 ผ่านฉลุยทั้งที่หีบไปตกผิดคน (พิสูจน์แล้วใน tests/test_employee_conservation.py)
#
# "ไม่มีแถว" กับ "มีแถวแต่เป็น 0" ต่างกันมากสำหรับทุกอย่างที่อยู่ปลายน้ำ:
# ตารางขั้นที่ 3 สร้างแถวจากผลลัพธ์ ถ้าไม่มีแถวก็ไม่มีคนคนนั้นบนจอ และตัวเกลี่ยอัตโนมัติ
# จะมองว่ายอดขาดแล้วยกหีบไปให้เพื่อนทันที


def _alloc_pair_key(emp_id: object, warehouse_code: object) -> tuple[str, str]:
    return (str(emp_id or "").strip(), str(warehouse_code or "").strip())


def missing_employee_alloc_keys(
    df_alloc: pd.DataFrame,
    requested: list[dict],
) -> list[dict]:
    """
    คู่ (emp_id, warehouse_code) ที่ขอมาแต่ไม่มีในผลลัพธ์

    requested = [{"emp_id", "warehouse_code", "yellow_target"}] จาก yellowTargets **ก่อนกรอง**
    คืนรายการเดิมเฉพาะตัวที่หาย พร้อม yellow_target ไว้ให้ผู้เรียกตัดสินความรุนแรง
    (เป้าเงิน 0 = หายอย่างถูกต้องตามกติกา · เป้าเงิน > 0 = ท่อแปลงข้อมูลพัง)
    """
    if not requested:
        return []
    have: set[tuple[str, str]] = set()
    if df_alloc is not None and not df_alloc.empty:
        whs = df_alloc["warehouse_code"] if "warehouse_code" in df_alloc.columns else ""
        if isinstance(whs, str):
            whs = [whs] * len(df_alloc)
        for emp, wh in zip(df_alloc["emp_id"], whs):
            have.add(_alloc_pair_key(emp, wh))

    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in requested:
        key = _alloc_pair_key(item.get("emp_id"), item.get("warehouse_code"))
        if not key[0] or key in seen:
            continue
        seen.add(key)
        if key not in have:
            out.append(
                {
                    "emp_id": key[0],
                    "warehouse_code": key[1],
                    "yellow_target": float(item.get("yellow_target") or 0.0),
                }
            )
    return out


def zero_fill_missing_employees(
    df_alloc: pd.DataFrame,
    missing: list[dict],
) -> pd.DataFrame:
    """
    เติมแถวหีบ 0 ให้พนักงานที่หายไป — ครบทุก SKU ที่มีในผลลัพธ์อยู่แล้ว

    เติมเฉพาะแถวที่หีบเป็น 0 จึงเปลี่ยนผลรวมต่อ SKU ไม่ได้เลย → I1 ยังจริงโดยโครงสร้าง
    (มีเทสพิสูจน์ว่า validate_allocation_vs_targets ให้ผลเท่าเดิมก่อน/หลังเติม)

    เมตาราย SKU (ราคา/ชื่อแบรนด์) ก๊อปจากแถวจริงของ SKU นั้น ไม่งั้นแท็บแบรนด์
    ในหน้าเว็บจะมีช่องว่างโผล่มาจากแถวที่เติม
    """
    if not missing or df_alloc is None or df_alloc.empty:
        return df_alloc

    skus = df_alloc["sku"].astype(str).str.strip().unique().tolist()
    if not skus:
        return df_alloc

    # เมตาราย SKU: เอาแถวแรกของแต่ละ SKU เป็นต้นแบบ
    meta_cols = [
        c for c in df_alloc.columns
        if c not in ("emp_id", "warehouse_code", "sku", "allocated_boxes")
    ]
    meta_by_sku = (
        df_alloc.drop_duplicates(subset=["sku"], keep="first").set_index("sku")
        if meta_cols
        else None
    )

    new_rows: list[dict] = []
    for item in missing:
        for sku in skus:
            row: dict = {
                "emp_id": item["emp_id"],
                "warehouse_code": item.get("warehouse_code", ""),
                "sku": sku,
                "allocated_boxes": 0,
            }
            for col in meta_cols:
                val = None
                if meta_by_sku is not None and sku in meta_by_sku.index:
                    val = meta_by_sku.at[sku, col]
                if pd.isna(val) if val is not None and not isinstance(val, str) else val is None:
                    val = 0 if pd.api.types.is_numeric_dtype(df_alloc[col]) else ""
                row[col] = val
            new_rows.append(row)

    if not new_rows:
        return df_alloc
    return pd.concat([df_alloc, pd.DataFrame(new_rows)], ignore_index=True)

