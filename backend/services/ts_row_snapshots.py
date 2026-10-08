"""
สำเนาแถวเป้าใน Target Sun "ก่อนส่ง" ระดับแถว — ไว้สร้างไฟล์คืนค่าเมื่อส่งผิด (ผลตรวจ 7 ต.ค. 2026 ก5)

ทำไมต้องมี: เป้าตั้งต้นเดิม (`target_baseline.py`) เก็บแค่ยอดหีบรวมต่อ SKU + เงินต่อคน
จึงสร้างไฟล์นำเข้า Target Sun ไม่ได้ (ไม่รู้ว่าเดิมแต่ละแถว คน×สินค้า×เขต×คลัง มีกี่หีบ)
ขณะที่ทางส่งจริงอ่านแถวสดจาก Target Sun ก่อน POST ทุกครั้งอยู่แล้ว (`_live_target_snapshot`)
แต่ทิ้งไป — ตอนนี้เก็บไว้สองชุด:

  1. **ชุดแรกของงวด** `data/baselines/rows/{SL}_{ปี}_{เดือน}.json` — เขียนครั้งเดียว ไม่ทับเด็ดขาด
     = เป้าใน Target Sun ก่อนระบบเราส่งครั้งแรก (ถ้าตอนเก็บ ledger มีการส่งไปแล้ว — เช่นเพิ่งมีฟีเจอร์นี้
     กลางงวด หรือ data/ หาย — ติดธง `captured_after_send` ให้แอดมินรู้ว่าไม่ใช่ของตั้งต้นจริง)
  2. **ทุกการส่ง** `data/ts_presend/{SL}_{ปี}_{เดือน}/{เวลา}_{token}.json` — ไว้ย้อนการส่งครั้งใดครั้งหนึ่ง

ไฟล์คืนค่า (`build_restore_rows`) = แถวในสำเนาตามจำนวนเดิม + แถวที่ระบบเราเคยส่ง (ledger ปลายทางเดียวกัน)
แต่ไม่มีในสำเนา = 0 (Target Sun นำเข้าแบบ upsert ลบแถวไม่ได้ จึงต้องส่ง 0 ทับแถวที่เราสร้างเพิ่ม)
**ไม่มีปุ่มส่งคืนในแอป** — แอดมินดาวน์โหลดไฟล์ไปนำเข้าเองที่ Target Sun (ผู้ใช้เลือก 7 ต.ค. 2026)

ข้อจำกัด: สำเนาครอบเฉพาะพนักงานที่อยู่ในไฟล์ส่งรอบนั้น (`emp_codes` — คนที่การส่งไปแตะ)
ไฟล์คืนค่าจึงเติมคนที่ไม่อยู่ในสำเนาที่เลือก จากสำเนารอบถัดไปที่ครอบเขาเป็นรอบแรก (= ค่าก่อนเราแตะเขาครั้งแรก
หลังจุดที่เลือก) · คนที่ไม่มีสำเนาไหนครอบเลย ไม่ใส่ในไฟล์ (ห้ามส่ง 0 ทับเป้าที่ไม่รู้ค่าเดิม) และแจ้งใน summary
ไม่มีอะไรลบไฟล์พวกนี้อัตโนมัติ (ตัวล้าง cache วนเฉพาะไฟล์ชั้นบนใน data/)
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

from ..core.atomic_io import atomic_write_json, read_locked
from ..core.paths import safe_id

logger = logging.getLogger("target_allocation")

ROW_FIELDS = (
    "PRODUCTCODE", "SALESTYPE", "DIVISIONCODE", "SALESMANCODE",
    "AREACODE", "PROVINCECODE", "WAREHOUSECODE",
)
_SNAP_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")


def first_snapshot_path(sup_id: str, month: int, year: int) -> str:
    return f"data/baselines/rows/{safe_id(str(sup_id).strip().upper())}_{int(year)}_{int(month):02d}.json"


def presend_dir(sup_id: str, month: int, year: int) -> str:
    return f"data/ts_presend/{safe_id(str(sup_id).strip().upper())}_{int(year)}_{int(month):02d}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _clean_rows(raw_rows: list[dict]) -> list[dict]:
    """เก็บค่าตามที่ Target Sun ส่งมา (ไม่ normalize) — ไฟล์คืนค่าต้องตรงคีย์แถวจริง"""
    out: list[dict] = []
    for r in raw_rows or []:
        if not isinstance(r, dict):
            continue
        row = {k: ("" if r.get(k) is None else str(r.get(k)).strip()) for k in ROW_FIELDS}
        if not row["PRODUCTCODE"] or not row["SALESMANCODE"]:
            continue
        try:
            row["QUANTITYCASE"] = int(float(r.get("QUANTITYCASE") or 0))
        except (TypeError, ValueError):
            row["QUANTITYCASE"] = 0
        out.append(row)
    return out


def _norm(code) -> str:
    from .lakehouse import norm_emp_code

    return norm_emp_code(code)


def _payload(sup_id, month, year, rows, *, kind, import_url, user, token, captured_after_send, emp_codes) -> dict:
    return {
        "kind": kind,
        "sup_id": str(sup_id).strip().upper(),
        "target_month": int(month),
        "target_year": int(year),
        "captured_at": _now_iso(),
        "captured_by": user or "",
        "send_token": token or "",
        "import_url": import_url or "",
        "captured_after_send": bool(captured_after_send),
        # พนักงานที่สำเนานี้ครอบ (คนในไฟล์ส่งรอบนั้น) — คนนอกชุดนี้ "ไม่รู้ค่า" ไม่ใช่ "ไม่มีแถว"
        "emp_codes": sorted({_norm(e) for e in (emp_codes or [])} | {_norm(r["SALESMANCODE"]) for r in rows}),
        "row_count": len(rows),
        "total_boxes": int(sum(int(r["QUANTITYCASE"]) for r in rows)),
        "rows": rows,
    }


def save_presend_snapshot(
    sup_id: str,
    month: int,
    year: int,
    raw_rows: list[dict] | None,
    *,
    import_url: str = "",
    user: str = "",
    token: str = "",
    ledger_had_sends: bool = False,
    emp_codes: list[str] | None = None,
) -> dict[str, bool]:
    """
    เก็บแถวที่อ่านสดก่อน POST — คืน {"first": เพิ่งเขียนชุดแรกไหม, "presend": เขียนรายครั้งได้ไหม}

    ไม่ throw: การเก็บหลักฐานต้องไม่ทำให้การส่งล้ม (แต่ log error ให้เห็น)
    raw_rows ว่าง (อ่านได้แต่ไม่มีแถว) ก็เก็บ — "เดิมไม่มีเป้าเลย" เป็นข้อมูลที่ใช้คืนค่าได้
    """
    result = {"first": False, "presend": False}
    if raw_rows is None:
        return result
    rows = _clean_rows(raw_rows)
    sid = str(sup_id).strip().upper()
    try:
        d = presend_dir(sid, month, year)
        os.makedirs(d, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        tok = re.sub(r"[^A-Za-z0-9]", "", str(token or ""))[:12] or "nosig"
        path = os.path.join(d, f"{stamp}_{tok}.json")
        n = 1
        while os.path.exists(path):  # ห้ามทับ — ชื่อชนกันในวินาทีเดียวกันให้เติมเลข
            n += 1
            path = os.path.join(d, f"{stamp}_{tok}_{n}.json")
        atomic_write_json(
            path,
            _payload(sid, month, year, rows, kind="presend", import_url=import_url, user=user,
                     token=token, captured_after_send=ledger_had_sends, emp_codes=emp_codes),
            ensure_ascii=False,
        )
        result["presend"] = True
    except Exception:
        logger.exception("เก็บสำเนาแถว Target Sun ก่อนส่งไม่สำเร็จ (%s %s-%02d)", sid, year, month)
    try:
        fp = first_snapshot_path(sid, month, year)
        if not os.path.isfile(fp):
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            # เช็ค "ยังไม่มี → เขียน" ใต้ล็อกเดียวกัน (แบบเดียวกับ target_baseline.capture_baseline_once)
            with read_locked(fp):
                if not os.path.isfile(fp):
                    atomic_write_json(
                        fp,
                        _payload(sid, month, year, rows, kind="first", import_url=import_url, user=user,
                                 token=token, captured_after_send=ledger_had_sends, emp_codes=emp_codes),
                        ensure_ascii=False,
                    )
                    result["first"] = True
                    if ledger_had_sends:
                        logger.warning(
                            "สำเนาเป้าตั้งต้นระดับแถว %s %s-%02d เก็บหลังเคยส่งแล้ว — ไม่ใช่ค่าก่อนส่งครั้งแรกจริง",
                            sid, year, month,
                        )
    except Exception:
        logger.exception("เก็บสำเนาเป้าตั้งต้นระดับแถวไม่สำเร็จ (%s %s-%02d)", sid, year, month)
    return result


def _read_json(path: str) -> dict | None:
    try:
        with read_locked(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.warning("อ่านสำเนาแถว %s ไม่ได้: %s", path, e)
        return None


def _summary(snap_id: str, data: dict) -> dict:
    return {
        "id": snap_id,
        "kind": data.get("kind"),
        "captured_at": data.get("captured_at"),
        "captured_by": data.get("captured_by"),
        "send_token": (str(data.get("send_token") or "")[:8] or None),
        "import_url": data.get("import_url"),
        "captured_after_send": bool(data.get("captured_after_send")),
        "row_count": int(data.get("row_count") or 0),
        "total_boxes": int(data.get("total_boxes") or 0),
    }


def list_snapshots(sup_id: str, month: int, year: int) -> list[dict]:
    """ชุดแรกก่อน แล้วรายครั้งจากใหม่ไปเก่า"""
    out: list[dict] = []
    fp = first_snapshot_path(sup_id, month, year)
    if os.path.isfile(fp):
        d = _read_json(fp)
        if d:
            out.append(_summary("first", d))
    pdir = presend_dir(sup_id, month, year)
    if os.path.isdir(pdir):
        for name in sorted(os.listdir(pdir), reverse=True):
            if not name.endswith(".json"):
                continue
            d = _read_json(os.path.join(pdir, name))
            if d:
                out.append(_summary(name[:-5], d))
    return out


def read_snapshot(sup_id: str, month: int, year: int, snap_id: str) -> dict | None:
    sid = str(snap_id or "").strip()
    if sid == "first":
        path = first_snapshot_path(sup_id, month, year)
    else:
        if not _SNAP_ID_RE.match(sid) or ".." in sid:
            return None
        path = os.path.join(presend_dir(sup_id, month, year), f"{sid}.json")
    return _read_json(path) if os.path.isfile(path) else None


def _key(row: dict) -> str:
    # normalize แบบเดียวกับฝั่งส่ง/อ่านสด — ใช้แค่ "จับคู่" แถวในสำเนากับ ledger ค่าที่ลงไฟล์ยังเป็นค่าดิบ
    from .lakehouse import _live_target_row_key

    return _live_target_row_key(row)


def _snap_emps(snap: dict) -> set[str]:
    codes = snap.get("emp_codes")
    if isinstance(codes, list) and codes:
        return {_norm(e) for e in codes}
    return {_norm(r.get("SALESMANCODE")) for r in (snap.get("rows") or [])}


def build_restore_rows(sup_id: str, month: int, year: int, snap_id: str) -> tuple[list[dict], dict]:
    """
    แถวของไฟล์คืนค่า + สรุป — คืนเป้าให้เป็น "ก่อนจุดที่เลือก"

    1. คนที่สำเนาที่เลือกครอบ: แถวตามสำเนา
    2. คนที่ไม่อยู่ในสำเนานั้นแต่การส่งรอบหลังไปแตะ: แถวจากสำเนารอบถัดไปที่ครอบเขาเป็นรอบแรก
    3. แถวที่เราเคยส่ง (ledger ปลายทางเดียวกัน) ของคนที่ครอบแล้ว แต่ไม่มีในสำเนา = 0 (แถวที่เราสร้างเพิ่ม)
    4. แถว ledger ของคนที่ไม่มีสำเนาไหนครอบ — ไม่ใส่ (ไม่รู้ค่าเดิม ห้ามส่ง 0 ทับเป้าจริง) แจ้งใน summary

    ledger ใช้คีย์ normalize แล้ว (เก็บเฉพาะคีย์) ค่าในไฟล์ของแถวกลุ่ม 3 จึงมาจากคีย์ ledger
    """
    snap = read_snapshot(sup_id, month, year, snap_id)
    if not snap:
        raise FileNotFoundError("ไม่พบสำเนาที่เลือก")
    url = str(snap.get("import_url") or "")
    t0 = str(snap.get("captured_at") or "")
    chain: list[dict] = [snap]
    for it in sorted(
        (i for i in list_snapshots(sup_id, month, year) if i["kind"] == "presend" and i["id"] != snap_id),
        key=lambda i: str(i.get("captured_at") or ""),
    ):
        if str(it.get("captured_at") or "") < t0 or (url and it.get("import_url") and it["import_url"] != url):
            continue
        later = read_snapshot(sup_id, month, year, it["id"])
        if later:
            chain.append(later)

    rows: list[dict] = []
    covered: set[str] = set()
    filled_from_later: set[str] = set()
    for i, sn in enumerate(chain):
        new = _snap_emps(sn) - covered
        if not new:
            continue
        rows += [dict(r) for r in (sn.get("rows") or []) if _norm(r.get("SALESMANCODE")) in new]
        covered |= new
        if i:
            filled_from_later |= new

    have = {_key(r) for r in rows}
    zeroed: list[dict] = []
    uncovered: set[str] = set()
    try:
        from . import sent_ledger

        led = sent_ledger.read_ledger(sup_id, month, year) or {}
    except Exception:
        led = {}
    if not url or not led.get("import_url") or led.get("import_url") == url:
        for k in (led.get("rows") or {}):
            if k in have:
                continue
            parts = (str(k).split("|") + [""] * 7)[:7]
            sku, emp, st, dv, ar, pv, wh = parts
            if _norm(emp) not in covered:
                uncovered.add(_norm(emp))
                continue
            zeroed.append({
                "PRODUCTCODE": sku, "SALESTYPE": st, "DIVISIONCODE": dv, "SALESMANCODE": emp,
                "AREACODE": ar, "PROVINCECODE": pv, "WAREHOUSECODE": wh, "QUANTITYCASE": 0,
            })
            have.add(k)
    summary = {
        **_summary(snap_id, snap),
        "restore_rows": len(rows),
        "zeroed_rows": len(zeroed),
        "restore_boxes": int(sum(int(r.get("QUANTITYCASE") or 0) for r in rows)),
        "emps_from_later_snapshots": sorted(filled_from_later),
        "uncovered_emps": sorted(uncovered),
    }
    return rows + zeroed, summary
