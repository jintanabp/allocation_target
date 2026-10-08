"""
ผลตรวจ 7 ต.ค. 2026 ก1 — ห้ามเชื่อ iss ในโทเคนแบบค้นข้อความ

เดิม iss = https://evil.example/login.microsoftonline.com ผ่าน แล้ว server ไปโหลด
openid-configuration / กุญแจจากโดเมนของคนปลอม → เซ็นโทเคนเองเป็นอีเมล dev ได้
"""

from __future__ import annotations

import base64
import json
import os
import unittest
from unittest import mock

from backend import auth_entra

TID = "11111111-2222-3333-4444-555555555555"
CID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _b64(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()


def _token(claims: dict) -> str:
    return f"{_b64({'alg': 'RS256', 'typ': 'JWT'})}.{_b64(claims)}.c2ln"


class TestIssuerPinned(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(
            os.environ, {"AZURE_AUTH_TENANT_ID": TID, "AZURE_AUTH_CLIENT_ID": CID}
        )
        self._env.start()
        auth_entra._JWKS_URIS_CACHE.clear()

    def tearDown(self):
        self._env.stop()
        auth_entra._JWKS_URIS_CACHE.clear()

    def test_forged_issuer_rejected_before_any_fetch(self):
        tok = _token({"tid": TID, "iss": "https://evil.example/login.microsoftonline.com", "aud": CID})
        with mock.patch.object(auth_entra.requests, "get") as get:
            with self.assertRaises(ValueError):
                auth_entra._decode_microsoft_jwt_verify_signature(tok)
            get.assert_not_called()

    def test_allowed_issuers_exact(self):
        self.assertTrue(auth_entra._issuer_ok(f"https://login.microsoftonline.com/{TID}/v2.0", TID))
        self.assertTrue(auth_entra._issuer_ok(f"https://sts.windows.net/{TID}/", TID))
        self.assertFalse(auth_entra._issuer_ok(f"https://sts.windows.net.evil.example/{TID}/", TID))
        self.assertFalse(auth_entra._issuer_ok(f"https://login.microsoftonline.com/other/v2.0", TID))
        self.assertFalse(auth_entra._issuer_ok("", TID))

    def test_jwks_from_metadata_must_be_microsoft(self):
        resp = mock.Mock(status_code=200)
        resp.json.return_value = {"jwks_uri": "https://evil.example/keys"}
        with mock.patch.object(auth_entra.requests, "get", return_value=resp):
            uris = auth_entra._candidate_jwks_uris(TID, f"https://login.microsoftonline.com/{TID}/v2.0")
        self.assertTrue(uris)
        self.assertTrue(all(u.startswith("https://login.microsoftonline.com/") for u in uris), uris)

    def test_graph_token_of_other_app_rejected(self):
        payload = {
            "tid": TID,
            "aud": "https://graph.microsoft.com",
            "appid": "99999999-0000-0000-0000-000000000000",
            "upn": "someone@sahapat.co.th",
        }
        with mock.patch.object(auth_entra, "_decode_microsoft_jwt_verify_signature", return_value=payload):
            with self.assertRaises(ValueError):
                auth_entra.verify_microsoft_identity("x.y.z")

    def test_graph_token_of_this_app_accepted(self):
        payload = {"tid": TID, "aud": "https://graph.microsoft.com", "appid": CID, "upn": "a@sahapat.co.th"}
        with mock.patch.object(auth_entra, "_decode_microsoft_jwt_verify_signature", return_value=payload):
            out = auth_entra.verify_microsoft_identity("x.y.z")
        self.assertEqual(out["email"], "a@sahapat.co.th")


if __name__ == "__main__":
    unittest.main()
