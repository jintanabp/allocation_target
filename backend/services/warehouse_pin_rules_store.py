"""
กติกาบังคับคลังเดียว — สำหรับ "ชุด SKU ที่แอดมินเลือกไว้" × AREACODE × DIVISIONCODE

ทำไมต้องมีกติกานี้: บางกลุ่มสินค้ามีเป้าที่ Target Sun กระจายอยู่หลายคลังตามประวัติขาย
ทั้งที่ธุรกิจต้องการให้สินค้ากลุ่มนั้น (ในภาค+division ที่กำหนด) ถูกกำหนดเป้าไว้ที่คลังเดียว
เท่านั้น — ดู docs/ALLOCATION_INVARIANTS.md และ backend/services/lakehouse.py::
_apply_warehouse_pin_rules สำหรับตัวบังคับใช้จริงตอนส่ง Target Sun

**คีย์การแมตช์คือ SKU ตรง ๆ ไม่ใช่ Section** — `Dim_Product[Section]` เป็นแค่ตัวช่วยกรอง
ตอนแอดมินเลือกสินค้าในหน้าเว็บ (เลือก Section แล้วเลือกสินค้าเฉพาะบางตัวจากในกลุ่มนั้นผ่าน
โมดัล ไม่บังคับว่าต้องเอาทั้งกลุ่ม) แต่ตัวกติกาที่บันทึกจริงคือ `skus: [รหัส, ...]` —
`section`/`section_label_hint` เก็บไว้แค่โชว์บนตาราง ไม่ถูกใช้ตอนจับคู่กติกาเลย

**เงื่อนไขเสริม 2 ช่อง (ผู้ใช้ขอ 21 ต.ค. 2026)** — ว่าง = ไม่จำกัด (กติกาเดิมทุกข้อเป็นแบบนี้):
  - `salestype` — หน่วยขายตาม SALESTYPE ในแถวเป้า Target Sun: "S" = เครดิต, "C" = รถเงินสด
  - `emp_ids` — เฉพาะพนักงานรหัสเหล่านี้ (คนอื่นในภาค/division เดียวกันไม่โดนแตะ
    ใช้คลังตามเป้า Target Sun เหมือนไม่มีกติกา)
SKU × ภาค × division เดียวกันมีได้หลายกติกาถ้าเงื่อนไขต่างกัน — แถวหนึ่งเข้าได้หลาย
กติกาเมื่อไหร่ ข้อที่เจาะจงกว่าชนะ (ระบุพนักงาน > ระบุหน่วย > ไม่ระบุ) ส่วนกติกาที่เจาะจง
เท่ากันแล้วครอบแถวเดียวกันได้ (จะไม่รู้ว่าคลังไหนชนะ) ถูกปฏิเสธตอนบันทึก

**ต่างจาก alloc_rules_store.py ตรงที่ไม่มี "ค่าเริ่มต้นจากโค้ด"** — กติกานี้ไม่มีอยู่แปลว่า
ไม่มีการบังคับคลังเลย (พฤติกรรมเดิม) จึงใช้ไฟล์เดียวพอ ไม่ต้องมี config/ default + data/
override สองไฟล์เหมือน alloc_rules

ไฟล์ต้องอยู่ใต้ data/ เท่านั้น (ไม่ใช่ config/) — config/ ที่ track ใน git ถูก git pull
ทับเงียบ ๆ ตอน deploy (docs/DEPLOY_QA_CHECKLIST.md หัวข้อ 10)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from ..core.atomic_io import atomic_write_json, read_locked

logger = logging.getLogger("target_allocation")

_STORE_LOCK = threading.Lock()

_REQUIRED_FIELDS = ("section", "areacode", "divisioncode", "warehouse_code")


def _repo_root() -> str:
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))


def warehouse_pin_rules_path() -> str:
    raw = (os.environ.get("WAREHOUSE_PIN_RULES_PATH") or "").strip()
    if raw:
        return os.path.normpath(os.path.abspath(raw))
    return os.path.join(_repo_root(), "data", "warehouse_pin_rules.json")


class WarehousePinRulesConflict(Exception):
    """มีคนอื่นบันทึกกติกาไปแล้วระหว่างที่หน้าจอนี้เปิดค้างอยู่"""

    def __init__(self, current: dict[str, Any]):
        super().__init__("warehouse pin rules were changed by someone else")
        self.current = current


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_file(path: str) -> dict[str, Any]:
    if not os.path.isfile(path):
        return {}
    try:
        with read_locked(path), open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        # ไฟล์ตั้งค่าเสริมพังต้องไม่ทำให้ส่ง Target Sun ไม่ได้ทั้งบริษัท — ใช้ค่าเริ่มต้นแทน
        logger.error("อ่าน %s ไม่ได้ (%s) — ใช้ว่าง (ไม่มีกติกาบังคับคลัง)", path, e)
        return {}


def _norm_str(s: Any) -> str:
    return str(s if s is not None else "").strip()


def _norm_skus(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return sorted({_norm_str(s) for s in raw if _norm_str(s)})


SALESTYPE_LABELS_TH = {"S": "เครดิต", "C": "รถเงินสด"}


def norm_emp(code: Any) -> str:
    """กติกาเดียวกับ lakehouse.norm_emp_code (import ตรงไม่ได้ — lakehouse import โมดูลนี้)"""
    s = _norm_str(code).upper()
    return s.zfill(5) if s.isdigit() else s


def _norm_emp_ids(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return sorted({norm_emp(e) for e in raw if norm_emp(e)})


def _normalize_rule(raw: dict[str, Any], *, assign_id: bool = True) -> dict[str, Any]:
    salestype = _norm_str(raw.get("salestype")).upper()
    if salestype and salestype not in SALESTYPE_LABELS_TH:
        raise ValueError(
            f"กติกาบังคับคลัง: หน่วย '{salestype}' ไม่รู้จัก — ใช้ S (เครดิต), C (รถเงินสด) หรือเว้นว่าง"
        )
    rule = {
        "id": _norm_str(raw.get("id")),
        "section": _norm_str(raw.get("section")),
        "section_label_hint": _norm_str(raw.get("section_label_hint")),
        "skus": _norm_skus(raw.get("skus")),
        "areacode": _norm_str(raw.get("areacode")),
        "divisioncode": _norm_str(raw.get("divisioncode")),
        "salestype": salestype,
        "emp_ids": _norm_emp_ids(raw.get("emp_ids")),
        "warehouse_code": _norm_str(raw.get("warehouse_code")),
        "created_by": _norm_str(raw.get("created_by")),
        "created_at": _norm_str(raw.get("created_at")) or _now_iso(),
        "note": _norm_str(raw.get("note")),
    }
    for f in _REQUIRED_FIELDS:
        if not rule[f]:
            raise ValueError(f"กติกาบังคับคลัง: ช่อง '{f}' ห้ามว่าง")
    if not rule["skus"]:
        raise ValueError("กติกาบังคับคลัง: ต้องเลือกสินค้าอย่างน้อย 1 รายการ")
    if not rule["id"] and assign_id:
        rule["id"] = f"whpin_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"
    return rule


def read_state() -> dict[str, Any]:
    """ค่าที่ใช้อยู่จริง — {rules, rev, updated_by, updated_at}"""
    raw = _read_file(warehouse_pin_rules_path())
    rules = raw.get("rules")
    rules = rules if isinstance(rules, list) else []
    return {
        "rules": rules,
        "rev": int(raw.get("rev") or 0),
        "updated_by": _norm_str(raw.get("updated_by")),
        "updated_at": _norm_str(raw.get("updated_at")),
    }


def rules_by_key() -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    """{(sku, areacode, divisioncode): [rule, ...]} — แตกทุก sku ในทุกกติกาออกเป็นคีย์ตรง ๆ
    คีย์หนึ่งมีได้หลายกติกา (ต่างกันที่หน่วย/พนักงาน) — เลือกข้อที่ใช้จริงด้วย match_rule()
    สแกนลิสต์ทุกครั้ง ไม่ทำ index ค้าง (จำนวนกติกา×SKU ที่คาดไว้ยังเป็นหลักร้อย ไม่ใช่
    หลักหมื่น — เทียบเท่าระดับเดียวกับ alloc_rules_store.disabled_sups)
    """
    state = read_state()
    out: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for rule in state["rules"]:
        if not isinstance(rule, dict):
            continue
        area = _norm_str(rule.get("areacode"))
        div = _norm_str(rule.get("divisioncode"))
        for sku in rule.get("skus") or []:
            out.setdefault((sku, area, div), []).append(rule)
    return out


def _specificity(rule: dict[str, Any]) -> int:
    return (2 if rule.get("emp_ids") else 0) + (1 if _norm_str(rule.get("salestype")) else 0)


def match_rule(
    candidates: list[dict[str, Any]] | None, emp_id: Any, salestype: Any
) -> dict[str, Any] | None:
    """กติกาที่ใช้กับแถวนี้ (หรือ None) — candidates คือรายการของคีย์เดียวกันจาก rules_by_key

    ข้อที่เจาะจงกว่าชนะ: ระบุพนักงาน > ระบุหน่วย > ไม่ระบุ (write_rules กันกรณีเสมอกันไว้แล้ว)
    """
    if not candidates:
        return None
    emp = norm_emp(emp_id)
    st = _norm_str(salestype).upper()
    best: dict[str, Any] | None = None
    for rule in candidates:
        r_st = _norm_str(rule.get("salestype")).upper()
        if r_st and r_st != st:
            continue
        r_emps = rule.get("emp_ids") or []
        if r_emps and emp not in {norm_emp(e) for e in r_emps}:
            continue
        if best is None or _specificity(rule) > _specificity(best):
            best = rule
    return best


def _scope_label(rule: dict[str, Any]) -> str:
    parts = [f"ภาค {rule['areacode']}", f"division {rule['divisioncode']}"]
    if rule.get("salestype"):
        parts.append(f"หน่วย{SALESTYPE_LABELS_TH.get(rule['salestype'], rule['salestype'])}")
    if rule.get("emp_ids"):
        parts.append("พนักงาน " + ", ".join(rule["emp_ids"][:5]) + (" ..." if len(rule["emp_ids"]) > 5 else ""))
    return " ".join(parts)


def _overlaps(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """สองกติกาของคีย์เดียวกันชนกันไหม — ชน = เจาะจงเท่ากันและมีแถวที่เข้าได้ทั้งคู่
    (เจาะจงไม่เท่ากันไม่ชน เพราะ match_rule เลือกข้อที่เจาะจงกว่าได้เสมอ)"""
    if _specificity(a) != _specificity(b):
        return False
    if a.get("salestype") != b.get("salestype"):
        return False
    ea, eb = set(a.get("emp_ids") or []), set(b.get("emp_ids") or [])
    if ea or eb:
        return bool(ea & eb)
    return True


def write_rules(
    rules: list[dict[str, Any]],
    *,
    updated_by: str = "",
    expected_rev: int | None = None,
) -> dict[str, Any]:
    """บันทึกกติกาทั้งก้อน (แทนที่ทั้งลิสต์เดิม) — CAS ด้วย rev เหมือน alloc_rules_store

    ห้ามกติกาสองข้อครอบแถวเดียวกันด้วยความเจาะจงเท่ากัน (จะไม่รู้ว่าคลังไหนชนะ) —
    ข้อที่เจาะจงต่างกัน (เช่น ทั้งภาค + เฉพาะพนักงานบางคน) อยู่ร่วมกันได้
    """
    normalized: list[dict[str, Any]] = []
    claimed: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for raw in rules or []:
        if not isinstance(raw, dict):
            raise ValueError("กติกาบังคับคลัง: รูปแบบข้อมูลไม่ถูกต้อง")
        rule = _normalize_rule(raw)
        for sku in rule["skus"]:
            key = (sku, rule["areacode"], rule["divisioncode"])
            for other in claimed.get(key, []):
                if _overlaps(rule, other):
                    raise ValueError(
                        f"กติกาบังคับคลัง: SKU {sku} {_scope_label(rule)} ซ้อนกับกติกากลุ่ม "
                        f"{other['section']} ({_scope_label(other)}) ที่ตั้งไว้แล้ว — "
                        "แถวเดียวกันต้องมีคลังปลายทางเดียว"
                    )
            claimed.setdefault(key, []).append(rule)
        normalized.append(rule)

    path = warehouse_pin_rules_path()
    with _STORE_LOCK:
        current = _read_file(path)
        cur_rev = int(current.get("rev") or 0)
        if expected_rev is not None and cur_rev != int(expected_rev):
            raise WarehousePinRulesConflict(read_state())
        # หน้าเว็บส่งกติกาทั้งก้อนกลับมาโดยไม่มี created_by/created_at (body ไม่มีช่องนี้) — เดิมเลย
        # ได้ค่าว่างทุกครั้ง ช่อง「ตั้งโดย」ขึ้น — ทุกแถว (ผู้ใช้เจอ 21 ต.ค. 2026)
        # กติกาที่มีอยู่แล้ว (id เดิม) คงคนตั้ง/เวลาตั้งเดิมไว้ · กติกาใหม่ = คนที่กดบันทึกรอบนี้
        prev_by_id = {
            _norm_str(r.get("id")): r
            for r in (current.get("rules") or [])
            if isinstance(r, dict) and _norm_str(r.get("id"))
        }
        editor = _norm_str(updated_by).lower()
        for rule in normalized:
            prev = prev_by_id.get(rule["id"])
            if prev is not None:
                rule["created_by"] = _norm_str(prev.get("created_by")) or rule["created_by"]
                rule["created_at"] = _norm_str(prev.get("created_at")) or rule["created_at"]
            elif not rule["created_by"]:
                rule["created_by"] = editor
        doc = {
            "_readme": (
                "กติกาบังคับคลังเดียวสำหรับกลุ่มสินค้า×ภาค×division ที่หัวหน้าแอดมินตั้งจาก"
                "หน้าเว็บ — ตั้งใจให้เป็นไฟล์เดียว (ไม่มีค่าเริ่มต้นจากโค้ดเหมือน alloc_rules "
                "เพราะไม่มีกติกา = ไม่บังคับอะไรเลย ซึ่งเป็นค่าเริ่มต้นที่ปลอดภัยอยู่แล้ว)"
            ),
            "rev": cur_rev + 1,
            "updated_by": _norm_str(updated_by).lower(),
            "updated_at": _now_iso(),
            "rules": normalized,
        }
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        atomic_write_json(path, doc, indent=2)
    logger.warning(
        "กติกาบังคับคลังถูกแก้โดย %s — ตอนนี้มี %d กติกา",
        updated_by or "-",
        len(normalized),
    )
    return read_state()
