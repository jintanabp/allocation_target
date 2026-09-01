"""
ส่งไฟล์ Excel TGA ไปบริการ TargetSun / SPC (Oracle ฝั่ง UAT/Prod อยู่ที่ service นั้น).

เอกสาร: targetsun-importTargetSalesmanNextFromExcel.md
ค่าเริ่มต้น UAT: https://spcuatws.sahapat.com/spc/targetsun/importTargetSalesmanNextFromExcel
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path

import requests
from fastapi import HTTPException
from requests import exceptions as req_exc

from ..schemas import LakehouseUploadRequest
from .lakehouse import (
    assert_target_snapshot_is_fresh,
    prepare_lakehouse_xlsx,
    verify_after_send,
)
from .targetsun_endpoints import targetsun_import_excel_url

logger = logging.getLogger("target_allocation")

_PREPARE_DIR = Path("data/ts_prepare")
_PREPARE_TTL_SEC = 30 * 60


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
) -> None:
    _prepare_dir()
    (_prepare_dir() / f"{token}.xlsx").write_bytes(content)
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
        # คู่ที่ผู้ใช้ต้องไปเพิ่มจำนวนเองใน Target Sun — ต้องติดไปถึงหน้าจอ "ส่งสำเร็จ"
        "shortfall": shortfall,
        "shortfall_boxes": sum(int(s.get("missing_boxes") or 0) for s in shortfall),
        "upload_user_code": upload_user_code,
        "created_at": time.time(),
    }
    (_prepare_dir() / f"{token}.json").write_text(
        json.dumps(meta, ensure_ascii=False),
        encoding="utf-8",
    )


def _load_prepare_bundle(token: str, sup_id: str) -> tuple[bytes, str, dict]:
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
        _, _, meta = _load_prepare_bundle(t, "")
        meta = dict(meta)
        meta["prepare_token"] = t
        metas.append(meta)
    if not metas:
        raise HTTPException(400, detail="ไม่มี prepare_token ที่ใช้ได้")
    return metas


def _delete_prepare_bundle(token: str) -> None:
    tok = (token or "").strip()
    if not tok:
        return
    (_prepare_dir() / f"{tok}.json").unlink(missing_ok=True)
    (_prepare_dir() / f"{tok}.xlsx").unlink(missing_ok=True)


def prepare_targetsun_import(req: LakehouseUploadRequest) -> dict:
    """ขั้นที่ 1: สร้าง Excel TGA และเก็บชั่วคราวบน server"""
    _cleanup_stale_prepare_files()
    if not (req.allocations or []):
        raise HTTPException(400, detail="ไม่มีข้อมูลผลกระจายหีบให้ส่ง")

    # อ่านของจริงมาเทียบว่าเป้ายังไม่ขยับ — ทำที่นี่ไม่ใช่ในตัวสร้างไฟล์
    # เพราะตัวสร้างไฟล์ต้องออฟไลน์ล้วน (ดาวน์โหลด Excel ห้ามยิงเน็ต)
    assert_target_snapshot_is_fresh(
        req.sup_id,
        int(req.target_month),
        int(req.target_year),
        confirmed=bool(getattr(req, "confirm_stale_target", False)),
    )

    t0 = time.perf_counter()
    content, fname, df, dropped_dims, not_in_ts, shortfall = prepare_lakehouse_xlsx(
        req, drop_incomplete_rows=True, enforce_targets=True
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
        "step": "import",
    }

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

    return out


def _attach_readback(
    out: dict,
    *,
    sup_id: str,
    month: int,
    year: int,
    sku_totals: dict,
    emp_codes: list,
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
    if isinstance(ts, dict) and ts.get("success") is False:
        out["readback"] = {"checked": False, "reason": "send_failed"}
        return out
    out["readback"] = verify_after_send(
        sup_id,
        int(month),
        int(year),
        sent_by_sku=sku_totals or {},
        emp_codes=list(emp_codes or []),
    )
    return out


def import_prepared_targetsun(req: LakehouseUploadRequest) -> dict:
    """ขั้นที่ 2: POST ไฟล์ที่เตรียมไว้แล้ว"""
    token = (req.prepare_token or "").strip()
    if not token:
        raise HTTPException(400, detail="ไม่มี prepare_token")
    content, fname, meta = _load_prepare_bundle(token, req.sup_id)
    nrow = int(meta.get("rows_sent") or 0)
    zero_rows = int(meta.get("zero_rows_sent") or 0)
    dropped_dims = int(meta.get("rows_dropped_missing_dims") or 0)
    not_in_ts = meta.get("rows_not_in_targetsun") or []
    shortfall = meta.get("shortfall") or []

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
    finally:
        _delete_prepare_bundle(token)

    out["prepare_token"] = token
    return _attach_readback(
        out,
        sup_id=req.sup_id,
        month=meta.get("target_month") or req.target_month,
        year=meta.get("target_year") or req.target_year,
        sku_totals=meta.get("sku_totals") if isinstance(meta.get("sku_totals"), dict) else {},
        emp_codes=meta.get("emp_codes") if isinstance(meta.get("emp_codes"), list) else [],
    )


def import_allocations_to_targetsun(req: LakehouseUploadRequest) -> dict:
    """
    สร้าง .xlsx แล้ว POST ในคำขอเดียว (backward compatible)
    หรือใช้ prepare_token จาก prepare_targetsun_import
    """
    if (req.prepare_token or "").strip():
        return import_prepared_targetsun(req)

    url = targetsun_import_excel_url().strip()
    t0 = time.perf_counter()
    logger.info("TargetSun import: start allocations_in=%d", len(req.allocations or []))

    assert_target_snapshot_is_fresh(
        req.sup_id,
        int(req.target_month),
        int(req.target_year),
        confirmed=bool(getattr(req, "confirm_stale_target", False)),
    )

    content, fname, df, dropped_dims, not_in_ts, shortfall = prepare_lakehouse_xlsx(
        req, drop_incomplete_rows=True, enforce_targets=True
    )
    t_build = time.perf_counter()
    nrow = int(len(df))
    zero_rows = int((df["QUANTITYCASE"] == 0).sum()) if "QUANTITYCASE" in df.columns else 0

    logger.info(
        "TargetSun import: build done (%d rows) [build=%.2fs]",
        nrow,
        t_build - t0,
    )

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
    emp_codes = (
        sorted({str(e).strip() for e in df["SALESMANCODE"] if str(e).strip()})
        if "SALESMANCODE" in df.columns
        else []
    )
    return _attach_readback(
        out,
        sup_id=req.sup_id,
        month=req.target_month,
        year=req.target_year,
        sku_totals=sku_totals,
        emp_codes=emp_codes,
    )
