"""
Microsoft Entra — ล็อกอินผ่าน Microsoft จากนั้นใช้อีเมลไปผูกกับ ACC_USER_CONTROL ในฐานข้อมูล
(ไม่บังคับ membership ใน security group อีกต่อไป — เก่าใช้ AZURE_AUTH_ALLOWED_GROUP_ID)

ปิดการบังคับ: AZURE_AUTH_DISABLED=1
"""

from __future__ import annotations

import logging
import os
from typing import Any

import jwt
import requests
from jwt import PyJWKClient
from jwt import exceptions as jwt_exc

logger = logging.getLogger("target_allocation.auth")


def _tenant_id() -> str:
    return (
        os.environ.get("AZURE_AUTH_TENANT_ID")
        or os.environ.get("FABRIC_TENANT_ID")
        or ""
    ).strip()


def _client_id() -> str:
    return os.environ.get("AZURE_AUTH_CLIENT_ID", "").strip()


# ค่าเริ่มต้นตอน import (สำหรับ log / เอกสาร)
TENANT_ID = _tenant_id()
CLIENT_ID = _client_id()

GRAPH_AUDIENCES = (
    "https://graph.microsoft.com",
    "00000003-0000-0000-c000-000000000000",
)

_UNVERIFIED_ALGS = ["RS256", "PS256", "ES256"]


def _unverified_payload(token: str) -> dict[str, Any]:
    """อ่าน payload โดยไม่ verify — ใช้หา tid / iss"""
    return jwt.decode(
        token,
        algorithms=_UNVERIFIED_ALGS,
        options={
            "verify_signature": False,
            "verify_aud": False,
            "verify_exp": False,
        },
    )


def _jwks_uri_variants(tid: str) -> tuple[str, str]:
    return (
        f"https://login.microsoftonline.com/{tid}/discovery/v2.0/keys",
        f"https://login.microsoftonline.com/{tid}/discovery/keys",
    )


def _fetch_jwks_uri_from_issuer(iss: str) -> str | None:
    """
    ดึง jwks_uri จาก OpenID configuration ของ issuer ในโทเคน
    (มาตรฐาน Microsoft — ตรงกว่า hardcode discovery/keys อย่างเดียว)
    """
    iss = (iss or "").strip().rstrip("/")
    if not iss:
        return None
    meta_urls: list[str] = []
    if "login.microsoftonline.com" in iss:
        meta_urls.append(f"{iss}/.well-known/openid-configuration")
    if "sts.windows.net" in iss:
        parts = [p for p in iss.split("/") if p]
        tid = parts[-1] if parts else ""
        if tid:
            meta_urls.extend(
                [
                    f"https://login.microsoftonline.com/{tid}/v2.0/.well-known/openid-configuration",
                    f"https://login.microsoftonline.com/{tid}/.well-known/openid-configuration",
                ]
            )
    for meta_url in meta_urls:
        try:
            r = requests.get(meta_url, timeout=15)
            if r.status_code != 200:
                continue
            data = r.json()
            jwks_uri = data.get("jwks_uri")
            if isinstance(jwks_uri, str) and jwks_uri.startswith("http"):
                return jwks_uri
        except requests.RequestException as e:
            logger.info("openid-configuration fetch failed %s: %s", meta_url, e)
    return None


def _jwks_uri_from_tenant_oidc_metadata(tid: str) -> list[str]:
    """ดึง jwks_uri จาก .well-known ของ tenant โดยตรง (กรณี iss ในโทเคนว่าง/แปลก)"""
    if not tid:
        return []
    found: list[str] = []
    for meta_url in (
        f"https://login.microsoftonline.com/{tid}/v2.0/.well-known/openid-configuration",
        f"https://login.microsoftonline.com/{tid}/.well-known/openid-configuration",
    ):
        try:
            r = requests.get(meta_url, timeout=15)
            if r.status_code != 200:
                continue
            ju = r.json().get("jwks_uri")
            if isinstance(ju, str) and ju.startswith("http"):
                found.append(ju)
        except requests.RequestException as e:
            logger.info("tenant oidc meta failed %s: %s", meta_url, e)
    return found


def _graph_me_accepts_bearer(token: str) -> bool:
    """
    ถ้า Graph ตอบ 200 แปลว่าโทเคนเป็น access token ที่ Microsoft ตรวจแล้ว
    (fallback เมื่อ PyJWT ตรวจลายเซ็นในเครื่องไม่ผ่าน — มักเจอกับ cryptography/pyjwt บางเวอร์ชัน)
    """
    try:
        r = requests.get(
            "https://graph.microsoft.com/v1.0/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if r.status_code != 200:
            # ไม่ log โทเคน — ช่วยวินิจฉัยว่าเป็น AT ผิดประเภท / หมดอายุ / เครือข่ายบล็อก Graph
            logger.info(
                "Graph /me rejected bearer: HTTP %s — %s",
                r.status_code,
                (r.text or "")[:200].replace("\n", " "),
            )
        return r.status_code == 200
    except requests.RequestException as e:
        logger.warning("Graph /me request error (เช็คว่าเซิร์ฟเวอร์เข้า graph.microsoft.com ได้): %s", e)
        return False


def _candidate_jwks_uris(tid: str, iss: str) -> list[str]:
    """ลำดับ JWKS ที่ลองได้ — ลายเซ็นบางโทเคนตรงกับชุด keys คนละ URL"""
    out: list[str] = []
    out.extend(_jwks_uri_from_tenant_oidc_metadata(tid))
    u = _fetch_jwks_uri_from_issuer(iss)
    if u:
        out.append(u)
    if tid:
        v2, v1 = _jwks_uri_variants(tid)
        out.extend([v2, v1])
    seen: set[str] = set()
    deduped: list[str] = []
    for x in out:
        if x and x not in seen:
            seen.add(x)
            deduped.append(x)
    return deduped


def _decode_microsoft_jwt_verify_signature(token: str) -> dict[str, Any]:
    """
    ตรวจลายเซ็นก่อน โดยไม่ verify aud — แล้วค่อยแยกเส้น Graph / ID token ทีหลัง
    ลองหลาย jwks_uri (cache_keys=False) กัน PyJWKClient ค้างคีย์เก่า
    """
    try:
        header = jwt.get_unverified_header(token)
    except Exception as e:
        raise ValueError(f"อ่าน header โทเคนไม่ได้: {e}") from e

    sig_alg = (header.get("alg") or "RS256").upper()
    if sig_alg not in ("RS256", "PS256", "ES256"):
        raise ValueError(f"ไม่รองรับ alg โทเคน: {sig_alg}")

    try:
        claims = _unverified_payload(token)
    except Exception as e:
        raise ValueError(f"แปลงโทเคนไม่ได้: {e}") from e

    tid = str(claims.get("tid") or "").strip() or _tenant_id()
    expected = _tenant_id().lower()
    if expected and tid.lower() != expected:
        raise ValueError(
            "tid ในโทเคนไม่ตรงกับ FABRIC_TENANT_ID / AZURE_AUTH_TENANT_ID ใน config/.env หรือ .env ที่ราก"
        )

    iss = str(claims.get("iss") or "").strip()
    uris = _candidate_jwks_uris(tid, iss)
    last_err: Exception | None = None

    for jwks_uri in uris:
        try:
            client = PyJWKClient(jwks_uri, cache_keys=False)
            sk = client.get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token,
                sk.key,
                algorithms=[sig_alg],
                options={"verify_aud": False, "verify_iss": False},
                leeway=120,
            )
            return payload
        except jwt_exc.ExpiredSignatureError as e:
            raise ValueError("โทเคนหมดอายุ — กรุณากดล็อกอิน Microsoft ใหม่") from e
        except jwt_exc.InvalidSignatureError as e:
            last_err = e
            continue
        except Exception as e:
            last_err = e
            continue

    # Fallback: ยืนยันผ่าน Microsoft Graph (โทเคนต้องยังใช้กับ Graph ได้)
    if _graph_me_accepts_bearer(token):
        logger.info(
            "JWT signature verify skipped — Graph /me accepted token (tid=%s)",
            tid,
        )
        try:
            return jwt.decode(
                token,
                algorithms=_UNVERIFIED_ALGS,
                options={
                    "verify_signature": False,
                    "verify_aud": False,
                    "verify_exp": True,
                },
                leeway=120,
            )
        except jwt_exc.ExpiredSignatureError as e:
            raise ValueError(
                "โทเคนหมดอายุ — กรุณากดล็อกอิน Microsoft ใหม่"
            ) from e

    raise ValueError(
        f"ลายเซ็นโทเคนตรวจไม่ผ่าน (ลอง JWKS {len(uris)} แหล่ง, tid={tid}) "
        f"และ Microsoft Graph /me ไม่รับโทเคนนี้ — "
        f"ให้ใช้ access token ของ Graph (scope User.Read / https://graph.microsoft.com/User.Read) "
        f"ไม่ใช่แค่ ID token; ดู log บรรทัด Graph /me rejected — สาเหตุ: {last_err}"
    ) from last_err


def _aud_matches_graph(aud: Any) -> bool:
    want = {a.lower() for a in GRAPH_AUDIENCES}
    want.add("https://graph.microsoft.com")
    if isinstance(aud, str):
        return aud.strip().lower() in want
    if isinstance(aud, list):
        return any(
            isinstance(a, str) and a.strip().lower() in want for a in aud
        )
    return False


def _aud_matches_client(aud: Any, client_id: str) -> bool:
    if not client_id:
        return False
    cid = client_id.lower()
    if isinstance(aud, str):
        return aud.strip().lower() == cid
    if isinstance(aud, list):
        return any(str(a).strip().lower() == cid for a in aud)
    return False


def auth_enabled() -> bool:
    if os.environ.get("AZURE_AUTH_DISABLED", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        return False
    return bool(_client_id() and _tenant_id())


def spa_config_payload() -> dict[str, Any]:
    """ค่าที่ส่งให้ frontend MSAL (ไม่มี secret)"""
    en = auth_enabled()
    return {
        "authRequired": en,
        "tenantId": _tenant_id() if en else None,
        "clientId": _client_id() if en else None,
    }



def fetch_graph_primary_email(bearer: str) -> str | None:
    """ดึง mail จาก Microsoft Graph เมื่อ claims ไม่มีอีเมลชัดเจน"""
    try:
        r = requests.get(
            "https://graph.microsoft.com/v1.0/me?$select=mail,userPrincipalName",
            headers={"Authorization": f"Bearer {bearer}"},
            timeout=15,
        )
        if r.status_code != 200:
            return None
        js = r.json()
        for k in ("mail", "userPrincipalName"):
            v = js.get(k)
            if isinstance(v, str) and "@" in v:
                return v.strip().lower()
    except requests.RequestException as e:
        logger.warning("Graph /me fetch email failed: %s", e)
    return None


def get_primary_email_from_claims(payload: dict[str, Any]) -> str | None:
    for key in ("email", "preferred_username", "unique_name", "upn"):
        v = payload.get(key)
        if isinstance(v, str) and "@" in v:
            return v.strip().lower()
    return None


def _verify_tid(payload: dict[str, Any]) -> None:
    tid = str(payload.get("tid") or "").lower()
    expected = _tenant_id().lower()
    if expected and tid != expected:
        raise ValueError("โทเคนไม่ใช่ของ tenant นี้")


def verify_microsoft_identity(token: str) -> dict[str, Any]:
    """
    ตรวจว่า Bearer เป็น JWT ของ tenant เรา (Graph AT หรือ SPA ID token)
    คืน payload ดั้งเดิม + email (สำหรับ ACC_USER_CONTROL)
    """
    if not token:
        raise ValueError("ไม่มีโทเคน")
    token = token.strip()

    payload = _decode_microsoft_jwt_verify_signature(token)
    _verify_tid(payload)

    aud = payload.get("aud")
    cid = _client_id()

    if _aud_matches_graph(aud):
        email = get_primary_email_from_claims(payload)
        if not email:
            email = fetch_graph_primary_email(token)
        if not email:
            raise ValueError(
                "ไม่พบที่อยู่อีเมลในโทเคน — "
                "ลองล็อกอินด้วย scope เช่น User.Read และตรวจว่าได้ access token ของ Graph"
            )
        return {"payload": payload, "email": email}

    if _aud_matches_client(aud, cid):
        email = get_primary_email_from_claims(payload)
        if email:
            return {"payload": payload, "email": email}

        raise ValueError(
            "โทเคน ID token ของแอปไม่มี claim อีเมล — "
            "ใน Azure AD เพิ่ม optional claim `email` หรือล็อกอินให้ได้ access token จาก Graph "
            "(scope User.Read)"
        )

    raise ValueError(
        "โทเคนไม่ใช่ Microsoft Graph access token หรือ ID token ของแอปนี้ "
        f"(aud ในโทเคนไม่ตรง Graph / client_id={cid})"
    )


def verify_bearer_and_group(token: str) -> dict[str, Any]:
    """คงชื่อเดิม — ไม่เช็ค group แล้ว คืนเฉพาะ JWT payload"""
    return verify_microsoft_identity(token)["payload"]
