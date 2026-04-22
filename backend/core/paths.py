import re


def safe_id(s: str) -> str:
    """Sanitize sup_id / strategy สำหรับใส่ใน filename"""
    return re.sub(r"[^A-Za-z0-9_]", "_", str(s))


def hist_cache_path(sup_id: str, month: int, year: int, n_months: int = 3) -> str:
    """
    3 เดือน: data/hist_cache_{sup}_{year}_{mm}.csv (รูปแบบเดิม)
    6 เดือน: data/hist_cache_{sup}_{year}_{mm}_6m.csv (สำหรับกลยุทธ์ L6M)
    """
    base = f"data/hist_cache_{safe_id(sup_id)}_{year}_{month:02d}"
    if n_months == 3:
        return f"{base}.csv"
    return f"{base}_{int(n_months)}m.csv"


def hist_ly_same_month_cache_path(sup_id: str, month: int, year: int) -> str:
    """ยอดหีบ emp×sku เดือนเดียวกับงวดที่เลือก แต่ปีที่แล้ว"""
    return f"data/hist_lysm_{safe_id(sup_id)}_{year}_{month:02d}.csv"


def hist_prev_month_cache_path(sup_id: str, month: int, year: int) -> str:
    """ยอดหีบ emp×sku เดือนล่าสุดก่อนงวดที่เลือก (เดือนที่แล้ว)"""
    return f"data/hist_prev_{safe_id(sup_id)}_{year}_{month:02d}.csv"


def hist_calendar_year_cache_path(sup_id: str, calendar_year: int) -> str:
    """ยอดหีบ emp×sku รวมทั้งปีปฏิทิน (Jan–Dec) — ใช้ตรวจสินค้าใหม่"""
    return f"data/hist_cy_{safe_id(sup_id)}_{int(calendar_year)}.csv"


def emp_cache_path(sup_id: str, month: int, year: int) -> str:
    return f"data/emp_cache_{safe_id(sup_id)}_{year}_{month:02d}.csv"


def result_path(sup_id: str) -> str:
    return f"data/final_allocation_{safe_id(sup_id)}.csv"


def excel_path(sup_id: str) -> str:
    return f"data/Final_Dashboard_{safe_id(sup_id)}.xlsx"


def excel_export_path(sup_id: str, brand: str) -> str:
    """
    ไฟล์ Excel สำหรับ download/export ตามแบรนด์
    - ใช้แยกไฟล์เพื่อกันความสับสน/แคช เมื่อ export หลายแบรนด์สลับกัน
    """
    brand_safe = safe_id(brand) if brand and brand != "ALL" else "ALL"
    return f"data/Target_{safe_id(sup_id)}_{brand_safe}.xlsx"


def export_result_path(sup_id: str, brand: str) -> str:
    brand_safe = safe_id(brand) if brand != "ALL" else "ALL"
    return f"data/export_{safe_id(sup_id)}_{brand_safe}.csv"

