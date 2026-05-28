"""
ส่งไฟล์ Excel TGA ไปบริการ TargetSun / SPC (Oracle ฝั่ง UAT/Prod อยู่ที่ service นั้น).

เอกสาร: targetsun-importTargetSalesmanNextFromExcel.md
ค่าเริ่มต้น UAT: https://spcuatws.sahapat.com/spc/targetsun/importTargetSalesmanNextFromExcel
"""

from __future__ import annotations

import logging
import os

import requests
from fastapi import HTTPException
from requests import exceptions as req_exc

from ..schemas import LakehouseUploadRequest
from .lakehouse import prepare_lakehouse_xlsx

logger = logging.getLogger("target_allocation")

_DEFAULT_UAT_URL = (
    "https://spcuatws.sahapat.com/spc/targetsun/importTargetSalesmanNextFromExcel"
)


def import_allocations_to_targetsun(req: LakehouseUploadRequest) -> dict:
    """
    สร้าง .xlsx เหมือน export แล้ว POST multipart field `file` ไปยัง importTargetSalesmanNextFromExcel
    คืนค่าเป็น dict สำหรับส่ง JSON ให้ frontend (รวมผลจาก TargetSun เดิม)
    """
    url = (os.environ.get("TARGETSUN_IMPORT_EXCEL_URL") or _DEFAULT_UAT_URL).strip()
    if not url:
        raise HTTPException(
            500,
            detail="ยังไม่ได้ตั้งค่า TARGETSUN_IMPORT_EXCEL_URL",
        )

    try:
        timeout = int(os.environ.get("TARGETSUN_IMPORT_TIMEOUT_SEC", "600"))
    except ValueError:
        timeout = 600
    timeout = max(30, min(timeout, 3600))

    content, fname, df = prepare_lakehouse_xlsx(req)
    nrow = int(len(df))

    logger.info(
        "TargetSun import: POST %s (%d rows, multipart field=file)",
        url,
        nrow,
    )

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
        # ตัวอย่าง: "Bearer xxx" หรือส่งทั้งบรรทัดที่ API ต้องการ
        headers["Authorization"] = auth_raw if " " in auth_raw else f"Bearer {auth_raw}"

    verify_ssl = os.environ.get("TARGETSUN_IMPORT_VERIFY_SSL", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )

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
                    "เชื่อมถึงโฮสต์ไม่ได้ — ตรวจ VPN / firewall / ว่า URL ใน TARGETSUN_IMPORT_EXCEL_URL ถูกต้อง และเครื่องรัน backend เข้าอินเทอร์เน็ตได้"
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
                "hint_th": (
                    "มักเกิดเมื่อ URL ชี้ผิด หรือ reverse proxy คืนหน้า HTML/502 — "
                    "ลองเปิด URL เดียวกันจาก Postman และตรวจ TARGETSUN_IMPORT_EXCEL_URL"
                ),
            },
        )

    out = {
        "upload_filename": fname,
        "rows_sent": nrow,
        "zero_rows_sent": int((df["QUANTITYCASE"] == 0).sum()) if "QUANTITYCASE" in df.columns else 0,
        "import_url": url,
        "http_status": int(r.status_code),
        "targetsun": body,
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
