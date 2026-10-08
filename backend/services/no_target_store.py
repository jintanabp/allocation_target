"""
พนักงานที่ไม่ต้องตั้งเป้า — รายชื่อกรณีพิเศษที่ถูกกันออกจากการตั้งเป้าและการกระจายหีบ

ต่างจาก "ไม่นำไปกระจายเป้า" ที่ระบบอนุมานเอง (ไม่มีแถว TGA / เป้าเงินเป็น 0) ตรงที่
รายชื่อชุดนี้เป็น **การตัดสินใจของคน** จึงต้องอยู่ถาวรจนกว่าจะปลด ไม่หายไปเมื่อ
เป้าต้นทางเปลี่ยน และไม่ถูกคำนวณกลับมาเองตอนรีเฟรชเป้าสด

คีย์เป็น (super_code, emp_id) ไม่ใช่ emp_id เดี่ยว ๆ เพราะโหมดรวมภาคเอาพนักงาน
หลายทีมมาไว้ด้วยกัน และ emp_id ซ้ำข้ามทีมได้ (I7) — กันคนละทีมพลอยโดนไปด้วย

เป้าหีบของทีม **ยังต้องกระจายครบเท่าเดิม** (I1) คนที่เหลือรับส่วนนั้นไป
รายชื่อนี้ตัดแค่ "ใครรับได้" ไม่ได้ลดเป้า
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

# RLock: set_for_supervisor ถือ lock ตลอด read → แก้ → write แล้วเรียก read_entries /
# write_entries ที่จับ lock ตัวเดียวกันซ้ำใน thread เดิม (ผลตรวจ §5.1-2)
_STORE_LOCK = threading.RLock()


def _repo_root() -> str:
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))


def no_target_json_path() -> str:
    """
    ที่เขียน — **data/** (ผลตรวจ 7 ต.ค. 2026 ข10) · แอดมินแก้จากหน้าเว็บ ห้ามเก็บใน config/ ที่อยู่ใน git
    (deploy แบบ git pull/reset ทับค่าที่ตั้งไว้เงียบ ๆ — กติกาเดียวกับ data/alloc_rules.json)
    """
    raw = (os.environ.get("NO_TARGET_EMPLOYEES_JSON_PATH") or "").strip()
    if raw:
        return os.path.normpath(os.path.abspath(raw))
    return os.path.join(_repo_root(), "data", "no_target_employees.json")


def _legacy_config_path() -> str:
    return os.path.join(_repo_root(), "config", "no_target_employees.json")


def no_target_read_path() -> str:
    """
    ที่อ่าน — ไฟล์ใน data/ ถ้ามีแล้ว · ยังไม่มี (เพิ่ง deploy รุ่นนี้) = อ่านไฟล์เดิมใน config/ เป็นค่าตั้งต้น
    บันทึกครั้งแรกจากหน้าเว็บจะเขียนลง data/ แล้วจากนั้นใช้ data/ ตลอด (ไม่ต้องย้ายไฟล์เอง ไม่แตะ .env)
    """
    path = no_target_json_path()
    if (os.environ.get("NO_TARGET_EMPLOYEES_JSON_PATH") or "").strip() or os.path.isfile(path):
        return path
    legacy = _legacy_config_path()
    return legacy if os.path.isfile(legacy) else path


def norm_sup(s: Any) -> str:
    return str(s or "").strip().upper()


def norm_emp(s: Any) -> str:
    return str(s or "").strip().upper()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _normalize_entry(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    sup = norm_sup(row.get("super_code") or row.get("supervisor_code"))
    emp = norm_emp(row.get("emp_id"))
    if not sup or not emp:
        return None
    return {
        "super_code": sup,
        "emp_id": emp,
        "emp_name": str(row.get("emp_name") or "").strip(),
        "note": str(row.get("note") or "").strip(),
        "updated_by": str(row.get("updated_by") or "").strip(),
        "updated_at": str(row.get("updated_at") or "").strip(),
    }


def _dedupe(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for row in entries:
        norm = _normalize_entry(row)
        if norm:
            seen[(norm["super_code"], norm["emp_id"])] = norm   # แถวหลังชนะ
    return sorted(seen.values(), key=lambda r: (r["super_code"], r["emp_id"]))


def read_entries() -> list[dict[str, Any]]:
    """
    อ่านรายชื่อทั้งหมด — ไฟล์ไม่มี = ยังไม่เคยตั้งใคร (ปกติ ไม่ใช่ error)

    ไฟล์พังถึงจะ raise เพราะ "อ่านไม่ออก" กับ "ไม่มีใครถูกกัน" ต่างกันคนละเรื่อง
    ผู้เรียกที่ยอมให้ผ่านได้ต้องจงใจ catch เอง
    """
    path = no_target_read_path()
    if not os.path.isfile(path):
        return []
    with _STORE_LOCK:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.error("อ่านรายชื่อไม่ต้องตั้งเป้าไม่ได้ %s: %s", path, e)
            raise PermissionError(f"ไม่สามารถโหลดรายชื่อพนักงานที่ไม่ต้องตั้งเป้า ({path})") from e
    if isinstance(data, dict):
        rows = data.get("employees")
    elif isinstance(data, list):
        rows = data
    else:
        raise PermissionError(f"รูปแบบ no_target_employees JSON ไม่ถูกต้อง: {path}")
    if not isinstance(rows, list):
        raise PermissionError("รูปแบบ no_target_employees JSON ไม่ถูกต้อง (employees ต้องเป็น array)")
    return _dedupe(rows)


def write_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = _dedupe(entries)
    path = no_target_json_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with _STORE_LOCK:
        atomic_write_json(path, {"employees": normalized}, indent=2)
    logger.info("บันทึกรายชื่อไม่ต้องตั้งเป้า %d คน → %s", len(normalized), path)
    return normalized


def no_target_map(entries: list[dict[str, Any]] | None = None) -> dict[str, set[str]]:
    """{รหัสซุป: {รหัสพนักงาน}} — รูปแบบที่ใช้ค้นเร็วตอน enrich payload"""
    rows = entries if entries is not None else read_entries()
    out: dict[str, set[str]] = {}
    for r in rows:
        out.setdefault(r["super_code"], set()).add(r["emp_id"])
    return out


def no_target_map_safe() -> dict[str, set[str]]:
    """
    เหมือน no_target_map แต่ไฟล์พังแล้วคืน {} พร้อม log error

    ยอมให้ผ่าน (fail-open) โดยตั้งใจ: ไฟล์ตั้งค่าเสริมพังไม่ควรทำให้ซุปทั้งบริษัท
    เปิดหน้ากระจายหีบไม่ได้ ผลที่แย่กว่าคือคนในลิสต์ได้เป้ากลับมาชั่วคราว
    ซึ่งเห็นได้ทันทีบนหน้าจอและแก้ได้ด้วยการซ่อมไฟล์
    """
    try:
        return no_target_map()
    except Exception as e:
        logger.error("รายชื่อไม่ต้องตั้งเป้าใช้ไม่ได้ — ถือว่าไม่มีใครถูกกัน: %s", e)
        return {}


def no_target_emp_ids(super_code: str, entries: list[dict[str, Any]] | None = None) -> set[str]:
    return no_target_map(entries).get(norm_sup(super_code), set())


def no_target_emp_ids_for_sups(
    super_codes: list[str] | set[str],
    entries: list[dict[str, Any]] | None = None,
) -> set[str]:
    """
    รวมรหัสพนักงานที่ถูกกันของหลายทีม — ใช้เป็น **ทางถอย** เมื่อไม่รู้ทีมของแถวนั้น

    ผู้เรียกที่รู้ทีมอยู่แล้วต้องใช้ no_target_emp_ids รายทีมแทน ชุดรวมนี้กันเกินได้
    ถ้าบังเอิญมี emp_id ซ้ำข้ามทีม (กันเกิน = คนนั้นไม่ได้หีบ ซึ่งกู้ได้ทันทีด้วยการปลด
    ส่วนกันขาด = หีบไหลไปหาคนที่ไม่ควรได้แล้วถูกส่งขึ้นระบบจริง)
    """
    m = no_target_map(entries)
    out: set[str] = set()
    for code in super_codes:
        out |= m.get(norm_sup(code), set())
    return out


def set_for_supervisor(
    super_code: str,
    emp_ids: list[str],
    *,
    updated_by: str | None = None,
    notes: dict[str, str] | None = None,
    names: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """
    แทนที่รายชื่อของ "ทีมเดียว" ทั้งชุด — ทีมอื่นไม่ถูกแตะ

    หน้าแอดมินส่งสถานะทั้งทีมมาทีเดียว (ติ๊ก/ไม่ติ๊กรายคน) การแทนที่ทั้งชุดจึงตรง
    กับสิ่งที่ผู้ใช้เห็น และปลดคนที่ถูกเอาติ๊กออกได้โดยไม่ต้องส่งคำสั่งลบแยก
    """
    sup = norm_sup(super_code)
    if not sup:
        raise ValueError("ไม่ได้ระบุรหัสซุป")
    # ผลตรวจ §5.1-2: อ่าน → แก้ → เขียน ต้องอยู่ใต้ lock เดียว — เดิมอ่านกับเขียน
    # จับ lock คนละรอบ สองทีมกดบันทึกพร้อมกันแล้วรายชื่อของทีมแรกหาย คนที่ควรถูกกัน
    # กลับได้เป้าและถูกส่งขึ้น Target Sun
    with _STORE_LOCK:
        return _set_for_supervisor_locked(
            sup, emp_ids, updated_by=updated_by, notes=notes, names=names
        )


def _set_for_supervisor_locked(
    sup: str,
    emp_ids: list[str],
    *,
    updated_by: str | None,
    notes: dict[str, str] | None,
    names: dict[str, str] | None,
) -> list[dict[str, Any]]:
    """ตัวทำงานของ set_for_supervisor — ผู้เรียกต้องถือ _STORE_LOCK อยู่แล้ว"""
    existing = read_entries()
    keep = [r for r in existing if r["super_code"] != sup]
    prev = {r["emp_id"]: r for r in existing if r["super_code"] == sup}
    stamp = _now_iso()
    who = str(updated_by or "").strip()
    for raw in emp_ids:
        emp = norm_emp(raw)
        if not emp:
            continue
        old = prev.get(emp)
        note = (notes or {}).get(emp, (old or {}).get("note") or "")
        # คนที่อยู่ในลิสต์อยู่แล้วและไม่มีอะไรเปลี่ยนต้องคงเวลาเดิม ไม่งั้นทุกครั้งที่
        # กดบันทึกทีม เวลาจะขยับทั้งชุด แล้วตามรอยไม่ได้ว่าใครถูกกันตั้งแต่เมื่อไหร่
        touched = old is None or note != (old.get("note") or "")
        keep.append(
            {
                "super_code": sup,
                "emp_id": emp,
                "emp_name": (names or {}).get(emp) or (old or {}).get("emp_name") or "",
                "note": note,
                "updated_by": who if touched else (old.get("updated_by") or ""),
                "updated_at": stamp if touched else (old.get("updated_at") or stamp),
            }
        )
    return write_entries(keep)
