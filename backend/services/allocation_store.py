"""
เก็บ snapshot ผลกระจายหีบบน server ต่อ Supervisor × งวด
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any

from ..core.atomic_io import atomic_write_json, read_locked
from ..core.paths import safe_id

logger = logging.getLogger("target_allocation")

# RLock: write_snapshot ต้องอ่าน snapshot เดิมใต้ lock เดียวกัน (compare-and-swap)
# และ mark_sent_targetsun ก็เป็น read-modify-write ที่ต้องอะตอมมิกทั้งก้อน
_STORE_LOCK = threading.RLock()
_VALID_STATUS = frozenset({"draft", "optimized", "sent_targetsun"})


class SnapshotConflict(Exception):
    """มีคนอื่นบันทึกทับไปแล้วระหว่างที่ client ถือข้อมูลเก่าอยู่"""

    def __init__(self, current: dict[str, Any] | None):
        super().__init__("snapshot conflict")
        self.current = current or {}


class SnapshotPreconditionRequired(Exception):
    """เปิดโหมดบังคับ if_match_version แล้ว แต่ client ไม่ส่งมา (tab เก่า)"""

    def __init__(self, current: dict[str, Any] | None):
        super().__init__("precondition required")
        self.current = current or {}


def require_if_match() -> bool:
    """
    ค่าเริ่มต้น = ปิด — เพื่อให้ tab เก่าที่ยังไม่ส่ง if_match_version บันทึกได้เหมือนเดิม
    เปิดเป็น 1 ได้หลังยืนยันว่าไม่มี save_allocation_no_precondition ใน usage log แล้ว
    """
    return (os.environ.get("ALLOC_REQUIRE_IF_MATCH") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _repo_root() -> str:
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))


def allocations_dir() -> str:
    raw = (os.environ.get("ALLOCATIONS_DATA_DIR") or "").strip()
    if raw:
        return os.path.normpath(os.path.abspath(raw))
    return os.path.join(_repo_root(), "data", "allocations")


def allocation_snapshot_path(sup_id: str, month: int, year: int) -> str:
    return os.path.join(allocations_dir(), f"{safe_id(sup_id)}_{int(year)}_{int(month):02d}.json")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalize_sup(s: str) -> str:
    return str(s or "").strip().upper()


def _validate_body(body: dict[str, Any]) -> dict[str, Any]:
    sup = _normalize_sup(body.get("sup_id"))
    month = int(body.get("target_month") or 0)
    year = int(body.get("target_year") or 0)
    if not sup or not (1 <= month <= 12) or year < 2020:
        raise ValueError("sup_id, target_month, target_year ไม่ถูกต้อง")
    status = str(body.get("status") or "draft").strip().lower()
    if status not in _VALID_STATUS:
        raise ValueError(f"status ต้องเป็น {' | '.join(sorted(_VALID_STATUS))}")
    allocs = body.get("allocations")
    if not isinstance(allocs, list):
        raise ValueError("allocations ต้องเป็น list")
    yellow = body.get("yellow")
    if yellow is not None and not isinstance(yellow, dict):
        raise ValueError("yellow ต้องเป็น object")
    yellow_locked = body.get("yellow_locked")
    out: dict[str, Any] = {
        "sup_id": sup,
        "target_month": month,
        "target_year": year,
        "status": status,
        "allocations": allocs,
        "yellow": yellow if isinstance(yellow, dict) else {},
        "yellow_locked": yellow_locked if isinstance(yellow_locked, dict) else {},
        "strategy": str(body.get("strategy") or "").strip(),
        "updated_by": str(body.get("updated_by") or "").strip(),
        "updated_at": _now_iso(),
    }
    sent_at = body.get("target_sun_sent_at")
    if sent_at:
        out["target_sun_sent_at"] = str(sent_at)
    elif status == "sent_targetsun":
        out["target_sun_sent_at"] = _now_iso()
    return out


def _read_snapshot_unlocked(path: str) -> dict[str, Any] | None:
    if not os.path.isfile(path):
        return None
    try:
        with read_locked(path), open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception as e:
        logger.warning("read allocation snapshot %s: %s", path, e)
    return None


def read_snapshot(sup_id: str, month: int, year: int) -> dict[str, Any] | None:
    with _STORE_LOCK:
        return _read_snapshot_unlocked(allocation_snapshot_path(sup_id, month, year))


def _version_of(snap: dict[str, Any] | None) -> int:
    try:
        return int((snap or {}).get("version") or 0)
    except (TypeError, ValueError):
        return 0


def write_snapshot(
    body: dict[str, Any],
    *,
    expected_version: int | None = None,
) -> dict[str, Any]:
    """
    บันทึก snapshot แบบ compare-and-swap

    expected_version = version ที่ client เห็นตอนโหลด
      - ตรงกับบนดิสก์ → เขียนได้ version +1
      - ไม่ตรง        → SnapshotConflict (มีคนบันทึกทับไปแล้ว)
      - None          → เขียนทับเลย (พฤติกรรมเดิม) เว้นแต่เปิด ALLOC_REQUIRE_IF_MATCH

    ใช้ version (int) ไม่ใช่ updated_at เพราะ _now_iso() ตัดหน่วยไมโครวินาที
    และ autosave ฝั่ง frontend debounce 800ms — สอง save ในวินาทีเดียวกันจะได้
    updated_at เท่ากัน ทำให้ precondition แบบ timestamp มีรูให้เล็ดลอด
    """
    row = _validate_body(body)
    path = allocation_snapshot_path(row["sup_id"], row["target_month"], row["target_year"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _STORE_LOCK:
        current = _read_snapshot_unlocked(path)
        cur_ver = _version_of(current)
        if expected_version is not None:
            if cur_ver != int(expected_version):
                raise SnapshotConflict(current)
        elif current is not None and require_if_match():
            raise SnapshotPreconditionRequired(current)
        # "เคยส่ง Target Sun แล้ว" ต้องอยู่ถาวร แม้สถานะจะกลับเป็น draft หลังแก้ต่อ
        # เก็บฝั่ง server ไม่พึ่ง client ส่งกลับมา — client เก่าที่ไม่ส่ง field นี้จะลบประวัติทิ้ง
        if not row.get("target_sun_sent_at") and current:
            prev_sent = current.get("target_sun_sent_at")
            if prev_sent:
                row["target_sun_sent_at"] = str(prev_sent)
        row["version"] = cur_ver + 1
        atomic_write_json(path, row, indent=2)
    return row


def mark_sent_targetsun(
    sup_id: str,
    month: int,
    year: int,
    *,
    updated_by: str = "",
    allocations: list[dict[str, Any]] | None = None,
    yellow: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # อ่าน+เขียนต้องอยู่ใน lock เดียวกัน ไม่งั้นมีคน save คั่นกลางแล้ว allocations เดิมหาย
    with _STORE_LOCK:
        existing = _read_snapshot_unlocked(allocation_snapshot_path(sup_id, month, year)) or {}
        # ต้องส่ง expected_version เสมอ ไม่ใช่ None:
        # ถ้าเปิด ALLOC_REQUIRE_IF_MATCH=1 แล้วส่ง None + มี snapshot เดิมอยู่
        # write_snapshot จะโยน SnapshotPreconditionRequired → "ส่ง Target Sun" พังทุกครั้ง
        # อ่านค่าใต้ _STORE_LOCK เดียวกันอยู่แล้ว (RLock) จึงยังอะตอมมิก
        expected = _version_of(existing) if existing else None
        body: dict[str, Any] = {
            "sup_id": _normalize_sup(sup_id),
            "target_month": int(month),
            "target_year": int(year),
            "status": "sent_targetsun",
            "allocations": (
                allocations if allocations is not None else existing.get("allocations") or []
            ),
            "yellow": yellow if yellow is not None else existing.get("yellow") or {},
            "yellow_locked": existing.get("yellow_locked") or {},
            "strategy": existing.get("strategy") or "",
            "updated_by": updated_by or existing.get("updated_by") or "",
        }
        return write_snapshot(body, expected_version=expected)


def _snapshot_has_work(snap: dict[str, Any]) -> bool:
    if snap.get("status") in ("optimized", "sent_targetsun"):
        return True
    allocs = snap.get("allocations") or []
    return any(
        float(a.get("allocated_boxes") or 0) > 0
        for a in allocs
        if isinstance(a, dict)
    )


def delete_snapshot(sup_id: str, month: int, year: int) -> bool:
    """ลบ snapshot — คืน True ถ้าลบได้ (มีไฟล์)"""
    path = allocation_snapshot_path(sup_id, month, year)
    with _STORE_LOCK:
        if not os.path.isfile(path):
            return False
        try:
            os.unlink(path)
            return True
        except OSError as e:
            logger.warning("delete allocation snapshot %s: %s", path, e)
            raise


def list_all_snapshots(
    month: int | None = None,
    year: int | None = None,
) -> list[dict[str, Any]]:
    """รายการ snapshot ทั้งหมด — filter งวดได้"""
    root = allocations_dir()
    if not os.path.isdir(root):
        return []
    out: list[dict[str, Any]] = []
    for name in sorted(os.listdir(root)):
        if not name.endswith(".json"):
            continue
        base = name[:-5]
        parts = base.rsplit("_", 2)
        if len(parts) != 3:
            continue
        sid, y_s, m_s = parts
        try:
            y_i, m_i = int(y_s), int(m_s)
        except ValueError:
            continue
        if month is not None and year is not None and (m_i != int(month) or y_i != int(year)):
            continue
        snap = read_snapshot(sid, m_i, y_i)
        if not snap or not _snapshot_has_work(snap):
            continue
        allocs = snap.get("allocations") or []
        out.append(
            {
                "sup_id": snap.get("sup_id") or _normalize_sup(sid),
                "target_month": m_i,
                "target_year": y_i,
                "status": snap.get("status"),
                "updated_at": snap.get("updated_at"),
                "updated_by": snap.get("updated_by"),
                "target_sun_sent_at": snap.get("target_sun_sent_at"),
                "allocation_rows": len(allocs),
                "strategy": snap.get("strategy") or "",
            }
        )
    out.sort(
        key=lambda r: (
            -_snapshot_updated_ts(r),
            -int(r["target_year"]),
            -int(r["target_month"]),
            str(r["sup_id"]),
        )
    )
    return out


def _snapshot_updated_ts(row: dict[str, Any]) -> float:
    raw = str(row.get("updated_at") or "").strip()
    if not raw:
        return 0.0
    try:
        from datetime import datetime

        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw).timestamp()
    except (TypeError, ValueError):
        return 0.0


def list_summaries(
    sup_ids: list[str],
    month: int,
    year: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for sid in sup_ids:
        norm = _normalize_sup(sid)
        snap = read_snapshot(norm, month, year)
        if not snap or not _snapshot_has_work(snap):
            out.append({"sup_id": norm, "has_snapshot": False})
            continue
        allocs = snap.get("allocations") or []
        out.append(
            {
                "sup_id": snap.get("sup_id") or norm,
                "has_snapshot": True,
                "status": snap.get("status"),
                "updated_at": snap.get("updated_at"),
                "updated_by": snap.get("updated_by"),
                "target_sun_sent_at": snap.get("target_sun_sent_at"),
                "allocation_rows": len(allocs),
                "strategy": snap.get("strategy") or "",
            }
        )
    return out
