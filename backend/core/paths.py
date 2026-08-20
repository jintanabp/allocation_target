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


def target_boxes_cache_path(sup_id: str, month: int, year: int) -> str:
    """
    เป้าหีบราย SKU ต่อ (sup, งวด) — แทนไฟล์ global data/target_boxes.csv เดิม
    ที่ไม่มี sup_id ในชื่อ ทำให้ทีมที่โหลดทีหลังเขียนทับของทีมก่อนหน้า
    """
    return f"data/target_boxes_{safe_id(sup_id)}_{year}_{month:02d}.csv"


def target_boxes_union_cache_path(sup_id: str, month: int, year: int) -> str:
    """
    เป้าหีบ "รวมหลายทีม" ของรอบกระจายรวมทั้งภาค — ใช้เป็นแหล่งของแถว
    "เป้าหีบ (หัวหน้า)" ใน Excel ผลกระจายเท่านั้น

    แยกชื่อจาก target_boxes_{sup}_ ของจริงโดยตั้งใจ: ไฟล์ราย sup คือหลักฐาน
    ที่ด่านก่อนส่งใช้เทียบ ถ้าเขียนทับด้วยยอดรวมภาค ทีมนั้นจะถูกตรวจด้วยเป้า
    ของทั้งภาคแล้วส่งไม่ผ่านทุกครั้ง
    """
    return f"data/target_boxes_union_{safe_id(sup_id)}_{year}_{month:02d}.csv"


def target_sun_cache_path(sup_id: str, month: int, year: int) -> str:
    """เป้า Target Sun ราย emp ต่อ (sup, งวด) — แทนไฟล์ global data/target_sun.csv เดิม"""
    return f"data/target_sun_{safe_id(sup_id)}_{year}_{month:02d}.csv"


def target_baseline_path(sup_id: str, month: int, year: int) -> str:
    """
    เป้า "ตอนเปิดงวดครั้งแรก" — เขียนครั้งเดียวแล้วไม่แตะอีก

    ไฟล์เป้าจริง (target_boxes_/target_sun_) ถูกเขียนทับทุกครั้งที่โหลดขั้นที่ 1 ใหม่
    และไม่มีสำเนาเก่าเก็บไว้เลย ถ้าเป้าต้นทางเปลี่ยน/หาย จึงไม่มีอะไรให้เทียบหรือกู้
    ไฟล์นี้คือสำเนาชุดแรกไว้เป็นหลักฐาน — อยู่คนละโฟลเดอร์เพื่อไม่ให้ปนกับ cache
    ที่ถูกล้างตามอายุ (ตัวล้างวนเฉพาะไฟล์ใน data/ ชั้นเดียว ไม่ลงโฟลเดอร์ย่อย)
    """
    return f"data/baselines/{safe_id(sup_id)}_{year}_{month:02d}.json"


def emp_cache_path(sup_id: str, month: int, year: int) -> str:
    return f"data/emp_cache_{safe_id(sup_id)}_{year}_{month:02d}.csv"


def employee_payload_cache_path(sup_id: str, month: int, year: int) -> str:
    """JSON cache ของ GET /data/employees ต่อ (sup, งวด) — ลดการยิง DAX ซ้ำ"""
    return f"data/payload_cache_{safe_id(sup_id)}_{year}_{month:02d}.json"


def tga_grain_cache_path(sup_id: str, month: int, year: int) -> str:
    """
    เป้าหมายจาก tga_target_salesman_next — grain เต็ม (SALESMAN×PRODUCT×SALESTYPE×…)
    ใช้ตอนส่งออก Excel/CSV เพื่อแยกบรรทัดตาม PROVINCECODE / AREACODE ฯลฯ ให้ตรงกับตาราง TGA
    """
    return f"data/tga_lines_{safe_id(sup_id)}_{year}_{month:02d}.csv"


def result_path(sup_id: str, month: int | None = None, year: int | None = None) -> str:
    """
    ผลกระจายล่าสุดของทีม — ต้องผูกกับงวดด้วย

    ชื่อเดิมไม่มีเดือน/ปี ทำให้กระจายสองงวดของซุปเดียวกันพร้อมกันเขียนทับกัน
    แล้ว create_target_excel ที่อ่านไฟล์นี้ต่อทันทีอาจได้ข้อมูลของอีกงวด
    (atomic_write_csv กันได้แค่ "อ่านไฟล์ครึ่งใบ" ไม่ได้กันสองงวดชนกัน)

    ไม่ระบุงวด = ชื่อเดิม — ใช้เฉพาะโค้ดเก่าที่ยังไม่มีบริบทงวด
    """
    if month and year:
        return f"data/final_allocation_{safe_id(sup_id)}_{int(year)}_{int(month):02d}.csv"
    return f"data/final_allocation_{safe_id(sup_id)}.csv"


def excel_path(sup_id: str, month: int | None = None, year: int | None = None) -> str:
    """Excel ผลกระจาย — ผูกกับงวดด้วยเหตุผลเดียวกับ result_path"""
    if month and year:
        return f"data/Final_Dashboard_{safe_id(sup_id)}_{int(year)}_{int(month):02d}.xlsx"
    return f"data/Final_Dashboard_{safe_id(sup_id)}.xlsx"


def latest_excel_path_for_sup(sup_id: str) -> str | None:
    """
    ไฟล์ Excel ผลกระจายงวดล่าสุดของทีม (ไม่รู้งวด — ใช้ตอน download แบบ backward compat)
    คืน None ถ้าไม่มีเลย
    """
    import glob
    import os

    sid = safe_id(sup_id)
    matches = sorted(glob.glob(f"data/Final_Dashboard_{sid}_*.xlsx"), reverse=True)
    for p in matches:
        if os.path.isfile(p):
            return p
    legacy = f"data/Final_Dashboard_{sid}.xlsx"
    return legacy if os.path.isfile(legacy) else None


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


def allocation_snapshot_path(sup_id: str, month: int, year: int) -> str:
    """JSON snapshot ผลกระจายหีบต่อ (sup, งวด)"""
    return f"data/allocations/{safe_id(sup_id)}_{int(year)}_{int(month):02d}.json"

