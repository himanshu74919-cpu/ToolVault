import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
from starlette.requests import Request


class ImeiTacPrivacyTests(unittest.TestCase):
    def test_native_lookup_uses_tac_and_omits_serial(self):
        result, partial = asyncio.run(main.native_imei({"imei": "353010111111110"}, None))
        self.assertFalse(partial)
        self.assertEqual(result["tac"], "35301011")
        self.assertEqual(result["model"], "iPhone 12 mini")
        self.assertNotIn("imei", result)
        self.assertNotIn("serial_number", result)

    def test_sensitive_endpoints_are_disabled_before_querying_or_logging(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_db = main.DB_PATH
            main.DB_PATH = str(Path(tmp) / "test.db")
            logged = []
            blocked_paths = {
                "num-info", "number-info", "num", "leak-v1", "leak-v2", "family", "num-family",
                "email-info", "email", "aadhaar-family", "aadhaar", "ration",
                "vehicle-report", "vehicle-full", "rc-info",
            }
            paths = [ep["path"] for ep in main.ENDPOINTS
                     if ep["path"] in blocked_paths or ep["path"].startswith("vehicle-")]

            async def invoke(path):
                raw_path = f"/api/{path}".encode()
                scope = {
                    "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
                    "http_version": "1.1", "method": "GET", "scheme": "https",
                    "path": raw_path.decode(), "raw_path": raw_path,
                    "query_string": b"key=Demo&q=0000000000&number=XX00XX0000", "headers": [],
                    "client": ("127.0.0.1", 12345), "server": ("test", 443),
                }
                request = Request(scope)
                with patch.object(main, "validate_key", return_value=(True, "Demo", "")), patch.object(
                    main, "brand", return_value="@Supermannn_x"
                ), patch.object(main, "log_request", side_effect=lambda *args: logged.append(args)), patch.object(
                    main, "upstream_call", side_effect=AssertionError("restricted route reached upstream")
                ):
                    return await main.run_endpoint(
                        request, main.ENDPOINT_MAP[path], extra_params=dict(request.query_params)
                    )

            try:
                self.assertGreaterEqual(len(paths), 20)
                for path in paths:
                    response = asyncio.run(invoke(path))
                    self.assertEqual(response.status_code, 410, path)
                    body = json.loads(response.body)
                    self.assertEqual(body["status"], "disabled", path)
                    self.assertEqual(body["powered_by"], "@Supermannn_x", path)
                self.assertEqual(logged, [])
            finally:
                main.DB_PATH = old_db

    def test_disabled_records_are_not_advertised_or_saved(self):
        plans = [
            {"id": "number", "name": "Number Pack", "endpoints": "num-info,family"},
            {"id": "vehicle", "name": "Vehicle Pack", "endpoints": "vehicle-report,ifsc"},
            {"id": "utility", "name": "Utility Pack", "endpoints": "imei,ifsc"},
            {"id": "full", "name": "Full Access", "endpoints": "*", "description": "30 din"},
        ]
        with patch.object(main, "get_setting", return_value=json.dumps(plans)):
            visible = main.store_plans()
        self.assertEqual([p["id"] for p in visible], ["utility", "full"])
        self.assertIn("disabled", visible[-1]["description"].lower())
        self.assertIsNone(main.safe_endpoint_allowlist("num-info,family"))
        self.assertIsNone(main.safe_endpoint_allowlist("vehicle-report"))
        self.assertEqual(main.safe_endpoint_allowlist("imei,ifsc"), "imei,ifsc")
        disabled_only = [
            {"id": "number", "name": "Number Pack", "endpoints": "num-info,family"},
            {"id": "vehicle", "name": "Vehicle Pack", "endpoints": "vehicle-report"},
        ]
        with patch.object(main, "get_setting", return_value=json.dumps(disabled_only)):
            fallback = main.store_plans()
        self.assertTrue(any(p["id"] == "utility" for p in fallback))

        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1", "method": "POST", "scheme": "https",
            "path": "/admin/records", "raw_path": b"/admin/records", "query_string": b"",
            "headers": [], "client": ("127.0.0.1", 12345), "server": ("test", 443),
        }
        request = Request(scope)

        async def invoke():
            with patch.object(main, "require_admin", return_value=None):
                leaked = await main.admin_add_record(request, {
                    "category": "leak", "key_value": "synthetic", "data": {"name": "not stored"}
                })
                imported = await main.admin_import_records(request, {
                    "category": "phone", "text": "synthetic,not stored"
                })
                vehicle = await main.admin_add_record(request, {
                    "category": "vehicle", "key_value": "XX00XX0000", "data": {"owner": "not stored"}
                })
                paid_plan = await main.admin_create_key(request, {"allowed_endpoints": "family"})
                reseller_plan = await main.admin_create_reseller(request, {
                    "username": "test-reseller", "password": "test-only", "allowed_endpoints": "vehicle-report"
                })
                return leaked, imported, vehicle, paid_plan, reseller_plan

        responses = asyncio.run(invoke())
        self.assertTrue(all(r.status_code == 410 for r in responses))

    def test_endpoint_sanitizes_before_cache_and_request_logging(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_db = main.DB_PATH
            main.DB_PATH = str(Path(tmp) / "test.db")
            logged = []
            scope = {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "https",
                "path": "/api/imei",
                "raw_path": b"/api/imei",
                "query_string": b"key=Demo&imei=353010111111110",
                "headers": [],
                "client": ("127.0.0.1", 12345),
                "server": ("test", 443),
            }
            request = Request(scope)

            async def invoke():
                with patch.object(main, "over_rate_limit", return_value=False), patch.object(
                    main,
                    "log_request",
                    side_effect=lambda path, key, params, source, status, ms, ip: logged.append(dict(params)),
                ):
                    response = await main.run_endpoint(
                        request, main.ENDPOINT_MAP["imei"], extra_params=dict(request.query_params)
                    )
                    return json.loads(response.body)

            try:
                body = asyncio.run(invoke())
                self.assertEqual(body["tac"], "35301011")
                self.assertNotIn("imei", body)
                self.assertNotIn("353010111111110", json.dumps(body))
                self.assertEqual(logged[0].get("imei"), "35301011")
                self.assertNotIn("353010111111110", json.dumps(logged))
            finally:
                main.DB_PATH = old_db


if __name__ == "__main__":
    unittest.main(verbosity=2)
