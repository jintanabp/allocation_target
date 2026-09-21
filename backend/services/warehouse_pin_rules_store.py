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


def _normalize_rule(raw: dict[str, Any], *, assign_id: bool = True) -> dict[str, Any]:
    rule = {
        "id": _norm_str(raw.get("id")),
        "section": _norm_str(raw.get("section")),
        "section_label_hint": _norm_str(raw.get("section_label_hint")),
        "skus": _norm_skus(raw.get("skus")),
        "areacode": _norm_str(raw.get("areacode")),
        "divisioncode": _norm_str(raw.get("divisioncode")),
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


def rules_by_key() -> dict[tuple[str, str, str], dict[str, Any]]:
    """{(sku, areacode, divisioncode): rule} — แตกทุก sku ในทุกกติกาออกเป็นคีย์ตรง ๆ
    สแกนลิสต์ทุกครั้ง ไม่ทำ index ค้าง (จำนวนกติกา×SKU ที่คาดไว้ยังเป็นหลักร้อย ไม่ใช่
    หลักหมื่น — เทียบเท่าระดับเดียวกับ alloc_rules_store.disabled_sups)
    """
    state = read_state()
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for rule in state["rules"]:
        if not isinstance(rule, dict):
            continue
        area = _norm_str(rule.get("areacode"))
        div = _norm_str(rule.get("divisioncode"))
        for sku in rule.get("skus") or []:
            out[(sku, area, div)] = rule
    return out


def write_rules(
    rules: list[dict[str, Any]],
    *,
    updated_by: str = "",
    expected_rev: int | None = None,
) -> dict[str, Any]:
    """บันทึกกติกาทั้งก้อน (แทนที่ทั้งลิสต์เดิม) — CAS ด้วย rev เหมือน alloc_rules_store

    ห้าม SKU เดียวกันถูกปักหมุดสองกติกาในภาค+division เดียวกัน (จะไม่รู้ว่าคลังไหนชนะ)
    """
    normalized: list[dict[str, Any]] = []
    claimed: dict[tuple[str, str, str], str] = {}
    for raw in rules or []:
        if not isinstance(raw, dict):
            raise ValueError("กติกาบังคับคลัง: รูปแบบข้อมูลไม่ถูกต้อง")
        rule = _normalize_rule(raw)
        for sku in rule["skus"]:
            key = (sku, rule["areacode"], rule["divisioncode"])
            if key in claimed:
                raise ValueError(
                    f"กติกาบังคับคลัง: SKU {sku} ภาค {rule['areacode']} division "
                    f"{rule['divisioncode']} ถูกตั้งไว้แล้วในกติกากลุ่ม {claimed[key]} — "
                    "1 SKU × ภาค × division ต้องมีคลังเดียวเท่านั้น"
                )
            claimed[key] = rule["section"]
        normalized.append(rule)

    path = warehouse_pin_rules_path()
    with _STORE_LOCK:
        current = _read_file(path)
        cur_rev = int(current.get("rev") or 0)
        if expected_rev is not None and cur_rev != int(expected_rev):
            raise WarehousePinRulesConflict(read_state())
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
