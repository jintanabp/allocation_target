"""
ข้อมูลสาธิต — ทีมสมมติสำหรับ dev กด "ดูแบบนี้" เพื่อโชว์ระบบให้ผู้ใช้ดู

ทำไมต้องมี:
  เวลาสาธิตให้ผู้ใช้ดู ต้องเปิดทีมจริงซึ่งเห็นยอดขายและชื่อพนักงานจริงของคนอื่น
  บัญชีสาธิตทำให้เดโมได้โดยไม่แตะข้อมูลจริง และไม่ต้องต่อ Fabric เลย

กติกาของโมดูลนี้:
  1. **ไม่แตะเน็ตเด็ดขาด** — ทุกตัวเลขคำนวณจากสูตรคงที่ในไฟล์นี้
  2. **ผลลัพธ์ต้องเหมือนเดิมทุกครั้ง** ไม่มีการสุ่ม เพื่อให้เดโมซ้ำได้และเทียบผลได้
  3. **ส่ง Target Sun ไม่ได้** — กันสองชั้น: แถวในไฟล์ตั้ง can_import_targetsun=false
     และ ensure_targetsun_import_allowed ปฏิเสธรหัสทีมสาธิตซ้ำอีกชั้น
  4. มี **สามทีมในภาค/หน่วยเดียวกัน** เพื่อให้เดโมได้ทั้งโหมด "รายคน" และ
     "รวมภาค" รวมถึงการกระจายหีบรวมทั้งหน่วย ซึ่งเป็นของที่ต้องโชว์จริง

บัญชีทั้งสาม (อยู่ใน config/user_access.json):
  demosuper           — Supervisor เห็นแดชบอร์ดปกติ + เห็นทั้งสามทีมในโหมดรวมภาค
  demoadmin           — แอดมินอย่างเดียว ไม่มีตำแหน่งงาน ไม่เห็นข้อมูลทีมใด
  demosuperwithadmin  — Supervisor ที่มีสิทธิ์แอดมินซ้อน (หน้าแรกแบบ super + ปุ่มเข้าแอดมิน)
"""
from __future__ import annotations

from typing import Any

# สามทีมในภาค/หน่วยเดียวกัน — ถ้ามีทีมเดียวจะเดโมโหมดรวมภาคและกระจายรวมหน่วยไม่ได้
DEMO_SUP_IDS = ("SLDEMO1", "SLDEMO2", "SLDEMO3")
DEMO_SUP_ID = DEMO_SUP_IDS[0]          # ทีมหลักของบัญชีสาธิต
DEMO_REGION = "ภาคสาธิต"               # ชื่อภาคเฉพาะกิจ — กันไม่ให้ดึงทีมจริงมาปน
DEMO_DIVISION = "Div.B"
DEMO_UNIT = "credit"
DEMO_SUP_NAMES = {
    "SLDEMO1": "ทีมสาธิต 1 (ข้อมูลสมมติ)",
    "SLDEMO2": "ทีมสาธิต 2 (ข้อมูลสมมติ)",
    "SLDEMO3": "ทีมสาธิต 3 (ข้อมูลสมมติ)",
}

DEMO_EMAILS = frozenset({
    "demosuper@sahapat.co.th",
    "demoadmin@sahapat.co.th",
    "demosuperwithadmin@sahapat.co.th",
})

DEMO_WARNING = (
    "ข้อมูลชุดนี้เป็นข้อมูลสมมติสำหรับสาธิตเท่านั้น "
    "ไม่ใช่ยอดขายหรือเป้าจริง และส่งเข้า Target Sun ไม่ได้"
)


def is_demo_supervisor(code: Any) -> bool:
    return str(code or "").strip().upper() in DEMO_SUP_IDS


def any_demo_supervisor(codes) -> bool:
    """ชุดรหัสนี้มีทีมสาธิตปนอยู่ไหม — ใช้กันเส้นทางส่งจริง"""
    return any(is_demo_supervisor(c) for c in (codes or []))


def demo_supervisor_name(code: Any) -> str:
    return DEMO_SUP_NAMES.get(str(code or "").strip().upper(), "ทีมสาธิต")


def is_demo_email(email: Any) -> bool:
    return str(email or "").strip().lower() in DEMO_EMAILS


# ── ทีมสมมติ ────────────────────────────────────────────────────────────
# ชื่อสมมติล้วน ไม่ตรงกับพนักงานจริงคนใด · แต่ละทีมมีคนคนละชุด
_TEAM_EMPLOYEES: dict[str, list[tuple[str, str, float, str]]] = {
    "SLDEMO1": [
        ("D101", "สมชาย ใจดี", 1_250_000.0, "D900"),
        ("D102", "สมหญิง ตั้งใจ", 980_000.0, "D900"),
        ("D103", "ประเสริฐ ขยัน", 1_430_000.0, "D900"),
        ("D104", "วราภรณ์ มุ่งมั่น", 760_000.0, "D901"),
        ("D105", "อนุชา พากเพียร", 1_105_000.0, "D901"),
    ],
    "SLDEMO2": [
        ("D201", "ธนพล รุ่งเรือง", 1_020_000.0, "D902"),
        ("D202", "กมลวรรณ สุขใจ", 1_340_000.0, "D902"),
        ("D203", "ณัฐวุฒิ ก้าวหน้า", 880_000.0, "D902"),
        ("D204", "พิมพ์ชนก ยิ้มแย้ม", 1_190_000.0, "D903"),
    ],
    "SLDEMO3": [
        ("D301", "ศิริพร แข็งขัน", 1_460_000.0, "D904"),
        ("D302", "กิตติศักดิ์ ตรงเวลา", 930_000.0, "D904"),
        ("D303", "เบญจมาศ รอบคอบ", 1_075_000.0, "D905"),
        ("D304", "วีรยุทธ อดทน", 845_000.0, "D905"),
        ("D305", "อรวรรณ ละเอียด", 1_250_000.0, "D905"),
        ("D306", "ชัยวัฒน์ มานะ", 690_000.0, "D905"),
    ],
}

# (รหัส, ชื่อสินค้า, แบรนด์, ราคา/หีบ, เป้าหีบฐาน) — สินค้าชุดเดียวกันทุกทีม
# เหมือนของจริงที่ทั้งภาคขายสินค้าชุดเดียวกันแต่เป้าต่างกันตามขนาดทีม
_SKUS: list[tuple[str, str, str, float, int]] = [
    ("900101", "น้ำดื่มสาธิต 600 มล. (12 ขวด)", "สาธิต A", 96.0, 420),
    ("900102", "น้ำดื่มสาธิต 1500 มล. (6 ขวด)", "สาธิต A", 84.0, 260),
    ("900201", "บะหมี่สาธิต รสต้มยำ (30 ซอง)", "สาธิต B", 180.0, 310),
    ("900202", "บะหมี่สาธิต รสหมูสับ (30 ซอง)", "สาธิต B", 180.0, 175),
    ("900301", "ผงซักฟอกสาธิต 800 ก. (12 ถุง)", "สาธิต C", 456.0, 95),
    ("900302", "น้ำยาปรับผ้านุ่มสาธิต 500 มล. (24 ขวด)", "สาธิต C", 612.0, 60),
    ("900401", "ขนมสาธิต ถุงใหญ่ (24 ถุง)", "สาธิต D", 288.0, 140),
    ("900402", "ขนมสาธิต ถุงเล็ก (48 ถุง)", "สาธิต D", 336.0, 88),
]

# ตัวคูณเป้าต่อทีม — ให้แต่ละทีมมีขนาดต่างกันจริง เดโมรวมภาคจะได้ดูสมจริง
_TEAM_TARGET_SCALE = {"SLDEMO1": 1.0, "SLDEMO2": 0.8, "SLDEMO3": 1.25}


def _team_key(sup_id: Any) -> str:
    s = str(sup_id or "").strip().upper()
    return s if s in DEMO_SUP_IDS else DEMO_SUP_ID


def _employees_of(sup_id: str) -> list[tuple[str, str, float, str]]:
    return _TEAM_EMPLOYEES[_team_key(sup_id)]


def _target_boxes(sup_id: str, base: int) -> int:
    """เป้าหีบของ SKU นั้นสำหรับทีมนี้ — คงที่ ไม่สุ่ม"""
    return max(1, int(round(base * _TEAM_TARGET_SCALE[_team_key(sup_id)])))


def _hist_boxes(sup_id: str, emp_index: int, sku_index: int, target_boxes: int) -> int:
    """
    ประวัติขายสมมติ — สูตรคงที่ ไม่สุ่ม

    กระจายให้แต่ละคนไม่เท่ากันแบบมีแบบแผน จะได้เห็นผลของการกระจายตามสัดส่วน
    และตั้งใจให้บางคู่เป็น 0 เพื่อให้เห็นเคส "ไม่มีประวัติขาย" ในหน้าจอด้วย
    """
    seed = DEMO_SUP_IDS.index(_team_key(sup_id))
    if (emp_index + sku_index + seed) % 7 == 0:
        return 0
    weight = 3 + ((emp_index * 5 + sku_index * 3 + seed * 2) % 9)
    n_emp = len(_employees_of(sup_id))
    return max(1, int(round(target_boxes * weight / (12.0 * n_emp))))


def _team_hist_boxes(sup_id: str, sku_index: int, target_boxes: int) -> int:
    return sum(
        _hist_boxes(sup_id, i, sku_index, target_boxes)
        for i in range(len(_employees_of(sup_id)))
    )


def demo_skus(sup_id: str = DEMO_SUP_ID) -> list[dict[str, Any]]:
    out = []
    for sku, name, brand, price, base in _SKUS:
        out.append({
            "sku": sku,
            "price_per_box": float(price),
            "price_missing": False,
            "price_from_sales_history": False,
            "supervisor_target_boxes": _target_boxes(sup_id, base),
            "brand_name_thai": brand,
            "brand_name_english": "",
            "section": "999",
            "product_name_thai": name,
            "product_name_english": "",
        })
    return out


def demo_employees(sup_id: str = DEMO_SUP_ID) -> list[dict[str, Any]]:
    """
    พนักงานพร้อมเป้าเงินรายคน

    target_sun คิดแบบเดียวกับของจริง (Σ หีบ × ราคา) เพื่อให้ตัวเลขบนหน้าจอ
    สอดคล้องกันเองและตรวจย้อนได้ ไม่ใช่เลขลอย ๆ
    """
    code = _team_key(sup_id)
    out = []
    for ei, (emp_id, name, ly, wh) in enumerate(_employees_of(code)):
        target_sun = 0.0
        hist_3m = 0.0
        for si, (_sku, _n, _b, price, base) in enumerate(_SKUS):
            target = _target_boxes(code, base)
            boxes = _hist_boxes(code, ei, si, target)
            team = _team_hist_boxes(code, si, target) or 1
            target_sun += round(target * boxes / team) * price
            hist_3m += boxes * price
        out.append({
            "emp_id": emp_id,
            "emp_name": name,
            "super_code": code,
            "target_sun": round(target_sun, 2),
            "has_tga_rows": True,
            "allocation_eligible": True,
            "ly_sales": float(ly),
            "hist_avg_3m": round(hist_3m, 2),
            "warehouse_code": wh,
            "wh_split": False,
            "alloc_key": emp_id,
            "include_in_allocation": True,
            "view_only": False,
        })
    return out


def demo_history_rows(sup_id: str = DEMO_SUP_ID) -> list[dict[str, Any]]:
    """ประวัติขายราย emp×sku — ใช้เป็นน้ำหนักตอนกระจายหีบ"""
    code = _team_key(sup_id)
    rows = []
    for ei, (emp_id, *_rest) in enumerate(_employees_of(code)):
        for si, (sku, _n, _b, price, base) in enumerate(_SKUS):
            target = _target_boxes(code, base)
            boxes = _hist_boxes(code, ei, si, target)
            if boxes <= 0:
                continue
            rows.append({
                "emp_id": emp_id,
                "sku": sku,
                "hist_boxes": float(boxes),
                "hist_amount": float(boxes) * float(price),
            })
    return rows


def demo_tga_grain_rows(sup_id: str = DEMO_SUP_ID) -> list[dict[str, Any]]:
    """
    TGA grain สมมติ — ให้หน้าจอที่ต้องอ่าน grain ทำงานได้ครบ

    ไม่ได้ใช้ส่งจริง (บัญชีสาธิตส่งไม่ได้) แต่ถ้าไม่มี หน้าจอจะขึ้นว่า
    "ไม่มีเป้าใน Target Sun" ซึ่งไม่ใช่ภาพที่อยากให้ผู้ใช้เห็นตอนเดโม
    """
    code = _team_key(sup_id)
    rows = []
    for ei, (emp_id, _n, _ly, wh) in enumerate(_employees_of(code)):
        for si, (sku, _nm, _b, _price, base) in enumerate(_SKUS):
            target = _target_boxes(code, base)
            boxes = _hist_boxes(code, ei, si, target)
            if boxes <= 0:
                continue
            rows.append({
                "emp_id": emp_id,
                "sku": sku,
                "qty": float(boxes),
                "salestype": "C",
                "divisioncode": "B",
                "areacode": "1",
                "provincecode": "",
                "warehouse_code": wh,
            })
    return rows


def build_employees_payload(
    sup_id: str, target_month: int, target_year: int
) -> dict[str, Any]:
    """payload เดียวกับที่ GET /data/employees คืน — แต่เป็นข้อมูลสมมติล้วน"""
    code = _team_key(sup_id)
    return {
        "employees": demo_employees(code),
        "skus": demo_skus(code),
        "sku_warnings": [
            {"type": "demo_mode", "sku": "", "brand": "", "message": DEMO_WARNING}
        ],
        "tga_period_status": "ok",
        "supervisor_name": demo_supervisor_name(code),
        "new_product_skus": ["900402"],
        "new_products_detection_mode": "cy_ly",
        "target_read_source": "demo",
        "is_demo": True,
        "sup_id": code,
        "target_month": int(target_month),
        "target_year": int(target_year),
    }


def write_demo_caches(sup_id: str, target_month: int, target_year: int) -> None:
    """
    เขียนไฟล์ cache ชุดเดียวกับที่ Step 1 ของจริงเขียน

    จำเป็นเพราะขั้นตอนหลังจากนี้ (กระจายหีบ / ดาวน์โหลด Excel / ตรวจก่อนส่ง)
    อ่านเป้าและประวัติจากไฟล์เหล่านี้ ไม่ได้อ่านจาก payload ที่ส่งไปหน้าเว็บ
    ถ้าไม่เขียน ทีมสาธิตจะโหลดหน้าจอได้แต่กด "เริ่มคำนวณ" ไม่ได้
    (ขึ้น "ไม่พบเป้าหีบของทีมนี้")

    เขียนทับทุกครั้งได้ปลอดภัย เพราะข้อมูลคงที่ ไม่มีการสุ่ม
    """
    import csv
    import os

    from ..core.paths import (
        emp_cache_path,
        hist_cache_path,
        hist_calendar_year_cache_path,
        hist_ly_same_month_cache_path,
        hist_prev_month_cache_path,
        target_boxes_cache_path,
        target_sun_cache_path,
        tga_grain_cache_path,
    )

    code = _team_key(sup_id)
    os.makedirs("data", exist_ok=True)

    def _write(path: str, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fieldnames)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in fieldnames})
        os.replace(tmp, path)

    skus = demo_skus(code)
    _write(
        target_boxes_cache_path(code, target_month, target_year),
        [
            "sku", "price_per_box", "price_missing", "price_from_sales_history",
            "supervisor_target_boxes", "brand_name_thai", "brand_name_english",
            "section", "product_name_thai", "product_name_english",
        ],
        skus,
    )

    emps = demo_employees(code)
    _write(
        target_sun_cache_path(code, target_month, target_year),
        ["emp_id", "target_sun"],
        [{"emp_id": e["emp_id"], "target_sun": e["target_sun"]} for e in emps],
    )
    _write(
        emp_cache_path(code, target_month, target_year),
        ["emp_id", "emp_name", "super_code"],
        [
            {"emp_id": e["emp_id"], "emp_name": e["emp_name"], "super_code": code}
            for e in emps
        ],
    )

    hist = demo_history_rows(code)
    hist_cols = ["emp_id", "sku", "hist_boxes", "hist_amount"]
    # ประวัติชุดเดียวกันทุกหน้าต่างเวลา — เดโมไม่ต้องแยก 3M/6M/ปีที่แล้วให้ซับซ้อน
    # แต่ไฟล์ต้องมีครบ ไม่งั้นบางกลยุทธ์จะหาไฟล์ไม่เจอแล้วเตือนบนหน้าจอ
    for path in (
        hist_cache_path(code, target_month, target_year),
        hist_cache_path(code, target_month, target_year, n_months=6),
        hist_ly_same_month_cache_path(code, target_month, target_year),
        hist_prev_month_cache_path(code, target_month, target_year),
        hist_calendar_year_cache_path(code, target_year),
        hist_calendar_year_cache_path(code, target_year - 1),
    ):
        _write(path, hist_cols, hist)

    _write(
        tga_grain_cache_path(code, target_month, target_year),
        [
            "emp_id", "sku", "qty", "salestype", "divisioncode",
            "areacode", "provincecode", "warehouse_code",
        ],
        demo_tga_grain_rows(code),
    )


def inject_into_managers_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """
    ใส่ทีมสาธิตเข้าไปในรายชื่อทีม เพื่อให้ตัวเลือก Supervisor บนแดชบอร์ดมีให้เลือก

    แก้บนสำเนาเสมอ — payload ตัวจริงถูกแคชไว้ใช้ร่วมกันทั้งระบบ ถ้าแก้ทับจะทำให้
    ผู้ใช้จริงเห็นทีมสาธิตติดมาด้วย
    """
    if not isinstance(payload, dict):
        return payload
    out = dict(payload)

    sups = list(out.get("supervisors") or [])
    labels = list(out.get("managers") or [])
    rows = list(out.get("rows") or [])
    have_rows = {str(r.get("supervisor_code") or "") for r in rows}

    for code in DEMO_SUP_IDS:
        if code not in sups:
            sups.append(code)
        label = f"{code} (Supervisor)"
        if label not in labels:
            labels.append(label)
        if code not in have_rows:
            rows.append({"supervisor_code": code, "depend_on": "", "manager_code": ""})

    out["supervisors"] = sups
    out["managers"] = labels
    out["rows"] = rows
    return out
