"""
ส่งไฟล์ Excel TGA ไปบริการ TargetSun / SPC (Oracle ฝั่ง UAT/Prod อยู่ที่ service นั้น).

เอกสาร: targetsun-importTargetSalesmanNextFromExcel.md
ค่าเริ่มต้น UAT: https://spcuatws.sahapat.com/spc/targetsun/importTargetSalesmanNextFromExcel
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path

import requests
from fastapi import HTTPException
from requests import exceptions as req_exc

from ..core.atomic_io import atomic_write_text
from ..schemas import LakehouseUploadRequest
from .lakehouse import (
    _live_target_snapshot,
    assert_target_snapshot_is_fresh,
    live_grain_for_pairs,
    norm_emp_code,
    prepare_lakehouse_xlsx,
    team_emp_codes_from_grain,
    verify_after_send,
    verify_row_count_after_send,
    warehouse_conflicts,
)
from . import sent_ledger
from .targetsun_endpoints import targetsun_import_excel_url

logger = logging.getLogger("target_allocation")

_PREPARE_DIR = Path("data/ts_prepare")
_PREPARE_TTL_SEC = 30 * 60

# กันกดส่งซ้ำ/retry ระหว่างที่ POST เดิมของ token เดียวกันยังค้างอยู่จริง (ได้ถึง
# TARGETSUN_IMPORT_TIMEOUT_SEC วินาที ค่าเริ่มต้น 600) — ไม่มีตัวนี้แล้ว double-click หรือ
# client timeout-then-retry จะยิงไฟล์เดิมเข้า Target Sun จริงสองรอบ เป็น in-process lock
# ล้วนๆ ใช้ได้เพราะทั้งระบบรันเป็น uvicorn worker เดียวเท่านั้น (ข้อสมมติที่มีอยู่แล้ว
# ทั้งระบบ ดู docs/CONCURRENCY.md) ไม่ต้องล็อกข้าม process
_import_in_flight_lock = threading.Lock()
_import_in_flight_tokens: set[str] = set()


def _claim_import_token(token: str) -> None:
    with _import_in_flight_lock:
        if token in _import_in_flight_tokens:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "send_already_in_progress",
                    "message": "กำลังส่งไฟล์นี้อยู่ — กรุณารอสักครู่ อย่ากดส่งซ้ำ",
                },
            )
        _import_in_flight_tokens.add(token)


def _release_import_token(token: str) -> None:
    with _import_in_flight_lock:
        _import_in_flight_tokens.discard(token)


# กันสองคนส่งทีมเดียวกันงวดเดียวกันซ้อนกัน — การนับแถว "ก่อนส่ง/หลังส่ง" ของรอบหนึ่ง
# จะนับแถวของอีกรอบปนเข้ามา แล้วแจ้งเตือนว่าจำนวนแถวไม่ตรงทั้งที่ไม่มีอะไรผิด
# (token คนละใบจึงไม่ติดล็อกข้างบน) ล็อกในโปรเซสพอ — ระบบรัน worker เดียว
_team_send_in_flight: set[tuple[str, int, int]] = set()


def _claim_team_send(sup_id: str, month: int, year: int) -> tuple[str, int, int]:
    key = (str(sup_id or "").strip().upper(), int(month), int(year))
    with _import_in_flight_lock:
        if key in _team_send_in_flight:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "team_send_in_progress",
                    "message": (
                        f"มีการส่งเป้าของทีม {key[0]} งวด {key[1]:02d}/{key[2]} อยู่แล้ว — "
                        "รอให้รอบนั้นเสร็จก่อน แล้วค่อยส่งอีกครั้ง"
                    ),
                },
            )
        _team_send_in_flight.add(key)
    return key


def _release_team_send(key: tuple[str, int, int]) -> None:
    with _import_in_flight_lock:
        _team_send_in_flight.discard(key)


def _prepare_dir() -> Path:
    d = _PREPARE_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cleanup_stale_prepare_files() -> None:
    cutoff = time.time() - _PREPARE_TTL_SEC
    try:
        for meta_path in _prepare_dir().glob("*.json"):
            try:
                if meta_path.stat().st_mtime < cutoff:
                    token = meta_path.stem
                    xlsx = _prepare_dir() / f"{token}.xlsx"
                    meta_path.unlink(missing_ok=True)
                    xlsx.unlink(missing_ok=True)
            except OSError:
                pass
    except OSError:
        pass


def _save_prepare_bundle(
    token: str,
    *,
    content: bytes,
    fname: str,
    sup_id: str,
    nrow: int,
    zero_rows: int,
    dropped_dims: int,
    not_in_ts: list,
    upload_user_code: str | None,
    shortfall: list | None = None,
    sku_totals: dict | None = None,
    excluded_skus: list | None = None,
    target_month: int | None = None,
    target_year: int | None = None,
    emp_codes: list | None = None,
    new_rows_count: int = 0,
    new_rows_with_boxes_count: int = 0,
    stale_rows_cleared_count: int = 0,
    import_row_keys: list | None = None,
    send_batch_id: str | None = None,
    full_send: bool = False,
    team_target_mismatches: list | None = None,
    file_rows: list | None = None,
) -> None:
    _prepare_dir()
    (_prepare_dir() / f"{token}.xlsx").write_bytes(content)
    if file_rows is not None:
        # แถวของไฟล์แบบตรงตัว — ใช้เทียบว่าลงครบไหม และส่งซ้ำเฉพาะแถวที่ตกหล่น
        (_prepare_dir() / f"{token}.rows.json").write_text(
            json.dumps(file_rows, ensure_ascii=False), encoding="utf-8"
        )
    shortfall = shortfall or []
    meta = {
        "filename": fname,
        "sup_id": sup_id.strip().upper(),
        # ยอดต่อ SKU ของไฟล์ที่เตรียมไว้ — ให้ด่านตรวจระดับชุดรวมข้ามทีมได้
        # โดยไม่ต้องแกะ .xlsx ซ้ำ (และไม่ต้องเชื่อตัวเลขที่ client ส่งมา)
        "sku_totals": {str(k): int(v) for k, v in (sku_totals or {}).items()},
        "excluded_skus": [str(s) for s in (excluded_skus or [])],
        "target_month": int(target_month) if target_month else None,
        "target_year": int(target_year) if target_year else None,
        # รหัสพนักงานที่อยู่ในไฟล์นี้ — ใช้ตรวจยอดที่ลงจริงหลังส่ง ต้องเป็นชุดเดียว
        # กับที่อยู่ในไฟล์ ไม่ใช่ทั้งทีม ไม่งั้นสองฝั่งครอบคลุมคนละกลุ่มแล้วฟ้องผิด
        "emp_codes": [str(e) for e in (emp_codes or [])],
        "rows_sent": nrow,
        "zero_rows_sent": zero_rows,
        "rows_dropped_missing_dims": dropped_dims,
        "rows_not_in_targetsun": not_in_ts,
        "rows_not_in_targetsun_count": dropped_dims,
        # แถวที่ "สร้างใหม่" เพราะปลายทางไม่เคยมีคู่นี้ — เสี่ยงคลังไม่ตรงกับที่มีอยู่
        # แล้วกลายเป็นแถวคู่ขนานคนละคลัง (11.3 / ปริศนา SL453)
        "new_rows_count": int(new_rows_count),
        "new_rows_with_boxes_count": int(new_rows_with_boxes_count),
        # แถวเป้าเก่าที่หลุดจากรอบนี้แล้วถูกล้าง (ส่ง 0 ไปทับ) — ค8
        "stale_rows_cleared_count": int(stale_rows_cleared_count),
        # คีย์เต็มของทุกแถวในไฟล์นี้ — ใช้เทียบกับสแนปช็อต "ก่อนส่ง" ตอนตรวจจำนวนแถว
        # หลังส่ง (verify_row_count_after_send) หาว่ากี่แถวที่ Target Sun ยังไม่เคยมี
        "import_row_keys": [str(k) for k in (import_row_keys or [])],
        # คู่ที่ผู้ใช้ต้องไปเพิ่มจำนวนเองใน Target Sun — ต้องติดไปถึงหน้าจอ "ส่งสำเร็จ"
        "shortfall": shortfall,
        "shortfall_boxes": sum(int(s.get("missing_boxes") or 0) for s in shortfall),
        "upload_user_code": upload_user_code,
        # ส่งรวมภาค — token ของชุดนี้ส่งได้ก็ต่อเมื่อยอดรวมทั้งชุดผ่านการตรวจแล้ว
        # (verify_send_batch → mark_batch_verified) ดู import_prepared_targetsun
        "send_batch_id": (str(send_batch_id).strip() or None) if send_batch_id else None,
        "batch_verified": False,
        # ส่งทุกแบรนด์ทุกสินค้า — ด่านยอดรวมทั้งชุดตรวจ SKU ที่มีเป้าแต่ไม่อยู่ในไฟล์ด้วย
        "full_send": bool(full_send),
        # ยอดรายทีมที่ต่างจากเป้าทีม (โหมดรวมภาค ไม่บล็อกรายทีม) — เก็บไว้ให้ตรวจย้อนหลัง
        "team_target_mismatches": list(team_target_mismatches or [])[:50],
        "created_at": time.time(),
    }
    (_prepare_dir() / f"{token}.json").write_text(
        json.dumps(meta, ensure_ascii=False),
        encoding="utf-8",
    )


def _load_prepare_bundle(
    token: str, sup_id: str, *, require_owner: bool = True
) -> tuple[bytes, str, dict]:
    """
    require_owner=True (ทางส่งจริง) — sup_id ต้องระบุและตรงกับเจ้าของไฟล์เสมอ
    เดิม sup_id ว่างแล้วข้ามการตรวจเจ้าของไปเลย (ผลตรวจ §2.9)
    มีแค่ load_prepare_batch ที่อ่านหลายไฟล์พร้อมกันเท่านั้นที่ปิด (ผู้เรียกตรวจสิทธิ์รายทีมเอง)
    """
    tok = (token or "").strip()
    if not tok or "/" in tok or "\\" in tok or ".." in tok:
        raise HTTPException(400, detail="prepare_token ไม่ถูกต้อง")
    meta_path = _prepare_dir() / f"{tok}.json"
    xlsx_path = _prepare_dir() / f"{tok}.xlsx"
    if not meta_path.is_file() or not xlsx_path.is_file():
        raise HTTPException(
            404,
            detail="ไม่พบไฟล์ที่เตรียมไว้ — อาจหมดอายุ กรุณากดส่งใหม่อีกครั้ง",
        )
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise HTTPException(500, detail="อ่านข้อมูลเตรียมส่งไม่สำเร็จ") from e
    created = float(meta.get("created_at") or 0)
    if created and (time.time() - created) > _PREPARE_TTL_SEC:
        meta_path.unlink(missing_ok=True)
        xlsx_path.unlink(missing_ok=True)
        raise HTTPException(
            404,
            detail="ไฟล์เตรียมส่งหมดอายุแล้ว — กรุณากดส่งใหม่อีกครั้ง",
        )
    expected_sup = str(meta.get("sup_id") or "").strip().upper()
    got_sup = str(sup_id or "").strip().upper()
    if require_owner and not got_sup:
        raise HTTPException(400, detail="ต้องระบุรหัส Supervisor ของไฟล์ที่จะส่ง")
    if require_owner and not expected_sup:
        raise HTTPException(409, detail="ไฟล์ที่เตรียมไว้ไม่มีรหัสเจ้าของ — กรุณากดส่งใหม่อีกครั้ง")
    if expected_sup and got_sup and expected_sup != got_sup:
        raise HTTPException(403, detail="prepare_token ไม่ตรงกับ Supervisor ที่เลือก")
    try:
        content = xlsx_path.read_bytes()
    except OSError as e:
        raise HTTPException(500, detail="อ่านไฟล์ Excel ที่เตรียมไว้ไม่สำเร็จ") from e
    fname = str(meta.get("filename") or "targetsun_upload.xlsx")
    return content, fname, meta


def load_prepare_batch(tokens: list[str]) -> list[dict]:
    """
    อ่าน meta ของ prepare bundle หลายใบพร้อมกัน — ใช้ตรวจยอดรวมทั้งชุดก่อนส่ง

    คืนเฉพาะ meta (ไม่อ่านตัวไฟล์ Excel) เพราะด่านนี้ตรวจแค่ตัวเลข
    ผู้เรียกต้องตรวจสิทธิ์ราย sup_id ที่ได้กลับไปเองก่อนใช้งานต่อ
    """
    if not tokens:
        raise HTTPException(400, detail="ไม่มี prepare_token ให้ตรวจ")
    seen: set[str] = set()
    metas: list[dict] = []
    for tok in tokens:
        t = str(tok or "").strip()
        if not t or t in seen:
            continue
        seen.add(t)
        _, _, meta = _load_prepare_bundle(t, "", require_owner=False)
        meta = dict(meta)
        meta["prepare_token"] = t
        metas.append(meta)
    if not metas:
        raise HTTPException(400, detail="ไม่มี prepare_token ที่ใช้ได้")
    return metas


def mark_batch_verified(metas: list[dict]) -> None:
    """
    จดลง bundle ทุกใบว่ายอดรวมทั้งชุดผ่านการตรวจแล้ว — เรียกหลัง verify_send_batch ผ่าน

    ด่านนี้ต้องอยู่ที่ server: เดิมการตรวจยอดรวมทั้งภาคอยู่แค่ที่หน้าเว็บเรียก
    client ที่ข้ามการเรียกนั้น (หรือหน้าเว็บรุ่นเก่า) ก็ส่งได้เลย
    """
    tokens = sorted(str(m.get("prepare_token") or "").strip() for m in metas)
    sup_ids = sorted({str(m.get("sup_id") or "").strip().upper() for m in metas} - {""})
    for m in metas:
        tok = str(m.get("prepare_token") or "").strip()
        if not tok:
            continue
        meta = {k: v for k, v in m.items() if k != "prepare_token"}
        meta["batch_verified"] = True
        meta["batch_tokens"] = tokens
        # ทีมทั้งหมดในรอบนี้ — ให้แจ้งเตือนผลนับแถวถึงเจ้าของทุก SL ในรอบ
        meta["batch_sup_ids"] = sup_ids
        atomic_write_text(
            str(_prepare_dir() / f"{tok}.json"), json.dumps(meta, ensure_ascii=False)
        )


def _assert_batch_verified(token: str, meta: dict) -> None:
    """token ของการส่งรวมภาคต้องผ่านด่านยอดรวมทั้งชุดก่อนเสมอ ไม่มีทางยืนยันข้าม"""
    if not str(meta.get("send_batch_id") or "").strip():
        return  # ส่งทีมเดียว — ด่านรายทีมบังคับยอดตรงเป้าไปแล้วตอน prepare
    if meta.get("batch_verified") and token in (meta.get("batch_tokens") or []):
        return
    raise HTTPException(
        status_code=409,
        detail={
            "code": "send_batch_not_verified",
            "message": (
                "ยังไม่ได้ส่ง — การส่งรวมหลายทีมต้องผ่านการตรวจยอดรวมทั้งภาคก่อน"
            ),
            "hint_th": "กดส่งใหม่อีกครั้ง ระบบจะตรวจยอดรวมของทุกทีมก่อนส่ง",
        },
    )


def _delete_prepare_bundle(token: str) -> None:
    tok = (token or "").strip()
    if not tok:
        return
    (_prepare_dir() / f"{tok}.json").unlink(missing_ok=True)
    (_prepare_dir() / f"{tok}.xlsx").unlink(missing_ok=True)
    (_prepare_dir() / f"{tok}.rows.json").unlink(missing_ok=True)


# ── ไฟล์ที่ส่งไปแล้ว — เก็บไว้ส่งซ้ำเฉพาะแถวที่ตกหล่น ─────────────────────────
_SENT_DIR = Path("data/ts_sent")
_SENT_TTL_SEC = 14 * 24 * 3600


def _file_rows(df) -> list[dict]:
    from .lakehouse import LAKEHOUSE_CSV_COLUMNS

    return [
        {c: ("" if v is None else str(v)) for c, v in zip(LAKEHOUSE_CSV_COLUMNS, row)}
        for row in df[LAKEHOUSE_CSV_COLUMNS].itertuples(index=False)
    ]


def _file_qty_by_key(rows: list[dict]) -> dict[str, int]:
    from .lakehouse import import_row_key_series

    if not rows:
        return {}
    import pandas as pd

    df = pd.DataFrame(rows)
    keys = import_row_key_series(df)
    qty = pd.to_numeric(df["QUANTITYCASE"], errors="coerce").fillna(0).astype(int)
    out: dict[str, int] = {}
    for k, q in zip(keys, qty):
        out[k] = out.get(k, 0) + int(q)
    return out


def _keep_sent_record(token: str, meta: dict, send_status: str = "") -> None:
    """ย้ายแถวของไฟล์ที่เพิ่งส่งไปเก็บใน data/ts_sent (14 วัน) + บันทึก sent ledger ถาวร (F2)
    — ห้ามทำให้การส่งพัง"""
    try:
        _SENT_DIR.mkdir(parents=True, exist_ok=True)
        now = time.time()
        for p in _SENT_DIR.glob("*.json"):
            try:
                if now - p.stat().st_mtime > _SENT_TTL_SEC:
                    p.unlink(missing_ok=True)
            except OSError:
                pass
        src = _prepare_dir() / f"{token}.rows.json"
        if not src.is_file():
            return
        rows = json.loads(src.read_text(encoding="utf-8"))
        # F2: สิ่งที่ส่งจริงแบบถาวร ต่อทีม × งวด (ts_sent ข้างล่างลบเองใน 14 วัน)
        sent_ledger.record_send(
            str(meta.get("sup_id") or ""), int(meta.get("target_month") or 0), int(meta.get("target_year") or 0),
            rows, token=token, user=str(meta.get("upload_user_code") or ""),
            send_status=send_status, send_batch_id=meta.get("send_batch_id"),
            import_url=_current_import_url(),
        )
        rec = {k: meta.get(k) for k in ("sup_id", "target_month", "target_year", "upload_user_code", "send_batch_id")}
        # ปลายทางที่ส่งจริง (ผลตรวจ 1 ต.ค. 2026 ก4) — ส่งซ้ำได้เฉพาะปลายทางเดิม กันไฟล์ที่ส่ง UAT ไปลง Prod หลังสลับ preset
        rec.update(token=token, sent_at=now, rows=rows, send_status=send_status or None,
                   import_url=_current_import_url())
        atomic_write_text(str(_SENT_DIR / f"{token}.json"), json.dumps(rec, ensure_ascii=False))
    except Exception:
        logger.exception("เก็บไฟล์ที่ส่งไว้ส่งซ้ำไม่สำเร็จ (%s)", token[:8])


def _current_import_url() -> str:
    try:
        from .targetsun_endpoints import targetsun_endpoints_summary

        return str(targetsun_endpoints_summary().get("import_url") or "")
    except Exception:
        return ""


def _newer_send_exists(rec: dict) -> bool:
    """
    มีการส่งทีม×งวดเดียวกันที่ใหม่กว่าไฟล์นี้ไหม (ผลตรวจ 1 ต.ค. 2026 ก4)

    「ส่งแถวที่ไม่ลง」ต้องใช้ไฟล์ของรอบล่าสุดเท่านั้น — ไฟล์รอบเก่า (เก็บ 14 วัน) เทียบกับ Target Sun
    แล้วทุกแถวที่รอบใหม่เปลี่ยนไปจะดูเหมือน "ไม่ลง" แล้วตัวเลขเก่าทับของใหม่ · ดูทั้ง ts_sent และ sent ledger
    """
    sid = str(rec.get("sup_id") or "").strip().upper()
    m, y = int(rec.get("target_month") or 0), int(rec.get("target_year") or 0)
    at = float(rec.get("sent_at") or 0)
    tok = str(rec.get("token") or "")
    for p in _SENT_DIR.glob("*.json"):
        if p.stem == tok:
            continue
        try:
            other = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (str(other.get("sup_id") or "").strip().upper() == sid
                and int(other.get("target_month") or 0) == m and int(other.get("target_year") or 0) == y
                and float(other.get("sent_at") or 0) > at):
            return True
    led = sent_ledger.read_ledger(sid, m, y) or {}
    for snd in led.get("sends") or []:
        if str(snd.get("token") or "") != tok and float(snd.get("sent_at") or 0) > at + 1:
            return True
    return False




def load_sent_record(token: str, sup_id: str) -> dict:
    tok = (token or "").strip()
    if not tok or "/" in tok or "\\" in tok or ".." in tok:
        raise HTTPException(400, detail="prepare_token ไม่ถูกต้อง")
    p = _SENT_DIR / f"{tok}.json"
    if not p.is_file():
        raise HTTPException(404, detail="ไม่พบไฟล์ที่ส่งไว้ (เก็บ 14 วัน) — ให้กดส่งทีมนี้ใหม่ตามปกติ")
    rec = json.loads(p.read_text(encoding="utf-8"))
    if str(rec.get("sup_id") or "").strip().upper() != str(sup_id or "").strip().upper():
        raise HTTPException(403, detail="ไฟล์นี้ไม่ใช่ของทีมที่เลือก")
    return rec


_WH_CONFLICT_LIST_MAX = 300


def _warehouse_conflict_error(sup_id: str, conflicts: list[dict], *, resolvable: bool,
                              during_send: bool = False) -> HTTPException:
    n = len(conflicts)
    if during_send:
        msg = (f"Target Sun ของทีม {sup_id} เปลี่ยนระหว่างรอส่ง — {n} คู่พนักงาน×สินค้าจะมีเป้าเบิ้ล "
               "จึงยังไม่ส่งทีมนี้")
        hint = "กดส่งทีมนี้อีกครั้ง ระบบจะถามว่าจะใช้คลังตาม Target Sun ไหม (ไม่ต้องกระจายใหม่)"
    elif resolvable:
        msg = (f"ทีม {sup_id}: {n} คู่พนักงาน×สินค้ามีแถวใน Target Sun คนละคลังกับไฟล์ "
               "ถ้าส่งไปเป้าจะเบิ้ล")
        hint = "เลือก「ใช้คลังตาม Target Sun」— หีบเท่าเดิม ไม่ต้องกระจายใหม่"
    else:
        msg = (f"ทีม {sup_id}: ใช้คลังตาม Target Sun แล้วยังมี {n} คู่ที่คลังไม่ตรง จึงไม่ส่ง")
        hint = "แจ้ง dev พร้อมรายการคู่นี้"
    return HTTPException(
        409,
        detail={
            "code": "send_warehouse_conflict",
            "message": msg,
            "hint_th": hint,
            "sup_id": sup_id,
            "resolvable": bool(resolvable and not during_send),
            "confirm_field": "use_targetsun_warehouse" if resolvable and not during_send else None,
            "conflict_count": n,
            "conflicts": conflicts[:_WH_CONFLICT_LIST_MAX],
        },
    )


def _warehouse_check_expected() -> bool:
    """ระบบตั้งให้อ่านเป้าจาก Target Sun อยู่ = ด่านคลังซ้ำต้องทำงานได้ อ่านไม่ได้ถือว่าผิดปกติ"""
    from . import targetsun_read as tsr

    try:
        return bool(tsr.is_enabled() and tsr.get_target_read_source() == "targetsun")
    except Exception:
        return False


def _warehouse_check_unavailable(sup_id: str) -> HTTPException:
    """
    อ่าน Target Sun ไม่ได้ตอนต้องตรวจคลังซ้ำ = ไม่ส่ง (ผลตรวจ 1 ต.ค. 2026 ข3)

    เดิม "ไม่บล็อก" — แต่จังหวะที่อ่านไม่ได้คือจังหวะเดียวกับที่ไฟล์คนละคลังสร้างแถวที่สองของคู่เดิม
    แล้วเป้าเบิ้ล (SL380 / SL453) โดยไม่มีอะไรเตือน · โหมดที่ไม่ได้อ่านจาก Target Sun ไม่เข้าทางนี้
    """
    return HTTPException(
        503,
        detail={
            "code": "warehouse_check_unavailable",
            "message": (
                f"ทีม {sup_id}: อ่านข้อมูลจาก Target Sun ไม่ได้ตอนนี้ — ตรวจคลังซ้ำไม่ได้ จึงยังไม่ส่ง "
                "(กันเป้าเบิ้ล) ไม่มีอะไรถูกส่งเข้า Target Sun"
            ),
            "hint_th": "รอสักครู่แล้วกดส่งอีกครั้ง · ถ้ายังไม่ได้ แจ้ง dev ตรวจ Read API",
            "sup_id": sup_id,
        },
    )


def _live_warehouse_conflicts(sup_id: str, month: int, year: int, df) -> tuple[list[dict], dict | None]:
    """ไฟล์ที่เตรียมไว้ × Target Sun ตอนนี้ — อ่านไม่ได้ = ไม่บล็อก (เหมือนด่านเป้าเปลี่ยน)
    การนับแถวหลังส่งจะรายงาน「ตรวจไม่ได้」ให้เอง"""
    codes = (
        sorted({str(e).strip() for e in df["SALESMANCODE"] if str(e).strip()})
        if "SALESMANCODE" in df.columns else []
    )
    snap = _live_target_snapshot(sup_id, month, year, codes) if codes else None
    if snap is None:
        if codes:
            logger.warning("ตรวจคลังก่อนส่งไม่ได้ (%s) — อ่าน Target Sun ไม่ได้", sup_id)
            if _warehouse_check_expected():
                raise _warehouse_check_unavailable(str(sup_id or "").strip().upper())
        return [], None
    return warehouse_conflicts(snap.get("qty_by_key") or {}, _file_qty_by_key(_file_rows(df))), snap


def _build_send_file(req: LakehouseUploadRequest):
    """
    สร้างไฟล์ของเส้นทางส่งจริง (ตรวจยอดตรงเป้า) + ด่านคลัง — ใช้ทั้ง prepare และทางส่งรวดเดียว

    ด่านคลัง (ผู้ใช้ขอ 30 ก.ย. 2026): ว่างมาว่างไป มีรหัสไหนมาส่งรหัสนั้น — ถ้า Target Sun
    ตอนนี้มีแถวของคู่นี้คนละคลังกับไฟล์ ส่งไปเป้าจะเบิ้ล ให้ผู้ใช้เลือกใช้คลังตาม Target Sun
    ซึ่งแค่แตกหีบของคู่นั้นลงแถวที่มีจริง หีบเท่าเดิม ไม่ต้องโหลด/กระจายใหม่

    คืน (content, fname, df, dropped_dims, not_in_ts, shortfall, จำนวนคู่ที่ใช้คลังตาม Target Sun)
    """
    def build(live_grain=None):
        return prepare_lakehouse_xlsx(
            req, drop_incomplete_rows=True, enforce_targets=True, live_grain=live_grain
        )

    content, fname, df, dropped_dims, not_in_ts, shortfall = build()
    sid = str(req.sup_id or "").strip().upper()
    conflicts, snap = _live_warehouse_conflicts(
        sid, int(req.target_month), int(req.target_year), df
    )
    if not conflicts:
        return content, fname, df, dropped_dims, not_in_ts, shortfall, 0
    if not getattr(req, "use_targetsun_warehouse", False):
        raise _warehouse_conflict_error(sid, conflicts, resolvable=True)
    live_qty = snap.get("qty_by_key") or {}
    pairs = {(c["sku"], c["emp_id"]) for c in conflicts}
    content, fname, df, dropped_dims, not_in_ts, shortfall = build(
        live_grain_for_pairs(live_qty, pairs)
    )
    still = warehouse_conflicts(live_qty, _file_qty_by_key(_file_rows(df)))
    if still:
        raise _warehouse_conflict_error(sid, still, resolvable=False)
    logger.info("ใช้คลังตาม Target Sun %s: %d คู่", sid, len(pairs))
    return content, fname, df, dropped_dims, not_in_ts, shortfall, len(pairs)


def prepare_targetsun_import(req: LakehouseUploadRequest) -> dict:
    """ขั้นที่ 1: สร้าง Excel TGA และเก็บชั่วคราวบน server"""
    _cleanup_stale_prepare_files()
    if not (req.allocations or []):
        raise HTTPException(400, detail="ไม่มีข้อมูลผลกระจายหีบให้ส่ง")

    # อ่านของจริงมาเทียบว่าเป้ายังไม่ขยับ — ทำที่นี่ไม่ใช่ในตัวสร้างไฟล์
    # เพราะตัวสร้างไฟล์ต้องออฟไลน์ล้วน (ดาวน์โหลด Excel ห้ามยิงเน็ต)
    assert_target_snapshot_is_fresh(
        req.sup_id, int(req.target_month), int(req.target_year),
        send_batch_id=getattr(req, "send_batch_id", None),
    )

    t0 = time.perf_counter()
    content, fname, df, dropped_dims, not_in_ts, shortfall, warehouse_adjusted_pairs = (
        _build_send_file(req)
    )
    nrow = int(len(df))
    zero_rows = int((df["QUANTITYCASE"] == 0).sum()) if "QUANTITYCASE" in df.columns else 0
    sku_totals: dict[str, int] = {}
    if {"PRODUCTCODE", "QUANTITYCASE"} <= set(df.columns) and nrow:
        sku_totals = {
            str(k).strip(): int(v)
            for k, v in df.groupby("PRODUCTCODE")["QUANTITYCASE"].sum().items()
        }
    excluded_skus = [
        str(s.get("sku") or "").strip() for s in shortfall if s.get("excluded_whole_sku")
    ]
    emp_codes = (
        sorted({str(e).strip() for e in df["SALESMANCODE"] if str(e).strip()})
        if "SALESMANCODE" in df.columns
        else []
    )
    token = uuid.uuid4().hex
    _save_prepare_bundle(
        token,
        content=content,
        fname=fname,
        sup_id=req.sup_id,
        nrow=nrow,
        zero_rows=zero_rows,
        dropped_dims=int(dropped_dims),
        not_in_ts=not_in_ts,
        upload_user_code=req.upload_user_code,
        shortfall=shortfall,
        sku_totals=sku_totals,
        excluded_skus=excluded_skus,
        target_month=int(req.target_month),
        target_year=int(req.target_year),
        emp_codes=emp_codes,
        new_rows_count=int(df.attrs.get("new_rows_count") or 0),
        new_rows_with_boxes_count=int(df.attrs.get("new_rows_with_boxes_count") or 0),
        stale_rows_cleared_count=int(df.attrs.get("stale_rows_cleared_count") or 0),
        import_row_keys=list(df.attrs.get("import_row_keys") or []),
        send_batch_id=getattr(req, "send_batch_id", None),
        full_send=bool(df.attrs.get("full_send")),
        team_target_mismatches=list(df.attrs.get("team_target_mismatches") or []),
        file_rows=_file_rows(df),
    )
    logger.info(
        "TargetSun prepare: token=%s rows=%d build=%.2fs",
        token[:8],
        nrow,
        time.perf_counter() - t0,
    )
    return {
        "prepare_token": token,
        "upload_filename": fname,
        "rows_sent": nrow,
        "zero_rows_sent": zero_rows,
        "rows_dropped_missing_dims": int(dropped_dims),
        "rows_not_in_targetsun": not_in_ts,
        "rows_not_in_targetsun_count": int(dropped_dims),
        "shortfall": shortfall,
        "shortfall_boxes": sum(int(s.get("missing_boxes") or 0) for s in shortfall),
        # SKU ที่ส่งไม่ครบถูกตัดทั้งตัว — จำนวนหีบที่ "ไม่ถูกส่งเลย" ต่างจาก shortfall_boxes
        # ซึ่งนับเฉพาะส่วนที่ไม่มีเป้าใน TGA
        "excluded_boxes": sum(int(s.get("excluded_boxes") or 0) for s in shortfall),
        "excluded_skus": [str(s.get("sku") or "") for s in shortfall if s.get("excluded_whole_sku")],
        "warehouse_adjusted_pairs": warehouse_adjusted_pairs,
        "step": "prepare",
    }


def _post_targetsun_multipart(
    content: bytes,
    fname: str,
    *,
    nrow: int,
    zero_rows: int,
    dropped_dims: int,
    not_in_ts: list,
    import_url: str | None = None,
    shortfall: list | None = None,
) -> dict:
    url = (import_url or targetsun_import_excel_url()).strip()

    try:
        timeout = int(os.environ.get("TARGETSUN_IMPORT_TIMEOUT_SEC", "600"))
    except ValueError:
        timeout = 600
    timeout = max(30, min(timeout, 3600))

    files = {
        "file": (
            fname,
            content,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
    }
    headers: dict[str, str] = {}
    auth_raw = (os.environ.get("TARGETSUN_IMPORT_AUTH_HEADER") or "").strip()
    if auth_raw:
        headers["Authorization"] = auth_raw if " " in auth_raw else f"Bearer {auth_raw}"

    verify_ssl = os.environ.get("TARGETSUN_IMPORT_VERIFY_SSL", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )

    t0 = time.perf_counter()
    logger.info("TargetSun import: POST %s (%d rows)", url, nrow)

    try:
        r = requests.post(
            url,
            files=files,
            headers=headers or None,
            timeout=timeout,
            verify=verify_ssl,
        )
    except req_exc.SSLError as e:
        logger.exception("TargetSun import SSL error: %s", e)
        raise HTTPException(
            502,
            detail={
                "message": str(e),
                "error_kind": "ssl",
                "hint_th": (
                    "ยืนยันใบรับรอง HTTPS ไม่ผ่าน — ลองตั้ง TARGETSUN_IMPORT_VERIFY_SSL=0 "
                    "ใน config/.env เฉพาะเครือข่ายทดสอบ (ลดความปลอดภัยในการเข้ารหัส)"
                ),
            },
        ) from e
    except (req_exc.ConnectTimeout, req_exc.ReadTimeout) as e:
        logger.exception("TargetSun import timeout: %s", e)
        raise HTTPException(
            504,
            detail={
                "message": str(e),
                "error_kind": "timeout",
                "hint_th": (
                    f"เกินเวลา ({timeout}s) — ลองเพิ่ม TARGETSUN_IMPORT_TIMEOUT_SEC หรือตรวจความเร็วเครือข่าย"
                ),
            },
        ) from e
    except req_exc.ConnectionError as e:
        logger.exception("TargetSun import connection error: %s", e)
        raise HTTPException(
            502,
            detail={
                "message": str(e),
                "error_kind": "connection",
                "hint_th": (
                    "เชื่อมถึงโฮสต์ไม่ได้ — ตรวจ VPN / firewall / URL ใน backend/services/targetsun_endpoints.py และเครื่องรัน backend เข้าอินเทอร์เน็ตได้"
                ),
            },
        ) from e
    except requests.RequestException as e:
        logger.exception("TargetSun import request error: %s", e)
        raise HTTPException(
            502,
            detail={
                "message": str(e),
                "error_kind": "request",
                "hint_th": "ดู log บนเซิร์ฟเวอร์ allocation_target สำหรับรายละเอียด",
            },
        ) from e

    logger.info(
        "TargetSun import timing: post_upstream=%.2fs rows=%d http=%s",
        time.perf_counter() - t0,
        nrow,
        r.status_code,
    )

    ct = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    text_head = (r.text or "")[:2000]
    try:
        body = r.json()
    except Exception:
        logger.warning(
            "TargetSun คืนค่าไม่ใช่ JSON (HTTP %s content-type=%r): %s",
            r.status_code,
            ct or "?",
            text_head[:500],
        )
        if int(r.status_code) in (502, 504):
            # proxy ตอบหน้า HTML ตอนรอนาน = ปลายทางอาจยังบันทึกอยู่ ผลยังไม่รู้ (ผลตรวจ 1 ต.ค. 2026 ข5)
            # ส่งเป็น 504 ให้ทางเดียวกับหมดเวลารอ: เก็บไฟล์ที่ส่ง + จด ledger เป็น unknown ไม่ลบทิ้ง
            raise HTTPException(
                504,
                detail={
                    "message": (
                        f"ระบบเป้าหมายตอบกลับช้าจนตัวกลางตัดการเชื่อมต่อ (HTTP {r.status_code}) — "
                        "ข้อมูลอาจลงไปแล้ว ยังยืนยันผลไม่ได้"
                    ),
                    "error_kind": "gateway_unknown",
                    "upstream_status": int(r.status_code),
                    "content_type": ct or None,
                    "body_preview": text_head[:800],
                    "import_url": url,
                    "hint_th": "ตรวจยอดใน Target Sun ก่อนส่งซ้ำ (แท็บตรวจจำนวนแถวหลังส่ง)",
                },
            )
        raise HTTPException(
            502,
            detail={
                "message": (
                    f"ระบบเป้าหมายตอบกลับเป็นรูปแบบที่อ่านไม่ได้ (HTTP {r.status_code})"
                ),
                "error_kind": "not_json",
                "upstream_status": int(r.status_code),
                "content_type": ct or None,
                "body_preview": text_head[:800],
                # ปลายทางที่ยิงจริง — ตั้งจากหน้าแอดมิน > แหล่งข้อมูล (test/uat/prod)
                # ถ้าชี้ผิดสภาพแวดล้อม อาการจะออกมาเป็น 500/HTML แบบนี้พอดี
                "import_url": url,
                "hint_th": (
                    "มักเกิดเมื่อ URL ชี้ผิด หรือ reverse proxy คืนหน้า HTML/502 — "
                    "ลองเปิด URL เดียวกันจาก Postman และตรวจ backend/services/targetsun_endpoints.py"
                ),
            },
        )

    shortfall = shortfall or []
    send_status = classify_targetsun_reply(body)
    if not isinstance(body, dict):
        # list/string/null — เดิมหลุดไป AttributeError ที่ถูก except: pass กลบ log หายทั้งแถว
        body = {"success": None, "raw": body}
    out = {
        "upload_filename": fname,
        "rows_sent": nrow,
        "zero_rows_sent": int(zero_rows),
        "rows_dropped_missing_dims": int(dropped_dims),
        "rows_not_in_targetsun": not_in_ts,
        "rows_not_in_targetsun_count": int(dropped_dims),
        # ส่งสำเร็จแล้วก็ยังต้องเตือน — คู่เหล่านี้ต้องไปเพิ่มจำนวนเองใน Target Sun
        "shortfall": shortfall,
        "shortfall_boxes": sum(int(s.get("missing_boxes") or 0) for s in shortfall),
        "import_url": url,
        "http_status": int(r.status_code),
        "targetsun": body,
        # ok | partial | failed | unknown — ใช้ตัวนี้ตัดสิน ห้ามดูแค่ success is not False
        "send_status": send_status,
        "step": "import",
    }

    if int(r.status_code) in (502, 503, 504):
        # ตัวกลาง (gateway) ตอบ 502/503/504 แบบ JSON ก็ยังแปลว่า "ไม่รู้ผล" เหมือนแบบ HTML (ผลตรวจ 6 ต.ค. 2026 ก9)
        # เดิมกลายเป็น 502 "ไม่สำเร็จ" → ลบไฟล์ที่ส่ง ไม่จด ledger ทั้งที่ข้อมูลอาจลงไปแล้ว
        raise HTTPException(
            504,
            detail={
                "message": (
                    f"ระบบเป้าหมายตอบกลับช้าจนตัวกลางตัดการเชื่อมต่อ (HTTP {r.status_code}) — "
                    "ข้อมูลอาจลงไปแล้ว ยังยืนยันผลไม่ได้"
                ),
                "error_kind": "gateway_unknown",
                "upstream_status": int(r.status_code),
                "import_url": url,
                "hint_th": "ตรวจยอดใน Target Sun ก่อนส่งซ้ำ (แท็บตรวจจำนวนแถวหลังส่ง)",
            },
        )
    if r.status_code >= 400:
        msg = None
        if isinstance(body, dict):
            msg = body.get("resultMsg") or body.get("message")
        raise HTTPException(
            status_code=502,
            detail={
                "message": msg or f"TargetSun ตอบ HTTP {r.status_code}",
                **out,
            },
        )

    if isinstance(body, dict) and body.get("success") is True:
        rid = body.get("result") or {}
        logger.info(
            "TargetSun import success: inserted=%s updated=%s skipped=%s (rows_sent=%s)",
            rid.get("inserted"),
            rid.get("updated"),
            rid.get("skipped"),
            nrow,
        )
    elif isinstance(body, dict) and body.get("success") is False:
        logger.warning(
            "TargetSun import declined: resultMsg=%s", body.get("resultMsg")
        )
    if send_status == "unknown":
        logger.error(
            "TargetSun ตอบ HTTP %s แต่ไม่บอกว่าสำเร็จหรือไม่ — ถือว่ายังยืนยันผลไม่ได้: %s",
            r.status_code, str(body)[:300],
        )

    return out


def classify_targetsun_reply(body) -> str:
    """
    ผลการส่งจากคำตอบของ Target Sun (ผลตรวจ 28 ก.ย. 2026 §2.3)

      ok      success=true ไม่มี errors และไม่มีแถวถูกข้าม
      partial success=true แต่มี errors[] หรือ skipped > 0
      failed  success=false
      unknown ไม่มีช่อง success / ไม่ใช่ object (เช่น proxy ตอบ 200 {"message": ...})

    เดิมใช้ success is not False → คำตอบที่ไม่มีช่อง success ถูกนับว่าสำเร็จ
    """
    if not isinstance(body, dict):
        return "unknown"
    ok = body.get("success")
    if ok is False:
        return "failed"
    if ok is not True:
        return "unknown"
    res = body.get("result") if isinstance(body.get("result"), dict) else {}
    errors = res.get("errors")
    try:
        skipped = int(res.get("skipped") or 0)
    except (TypeError, ValueError):
        skipped = 0
    if (isinstance(errors, list) and errors) or skipped > 0:
        return "partial"
    return "ok"


def _attach_readback(
    out: dict,
    *,
    sup_id: str,
    month: int,
    year: int,
    sku_totals: dict,
    emp_codes: list,
    new_rows_count: int = 0,
    new_rows_with_boxes_count: int = 0,
    stale_rows_cleared_count: int = 0,
    before_row_snapshot: dict | None = None,
    file_row_keys: list | None = None,
    file_qty_by_key: dict | None = None,
) -> dict:
    """
    ตรวจซ้ำหลังส่งว่ายอด "ลงจริง" ครบตามไฟล์ไหม แล้วแนบผลไปกับคำตอบ

    ส่งไปแล้วย้อนไม่ได้ ตรงนี้จึงเป็นการรายงานล้วน ๆ ไม่ใช่ประตู — และห้ามทำให้
    การส่งที่สำเร็จแล้วกลายเป็นล้มเหลว (verify_after_send ไม่ raise อยู่แล้ว)
    """
    ts = out.get("targetsun") if isinstance(out, dict) else None
    # รหัสพนักงานที่อยู่ในไฟล์ที่ส่งจริง — เดิมคำนวณไว้ใช้แค่ตอนตรวจยอดย้อนกลับ
    # แล้วหายไปพร้อม bundle ที่ถูกลบใน finally ทำให้ไม่มีที่ไหนในระบบตอบได้เลยว่า
    # "งวดนี้ส่งเป้าให้พนักงานกี่คน" · ส่งต่อออกไปให้ตัวบันทึกการใช้งานเก็บไว้
    # แนบก่อนทางออกทุกทาง รวมทางที่ส่งไม่สำเร็จ — จะได้รู้ว่ากะจะส่งให้ใครบ้าง
    if isinstance(out, dict):
        out["emp_codes"] = [str(e).strip() for e in (emp_codes or []) if str(e).strip()]
        # แถวที่ "สร้างใหม่" (คู่ที่ปลายทางไม่เคยมีมาก่อน) — เสี่ยงคลังไม่ตรงกับที่
        # ปลายทางมีอยู่แล้ว แล้วกลายเป็นแถวคู่ขนานคนละคลัง (11.3 / ปริศนา SL453)
        # แนบไว้ให้บันทึกการใช้งานเก็บ จะได้สืบย้อนหลังได้โดยไม่ต้องเดา
        out["new_rows_count"] = int(new_rows_count)
        out["new_rows_with_boxes_count"] = int(new_rows_with_boxes_count)
        # แถวเป้าเก่าที่หลุดจากรอบนี้แล้วถูกล้าง (ส่ง 0 ไปทับ) — ค8
        out["stale_rows_cleared_count"] = int(stale_rows_cleared_count)
    if isinstance(ts, dict) and ts.get("success") is False:
        out["readback"] = {"checked": False, "reason": "send_failed"}
        return out
    # ยังยืนยันผลไม่ได้ — ยังอ่านกลับ/นับแถวต่อ เพราะของอาจลงไปแล้วจริง ตัวเลขช่วยให้ตัดสินได้
    out["readback"] = verify_after_send(
        sup_id,
        int(month),
        int(year),
        sent_by_sku=sku_totals or {},
        emp_codes=list(emp_codes or []),
    )
    out["readback"]["row_count"] = verify_row_count_after_send(
        sup_id,
        int(month),
        int(year),
        emp_codes=list(emp_codes or []),
        before_snapshot=before_row_snapshot,
        file_keys=set(file_row_keys or []),
        file_qty_by_key=file_qty_by_key,
    )
    return out


def _rows_boxes_by_sku(rows: list[dict]) -> dict[str, int]:
    by_sku: dict[str, int] = {}
    for r in rows or []:
        sku = str(r.get("PRODUCTCODE") or "").strip()
        if not sku:
            continue
        try:
            q = int(float(r.get("QUANTITYCASE") or 0))
        except (TypeError, ValueError):
            q = 0
        by_sku[sku] = by_sku.get(sku, 0) + q
    return by_sku


def _batch_records(rec: dict) -> list[dict]:
    """ไฟล์ทุกทีมในชุดส่งเดียวกัน (send_batch_id + งวดเดียวกัน) ที่ยังเก็บไว้ใน ts_sent"""
    bid = str(rec.get("send_batch_id") or "").strip()
    m, y = int(rec.get("target_month") or 0), int(rec.get("target_year") or 0)
    by_team: dict[str, dict] = {}
    for p in _SENT_DIR.glob("*.json"):
        try:
            other = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (str(other.get("send_batch_id") or "").strip() != bid
                or int(other.get("target_month") or 0) != m or int(other.get("target_year") or 0) != y):
            continue
        sid = str(other.get("sup_id") or "").strip().upper()
        # ทีมเดียวกันมีหลายไฟล์ในชุด (ส่งซ้ำ) — ใช้ไฟล์ล่าสุด
        if sid not in by_team or float(other.get("sent_at") or 0) > float(by_team[sid].get("sent_at") or 0):
            by_team[sid] = other
    by_team.setdefault(str(rec.get("sup_id") or "").strip().upper(), rec)
    return list(by_team.values())


def _assert_resend_file_still_matches_targets(sup_id: str, month: int, year: int, rows: list[dict],
                                              rec: dict | None = None) -> None:
    """
    ยอดหีบต่อ SKU ของไฟล์ที่จะส่งซ้ำ ต้องยังเท่าเป้าที่โหลดอยู่ตอนนี้ (กติกา: เป้ารับเข้า = ส่งออก)

    ไฟล์เก่าตรวจผ่านกับเป้า ณ วันที่ส่ง — ถ้าหลังจากนั้นเป้าเปลี่ยนแล้วโหลดขั้นที่ 1 ใหม่
    การส่งซ้ำจะเอาตัวเลขที่ไม่ตรงเป้าใหม่ลง Target Sun · อ่านเป้าไม่ได้ = ไม่บล็อกด้วยเหตุนี้
    (เหมือนด่านเป้าเปลี่ยนของการส่งปกติ) · ดูเฉพาะ SKU ที่อยู่ในไฟล์ (ส่งแยกแบรนด์ได้)

    ไฟล์จากการส่งรวมหลายทีม (มี send_batch_id): รายทีมไม่เท่าเป้าทีมเป็นเรื่องปกติ (ย้ายหีบข้ามทีม I7)
    เดิมเทียบรายทีมจึงบล็อกทุกครั้งด้วยข้อความผิดว่า「เป้าเปลี่ยน」(ผลตรวจ 6 ต.ค. 2026 ก7)
    → เทียบ "ยอดรวมทุกทีมในชุด" กับ "เป้ารวมของทีมเหล่านั้น" แบบเดียวกับด่านตอนส่งชุด
    """
    from .lakehouse import _sup_target_boxes_by_sku

    is_batch = bool(rec) and bool(str(rec.get("send_batch_id") or "").strip())
    if is_batch:
        records = _batch_records(rec)
        file_by_sku: dict[str, int] = {}
        target: dict[str, int] = {}
        for r in records:
            sid = str(r.get("sup_id") or "").strip().upper()
            t = _sup_target_boxes_by_sku(sid, month, year)
            if not t:
                return  # อ่านเป้าบางทีมไม่ได้ = ตรวจไม่ได้ ไม่บล็อกด้วยเหตุนี้ (เหมือนทีมเดียว)
            for k, v in _rows_boxes_by_sku(r.get("rows") or []).items():
                file_by_sku[k] = file_by_sku.get(k, 0) + v
            for k, v in t.items():
                target[str(k).strip()] = target.get(str(k).strip(), 0) + int(v)
        scope = f"ชุดส่ง {len(records)} ทีม"
    else:
        target = _sup_target_boxes_by_sku(sup_id, month, year)
        if not target:
            return
        file_by_sku = _rows_boxes_by_sku(rows)
        scope = f"ทีม {sup_id}"
    diff = [
        {"sku": k, "file_boxes": v, "target_boxes": int(target.get(k, 0))}
        for k, v in sorted(file_by_sku.items())
        if int(target.get(k, 0)) != v
    ]
    if diff:
        raise HTTPException(409, detail={
            "code": "RESEND_TARGET_CHANGED",
            "message": (
                f"เป้าของ{scope} เปลี่ยนหลังส่งไฟล์นี้ ({len(diff)} สินค้า) — ส่งซ้ำไม่ได้ "
                "ให้กระจายใหม่แล้วกดส่งตามปกติ"
            ),
            "skus": diff[:50],
        })


def resend_unlanded_rows(sup_id: str, token: str) -> dict:
    """
    ส่งซ้ำเฉพาะแถวที่ยังไม่ลง/ลงไม่ตรงของไฟล์ที่ส่งไปแล้ว (ผู้ใช้ขอ 29 ก.ย. 2026)

    Target Sun ทับแถวเดิมได้ (upsert) แต่ลบไม่ได้ — แถวที่ตกหล่นจึงแก้ด้วยการส่งแถวนั้นซ้ำ
    แถวทั้งหมดมาจากไฟล์ที่ส่งไปแล้วซึ่งเก็บไว้ที่ server ไม่รับตัวเลขจากหน้าเว็บ
    ยอดปลายทางจึงเข้าใกล้ไฟล์เดิมเสมอ ไม่มีทางไปทำให้เกินหรือขาดจากที่ตรวจผ่านไว้
    """
    import pandas as pd

    from .lakehouse import _build_xlsx_bytes, import_row_key_series
    from .targetsun_endpoints import targetsun_endpoints_summary

    rec = load_sent_record(token, sup_id)
    if str(targetsun_endpoints_summary().get("cross_env") or "") == "1":
        raise HTTPException(409, detail="ระบบอ่านกับระบบที่ส่งเป็นคนละที่ — ตรวจว่าแถวไหนตกหล่นไม่ได้")
    sent_url = str(rec.get("import_url") or "")
    if not sent_url or sent_url != _current_import_url():
        raise HTTPException(409, detail=(
            "ไฟล์นี้ส่งไปปลายทางอื่น (หรือส่งก่อนระบบจดปลายทาง) — ส่งซ้ำไม่ได้ ให้กดส่งทีมนี้ใหม่ตามปกติ"
        ))
    if _newer_send_exists(rec):
        raise HTTPException(409, detail=(
            "ทีมนี้มีการส่งรอบใหม่กว่าไฟล์นี้แล้ว — ส่งซ้ำจากไฟล์เก่าจะเอาตัวเลขเก่าไปทับ "
            "ให้ใช้ปุ่มส่งซ้ำของรอบล่าสุด หรือกดส่งทีมนี้ใหม่ตามปกติ"
        ))
    # รอบที่ Target Sun ตอบว่าไม่สำเร็จ ทุกแถวจะดูเหมือน "ไม่ลง" — ส่งซ้ำ = ส่งทั้งไฟล์ใหม่โดยไม่ผ่าน
    # ด่านของการส่งปกติ (ผลตรวจ 5 ต.ค. 2026 ข้อ 3)
    if str(rec.get("send_status") or "").strip().lower() == "failed":
        raise HTTPException(409, detail="รอบนั้นส่งไม่สำเร็จ — ส่งซ้ำไม่ได้ ให้กดส่งทีมนี้ใหม่ตามปกติ")
    month, year = int(rec["target_month"]), int(rec["target_year"])
    rows = rec.get("rows") or []
    file_qty = _file_qty_by_key(rows)
    _assert_resend_file_still_matches_targets(sup_id, month, year, rows, rec)
    emp_codes = sorted({str(r.get("SALESMANCODE") or "").strip() for r in rows} - {""})
    team_key = _claim_team_send(sup_id, month, year)
    try:
        live = _live_target_snapshot(sup_id, month, year, emp_codes)
        if live is None:
            raise HTTPException(503, detail="อ่านข้อมูลจาก Target Sun ไม่ได้ตอนนี้ — ลองใหม่อีกครั้ง")
        from .lakehouse import unlanded_rows

        missing = {u["key"] for u in unlanded_rows(file_qty, live.get("qty_by_key") or {})}
        if not missing:
            return {"resent_rows": 0, "remaining_unlanded": 0, "message": "ทุกแถวลงครบแล้ว ไม่มีอะไรต้องส่งซ้ำ"}
        # ด่านคลังเดียวกับการส่งปกติ: คู่ที่ "ไม่ลง" อาจมีแถวใน Target Sun อยู่แล้วคนละคลัง
        # (คนคีย์เพิ่มเอง / แถวซ้อนเดิม) ส่งซ้ำไปจะเป็นแถวที่สอง เป้าเบิ้ล — แบบเคส SL380
        # เทียบเฉพาะคู่ที่จะส่ง แต่ใช้ทุกแถวของคู่นั้นในไฟล์ แถวที่ลงแล้วจึงไม่นับเป็นแถวค้าง
        from .lakehouse import _pair_of_key, warehouse_conflicts

        pairs = {_pair_of_key(k) for k in missing}
        conflicts = warehouse_conflicts(
            live.get("qty_by_key") or {},
            {k: q for k, q in file_qty.items() if _pair_of_key(k) in pairs},
        )
        if conflicts:
            err = _warehouse_conflict_error(sup_id, conflicts, resolvable=False)
            err.detail["message"] = (
                f"ทีม {sup_id}: {len(conflicts)} คู่พนักงาน×สินค้าที่ยังไม่ลง มีแถวใน Target Sun คนละคลังอยู่แล้ว "
                "ส่งซ้ำไปเป้าจะเบิ้ล จึงไม่ส่ง"
            )
            err.detail["hint_th"] = "กดส่งทีมนี้ใหม่ตามปกติ ระบบจะถามว่าจะใช้คลังตาม Target Sun ไหม"
            raise err
        df = pd.DataFrame(rows)
        sub = df[import_row_key_series(df).isin(missing)].copy()
        content = _build_xlsx_bytes(sub)
        out = _post_targetsun_multipart(
            content, f"resend_{sup_id}_{year}_{month:02d}.xlsx",
            nrow=len(sub), zero_rows=int((pd.to_numeric(sub["QUANTITYCASE"], errors="coerce") == 0).sum()),
            dropped_dims=0, not_in_ts=[],
        )
        # จดแถวที่ส่งซ้ำลง ledger ด้วย — เดิมไม่จด แถวที่เพิ่งลงจึงถูกตรวจรายคืนนับเป็น「มีคนเพิ่มเอง」
        if str(out.get("send_status") or "").lower() != "failed":
            sent_ledger.record_send(
                sup_id, month, year, sub.to_dict(orient="records"), token=token,
                user=str(rec.get("upload_user_code") or ""), send_status=str(out.get("send_status") or ""),
                send_batch_id=rec.get("send_batch_id"), import_url=sent_url,
            )
        after = _live_target_snapshot(sup_id, month, year, emp_codes)
        remaining = (
            unlanded_rows(file_qty, after.get("qty_by_key") or {}) if after is not None else None
        )
        out.update(
            resent_rows=len(sub),
            remaining_unlanded=None if remaining is None else len(remaining),
            remaining_sample=(remaining or [])[:20],
            target_month=month, target_year=year, prepare_token=token,
        )
        return out
    finally:
        _release_team_send(team_key)


def import_prepared_targetsun(req: LakehouseUploadRequest) -> dict:
    """ขั้นที่ 2: POST ไฟล์ที่เตรียมไว้แล้ว"""
    token = (req.prepare_token or "").strip()
    if not token:
        raise HTTPException(400, detail="ไม่มี prepare_token")

    # จับจองก่อนแตะ bundle เลย — กันคำขอที่สองด้วย token เดียวกันมาระหว่างที่คำขอแรก
    # ยังไม่ทันลบ bundle ทิ้ง (โหลด+POST ยังไม่เสร็จ) ไม่งั้นทั้งสองคำขอจะโหลด bundle
    # เดิมสำเร็จแล้วยิง POST เข้า Target Sun จริงคนละรอบ
    _claim_import_token(token)
    try:
        content, fname, meta = _load_prepare_bundle(token, req.sup_id)
        _assert_batch_verified(token, meta)
        team_key = _claim_team_send(
            req.sup_id,
            int(meta.get("target_month") or req.target_month),
            int(meta.get("target_year") or req.target_year),
        )
    except BaseException:
        _release_import_token(token)
        raise
    try:
        nrow = int(meta.get("rows_sent") or 0)
        zero_rows = int(meta.get("zero_rows_sent") or 0)
        dropped_dims = int(meta.get("rows_dropped_missing_dims") or 0)
        not_in_ts = meta.get("rows_not_in_targetsun") or []
        shortfall = meta.get("shortfall") or []
        bundle_emp_codes = meta.get("emp_codes") if isinstance(meta.get("emp_codes"), list) else []
        bundle_month = int(meta.get("target_month") or req.target_month)
        bundle_year = int(meta.get("target_year") or req.target_year)
        try:
            _rows_path = _prepare_dir() / f"{token}.rows.json"
            file_qty = _file_qty_by_key(
                json.loads(_rows_path.read_text(encoding="utf-8")) if _rows_path.is_file() else []
            )
        except Exception:
            file_qty = {}

        # อ่านสด "ก่อนส่ง" ให้ใกล้เวลา POST จริงที่สุด (ลดโอกาสทีมอื่นแทรกส่งระหว่างรอ) —
        # ใช้ทั้งเทียบจำนวนแถวหลังส่งสำเร็จใน _attach_readback และตรวจซ้ำว่าเป้ายังไม่ขยับ
        # ข้างล่างนี้ (อ่านครั้งเดียวพอ ไม่ต้องยิง Target Sun ซ้ำสองรอบ)
        before_row_snapshot = _live_target_snapshot(
            req.sup_id, bundle_month, bundle_year, bundle_emp_codes
        )
        # ด่านคลังซ้ำอีกครั้งด้วยค่าที่อ่านสดข้างบน — Target Sun อาจถูกแก้ระหว่าง prepare กับ
        # ตอนนี้ (รอผู้ใช้ยืนยัน / รอส่งทีมก่อนหน้าในรอบรวมภาค) ส่งไปตอนนี้ = เป้าเบิ้ล
        if before_row_snapshot is None and file_qty and _warehouse_check_expected():
            raise _warehouse_check_unavailable(str(req.sup_id or "").strip().upper())
        if before_row_snapshot is not None and file_qty:
            _late = warehouse_conflicts(before_row_snapshot.get("qty_by_key") or {}, file_qty)
            if _late:
                raise _warehouse_conflict_error(
                    str(req.sup_id or "").strip().upper(), _late, resolvable=True, during_send=True
                )

        # เดิมด่านนี้เรียกแค่ตอน prepare — ระหว่างเตรียมครบทุกทีม/ถามยืนยัน/ตรวจยอดรวม
        # ทั้งชุด แล้วค่อยวน import ทีละทีม (แต่ละ POST ค้างได้ถึง
        # TARGETSUN_IMPORT_TIMEOUT_SEC วินาที) เป้าอาจขยับอีกรอบได้ในช่วงนี้โดยไม่มีอะไร
        # จับได้เลย ตรวจซ้ำตรงนี้อีกทีด้วยค่าที่อ่านสดมาแล้วข้างบน (ไม่ยิงซ้ำ) — ยึดการ
        # ยืนยันที่จดไว้ใน bundle ตอน prepare ถ้าผู้ใช้ยืนยันมาแล้วก็ไม่ถามซ้ำ
        # ตรงนี้ (นี่คือด่านสำหรับดักการเปลี่ยนแปลง "ใหม่" ระหว่างรอ ไม่ใช่ถามซ้ำของเดิม)
        #
        # ต้องเทียบด้วยพนักงาน "ทั้งทีม" ชุดเดียวกับตอน prepare (เป้าทีมใน snapshot
        # ครอบคนทั้งทีม) — bundle_emp_codes คือแค่คนที่อยู่ในไฟล์ ถ้ามีคนในทีมไม่อยู่ใน
        # ไฟล์ (เช่นคนที่ไม่ต้องตั้งเป้า) ยอดสดจะน้อยกว่าเป้าแล้วฟ้อง "เป้าเปลี่ยน" ผิด
        # บล็อกการส่งทุกครั้ง · ใช้ค่าที่อ่านมาแล้วได้เฉพาะตอนสองชุดตรงกันเท่านั้น
        # ไม่มีไฟล์ grain (ไม่ควรเกิดเพราะตัวสร้างไฟล์ต้องใช้ grain) — ถอยไปใช้คนในไฟล์
        # ดีกว่าปล่อยด่านนี้ข้ามไปเงียบ ๆ
        team_codes = (
            team_emp_codes_from_grain(req.sup_id, bundle_month, bundle_year)
            or list(bundle_emp_codes)
        )
        same_people = {norm_emp_code(e) for e in team_codes} == {
            norm_emp_code(e) for e in bundle_emp_codes
        }
        fresh_kwargs: dict = {"emp_codes": team_codes}
        if same_people:
            fresh_kwargs["live_by_sku"] = (
                before_row_snapshot["by_sku"] if before_row_snapshot else None
            )
        assert_target_snapshot_is_fresh(
            req.sup_id, bundle_month, bundle_year,
            send_batch_id=meta.get("send_batch_id"), **fresh_kwargs,
        )

        try:
            out = _post_targetsun_multipart(
                content,
                fname,
                nrow=nrow,
                zero_rows=zero_rows,
                dropped_dims=dropped_dims,
                not_in_ts=not_in_ts if isinstance(not_in_ts, list) else [],
                shortfall=shortfall if isinstance(shortfall, list) else [],
            )
        except HTTPException as e:
            # หมดเวลารอ (504) = ปลายทางอาจยังบันทึกต่อจนเสร็จ ยังยืนยันผลไม่ได้จริง (ผลตรวจ §2.4)
            # เก็บไฟล์ที่ส่งไว้ให้ dev ตรวจย้อนได้ว่าส่งอะไรไป (หมดอายุเองใน 30 นาที)
            # ผิดพลาดแบบอื่นลบทิ้งตามเดิม
            if e.status_code != 504:
                _delete_prepare_bundle(token)
            else:
                # จด ledger + ts_sent เป็น "unknown" (ผลตรวจ 1 ต.ค. 2026 ก5) — ของอาจลงแล้วจริง ถ้าไม่จด
                # ตรวจรายคืนจะนับแถวที่ลงเป็นการแก้มือ และปุ่มส่งแถวที่ไม่ลงก็ใช้ไม่ได้
                _keep_sent_record(token, meta, "unknown")
            raise
        except BaseException:
            _delete_prepare_bundle(token)
            raise
        _keep_sent_record(token, meta, str(out.get("send_status") or ""))
        _delete_prepare_bundle(token)

        out["prepare_token"] = token
        # งวดที่ส่งจริงคืองวดของไฟล์ที่เตรียมไว้ ไม่ใช่ค่าในคำขอ import — ให้ log ใช้ค่านี้ (§2.9)
        out["target_month"] = bundle_month
        out["target_year"] = bundle_year
        # รอบการส่งรวมภาค — router ใช้ส่งแจ้งเตือนถึงเจ้าของทุก SL ในรอบ
        out["send_batch_id"] = meta.get("send_batch_id") or None
        out["batch_sup_ids"] = list(meta.get("batch_sup_ids") or [])
        return _attach_readback(
            out,
            sup_id=req.sup_id,
            month=bundle_month,
            year=bundle_year,
            sku_totals=meta.get("sku_totals") if isinstance(meta.get("sku_totals"), dict) else {},
            emp_codes=bundle_emp_codes,
            new_rows_count=int(meta.get("new_rows_count") or 0),
            new_rows_with_boxes_count=int(meta.get("new_rows_with_boxes_count") or 0),
            stale_rows_cleared_count=int(meta.get("stale_rows_cleared_count") or 0),
            before_row_snapshot=before_row_snapshot,
            file_row_keys=meta.get("import_row_keys") if isinstance(meta.get("import_row_keys"), list) else [],
            file_qty_by_key=file_qty,
        )
    finally:
        _release_team_send(team_key)
        _release_import_token(token)


def import_allocations_to_targetsun(req: LakehouseUploadRequest) -> dict:
    """
    สร้าง .xlsx แล้ว POST ในคำขอเดียว (backward compatible)
    หรือใช้ prepare_token จาก prepare_targetsun_import
    """
    if (req.prepare_token or "").strip():
        return import_prepared_targetsun(req)
    if str(getattr(req, "send_batch_id", "") or "").strip():
        # ทางส่งรวดเดียวไม่มีด่านยอดรวมทั้งชุด — ส่งรวมภาคต้องผ่าน prepare → verify เท่านั้น
        raise HTTPException(
            400,
            detail="การส่งรวมหลายทีมต้องเตรียมไฟล์และตรวจยอดรวมก่อน — กรุณากดส่งใหม่อีกครั้ง",
        )
    team_key = _claim_team_send(req.sup_id, int(req.target_month), int(req.target_year))
    try:
        return _import_allocations_one_shot(req)
    finally:
        _release_team_send(team_key)


def _import_allocations_one_shot(req: LakehouseUploadRequest) -> dict:
    """ตัวงานของ import_allocations_to_targetsun — เรียกใต้ล็อกทีม×งวดเท่านั้น"""
    url = targetsun_import_excel_url().strip()
    t0 = time.perf_counter()
    logger.info("TargetSun import: start allocations_in=%d", len(req.allocations or []))

    assert_target_snapshot_is_fresh(req.sup_id, int(req.target_month), int(req.target_year))

    content, fname, df, dropped_dims, not_in_ts, shortfall, _wh_adjusted = _build_send_file(req)
    t_build = time.perf_counter()
    nrow = int(len(df))
    zero_rows = int((df["QUANTITYCASE"] == 0).sum()) if "QUANTITYCASE" in df.columns else 0

    logger.info(
        "TargetSun import: build done (%d rows) [build=%.2fs]",
        nrow,
        t_build - t0,
    )

    # ต้องคำนวณ emp_codes ก่อน POST (ไม่ใช่หลัง เหมือนเดิม) เพราะต้องใช้อ่านสด
    # "ก่อนส่ง" ให้ใกล้เวลาจริงที่สุด — ค่าที่คำนวณล้วน ๆ ไม่กระทบ payload/URL/POST เอง
    emp_codes = (
        sorted({str(e).strip() for e in df["SALESMANCODE"] if str(e).strip()})
        if "SALESMANCODE" in df.columns
        else []
    )
    before_row_snapshot = _live_target_snapshot(
        req.sup_id, int(req.target_month), int(req.target_year), emp_codes
    )

    try:
        out = _post_targetsun_multipart(
            content,
            fname,
            nrow=nrow,
            zero_rows=zero_rows,
            dropped_dims=int(dropped_dims),
            not_in_ts=not_in_ts,
            import_url=url,
            shortfall=shortfall,
        )
    except HTTPException as e:
        # หมดเวลารอ (504) = ของอาจลงแล้ว ต้องจด ledger เป็น "unknown" เหมือนทาง prepare
        # (ผลตรวจ 5 ต.ค. 2026 ข้อ 7.10) — ไม่จด = ตรวจรายคืนนับแถวที่ลงเป็นการแก้มือ
        # จดไม่สำเร็จห้ามกลบ 504 ตัวจริงที่ผู้ใช้ต้องเห็น
        if e.status_code == 504:
            try:
                sent_ledger.record_send(
                    req.sup_id, int(req.target_month), int(req.target_year), _file_rows(df),
                    user=str(req.upload_user_code or ""), send_status="unknown",
                    import_url=_current_import_url(),
                )
            except Exception:
                logger.exception("จด sent ledger หลัง 504 (ทางส่งรวดเดียว) ไม่สำเร็จ — %s", req.sup_id)
        raise
    # F2: ทางส่งรวดเดียว (หน้าเว็บรุ่นเก่า) ก็ต้องลง sent ledger เหมือนทาง prepare
    sent_ledger.record_send(
        req.sup_id, int(req.target_month), int(req.target_year), _file_rows(df),
        user=str(req.upload_user_code or ""), send_status=str(out.get("send_status") or ""),
        import_url=_current_import_url(),
    )
    logger.info(
        "TargetSun import timing: build_xlsx=%.2fs post_upstream=%.2fs total=%.2fs rows=%d",
        t_build - t0,
        time.perf_counter() - t_build,
        time.perf_counter() - t0,
        nrow,
    )
    sku_totals = (
        {
            str(k).strip(): int(v)
            for k, v in df.groupby("PRODUCTCODE")["QUANTITYCASE"].sum().items()
        }
        if {"PRODUCTCODE", "QUANTITYCASE"} <= set(df.columns) and nrow
        else {}
    )
    return _attach_readback(
        out,
        sup_id=req.sup_id,
        month=req.target_month,
        year=req.target_year,
        sku_totals=sku_totals,
        emp_codes=emp_codes,
        new_rows_count=int(df.attrs.get("new_rows_count") or 0),
        new_rows_with_boxes_count=int(df.attrs.get("new_rows_with_boxes_count") or 0),
        stale_rows_cleared_count=int(df.attrs.get("stale_rows_cleared_count") or 0),
        before_row_snapshot=before_row_snapshot,
        file_row_keys=list(df.attrs.get("import_row_keys") or []),
    )
