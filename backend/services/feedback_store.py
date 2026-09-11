"""
ข้อเสนอแนะจากผู้ใช้ — ข้อความที่ซุปกดส่งจากปุ่มมุมขวาล่างของหน้าเว็บ

ทำไมไม่เก็บรวมกับ usage log: บันทึกการใช้งานตั้งใจให้เป็น append-only แก้ไม่ได้เลย
(เคยมีปุ่ม "รับทราบแล้วลบ" แล้วถอดออก เพราะทำให้ตามย้อนหลังไม่ได้ — ดูหมายเหตุใน
backend/routers/admin.py เหนือ POST /usage-logs) ส่วนข้อเสนอแนะต้องมี **สถานะที่แก้ได้**
ว่าอ่านหรือยัง จัดการหรือยัง จึงต้องเป็นที่เก็บคนละก้อน

ทำไมอยู่ใต้ data/ ไม่ใช่ config/: ไฟล์ใน config/ ที่ track ใน git ถูก `git pull` ทับ
ตอน deploy (เคยทำรายชื่อผู้ใช้หายจริง — docs/DEPLOY_QA_CHECKLIST.md หัวข้อ 10)
ข้อความที่ผู้ใช้ส่งมาหายไม่ได้ และไม่ควรขึ้น git อยู่แล้วเพราะมีอีเมลคนติดมาด้วย

ไฟล์พังต้องไม่ทำให้ใครทำงานไม่ได้ — อ่านไม่ออกให้ถือว่าว่าง แล้ว log error ไว้
(หลักเดียวกับ no_target_store.no_target_map_safe และ alloc_rules_store._read_raw)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from ..core.atomic_io import atomic_write_json, read_locked

logger = logging.getLogger("target_allocation")

_STORE_LOCK = threading.Lock()

#: ข้อความยาวสุดที่รับ — ยาวกว่านี้ตัดทิ้งท้าย ไม่ใช่ปฏิเสธทั้งข้อความ
MAX_MESSAGE_CHARS = 2000
#: โน้ตของแอดมินต่อ 1 รายการ
MAX_NOTE_CHARS = 1000
#: เก็บกี่รายการ — เกินแล้วตัด "จัดการแล้ว" ที่เก่าที่สุดก่อน ยังไม่พอค่อยตัดของเก่าจริง ๆ
MAX_RECORDS = 2000
#: กันยิงรัว — คนเดียวส่งได้กี่ครั้งในกี่วินาที
RATE_LIMIT_PER_EMAIL = 5
RATE_LIMIT_WINDOW_SEC = 600

STATUSES: tuple[str, ...] = ("new", "read", "done")
STATUS_LABELS = {"new": "ใหม่", "read": "อ่านแล้ว", "done": "จัดการแล้ว"}

CATEGORIES: tuple[str, ...] = ("problem", "request", "question")
CATEGORY_LABELS = {"problem": "แจ้งปัญหา", "request": "ขอให้เพิ่ม", "question": "สงสัย"}


class FeedbackConflict(Exception):
    """มีแอดมินอีกคนเปลี่ยนสถานะรายการนี้ไปแล้ว — ฝั่งเรียกต้องโหลดใหม่ก่อนกดซ้ำ"""

    def __init__(self, current: dict[str, Any]):
        super().__init__("feedback item was changed by someone else")
        self.current = current


class FeedbackRateLimited(Exception):
    """ส่งถี่เกินกำหนด"""


def _repo_root() -> str:
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))


def feedback_dir() -> str:
    raw = (os.environ.get("FEEDBACK_DIR") or "").strip()
    if raw:
        return os.path.normpath(os.path.abspath(raw))
    return os.path.join(_repo_root(), "data", "feedback")


def feedback_json_path() -> str:
    return os.path.join(feedback_dir(), "feedback.json")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _clean(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def norm_category(value: Any) -> str:
    v = str(value or "").strip().lower()
    return v if v in CATEGORIES else "problem"


def norm_status(value: Any) -> str:
    v = str(value or "").strip().lower()
    return v if v in STATUSES else "new"


def read_doc() -> dict[str, Any]:
    """
    อ่านทั้งก้อน — ไฟล์หายหรืออ่านไม่ออกคืนก้อนว่าง ไม่ raise

    ต้องครอบ read_locked เพราะบน Windows ถ้า reader ถือ handle ค้างตอน writer
    เรียก os.replace ตัวเขียนจะพังด้วย PermissionError
    """
    path = feedback_json_path()
    if not os.path.isfile(path):
        return {"version": 0, "items": []}
    try:
        with read_locked(path), open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.error("อ่านไฟล์ข้อเสนอแนะไม่ได้ (%s): %s — ถือว่ายังไม่มีรายการ", path, e)
        return {"version": 0, "items": []}
    if not isinstance(data, dict):
        return {"version": 0, "items": []}
    items = data.get("items")
    if not isinstance(items, list):
        items = []
    return {"version": int(data.get("version") or 0), "items": [r for r in items if isinstance(r, dict)]}


def _write_doc(doc: dict[str, Any]) -> None:
    path = feedback_json_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    atomic_write_json(path, doc, indent=2)


def _trim(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    เกินเพดานแล้วตัดอะไรก่อน — "จัดการแล้ว" ที่เก่าที่สุดก่อนเสมอ

    ห้ามตัดของที่ยังไม่มีใครอ่านทิ้งเงียบ ๆ ถ้าจำเป็นต้องตัดจริงให้ log ไว้ด้วย
    """
    if len(items) <= MAX_RECORDS:
        return items
    keep = list(items)
    done_oldest_first = sorted(
        (r for r in keep if r.get("status") == "done"),
        key=lambda r: str(r.get("ts") or ""),
    )
    for row in done_oldest_first:
        if len(keep) <= MAX_RECORDS:
            break
        keep.remove(row)
    if len(keep) > MAX_RECORDS:
        dropped = len(keep) - MAX_RECORDS
        keep = sorted(keep, key=lambda r: str(r.get("ts") or ""))[dropped:]
        logger.warning(
            "ข้อเสนอแนะเกิน %d รายการ — ตัดของเก่าที่ยังไม่ได้จัดการทิ้ง %d รายการ",
            MAX_RECORDS,
            dropped,
        )
    return keep


def _recent_count(items: list[dict[str, Any]], email: str) -> int:
    if not email:
        return 0
    since = datetime.now(timezone.utc) - timedelta(seconds=RATE_LIMIT_WINDOW_SEC)
    n = 0
    for row in items:
        if str(row.get("email") or "").lower() != email.lower():
            continue
        raw = str(row.get("ts") or "")
        try:
            ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts >= since:
            n += 1
    return n


def append_entry(
    *,
    email: str = "",
    role_hint: str = "",
    acting_admin_email: str = "",
    category: str = "problem",
    message: str = "",
    sup_id: str = "",
    sup_name: str = "",
    target_month: int | None = None,
    target_year: int | None = None,
    screen: str = "",
    app_version: str = "",
    user_agent: str = "",
) -> dict[str, Any]:
    """เพิ่ม 1 ข้อความ — คืนแถวที่บันทึกจริง (ผ่านการตัดความยาวแล้ว)"""
    row = {
        "id": uuid.uuid4().hex[:12],
        "ts": _now_iso(),
        "email": _clean(email, 200).lower(),
        "role_hint": _clean(role_hint, 30),
        "acting_admin_email": _clean(acting_admin_email, 200).lower(),
        "category": norm_category(category),
        "message": _clean(message, MAX_MESSAGE_CHARS),
        "sup_id": _clean(sup_id, 20).upper(),
        "sup_name": _clean(sup_name, 120),
        "target_month": int(target_month) if target_month else None,
        "target_year": int(target_year) if target_year else None,
        "screen": _clean(screen, 20),
        "app_version": _clean(app_version, 40),
        "user_agent": _clean(user_agent, 200),
        "status": "new",
        "admin_note": "",
        "handled_by": "",
        "handled_at": "",
        "rev": 0,
    }
    with _STORE_LOCK:
        doc = read_doc()
        items = doc["items"]
        if _recent_count(items, row["email"]) >= RATE_LIMIT_PER_EMAIL:
            raise FeedbackRateLimited()
        items.append(row)
        doc["items"] = _trim(items)
        doc["version"] = int(doc.get("version") or 0) + 1
        _write_doc(doc)
    logger.info(
        "ข้อเสนอแนะใหม่จาก %s (%s · ทีม %s) — %s",
        row["email"] or "-",
        CATEGORY_LABELS.get(row["category"], row["category"]),
        row["sup_id"] or "-",
        row["message"][:80],
    )
    return row


def read_items(*, status: str = "", category: str = "", limit: int = 200) -> list[dict[str, Any]]:
    """รายการล่าสุดก่อน — กรองด้วยสถานะ/ประเภทได้"""
    items = read_doc()["items"]
    want_status = str(status or "").strip().lower()
    want_cat = str(category or "").strip().lower()
    picked = [
        (i, r)
        for i, r in enumerate(items)
        if (not want_status or r.get("status") == want_status)
        and (not want_cat or r.get("category") == want_cat)
    ]
    # เวลาเก็บละเอียดแค่ระดับวินาที — สองข้อความในวินาทีเดียวกันจะเรียงสลับกันได้
    # ถ้าใช้ ts อย่างเดียว จึงใช้ลำดับที่ถูกบันทึกลงไฟล์เป็นตัวตัดสินร่วม
    picked.sort(key=lambda pair: (str(pair[1].get("ts") or ""), pair[0]), reverse=True)
    return [r for _, r in picked[: max(1, int(limit or 200))]]


def counts_by_status() -> dict[str, int]:
    items = read_doc()["items"]
    out = {s: 0 for s in STATUSES}
    for row in items:
        s = str(row.get("status") or "new")
        if s in out:
            out[s] += 1
    out["total"] = len(items)
    return out


def set_status(
    feedback_id: str,
    *,
    status: str,
    admin_note: str | None = None,
    handled_by: str = "",
    expected_rev: int | None = None,
) -> dict[str, Any]:
    """
    เปลี่ยนสถานะ 1 รายการ — คืนแถวหลังแก้

    `expected_rev` เป็นตัวกันสองแอดมินแก้รายการ**เดียวกัน**ทับกัน (คนละรายการไม่ชนกัน
    จึงเก็บ rev รายแถว ไม่ใช่ทั้งไฟล์) · ไม่บังคับลำดับสถานะ เพราะกดผิดแล้วต้องถอยได้
    """
    fid = str(feedback_id or "").strip()
    new_status = norm_status(status)
    with _STORE_LOCK:
        doc = read_doc()
        target = next((r for r in doc["items"] if str(r.get("id")) == fid), None)
        if target is None:
            raise ValueError(f"ไม่พบข้อเสนอแนะรหัส {fid}")
        if expected_rev is not None and int(target.get("rev") or 0) != int(expected_rev):
            raise FeedbackConflict(dict(target))
        target["status"] = new_status
        if admin_note is not None:
            target["admin_note"] = _clean(admin_note, MAX_NOTE_CHARS)
        target["handled_by"] = _clean(handled_by, 200).lower()
        target["handled_at"] = _now_iso()
        target["rev"] = int(target.get("rev") or 0) + 1
        doc["version"] = int(doc.get("version") or 0) + 1
        _write_doc(doc)
        return dict(target)
