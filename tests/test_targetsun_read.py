"""Tests for Target Sun Read API client."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import targetsun_read as tsr  # noqa: E402


class TestTargetSunReadMapping(unittest.TestCase):
    def test_rows_to_granular_df_maps_fields(self):
        rows = [
            {
                "PRODUCTCODE": "123456",
                "SALESTYPE": "S",
                "DIVISIONCODE": "B",
                "SALESMANCODE": "12345",
                "AREACODE": "1",
                "PROVINCECODE": "10",
                "WAREHOUSECODE": "1001",
                "QUANTITYCASE": 50,
            }
        ]
        df = tsr.rows_to_granular_df(rows)
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["emp_id"], "12345")
        self.assertEqual(df.iloc[0]["sku"], "123456")
        self.assertEqual(int(df.iloc[0]["qty"]), 50)
        self.assertEqual(df.iloc[0]["salestype"], "S")
        self.assertEqual(df.iloc[0]["divisioncode"], "B")

    def test_normalize_salesman_code_pads_digits(self):
        self.assertEqual(tsr._normalize_salesman_code("42"), "00042")


class TestTargetSunScope(unittest.TestCase):
    @patch("backend.services.targetsun_read.read_rows")
    def test_resolve_from_user_access(self, mock_rows):
        mock_rows.return_value = [
            {
                "userpl": "SL330",
                "acc_division": "Div.B",
                "acc_unit": "credit",
            }
        ]
        div, st = tsr.resolve_targetsun_scope("SL330")
        self.assertEqual(div, "B")
        self.assertEqual(st, "S")

    @patch("backend.services.targetsun_read.read_rows")
    def test_resolve_van(self, mock_rows):
        mock_rows.return_value = [
            {"userpl": "SL520", "acc_division": "Div.S", "acc_unit": "van"}
        ]
        div, st = tsr.resolve_targetsun_scope("sl520")
        self.assertEqual(div, "S")
        self.assertEqual(st, "C")


class TestTargetSunEnvelope(unittest.TestCase):
    @patch("backend.services.targetsun_read.requests.request")
    def test_fetch_target_rows_success(self, mock_req):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "success": True,
            "result": {
                "targetYear": 2026,
                "targetMonth": 6,
                "rowCount": 1,
                "rows": [
                    {
                        "PRODUCTCODE": "111111",
                        "SALESMANCODE": "00001",
                        "QUANTITYCASE": 3,
                        "SALESTYPE": "S",
                        "DIVISIONCODE": "B",
                        "AREACODE": "1",
                        "PROVINCECODE": "",
                        "WAREHOUSECODE": None,
                    }
                ],
            },
            "resultMsg": "ok",
        }
        mock_req.return_value = mock_resp

        with patch(
            "backend.services.targetsun_endpoints.resolve_endpoint_bases",
            return_value=("https://example.test/spc/targetsun", "https://example-uat.test/spc/targetsun"),
        ):
            result = tsr.fetch_target_rows(2026, 6, ["1"])
        self.assertEqual(result["rowCount"], 1)
        self.assertEqual(len(result["rows"]), 1)

    @patch("backend.services.targetsun_read.requests.request")
    def test_fetch_rejects_success_false(self, mock_req):
        from fastapi import HTTPException

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "success": False,
            "result": None,
            "resultMsg": "salesmanCodes is required",
        }
        mock_req.return_value = mock_resp

        with self.assertRaises(HTTPException) as ctx:
            tsr.fetch_target_rows(2026, 6, ["12345"])
        self.assertEqual(ctx.exception.status_code, 502)


class TestTargetSunFlags(unittest.TestCase):
    def test_is_enabled_default_targetsun(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TARGETSUN_READ_ENABLED", None)
            with patch("backend.services.app_runtime_settings.get_target_read_source", return_value="targetsun"):
                self.assertTrue(tsr.is_enabled())

    def test_is_enabled_fabric_runtime(self):
        with patch("backend.services.app_runtime_settings.get_target_read_source", return_value="fabric"):
            self.assertFalse(tsr.is_enabled())

    def test_is_enabled_env_off(self):
        with patch.dict(os.environ, {"TARGETSUN_READ_ENABLED": "0"}):
            with patch("backend.services.app_runtime_settings.get_target_read_source", return_value="targetsun"):
                self.assertFalse(tsr.is_enabled())


if __name__ == "__main__":
    unittest.main()
