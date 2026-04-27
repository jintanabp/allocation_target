import io
import logging
import os
import uuid
from datetime import datetime, timezone

import msal
import pandas as pd
import requests
from fastapi import HTTPException

from ..core.paths import safe_id
from ..schemas import LakehouseUploadRequest

logger = logging.getLogger("target_allocation")


def _get_storage_token() -> str:
    tenant_id = (os.environ.get("FABRIC_TENANT_ID") or "").strip()
    client_id = (os.environ.get("FABRIC_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("FABRIC_CLIENT_SECRET") or "").strip()
    if not (tenant_id and client_id and client_secret):
        raise HTTPException(
            500,
            detail=(
                "ยังไม่ได้ตั้งค่า Service Principal สำหรับอัปโหลดเข้า OneLake "
                "(ต้องมี FABRIC_TENANT_ID / FABRIC_CLIENT_ID / FABRIC_CLIENT_SECRET)"
            ),
        )

    authority = f"https://login.microsoftonline.com/{tenant_id}"
    cca = msal.ConfidentialClientApplication(
        client_id,
        client_credential=client_secret,
        authority=authority,
    )
    # OneLake ใช้ API แบบ ADLS Gen2 → scope storage
    scopes = ["https://storage.azure.com/.default"]
    result = cca.acquire_token_for_client(scopes=scopes)
    token = result.get("access_token")
    if token:
        return token
    err = result.get("error_description") or result.get("error") or str(result)
    raise HTTPException(500, detail=f"ขอ token สำหรับอัปโหลด OneLake ไม่สำเร็จ: {err}")


def _onelake_base_path() -> tuple[str, str]:
    ws = (os.environ.get("ONELAKE_WORKSPACE_ID") or "").strip()
    lh = (os.environ.get("ONELAKE_LAKEHOUSE_ID") or "").strip()
    if not ws or not lh:
        raise HTTPException(
            500,
            detail=(
                "ยังไม่ได้ตั้งค่าเป้าหมาย Lakehouse (ต้องมี ONELAKE_WORKSPACE_ID / ONELAKE_LAKEHOUSE_ID)"
            ),
        )
    # รูปแบบ GUID-based (แนะนำ/เสถียร): {workspaceGUID}/{itemGUID}/Files/...
    # อ้างอิง: Microsoft Learn (OneLake access APIs)
    return ws, lh


def _upload_bytes_to_onelake(file_path: str, content: bytes, token: str) -> None:
    """
    Upload ไป OneLake via ADLS Gen2 (dfs endpoint):
    1) PUT ?resource=file
    2) PATCH append
    3) PATCH flush
    """
    ws, lh = _onelake_base_path()
    base = "https://onelake.dfs.fabric.microsoft.com"
    # Always write under Files/
    fp = file_path.lstrip("/").replace("\\", "/")
    if fp.lower().startswith("files/"):
        fp = fp[6:]
    url = f"{base}/{ws}/{lh}/Files/{fp}"

    headers = {
        "Authorization": f"Bearer {token}",
        "x-ms-version": "2021-08-06",
    }

    r0 = requests.put(url + "?resource=file", headers=headers, timeout=60)
    if r0.status_code not in (201, 200, 202):
        raise HTTPException(
            502,
            detail=f"สร้างไฟล์บน OneLake ไม่สำเร็จ (HTTP {r0.status_code}): {r0.text[:300]}",
        )

    r1 = requests.patch(
        url + "?action=append&position=0",
        headers={**headers, "Content-Type": "application/octet-stream"},
        data=content,
        timeout=120,
    )
    if r1.status_code not in (202, 200):
        raise HTTPException(
            502,
            detail=f"อัปโหลดเนื้อหาไป OneLake ไม่สำเร็จ (HTTP {r1.status_code}): {r1.text[:300]}",
        )

    r2 = requests.patch(
        url + f"?action=flush&position={len(content)}",
        headers=headers,
        timeout=60,
    )
    if r2.status_code not in (200, 201):
        raise HTTPException(
            502,
            detail=f"ยืนยันไฟล์ (flush) บน OneLake ไม่สำเร็จ (HTTP {r2.status_code}): {r2.text[:300]}",
        )


def upload_allocations_to_lakehouse(req: LakehouseUploadRequest) -> dict:
    """
    สร้างไฟล์ CSV (emp_id, sku, allocated_boxes, sup_id, target_month, target_year, uploaded_at, batch_id)
    แล้วอัปโหลดไป OneLake/Lakehouse (Files/)
    """
    if not req.allocations:
        raise HTTPException(400, detail="ไม่มีข้อมูล allocations สำหรับอัปโหลด")

    df = pd.DataFrame([a.model_dump() for a in req.allocations])
    df["sup_id"] = req.sup_id
    df["target_month"] = int(req.target_month)
    df["target_year"] = int(req.target_year)
    batch_id = str(uuid.uuid4())
    uploaded_at = datetime.now(timezone.utc).isoformat()
    df["upload_batch_id"] = batch_id
    df["uploaded_at_utc"] = uploaded_at

    cols = [
        "sup_id",
        "target_year",
        "target_month",
        "emp_id",
        "sku",
        "allocated_boxes",
        "upload_batch_id",
        "uploaded_at_utc",
    ]
    df = df[cols]

    # CSV utf-8-sig ให้เปิด Excel ไทยได้ง่าย (แต่ Lakehouse ก็อ่านได้ปกติ)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    content = ("\ufeff" + buf.getvalue()).encode("utf-8")

    # Path ใน Lakehouse Files/
    prefix = (os.environ.get("ONELAKE_UPLOAD_DIR") or "Files/target_allocation_uploads").strip()
    prefix = prefix.strip("/").replace("\\", "/")
    # prefix should be relative to Files/ (allow user to include Files/...)
    if prefix.lower().startswith("files/"):
        prefix = prefix[6:]
    fname = (
        f"alloc_{safe_id(req.sup_id)}_{req.target_year}_{req.target_month:02d}_{batch_id}.csv"
    )
    remote_path = f"{prefix}/{fname}"

    token = _get_storage_token()
    _upload_bytes_to_onelake(remote_path, content, token)

    logger.info("uploaded allocations to OneLake: %s (%d rows)", remote_path, len(df))
    return {
        "status": "ok",
        "rows": int(len(df)),
        "remote_path": remote_path,
        "upload_batch_id": batch_id,
    }

