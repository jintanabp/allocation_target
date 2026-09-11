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
import threading
from datetime import datetime, timezone
from typing import Any

from ..core.atomic_io import atomic_write_json, read_locked

logger = logging.getLogger("target_allocation")

_STORE_LOCK = threading.Lock()

#: เป้าใหญ่กว่าประวัติรวมของคนที่เคยขายกี่เท่า จึงถือว่าเป็นสินค้าดันเป้า
#: 5 มาจากการวัดงวด 09/2026 — เคสปกติ 90% อยู่ที่ไม่เกิน 2.4 เท่า ตั้ง 5 จึงไม่แตะของปกติ
DEFAULT_PUSH_MULTIPLE = 5.0


def _repo_root() -> str:
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))


def alloc_rules_json_path() -> str:
    """ค่าตั้งต้นที่มากับโค้ด — อ่านอย่างเดียว ไม่มีใครเขียนทับไฟล์นี้"""
    raw = (os.environ.get("ALLOC_RULES_JSON_PATH") or "").strip()
    if raw:
        return os.path.normpath(os.path.abspath(raw))
    return os.path.join(_repo_root(), "config", "allocation_rules.json")


def alloc_rules_override_path() -> str:
    """
    ค่าที่หัวหน้าแอดมินตั้งจากหน้าเว็บ — **ต้องอยู่ใต้ data/ เท่านั้น**

    ไฟล์ใน config/ ที่ track ใน git ถูก `git pull` ทับเงียบ ๆ ตอน deploy
    (docs/DEPLOY_QA_CHECKLIST.md หัวข้อ 10 — เคยทำรายชื่อผู้ใช้หายจริง)
    ถ้าเก็บสวิตช์นี้ไว้ที่นั่น ทีมที่สั่งปิดกติกาไว้จะถูกเปิดคืนโดยไม่มีใครรู้
    """
    raw = (os.environ.get("ALLOC_RULES_OVERRIDE_PATH") or "").strip()
    if raw:
        return os.path.normpath(os.path.abspath(raw))
    return os.path.join(_repo_root(), "data", "alloc_rules.json")


#: เกณฑ์ "สินค้าดันเป้า" ที่ยอมให้ตั้งได้ — ต่ำกว่า 1 เท่ากับปิดกฎข้อนี้ทิ้ง
#: สูงเกินก็ไม่มีเคสไหนเข้าเงื่อนไขเลย ทั้งสองทางคือตั้งแล้วไม่ได้อะไร
MIN_PUSH_MULTIPLE = 1.0
MAX_PUSH_MULTIPLE = 100.0


def norm_sup(s: Any) -> str:
    return str(s or "").strip().upper()


def _read_file(path: str) -> dict[str, Any]:
    if not os.path.isfile(path):
        return {}
    try:
        with read_locked(path), open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        # ไฟล์ตั้งค่าเสริมพังต้องไม่ทำให้กระจายเป้าไม่ได้ทั้งบริษัท — ใช้ค่าเริ่มต้นแทน
        logger.error("อ่าน %s ไม่ได้ (%s) — ใช้ค่าเริ่มต้นของกติกา", path, e)
        return {}


def _read_raw() -> dict[str, Any]:
    """
    ค่าที่ใช้จริง — ถ้ามีไฟล์ที่แอดมินตั้งไว้ ใช้ไฟล์นั้น**ทั้งก้อน** ไม่ผสมทีละคีย์

    ผสมทีละคีย์จะอ่านยากตอนมีปัญหา: เห็นค่าบนจอแล้วเดาไม่ออกว่ามาจากไฟล์ไหน
    หน้าแอดมินจึงบอกเสมอว่าค่าที่ใช้อยู่มาจาก "หน้าจอ" หรือ "ค่าตั้งต้นจากโค้ด"
    """
    override = _read_file(alloc_rules_override_path())
    if override.get("never_sold_zero") is not None:
        return override
    return _read_file(alloc_rules_json_path())


def never_sold_zero_enabled(sup_id: str) -> bool:
    """
    ทีมนี้เปิดกติกา "ไม่เคยขาย = เป้า 0" หรือไม่ — **ค่าเริ่มต้นคือเปิด**

    ปิดได้สองแบบ: ปิดทั้งระบบ (`"enabled": false`) หรือปิดเป็นรายทีม (`"disabled_sups"`)

    ทีมเดียวเสมอ — รอบที่มีหลายทีมในก้อนเดียวใช้ `never_sold_zero_round_state()`
    ซึ่งเป็นเจ้าของกติกาตัวจริง ตัวนี้เป็นทางลัดของทีมเดียวเพื่อไม่ให้มีสองชุดตรรกะ

    รหัส SL ที่ผูกกันไว้คือทีมเดียวกัน (เช่น SL524 -> SL508) — ถ้าเทียบรหัสดิบ
    ทีมที่ล็อกอินด้วยรหัสเก่าจะไม่โดนสวิตช์ที่แอดมินเพิ่งกดปิดให้ แล้วดูเหมือนปุ่มเสีย
    """
    return bool(never_sold_zero_round_state([sup_id])["enabled"])


def never_sold_zero_round_state(sup_ids: Any) -> dict[str, Any]:
    """
    กติกาทำงานไหม เมื่อ "หนึ่งรอบการคำนวณ" มีหลายทีมอยู่ในก้อนเดียว

    โหมดรวมเป้าทั้งภาค / รวมทั้งหน่วย ยิง `/optimize` **ครั้งเดียว**ด้วยพนักงานหลายทีม
    แต่กติกานี้เป็นสวิตช์ตัวเดียวต่อรอบ (OR_engine ตัดสินจาก `df_sold_12m` ก้อนเดียว)
    จะแยกรายคนตามทีมไม่ได้ ถ้ายังไม่ตอบก่อนว่ากฎข้อ 2/3 นับคำว่า "ทั้งทีม" จากทีมไหน

    **ผู้ใช้เคาะไว้ 11 ก.ย. 2026: มีทีมไหนสักทีมในรอบถูกปิด = ปิดทั้งรอบ**
    ทางกลับกันแย่กว่า — ทีมที่แอดมินสั่งปิดจะยังโดนกติกาเพราะบังเอิญไม่ได้เป็นทีมหลัก
    ของรอบนั้น แปลว่าปุ่มปิดในหน้าแอดมิน "กดแล้วไม่เกิดอะไร" ซึ่งเสียความเชื่อถือกว่า
    ทางนี้เสียแค่ทีมที่ยังเปิดอยู่ไม่ได้ใช้กติกาในรอบรวม และเลี่ยงได้ด้วยการเลือก
    「แยกตามทีม」ซึ่งมีปุ่มอยู่บนจอแล้ว — **แต่ต้องขึ้นบอกบนจอทุกครั้ง ห้ามปิดเงียบ**

    คืน `disabled_sups` เป็นรหัสตามที่ส่งเข้ามา (ไม่ใช่ canonical) เพราะเอาไปโชว์บนจอ
    ให้ผู้ใช้จำได้ว่าเป็นทีมไหน — ส่วนการเทียบใช้ canonical ทั้งสองฝั่ง
    """
    codes: list[str] = []
    seen: set[str] = set()
    for x in sup_ids or []:
        c = norm_sup(x)
        if c and c not in seen:
            seen.add(c)
            codes.append(c)

    cfg = _read_raw().get("never_sold_zero")
    if not isinstance(cfg, dict):
        return {"enabled": True, "system_off": False, "disabled_sups": []}
    if cfg.get("enabled") is False:
        return {"enabled": False, "system_off": True, "disabled_sups": []}
    off = cfg.get("disabled_sups")
    if not isinstance(off, list) or not off:
        return {"enabled": True, "system_off": False, "disabled_sups": []}

    # อ่านตารางผูกรหัสครั้งเดียวแล้วใช้ซ้ำ — `resolve_to_canonical` เปิดไฟล์ทุกครั้งที่เรียก
    # รอบรวมภาคมีได้หลายสิบทีม ถ้าเรียกต่อทีมคือเปิดไฟล์เดิมหลายสิบรอบต่อการคำนวณ
    cmap = _alias_to_canonical_map()
    off_canon = {cmap.get(norm_sup(x), norm_sup(x)) for x in off}
    off_canon.discard("")
    hit = [c for c in codes if cmap.get(c, c) in off_canon]
    return {"enabled": not hit, "system_off": False, "disabled_sups": hit}


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


def _alias_to_canonical_map() -> dict[str, str]:
    """ตารางผูกรหัส SL ทั้งก้อน — อ่านไฟล์ครั้งเดียวแล้วส่งต่อ ไม่ใช่เปิดซ้ำต่อรหัส"""
    try:
        from .sl_link_store import alias_to_canonical_map

        return alias_to_canonical_map()
    except Exception as e:  # ไฟล์ผูกรหัสมีปัญหาต้องไม่ทำให้กระจายเป้าไม่ได้
        logger.warning("อ่านตารางผูกรหัส SL ไม่ได้ (%s) — เทียบด้วยรหัสตรง ๆ", e)
        return {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class AllocRulesConflict(Exception):
    """มีคนอื่นบันทึกค่าไปแล้วระหว่างที่หน้าจอนี้เปิดค้างอยู่"""

    def __init__(self, current: dict[str, Any]):
        super().__init__("alloc rules were changed by someone else")
        self.current = current


def _normalize_settings(raw: dict[str, Any]) -> dict[str, Any]:
    cfg = raw.get("never_sold_zero")
    cfg = cfg if isinstance(cfg, dict) else {}
    sups = cfg.get("disabled_sups")
    sups = sorted({norm_sup(x) for x in sups if norm_sup(x)}) if isinstance(sups, list) else []
    try:
        pm = float(cfg.get("push_multiple", DEFAULT_PUSH_MULTIPLE))
    except (TypeError, ValueError):
        pm = DEFAULT_PUSH_MULTIPLE
    return {
        "enabled": cfg.get("enabled") is not False,
        "push_multiple": round(max(MIN_PUSH_MULTIPLE, min(MAX_PUSH_MULTIPLE, pm)), 1),
        "disabled_sups": sups,
    }


def read_settings() -> dict[str, Any]:
    """ค่าที่ใช้อยู่จริง + บอกว่ามาจากไหน — รูปแบบที่หน้าแอดมินวาดได้เลย"""
    override = _read_file(alloc_rules_override_path())
    has_override = override.get("never_sold_zero") is not None
    raw = override if has_override else _read_file(alloc_rules_json_path())
    out = _normalize_settings(raw)
    out.update({
        "source": "admin" if has_override else "config",
        "rev": int(override.get("rev") or 0) if has_override else 0,
        "updated_by": str(override.get("updated_by") or "") if has_override else "",
        "updated_at": str(override.get("updated_at") or "") if has_override else "",
        "default_push_multiple": DEFAULT_PUSH_MULTIPLE,
        "min_push_multiple": MIN_PUSH_MULTIPLE,
        "max_push_multiple": MAX_PUSH_MULTIPLE,
        "config_defaults": _normalize_settings(_read_file(alloc_rules_json_path())),
    })
    return out


def write_settings(
    *,
    enabled: bool,
    push_multiple: float,
    disabled_sups: list[str] | None = None,
    updated_by: str = "",
    expected_rev: int | None = None,
) -> dict[str, Any]:
    """
    บันทึกค่าที่แอดมินตั้ง — ลง data/alloc_rules.json เท่านั้น ไม่แตะไฟล์ใน config/

    ค่าที่ใช้ไม่ได้ต้อง **แจ้งกลับให้ชัด** ไม่ใช่เงียบ ๆ แล้วถอยไปใช้ค่าเริ่มต้น
    (ตัวอ่านถอยให้อยู่แล้วเพื่อกันไฟล์พัง แต่ถ้าตอนกดบันทึกก็ถอยด้วย แอดมินจะเห็น
    「บันทึกแล้ว」ทั้งที่ค่าที่ใช้จริงไม่ใช่ค่าที่เพิ่งพิมพ์)
    """
    try:
        pm = round(float(push_multiple), 1)
    except (TypeError, ValueError):
        raise ValueError("เกณฑ์สินค้าดันเป้าต้องเป็นตัวเลข")
    if not (MIN_PUSH_MULTIPLE <= pm <= MAX_PUSH_MULTIPLE):
        raise ValueError(
            f"เกณฑ์สินค้าดันเป้าต้องอยู่ระหว่าง {MIN_PUSH_MULTIPLE:g} ถึง {MAX_PUSH_MULTIPLE:g} เท่า "
            f"(ต่ำกว่า {MIN_PUSH_MULTIPLE:g} เท่ากับปิดกฎข้อนี้ทิ้ง)"
        )
    sups = sorted({norm_sup(x) for x in (disabled_sups or []) if norm_sup(x)})
    path = alloc_rules_override_path()
    with _STORE_LOCK:
        current = _read_file(path)
        cur_rev = int(current.get("rev") or 0)
        if expected_rev is not None and cur_rev != int(expected_rev):
            raise AllocRulesConflict(read_settings())
        doc = {
            "_readme": (
                "ค่ากติกาการเกลี่ยที่หัวหน้าแอดมินตั้งจากหน้าเว็บ — ทับ config/allocation_rules.json ทั้งก้อน "
                "ลบไฟล์นี้ = กลับไปใช้ค่าตั้งต้นจากโค้ด (ปุ่ม「คืนค่าตั้งต้น」ในหน้าแอดมินทำแบบนั้น)"
            ),
            "rev": cur_rev + 1,
            "updated_by": str(updated_by or "").strip().lower(),
            "updated_at": _now_iso(),
            "never_sold_zero": {
                "enabled": bool(enabled),
                "push_multiple": pm,
                "disabled_sups": sups,
            },
        }
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        atomic_write_json(path, doc, indent=2)
    logger.warning(
        "กติกาการเกลี่ยถูกแก้โดย %s — เปิด=%s เกณฑ์ดันเป้า=%s ปิดให้ %d ทีม",
        updated_by or "-",
        enabled,
        pm,
        len(sups),
    )
    return read_settings()


def reset_settings() -> dict[str, Any]:
    """ลบค่าที่แอดมินตั้ง กลับไปใช้ค่าตั้งต้นจากโค้ด"""
    path = alloc_rules_override_path()
    with _STORE_LOCK:
        if os.path.isfile(path):
            try:
                os.remove(path)
            except OSError as e:
                logger.error("ลบ %s ไม่ได้: %s", path, e)
                raise
    logger.warning("กติกาการเกลี่ยถูกคืนค่าตั้งต้นจากโค้ด")
    return read_settings()
