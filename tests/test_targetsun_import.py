"""Tests for Target Sun import URL resolution."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import targetsun_import as tsi  # noqa: E402


class TestTargetSunImportUrl(unittest.TestCase):
    @patch("backend.services.targetsun_import.requests.post")
    def test_post_uses_runtime_import_url(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True, "resultMsg": "ok"}
        mock_post.return_value = mock_resp

        uat_url = "https://spcuatws.sahapat.com/spc/targetsun/importTargetSalesmanNextFromExcel"
        with patch("backend.services.targetsun_import.targetsun_import_excel_url", return_value=uat_url):
            tsi._post_targetsun_multipart(
                b"fake",
                "test.xlsx",
                nrow=1,
                zero_rows=0,
                dropped_dims=0,
                not_in_ts=[],
            )

        called_url = mock_post.call_args[0][0]
        self.assertEqual(called_url, uat_url)

    @patch("backend.services.targetsun_import.requests.post")
    def test_explicit_import_url_override(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True, "resultMsg": "ok"}
        mock_post.return_value = mock_resp

        custom = "https://example.test/spc/targetsun/importTargetSalesmanNextFromExcel"
        tsi._post_targetsun_multipart(
            b"fake",
            "test.xlsx",
            nrow=1,
            zero_rows=0,
            dropped_dims=0,
            not_in_ts=[],
            import_url=custom,
        )
        self.assertEqual(mock_post.call_args[0][0], custom)


if __name__ == "__main__":
    unittest.main()
