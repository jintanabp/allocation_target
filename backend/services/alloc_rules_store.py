"""
กติกาการเกลี่ยที่เปิด/ปิดได้ — เริ่มจากกติกา "หน่วยไม่เคยขายสินค้านั้น = เป้า 0"

ทำไมต้องมีสวิตช์ทั้งที่ตั้งใจเปิดให้ทุกทีม: กติกานี้ทำให้ตัวเลขที่ซุปเห็นเปลี่ยนจริง
(วัดจากงวด 09/2026: หีบย้ายที่ ~3.4% ทั้งบริษัท · ทีมที่หนักสุด 7.2%) ถ้าทีมไหนมีปัญหา
ต้องปิดให้เขาได้ทันทีโดยไม่ต้อง deploy และไม่กระทบทีมอื่น — แก้ไฟล์ config ไฟล์เดียว

กติกาที่ผู้ใช้เคาะไว้ (10 ก.ย. 2026):
  1. ไม่เคยขายคู่นั้นเลยใน 12 เดือนล่าสุด (ถึงเดือนเดียวกันปีที่แล้ว) -> เป้า 0
  2. ถ้าทั้งทีมไม่มีใครเคยขาย SKU นั้นเลย -> เฉลี่ยทุกคน ไม่ใช่ไปกองที่คนเดียว
  3. ถ้าเป้าของ SKU ใหญ่เกิน `push_multiple` เท่าของที่ทั้งทีมเคยขายรวมกัน ->
     ถือว่าทีมยังไม่เคยขายสินค้าตัวนี้ (สินค้าดันเป้า) -> เฉลี่ยทุกคนเช่นกัน

ข้อ 3 มาจากของจริง: SL531 สินค้า 351320 เป้า 2,052 หีบ ทีม 5 คน มีคนเคยขายคนเดียว
ประวัติ 1 หีบ — ถ้าไม่มีข้อนี้ หีบทั้งก้อนจะตกที่คนเดียวซึ่งไม่มีใครยอมรับได้
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger("target_allocation")

#: เป้าใหญ่กว่าประวัติรวมของคนที่เคยขายกี่เท่า จึงถือว่าเป็นสินค้าดันเป้า
#: 5 มาจากการวัดงวด 09/2026 — เคสปกติ 90% อยู่ที่ไม่เกิน 2.4 เท่า ตั้ง 5 จึงไม่แตะของปกติ
DEFAULT_PUSH_MULTIPLE = 5.0


def _repo_root() -> str:
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))


def alloc_rules_json_path() -> str:
    raw = (os.environ.get("ALLOC_RULES_JSON_PATH") or "").strip()
    if raw:
        return os.path.normpath(os.path.abspath(raw))
    return os.path.join(_repo_root(), "config", "allocation_rules.json")


def norm_sup(s: Any) -> str:
    return str(s or "").strip().upper()


def _read_raw() -> dict[str, Any]:
    path = alloc_rules_json_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        # ไฟล์ตั้งค่าเสริมพังต้องไม่ทำให้กระจายเป้าไม่ได้ทั้งบริษัท — ใช้ค่าเริ่มต้นแทน
        logger.error("อ่าน %s ไม่ได้ (%s) — ใช้ค่าเริ่มต้นของกติกา", path, e)
        return {}


def never_sold_zero_enabled(sup_id: str) -> bool:
    """
    ทีมนี้เปิดกติกา "ไม่เคยขาย = เป้า 0" หรือไม่ — **ค่าเริ่มต้นคือเปิด**

    ปิดได้สองแบบ: ปิดทั้งระบบ (`"enabled": false`) หรือปิดเป็นรายทีม (`"disabled_sups"`)
    """
    cfg = _read_raw().get("never_sold_zero")
    if not isinstance(cfg, dict):
        return True
    if cfg.get("enabled") is False:
        return False
    off = cfg.get("disabled_sups")
    if isinstance(off, list) and norm_sup(sup_id) in {norm_sup(x) for x in off}:
        return False
    return True


def push_multiple() -> float:
    """เกินกี่เท่าถึงถือว่าเป็นสินค้าดันเป้า — ปรับได้จาก config โดยไม่ต้องแก้โค้ด"""
    cfg = _read_raw().get("never_sold_zero")
    if isinstance(cfg, dict):
        try:
            val = float(cfg.get("push_multiple", DEFAULT_PUSH_MULTIPLE))
            # ต่ำกว่า 1 เท่ากับยกเว้นเกือบทุก SKU = กติกาไม่ทำงานเลย กันไว้ก่อน
            if val >= 1.0:
                return val
            logger.warning("push_multiple=%s ต่ำเกินไป — ใช้ค่าเริ่มต้น %s", val, DEFAULT_PUSH_MULTIPLE)
        except (TypeError, ValueError):
            logger.warning("push_multiple ในไฟล์ตั้งค่าไม่ใช่ตัวเลข — ใช้ค่าเริ่มต้น")
    return DEFAULT_PUSH_MULTIPLE
