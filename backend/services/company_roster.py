"""
รายชื่อพนักงานทั้งบริษัท — "ตัวหาร" ของรายงานสรุปการใช้งาน

ทำไมต้องมาจาก Fabric ไม่ใช่ไฟล์ในเครื่อง:
  ไฟล์แคชในเครื่อง (emp_cache_* / tga_lines_*) มีเฉพาะทีมที่เคยเปิดใช้งานระบบ
  ถ้าเอามาเป็นตัวหาร ทีมที่ไม่เคยเข้าเลยจะหายไปจากทั้งเศษและส่วน แล้วเปอร์เซ็นต์
  จะออกมาสวยเกินจริงแบบดูไม่ออก — ซึ่งตรงข้ามกับสิ่งที่รายงานนี้มีไว้ตอบ

กติกาของโมดูลนี้:
  1. `get_company_roster()` **ไม่แตะเน็ตเด็ดขาด** อ่านแคชอย่างเดียว
     หน้าสรุปการใช้งานต้องเปิดได้แม้ Fabric ล่ม (ซึ่งเป็นตอนที่คนอยากเข้ามาดูพอดี)
  2. `refresh=True` เท่านั้นที่ยิง Fabric และ **ยิงคำสั่งเดียวทั้งบริษัท**
  3. ดึงใหม่ไม่สำเร็จ = ไม่แตะไฟล์เดิม คืนของเก่าพร้อม error
     (บทเรียนเดียวกับแคชราคา: กด "รีเฟรช" แล้วแย่กว่าเดิมเป็นพฤติกรรมที่ไม่ควรมี)
  4. ตัดพนักงานรถเงินสด (รหัสขึ้นต้น V) ตั้งแต่ตอนเขียนแคช ด้วยกฎตัวเดียวกับ
     ที่ทุกหน้าจอใช้ ตัวเลขจะได้กระทบยอดกับที่อื่นในระบบได้
"""

from __future__ import annotations

import logging
from typing import Any

from ..core.employee_filter import is_van_employee_id
from . import fabric_cache

logger = logging.getLogger("target_allocation")


def _normalize(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """
    รหัสตัวใหญ่ ตัดรถเงินสด และไม่ให้แถวซ้ำ

    คีย์กันซ้ำคือ **(ทีม, รหัสพนักงาน)** ไม่ใช่รหัสพนักงานล้วน — รหัสพนักงานซ้ำ
    ข้ามทีมได้ (invariant I7) ถ้ากันซ้ำด้วยรหัสอย่างเดียว คนของทีมที่มาทีหลัง
    จะหายไปจากทะเบียน แล้วตัวหารของรายงานต่ำกว่าความจริงแบบดูไม่ออก
    """
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        emp = str(r.get("emp_id") or "").strip().upper()
        sup_key = str(r.get("super_code") or "").strip().upper()
        if not emp or (sup_key, emp) in seen or is_van_employee_id(emp):
            continue
        seen.add((sup_key, emp))
        out.append({
            "emp_id": emp,
            "emp_name": str(r.get("emp_name") or "").strip(),
            "super_code": str(r.get("super_code") or "").strip().upper(),
        })
    out.sort(key=lambda r: (r["super_code"], r["emp_id"]))
    return out


def fetch_company_roster() -> list[dict[str, Any]]:
    """ยิง Fabric หนึ่งครั้ง — ผู้เรียกต้องรู้ตัวว่ากำลังออกเน็ต"""
    from ..fabric_dax_connector import FabricDAXConnector

    return _normalize(FabricDAXConnector().get_dim_salesman_roster())


def _empty(reason: str = "") -> dict[str, Any]:
    return {
        "available": False,
        "rows": [],
        "row_count": 0,
        "cached_at": None,
        "age_sec": None,
        "stale": False,
        "error": reason or None,
    }


def get_company_roster(*, refresh: bool = False) -> dict[str, Any]:
    """
    รายชื่อพนักงานทั้งบริษัท

    refresh=False (ค่าเริ่มต้น) → อ่านแคชล้วน ไม่ออกเน็ต ยอมใช้ของเก่า
    refresh=True               → ดึงใหม่ ถ้าล้มเหลวคืนของเก่าพร้อม error
    """
    cached = fabric_cache.read_salesman_roster(allow_stale=True)
    if not refresh:
        if not cached:
            return _empty()
        return {"available": True, "error": None, **cached}

    try:
        rows = fetch_company_roster()
    except Exception as e:                       # ปลายทางล่มได้หลายแบบ
        logger.warning("ดึงรายชื่อพนักงานทั้งบริษัทไม่สำเร็จ: %s", e)
        reason = f"{type(e).__name__}: {e}"
        if cached:
            # ของเดิมยังอยู่ครบทุกไบต์ — ไม่ลบก่อนดึง จึงไม่มีทางเหลือศูนย์
            return {"available": True, "error": reason, **cached}
        return _empty(reason)

    fabric_cache.write_salesman_roster(rows)
    fresh = fabric_cache.read_salesman_roster(allow_stale=True)
    if not fresh:
        # เขียนแคชไม่ได้ (เช่น ปิด TTL ไว้) — ยังตอบด้วยของที่เพิ่งดึงมาได้
        return {
            "available": True, "rows": rows, "row_count": len(rows),
            "cached_at": None, "age_sec": 0.0, "stale": False, "error": None,
        }
    return {"available": True, "error": None, **fresh}


def roster_by_team(rows: list[dict[str, Any]]) -> dict[str, set[str]]:
    """{รหัสทีม: เซ็ตรหัสพนักงาน} — คีย์เป็นทีมเสมอ เพราะรหัสพนักงานซ้ำข้ามทีมได้"""
    out: dict[str, set[str]] = {}
    for r in rows or []:
        sup = str(r.get("super_code") or "").strip().upper()
        emp = str(r.get("emp_id") or "").strip().upper()
        if not sup or not emp:
            continue
        out.setdefault(sup, set()).add(emp)
    return out
