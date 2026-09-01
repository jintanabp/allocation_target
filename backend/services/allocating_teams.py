"""
ใครในระบบ "กระจายเป้าได้" — นิยามเดียวที่ทุกรายงานต้องใช้ร่วมกัน

ทำไมต้องมีไฟล์นี้:
  เดิมไม่มีคำตอบกลางว่า "ทีมที่กระจายได้มีกี่ทีม" — แต่ละที่นับเอาเองจากคนละแหล่ง
  (บ้างจาก access_hierarchy บ้างจากไฟล์แคชในเครื่อง) แล้วได้เลขไม่ตรงกัน
  รายงานสรุปการใช้งานต้องใช้เลขนี้เป็น "ตัวหาร" จึงต้องนิยามที่เดียวและตรึงด้วยเทสต์

ทำไมไม่ไปอยู่ใน access_hierarchy.py (ซึ่งมีการตัดทีมสาธิต/alias อยู่แล้ว):
  โมดูลนั้น **เขียนไฟล์** และ load_hierarchy_payload() จะ rebuild ให้เองถ้าไฟล์หาย
  รายงานแบบอ่านอย่างเดียวต้องไม่มีทาง trigger การ rebuild ลำดับชั้นทั้งระบบได้

กติกาการนับ:
  - ตำแหน่งต้องเป็น supervisor_acc หรือ manager_acc — marketing/standard ไม่มีข้อมูลให้กระจาย
  - ต้องมีรหัส SL จริง ผ่าน real_userpl() เสมอ · บัญชี "แอดมินอย่างเดียว" เก็บ
    userpl เป็น "none" ถ้า .upper() ตรง ๆ จะได้ทีมผีชื่อ "NONE" โผล่มาในรายงาน
  - ตัดทีมสาธิตออก (หลักเดียวกับ access_hierarchy.build_hierarchy_payload)
  - **แก้ alias ก่อน dedupe** — SL ที่ผูกกันแล้วคือทีมเดียวกัน ถ้าไม่แก้ก่อน
    เจ้าของสองรหัสจะถูกนับสองครั้ง แล้วตัวหารเฟ้อขึ้นเงียบ ๆ
  - **นับรหัสละ 1 ไม่กระจายผู้จัดการออกเป็นทีมลูกน้อง** — ทีมของผู้จัดการซ้อนทับ
    กันเอง (ผู้จัดการ 25 คนคุม 91 รหัสที่ทับกัน) ถ้านับซ้ำตัวเลขจะเกินความจริง
"""

from __future__ import annotations

from typing import Any

from .demo_data import is_demo_supervisor
from .sl_link_store import read_links, resolve_to_canonical
from .user_access_store import read_rows, real_userpl

# ตำแหน่งที่มีทีมให้กระจาย — marketing ได้ allowed_supervisor_codes ว่างเสมอ
# ส่วน standard คือบัญชีที่มีแต่สิทธิ์ดูแลระบบ ไม่มีตำแหน่งงาน
ALLOCATING_LOGIN_KINDS = ("supervisor_acc", "manager_acc")

# ฟิลด์ที่รายงานต้องใช้ต่อ — คัดมาเท่าที่จำเป็น ไม่ส่งทั้งแถวออกไป
# (แถวเต็มมีอีเมลของทุกคน ซึ่งรายงานสรุปไม่ควรพกติดตัวไปทุกที่)
_TEAM_FIELDS = (
    "full_name",
    "login_kind",
    "manager_level",
    "acc_division",
    "acc_region",
    "acc_unit",
)


def _clean(value: Any) -> str:
    """ค่าว่างในไฟล์เก็บเป็น sentinel 'none' — แปลงกลับเป็นสตริงว่าง"""
    s = str(value or "").strip()
    return "" if s.lower() == "none" else s


def can_allocate_row(row: dict[str, Any]) -> bool:
    """แถวสิทธิ์แถวนี้เป็นคนที่กระจายเป้าได้ไหม"""
    if not isinstance(row, dict):
        return False
    if str(row.get("login_kind") or "").strip() not in ALLOCATING_LOGIN_KINDS:
        return False
    code = real_userpl(row.get("userpl"))
    return bool(code) and not is_demo_supervisor(code)


def _better_row(current: dict[str, Any], candidate: dict[str, Any]) -> bool:
    """
    แถวใหม่ควรชนะแถวเดิมไหม เวลาสองแถวชี้รหัสเดียวกัน

    ให้ supervisor_acc ชนะ (ข้อมูลสังกัดครบกว่า) — หลักเดียวกับ
    _supervisor_meta_index ในหน้าแอดมิน ที่อื่นจะได้เห็นสังกัดชุดเดียวกัน
    """
    if current.get("login_kind") == "supervisor_acc":
        return False
    return candidate.get("login_kind") == "supervisor_acc"


def allocating_teams(rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """
    รหัสทีมทั้งหมดที่กระจายเป้าได้ พร้อมสังกัด — เรียงตามรหัส

    อ่านไฟล์ล้วน ไม่ยิงเน็ต และไม่ trigger การ rebuild อะไรทั้งสิ้น
    """
    src = read_rows() if rows is None else rows
    links = read_links()
    by_code: dict[str, dict[str, Any]] = {}
    for row in src:
        if not can_allocate_row(row):
            continue
        code = resolve_to_canonical(real_userpl(row.get("userpl")), links)
        if not code or is_demo_supervisor(code):
            continue
        entry = {"sup_id": code, **{k: _clean(row.get(k)) for k in _TEAM_FIELDS}}
        cur = by_code.get(code)
        if cur is None or _better_row(cur, entry):
            by_code[code] = entry
    return [by_code[c] for c in sorted(by_code)]


def allocating_team_codes(rows: list[dict[str, Any]] | None = None) -> set[str]:
    """เซ็ตรหัสทีมที่กระจายได้ — ใช้เป็นตัวหารของรายงานสรุปการใช้งาน"""
    return {t["sup_id"] for t in allocating_teams(rows)}


def allocating_emails(rows: list[dict[str, Any]] | None = None) -> set[str]:
    """
    อีเมลของคนที่กระจายได้ — คนละเลขกับจำนวนทีม

    หนึ่งคนถือได้หลายรหัส และผู้จัดการหนึ่งคนกระจายแทนหลายทีม เลข "คน" จึงต้อง
    นับจากอีเมล ส่วนเลข "ทีม" นับจากรหัส · รายงานแสดงทั้งคู่เพื่อไม่ให้สับสน
    """
    src = read_rows() if rows is None else rows
    return {
        str(r.get("email") or "").strip().lower()
        for r in src
        if can_allocate_row(r) and str(r.get("email") or "").strip()
    }
