"""
ย้ายพนักงานไปสังกัดทีมอื่นสำหรับการตั้งเป้า — กรณีพิเศษที่โครงสร้างจริงไม่ตรงกับงาน

เจอของจริง: พนักงานขายชายแดนอยู่ใต้ซุปหน่วยรถตามโครงสร้างใน Dim_Salesman แต่เวลา
ตั้งเป้าต้องไปเกลี่ยร่วมกับทีมหน่วยเครดิตของอีกภาคหนึ่ง · โครงสร้างต้นทางแก้ไม่ได้
(เป็นข้อมูลกลางของบริษัท) และไม่ควรแก้ด้วย เพราะสังกัดจริงของเขาไม่ได้เปลี่ยน

**ย้ายเฉพาะ "ใครเกลี่ยเป้าให้" เท่านั้น** — เขต ดิวิชัน พื้นที่ และหน่วยขายในแถวเป้า
ของพนักงานยังเป็นของเขาเองทุกประการ ตอนส่งกลับ Target Sun จึงลงแถวเดิมไม่ผิดเขต

คีย์เป็น emp_id เดี่ยว: พนักงานหนึ่งคนมีทีมที่เกลี่ยเป้าให้ได้ทีมเดียว ถ้าปล่อยให้
ซ้ำได้ เป้าของเขาจะถูกนับสองรอบตอนรวมภาค แล้วยอดรวมทั้งภาคเกินจริงแบบเงียบ ๆ

อยู่ถาวรจนกว่าจะปลด (ไม่ผูกกับงวด) — งวดที่กระจายไปแล้วไม่ย้อนไปแก้ ผลกระจายเก่า
เก็บอยู่ในไฟล์ผลของงวดนั้นตามเดิม
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any

from ..core.atomic_io import atomic_write_json

logger = logging.getLogger("target_allocation")

_STORE_LOCK = threading.Lock()


def _repo_root() -> str:
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))


def emp_assignments_json_path() -> str:
    raw = (os.environ.get("EMP_ASSIGNMENTS_JSON_PATH") or "").strip()
    if raw:
        return os.path.normpath(os.path.abspath(raw))
    return os.path.join(_repo_root(), "config", "emp_assignments.json")


def norm_emp(s: Any) -> str:
    return str(s or "").strip().upper()


def norm_sup(s: Any) -> str:
    return str(s or "").strip().upper()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean_row(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    emp = norm_emp(raw.get("emp_id"))
    to_sup = norm_sup(raw.get("to_sup"))
    from_sup = norm_sup(raw.get("from_sup"))
    if not emp or not to_sup:
        return None
    if to_sup == from_sup:
        # ย้ายไปทีมเดิม = ไม่ได้ย้าย · เก็บไว้จะกลายเป็นแถวที่ทำให้คนอ่านเข้าใจผิด
        return None
    return {
        "emp_id": emp,
        "emp_name": str(raw.get("emp_name") or "").strip(),
        "from_sup": from_sup,
        "to_sup": to_sup,
        "note": str(raw.get("note") or "").strip(),
        "updated_by": str(raw.get("updated_by") or "").strip(),
        "updated_at": str(raw.get("updated_at") or "").strip() or _now_iso(),
    }


def read_rows() -> list[dict[str, Any]]:
    """ทุกการย้ายที่ตั้งไว้ — ไฟล์หายหรือพังต้องไม่ทำให้ระบบล่ม แค่ถือว่าไม่มีการย้าย"""
    path = emp_assignments_json_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.error("อ่าน emp_assignments ไม่ได้ %s: %s — ถือว่าไม่มีการย้าย", path, e)
        return []
    raw = doc.get("assignments") if isinstance(doc, dict) else doc
    if not isinstance(raw, list):
        logger.error("รูปแบบ emp_assignments ไม่ถูกต้อง %s — ถือว่าไม่มีการย้าย", path)
        return []
    out: dict[str, dict[str, Any]] = {}
    for item in raw:
        row = _clean_row(item)
        if row:
            # แถวหลังชนะ — กันไฟล์ที่ถูกแก้มือจนมีพนักงานคนเดียวสองแถว
            out[row["emp_id"]] = row
    return sorted(out.values(), key=lambda r: r["emp_id"])


def write_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: dict[str, dict[str, Any]] = {}
    for item in rows or []:
        row = _clean_row(item)
        if row:
            cleaned[row["emp_id"]] = row
    out = sorted(cleaned.values(), key=lambda r: r["emp_id"])
    path = emp_assignments_json_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    atomic_write_json(path, {"assignments": out}, ensure_ascii=False)
    logger.info("บันทึกการย้ายพนักงาน %d รายการ → %s", len(out), path)
    return out


def set_assignment(
    emp_id: str,
    to_sup: str,
    *,
    from_sup: str = "",
    emp_name: str = "",
    note: str = "",
    updated_by: str = "",
) -> list[dict[str, Any]]:
    """ตั้ง/แก้การย้ายของพนักงานหนึ่งคน — to_sup ว่าง = ปลดการย้าย"""
    emp = norm_emp(emp_id)
    if not emp:
        raise ValueError("ต้องระบุรหัสพนักงาน")
    with _STORE_LOCK:
        rows = [r for r in read_rows() if r["emp_id"] != emp]
        if norm_sup(to_sup):
            rows.append({
                "emp_id": emp,
                "emp_name": emp_name,
                "from_sup": from_sup,
                "to_sup": to_sup,
                "note": note,
                "updated_by": updated_by,
                "updated_at": _now_iso(),
            })
        return write_rows(rows)


def assignment_for_emp(emp_id: str) -> dict[str, Any] | None:
    emp = norm_emp(emp_id)
    for r in read_rows():
        if r["emp_id"] == emp:
            return r
    return None


def moved_away_from(sup_id: str) -> set[str]:
    """
    พนักงานที่ต้อง "หายไป" จากรายชื่อของทีมนี้ เพราะถูกย้ายไปเกลี่ยที่ทีมอื่น

    ไม่ผูกกับ from_sup ที่บันทึกไว้ — ยึดว่า "ถ้าคนนี้ถูกย้ายไปทีม X แล้ว
    ทีมไหนก็ตามที่ไม่ใช่ X ต้องไม่เห็นเขา" ไม่งั้นถ้าโครงสร้างต้นทางเปลี่ยนหัวหน้า
    ทีหลัง เขาจะโผล่สองทีมพร้อมกันแล้วเป้าถูกนับซ้ำ
    """
    sid = norm_sup(sup_id)
    return {r["emp_id"] for r in read_rows() if r["to_sup"] != sid}


def moved_into(sup_id: str) -> list[dict[str, Any]]:
    """พนักงานจากทีมอื่นที่ถูกย้ายมาให้ทีมนี้เกลี่ยเป้าให้"""
    sid = norm_sup(sup_id)
    return [r for r in read_rows() if r["to_sup"] == sid]


def apply_to_employee_list(sup_id: str, rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """
    ปรับรายชื่อพนักงานของทีมหนึ่งตามการย้ายที่ตั้งไว้

    คืน (รายชื่อใหม่, สรุปว่าเอาออกกี่คน เพิ่มกี่คน ติดธงให้กี่คน)
    ผู้เรียกเอา removed/added ไปบอกผู้ใช้บนจอ ส่วน flagged บอกว่า "แถวเปลี่ยนแล้ว"
    ทั้งที่จำนวนคนเท่าเดิม — ต้องเอารายชื่อใหม่ไปใช้ ไม่ใช่ของเดิม
    """
    sid = norm_sup(sup_id)
    away = moved_away_from(sid)
    kept = [r for r in rows if norm_emp(r.get("emp_id")) not in away]
    removed = len(rows) - len(kept)
    flagged = 0

    by_id = {norm_emp(r.get("emp_id")): r for r in kept}
    have = set(by_id)
    added = 0
    for a in moved_into(sid):
        if a["emp_id"] in have:
            # อยู่ในลิสต์อยู่แล้ว — ยังต้องติดธงให้ ไม่ใช่ข้ามเงียบ ๆ
            #
            # เกิดได้สองทาง: แคชรายชื่อเก่าที่เคยถูกเขียนทับด้วยรายชื่อหลังย้าย
            # และกรณีที่ต้นทางรายงานคนนี้ไว้ใต้ทีมปลายทางอยู่ก่อนแล้ว
            # ถ้าข้ามไป ป้าย "ย้ายมา" จะไม่ขึ้นสักจอ ทั้งที่เป้าถูกเกลี่ยรวมไปแล้ว
            cur = by_id.get(a["emp_id"])
            if cur is not None and not str(cur.get("reassigned_from") or "").strip():
                cur["reassigned_from"] = a.get("from_sup") or ""
                flagged += 1
            continue
        row = {
            "emp_id": a["emp_id"],
            "super_code": sid,
            # ติดไว้กับตัวแถว เพื่อให้ทุกขั้นของการกระจายบอกได้ว่าคนนี้ถูกย้ายมา
            # (เขต/หน่วยขายของเขายังเป็นของเดิม ตัวเลขบางอย่างจึงดูแปลกเมื่อเทียบ
            #  กับเพื่อนร่วมทีม — ต้องรู้ตั้งแต่แรกว่าทำไม)
            "reassigned_from": a.get("from_sup") or "",
        }
        if a.get("emp_name"):
            row["emp_name"] = a["emp_name"]
        kept.append(row)
        by_id[a["emp_id"]] = row
        have.add(a["emp_id"])
        added += 1
    return kept, {"removed": removed, "added": added, "flagged": flagged}
