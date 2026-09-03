"""
ทะเบียน capability ของหน้าแอดมิน — แหล่งความจริงเดียวว่า "หน้าไหนคู่กับสิทธิ์ตัวไหน"

ทำไมอยู่ในโค้ดไม่ใช่ config: รายการนี้ผูกกับแท็บและ endpoint ที่มีอยู่จริง
ถ้าให้แก้ใน JSON ได้ จะตั้งสิทธิ์ให้ของที่ไม่มีอยู่ แล้วผู้ใช้เห็นแท็บว่าง ๆ
ส่วน "ใครได้สิทธิ์ตัวไหน" ต่างหากที่อยู่ใน config/admin_permissions.json และ dev แก้ได้จากหน้าแอดมิน
"""

from __future__ import annotations

from typing import Any

# บทบาทที่ตั้งค่าได้ — dev ไม่อยู่ในนี้โดยตั้งใจ (ได้ทุกอย่างเสมอ ถอดไม่ได้)
CONFIGURABLE_ROLES = ("head_admin", "admin", "marketing")

ROLE_LABELS = {
    "head_admin": "หัวหน้าแอดมิน",
    "admin": "แอดมิน",
    "marketing": "มาร์เก็ตติ้ง",
}

# allowed_roles = บทบาทที่ "มอบสิทธิ์นี้ให้ได้"
#   ว่าง () = ล็อกตายตัว เป็นของ dev เท่านั้น มอบให้ใครไม่ได้
CAPABILITIES: dict[str, dict[str, Any]] = {
    "users": {
        "label": "รายชื่อผู้ใช้",
        "desc": "ดูและแก้ทะเบียนผู้ใช้ตามขอบเขตของตัวเอง",
        "tab": "users",
        "allowed_roles": ("head_admin", "admin"),
    },
    "roles": {
        "label": "ผู้ดูแลระบบ",
        "desc": "เพิ่ม/ถอดสิทธิ์แอดมินคนอื่น — ให้แอดมินธรรมดาไม่ได้ เพราะจะยกระดับตัวเองได้",
        "tab": "roles",
        "allowed_roles": ("head_admin",),
    },
    "sl_links": {
        "label": "ผูกรหัส SL",
        "desc": "ผูกรหัสซุปเก่ากับรหัสใหม่",
        "tab": "slLinks",
        "allowed_roles": ("head_admin", "admin", "marketing"),
    },
    "sku_links": {
        "label": "ผูกรหัส SKU",
        "desc": "ผูกรหัสสินค้าเก่ากับรหัสใหม่เพื่อรวมประวัติขาย",
        "tab": "skuLinks",
        "allowed_roles": ("head_admin", "admin", "marketing"),
    },
    "allocations": {
        "label": "ผลการกระจาย",
        "desc": "ดู snapshot ผลกระจายของทีมในขอบเขต",
        "tab": "allocations",
        "allowed_roles": ("head_admin", "admin"),
    },
    "usage_logs": {
        "label": "บันทึกการใช้งาน",
        "desc": "อ่าน log การใช้งานและการส่งเข้า Target Sun",
        "tab": "usageLogs",
        "allowed_roles": ("head_admin", "admin"),
    },
    "usage_summary": {
        "label": "สรุปการใช้งาน",
        "desc": "ตารางสรุปว่าทีมไหนใช้ระบบแล้วบ้าง",
        "tab": "usageSummary",
        "allowed_roles": ("head_admin", "admin"),
    },
    "team": {
        "label": "ทีมพนักงาน",
        "desc": "ดูรายชื่อพนักงานในทีม",
        "tab": "team",
        "allowed_roles": ("head_admin", "admin", "marketing"),
    },
    "emp_moves": {
        "label": "ย้ายพนักงาน",
        "desc": (
            "ย้ายพนักงานไปเกลี่ยเป้ากับทีมอื่น — ย้ายได้ทุกทีมไม่จำกัดขอบเขต "
            "และการย้าย 1 คนเปลี่ยนยอดรวมของทั้งทีมต้นทางและปลายทาง"
        ),
        "tab": "empMoves",
        "allowed_roles": ("head_admin", "admin"),
    },
    "data_source": {
        "label": "แหล่งข้อมูล",
        "desc": (
            "ตั้งค่าปลายทาง Target Sun · ล้าง cache · rebuild ลำดับชั้น · ลบผลกระจาย · "
            "export รายชื่อทั้งไฟล์ — มีผลทั้งระบบ จึงล็อกไว้ให้ dev เท่านั้น"
        ),
        "tab": "data",
        "allowed_roles": (),
    },
}

# ค่าตั้งต้น = พฤติกรรมก่อนมีตารางสิทธิ์ (ยกมาจาก ADMIN_TABS_* ใน frontend/app.js)
DEFAULT_ROLE_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "head_admin": (
        "users", "roles", "sl_links", "sku_links",
        "allocations", "usage_logs", "usage_summary", "team",
    ),
    "admin": (
        "users", "sl_links", "sku_links",
        "allocations", "usage_logs", "usage_summary", "team",
    ),
    "marketing": ("team", "sku_links", "sl_links"),
}


def all_capability_keys() -> tuple[str, ...]:
    return tuple(CAPABILITIES.keys())


def is_known_capability(cap: str) -> bool:
    return str(cap or "").strip() in CAPABILITIES


def is_grantable(cap: str) -> bool:
    """มอบให้บทบาทใดได้บ้างไหม — False แปลว่าเป็นของ dev อย่างเดียว"""
    meta = CAPABILITIES.get(str(cap or "").strip())
    return bool(meta and meta.get("allowed_roles"))


def can_grant(cap: str, role: str) -> bool:
    meta = CAPABILITIES.get(str(cap or "").strip())
    if not meta:
        return False
    return str(role or "").strip() in (meta.get("allowed_roles") or ())


def tab_for(cap: str) -> str:
    meta = CAPABILITIES.get(str(cap or "").strip())
    return str(meta.get("tab") or "") if meta else ""


def registry_for_api() -> list[dict[str, Any]]:
    """รูปแบบที่หน้าแอดมินใช้วาดตาราง role x capability"""
    out: list[dict[str, Any]] = []
    for key, meta in CAPABILITIES.items():
        allowed = tuple(meta.get("allowed_roles") or ())
        out.append(
            {
                "key": key,
                "label": meta.get("label") or key,
                "desc": meta.get("desc") or "",
                "tab": meta.get("tab") or "",
                "grantable": bool(allowed),
                "allowed_roles": list(allowed),
            }
        )
    return out
