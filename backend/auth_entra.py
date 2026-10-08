"""
Microsoft Entra — ล็อกอินผ่าน Microsoft จากนั้นใช้อีเมลไปผูกกับ ACC_USER_CONTROL ในฐานข้อมูล
(ไม่บังคับ membership ใน security group อีกต่อไป — เก่าใช้ AZURE_AUTH_ALLOWED_GROUP_ID)

ปิดการบังคับ: AZURE_AUTH_DISABLED=1
"""

from __future__ import annotations

import logging
import os
import threading
import time
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

_JWK_CLIENTS: dict[str, PyJWKClient] = {}
_JWK_CLIENTS_LOCK = threading.Lock()
_JWKS_URIS_CACHE: dict[str, tuple[float, list[str]]] = {}
_JWKS_URIS_LOCK = threading.Lock()
_JWKS_URIS_TTL_SEC = int(os.environ.get("AZURE_AUTH_JWKS_URI_CACHE_SEC", "3600"))


def _get_jwk_client(jwks_uri: str) -> PyJWKClient:
    """PyJWKClient แบบ cache — อย่าดึง keys จาก Microsoft ทุก request"""
    with _JWK_CLIENTS_LOCK:
        client = _JWK_CLIENTS.get(jwks_uri)
        if client is None:
            client = PyJWKClient(jwks_uri, cache_keys=True, lifespan=3600)
            _JWK_CLIENTS[jwks_uri] = client
        return client


def _candidate_jwks_uris_cached(tid: str, iss: str) -> list[str]:
    cache_key = f"{tid}|{iss}"
    now = time.time()
    with _JWKS_URIS_LOCK:
        hit = _JWKS_URIS_CACHE.get(cache_key)
        if hit and (now - hit[0]) < _JWKS_URIS_TTL_SEC:
            return list(hit[1])
    uris = _candidate_jwks_uris(tid, iss)
    with _JWKS_URIS_LOCK:
        _JWKS_URIS_CACHE[cache_key] = (now, uris)
    return uris


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


_MS_LOGIN_PREFIX = "https://login.microsoftonline.com/"


def _allowed_issuers(tid: str) -> set[str]:
    """
    issuer ที่ยอมรับได้ของ tenant นี้ — ตรงตัวเท่านั้น (ผลตรวจ 7 ต.ค. 2026 ก1)

    เดิมเช็ค `"login.microsoftonline.com" in iss` แบบค้นข้อความ แล้วไปโหลดกุญแจจาก jwks_uri
    ที่ issuer นั้นบอก → คนปลอม iss เป็น https://โดเมนตัวเอง/login.microsoftonline.com
    แล้วเซ็นโทเคนเองเป็นอีเมล dev ได้ (+ ใช้ server ยิง URL ภายนอกได้)
    """
    t = (tid or "").strip().lower()
    if not t:
        return set()
    return {
        f"https://login.microsoftonline.com/{t}/v2.0",
        f"https://login.microsoftonline.com/{t}",
        f"https://sts.windows.net/{t}",
    }


def _issuer_ok(iss: str, tid: str) -> bool:
    return (iss or "").strip().rstrip("/").lower() in _allowed_issuers(tid)


def _fetch_jwks_uri_from_issuer(iss: str, tid: str = "") -> str | None:
    """
    ดึง jwks_uri จาก OpenID configuration ของ issuer ในโทเคน
    (มาตรฐาน Microsoft — ตรงกว่า hardcode discovery/keys อย่างเดียว)
    ยอมเฉพาะ issuer ของ tenant นี้ (ตรงตัว) และ jwks_uri ต้องอยู่ที่ login.microsoftonline.com
    """
    iss = (iss or "").strip().rstrip("/")
    if not iss or not _issuer_ok(iss, tid):
        return None
    meta_urls: list[str] = []
    if iss.lower().startswith(_MS_LOGIN_PREFIX):
        meta_urls.append(f"{iss}/.well-known/openid-configuration")
    if iss.lower().startswith("https://sts.windows.net/"):
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
            if isinstance(jwks_uri, str) and jwks_uri.lower().startswith(_MS_LOGIN_PREFIX):
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
            if isinstance(ju, str) and ju.lower().startswith(_MS_LOGIN_PREFIX):
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
    u = _fetch_jwks_uri_from_issuer(iss, tid)
    if u:
        out.append(u)
    if tid:
        v2, v1 = _jwks_uri_variants(tid)
        out.extend([v2, v1])
    seen: set[str] = set()
    deduped: list[str] = []
    for x in out:
        # กุญแจต้องมาจาก Microsoft เท่านั้น — ไม่ว่า metadata จะบอกอะไร
        if x and x not in seen and x.lower().startswith(_MS_LOGIN_PREFIX):
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
    if not _issuer_ok(iss, tid):
        raise ValueError("ผู้ออกโทเคน (iss) ไม่ใช่ Microsoft ของบริษัท — กรุณาล็อกอินใหม่")
    uris = _candidate_jwks_uris_cached(tid, iss)
    last_err: Exception | None = None

    for jwks_uri in uris:
        try:
            client = _get_jwk_client(jwks_uri)
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


def auth_explicitly_disabled() -> bool:
    return os.environ.get("AZURE_AUTH_DISABLED", "").strip().lower() in ("1", "true", "yes")


def auth_enabled() -> bool:
    if auth_explicitly_disabled():
        return False
    return bool(_client_id() and _tenant_id())


_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _bind_host_from_argv(argv: list[str] | None = None) -> str | None:
    """host ที่ uvicorn ถูกสั่งให้ฟัง (จาก command line) — ไม่รู้คืน None"""
    import sys

    args = list(sys.argv if argv is None else argv)
    if not any("uvicorn" in str(a) for a in args):
        return None  # ไม่ได้รันผ่าน uvicorn (เช่นเทสต์) — ไม่รู้ host
    for i, a in enumerate(args):
        if a == "--host" and i + 1 < len(args):
            return str(args[i + 1]).strip()
        if str(a).startswith("--host="):
            return str(a).split("=", 1)[1].strip()
    return "127.0.0.1"  # ค่าเริ่มต้นของ uvicorn


# header ที่ reverse proxy ใส่มา — มีอย่างใดอย่างหนึ่ง = คำขอไม่ได้มาจากเครื่องนี้โดยตรง
_PROXY_HEADERS = ("x-forwarded-for", "x-real-ip", "forwarded", "x-forwarded-host")


def request_allowed_without_login(client_host: str | None, headers) -> bool:
    """
    ตอนล็อกอินปิดอยู่ (ไม่มี Entra) รับเฉพาะคำขอจากเครื่องนี้เองโดยตรง (ผลตรวจ 5 ต.ค. 2026 ข้อ 7.9)

    ตัวกันตอนสตาร์ทดูได้แค่ `--host` ของ uvicorn — รันผ่าน gunicorn/hypercorn ที่ 0.0.0.0
    หรือมี proxy อยู่หน้า 127.0.0.1 ก็หลุด แล้วทุกคนที่เข้า URL ได้เป็น dev
    ตัวนี้ตรวจทุกคำขอจากที่อยู่จริงของ socket + header ของ proxy จึงไม่ขึ้นกับวิธีรัน
    "testclient" = Starlette TestClient (ที่อยู่ IP จริงจากเครือข่ายเป็นค่านี้ไม่ได้)
    """
    host = str(client_host or "").strip().lower()
    if host not in _LOOPBACK_HOSTS and host != "testclient":
        return False
    try:
        return not any(headers.get(h) for h in _PROXY_HEADERS)
    except Exception:
        return False


def check_auth_config_at_startup(argv: list[str] | None = None) -> None:
    """
    ล็อกอินต้องไม่ถูกปิดเองโดยไม่มีใครตั้งใจ (ผลตรวจ 28 ก.ย. 2026 §1.4)

    เดิม: ขาด AZURE_AUTH_CLIENT_ID หรือ tenant เมื่อไร auth_enabled() = False เงียบ ๆ
    ทุกคนที่เข้า URL ได้กลายเป็น dev รวมสิทธิ์ส่ง Target Sun โดยไม่มี log เตือนเลย

    ตอนนี้:
      - config ไม่ครบ และไม่ได้ตั้ง AZURE_AUTH_DISABLED=1 → ไม่ยอมสตาร์ท
      - ตั้ง AZURE_AUTH_DISABLED=1 → สตาร์ทได้เฉพาะเมื่อฟังแค่ localhost
        (ใช้บนเครื่อง dev) และ log ระดับ ERROR ทุกครั้ง
    server ที่ตั้ง Entra ครบอยู่แล้วไม่กระทบ — ไม่ต้องแก้ .env
    """
    if auth_explicitly_disabled():
        host = _bind_host_from_argv(argv)
        if host is not None and host not in _LOOPBACK_HOSTS:
            raise RuntimeError(
                f"AZURE_AUTH_DISABLED=1 แต่ server ฟังที่ {host} (ไม่ใช่ localhost) — "
                "ไม่ยอมสตาร์ท เพราะทุกคนที่เข้า URL ได้จะได้สิทธิ์ dev "
                "ปิดล็อกอินได้เฉพาะเครื่อง dev ที่รัน --host 127.0.0.1"
            )
        logger.error(
            "ปิดการล็อกอินอยู่ (AZURE_AUTH_DISABLED=1) — ทุกคนที่เข้าได้คือ dev "
            "ใช้ได้เฉพาะเครื่องพัฒนาเท่านั้น"
        )
        return
    missing = [
        name
        for name, val in (("AZURE_AUTH_CLIENT_ID", _client_id()), ("AZURE_AUTH_TENANT_ID", _tenant_id()))
        if not val
    ]
    if missing:
        raise RuntimeError(
            "ตั้งค่าการล็อกอินไม่ครบ — ขาด " + ", ".join(missing) + " ระบบไม่ยอมสตาร์ท "
            "(ถ้าไม่เช็คตรงนี้ ระบบจะปิดล็อกอินเองแล้วทุกคนได้สิทธิ์ dev) "
            "· เครื่องพัฒนาที่ตั้งใจปิดล็อกอิน ให้ตั้ง AZURE_AUTH_DISABLED=1 และรันแบบ --host 127.0.0.1"
        )


def spa_config_payload() -> dict[str, Any]:
    """ค่าที่ส่งให้ frontend MSAL (ไม่มี secret)"""
    en = auth_enabled()
    return {
        "authRequired": en,
        "tenantId": _tenant_id() if en else None,
        "clientId": _client_id() if en else None,
    }



def fetch_graph_primary_email(bearer: str) -> str | None:
    """
    ดึง mail จาก Microsoft Graph เมื่อ claims ไม่มีอีเมลชัดเจน

    timeout ต้อง "สั้นกว่า" ฝั่ง client ชัดเจน: frontend เรียก /managers ด้วย timeout 15 วิ
    (app.js: fetchWithTimeout(`${API_BASE_URL}/managers`, {}, 15000)) ถ้าที่นี่ก็ 15 วิเท่ากัน
    client จะยอมแพ้พอดีตอน server ยังรอ Graph อยู่ → ผู้ใช้เห็นหน้า login ค้างแล้วต้องรีเฟรชเอง
    """
    try:
        r = requests.get(
            "https://graph.microsoft.com/v1.0/me?$select=mail,userPrincipalName",
            headers={"Authorization": f"Bearer {bearer}"},
            timeout=6,
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
        # Graph token ของแอปอื่นในบริษัทห้ามใช้แทน (ผลตรวจ 7 ต.ค. 2026) — ต้องออกให้แอปนี้
        # v1 token ใช้ appid · v2 ใช้ azp
        issued_to = str(payload.get("appid") or payload.get("azp") or "").strip().lower()
        if cid and issued_to and issued_to != cid.lower():
            raise ValueError("โทเคนนี้ออกให้แอปอื่น ไม่ใช่ระบบกระจายเป้า — กรุณาล็อกอินใหม่")
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
