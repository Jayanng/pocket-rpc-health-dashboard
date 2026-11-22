"""Unit tests for the Pocket RPC health dashboard.

These tests use fabricated results and a mocked transport, so they
run offline. Live behaviour is verified separately, with
a fresh run against the real Pocket endpoints.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from dashboard import (
    ERROR_PROBE_METHOD,
    build_evidence,
    build_report,
    check_service,
    render_html,
    rpc_call,
    run_checks,
)


def _payloads(*payloads):
    """Return a urlopen stand-in serving one payload per call, in order."""

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps(self._payload).encode()

    calls = {"n": 0}

    def fake_urlopen(request, timeout=None):
        payload = payloads[min(calls["n"], len(payloads) - 1)]
        calls["n"] += 1
        return FakeResponse(payload)

    return fake_urlopen


def _service_result(**overrides):
    """A plausible successful result for one service in one round."""
    result = {
        "status": "ok",
        "chain_id": "0x1",
        "chain_id_expected": "0x1",
        "chain_id_match": True,
        "block_height": "0x100",
        "block_height_int": 256,
        "chain_latency_ms": 10.0,
        "block_latency_ms": 12.0,
        "latency_ms": 10.0,
        "error_case": {
            "method": ERROR_PROBE_METHOD,
            "code": -32601,
            "message": "Method not found",
            "received_error": True,
        },
    }
    result.update(overrides)
    return result


class CheckServiceTests(unittest.TestCase):
    def test_success_records_all_four_checks(self):
        with mock.patch(
            "urllib.request.urlopen",
            _payloads(
                {"jsonrpc": "2.0", "id": 1, "result": "0x1"},
                {"jsonrpc": "2.0", "id": 2, "result": "0x100"},
                {"jsonrpc": "2.0", "id": 3, "error": {"code": -32601, "message": "Method not found"}},
            ),
        ):
            result = check_service("eth", "https://eth.api.pocket.network")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["chain_id"], "0x1")
        self.assertTrue(result["chain_id_match"])
        self.assertEqual(result["block_height"], "0x100")
        self.assertEqual(result["block_height_int"], 256)
        self.assertTrue(result["error_case"]["received_error"])
        self.assertEqual(result["error_case"]["code"], -32601)
        self.assertEqual(result["error_case"]["message"], "Method not found")

    def test_chain_mismatch_is_flagged_not_hidden(self):
        with mock.patch(
            "urllib.request.urlopen",
            _payloads(
                {"jsonrpc": "2.0", "id": 1, "result": "0x2"},
                {"jsonrpc": "2.0", "id": 2, "result": "0x100"},
                {"jsonrpc": "2.0", "id": 3, "error": {"code": -32601, "message": "Method not found"}},
            ),
        ):
            result = check_service("eth", "https://eth.api.pocket.network")
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["chain_id_match"])
        self.assertEqual(result["chain_id"], "0x2")

    def test_transport_failure_is_an_error(self):
        with mock.patch(
            "urllib.request.urlopen", side_effect=OSError("connection refused")
        ):
            result = check_service("eth", "https://eth.api.pocket.network")
        self.assertEqual(result["status"], "error")
        self.assertIsNone(result["chain_id"])
        self.assertIsNone(result["block_height"])
        self.assertIn("connection refused", result["error"])
        self.assertFalse(result["error_case"]["received_error"])

    def test_block_failure_is_an_error(self):
        with mock.patch(
            "urllib.request.urlopen",
            _payloads(
                {"jsonrpc": "2.0", "id": 1, "result": "0x1"},
                {"jsonrpc": "2.0", "id": 2, "error": {"code": -32000, "message": "node busy"}},
                {"jsonrpc": "2.0", "id": 3, "error": {"code": -32601, "message": "Method not found"}},
            ),
        ):
            result = check_service("eth", "https://eth.api.pocket.network")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["chain_id"], "0x1")
        self.assertIsNone(result["block_height"])
        self.assertIn("node busy", result["error"])

    def test_unknown_method_returning_a_result_is_flagged(self):
        with mock.patch(
            "urllib.request.urlopen",
            _payloads(
                {"jsonrpc": "2.0", "id": 1, "result": "0x1"},
                {"jsonrpc": "2.0", "id": 2, "result": "0x100"},
                {"jsonrpc": "2.0", "id": 3, "result": "surprise"},
            ),
        ):
            result = check_service("eth", "https://eth.api.pocket.network")
        self.assertFalse(result["error_case"]["received_error"])
        self.assertIsNone(result["error_case"]["code"])


class RpcCallTests(unittest.TestCase):
    def test_bad_json_becomes_an_error_dict(self):
        class BadResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b"not json"

        with mock.patch("urllib.request.urlopen", return_value=BadResponse()):
            payload, latency = rpc_call("https://eth.api.pocket.network", "eth_chainId")
        self.assertIn("error", payload)
        self.assertGreaterEqual(latency, 0)


class EvidenceTests(unittest.TestCase):
    def test_evidence_aggregates_blocks_latency_and_error_case(self):
        reports = [
            build_report({"eth": _service_result(block_height_int=256, latency_ms=10.0)}),
            build_report({"eth": _service_result(block_height="0x101", block_height_int=257, latency_ms=12.0)}),
            build_report({"eth": _service_result(block_height="0x102", block_height_int=258, latency_ms=14.0)}),
        ]
        evidence = build_evidence(reports)
        details = evidence["services"]["eth"]
        self.assertEqual(evidence["checks_per_service"], 3)
        self.assertEqual(details["successful_checks"], 3)
        self.assertEqual(details["failed_checks"], 0)
        self.assertEqual(details["chain_ids"], ["0x1"])
        self.assertTrue(details["chain_id_stable"])
        self.assertTrue(details["chain_id_match_all"])
        self.assertEqual(details["block_first"], 256)
        self.assertEqual(details["block_last"], 258)
        self.assertTrue(details["block_advanced"])
        self.assertTrue(details["block_monotonic"])
        self.assertEqual(details["min_latency_ms"], 10.0)
        self.assertEqual(details["mean_latency_ms"], 12.0)
        self.assertEqual(details["max_latency_ms"], 14.0)
        self.assertTrue(details["error_case_ok"])
        self.assertEqual(details["error_case_codes"], [-32601])

    def test_evidence_reports_non_advancing_blocks_honestly(self):
        reports = [
            build_report({"eth": _service_result(block_height_int=256, latency_ms=10.0)}),
            build_report({"eth": _service_result(block_height_int=256, latency_ms=11.0)}),
        ]
        details = build_evidence(reports)["services"]["eth"]
        self.assertFalse(details["block_advanced"])
        self.assertTrue(details["block_monotonic"])

    def test_evidence_reports_a_chain_mismatch(self):
        reports = [
            build_report({"eth": _service_result()}),
            build_report({"eth": _service_result(chain_id="0x2", chain_id_match=False)}),
        ]
        details = build_evidence(reports)["services"]["eth"]
        self.assertEqual(details["chain_ids"], ["0x1", "0x2"])
        self.assertFalse(details["chain_id_stable"])
        self.assertFalse(details["chain_id_match_all"])


class RenderTests(unittest.TestCase):
    def test_html_shows_chain_block_and_error_case(self):
        report = build_report({"eth": _service_result()})
        html_text = render_html(report)
        self.assertIn("Pocket RPC Health Dashboard", html_text)
        self.assertIn("eth", html_text)
        self.assertIn("0x1", html_text)
        self.assertIn("0x100", html_text)
        self.assertIn("256", html_text)
        self.assertIn("-32601", html_text)
        self.assertIn("Method not found", html_text)
        self.assertNotIn("{slug}", html_text)
        self.assertIn("eth.api.pocket.network", html_text)
        self.assertIn("1 of 1 services ok", html_text)

    def test_html_evidence_table_shows_block_range(self):
        reports = [
            build_report({"eth": _service_result(block_height_int=256, latency_ms=10.0)}),
            build_report({"eth": _service_result(block_height="0x101", block_height_int=257, latency_ms=12.0)}),
        ]
        report = reports[-1]
        report["evidence"] = build_evidence(reports)
        html_text = render_html(report)
        self.assertIn("256 to 257", html_text)
        self.assertIn("10.0 / 11.0 / 12.0 ms", html_text)


class RunChecksTests(unittest.TestCase):
    def test_run_checks_wires_all_services(self):
        def fake_rpc(endpoint, method, params=None, timeout=15):
            if method == "eth_chainId":
                return {"jsonrpc": "2.0", "id": 1, "result": "0x1"}, 10.0
            if method == "eth_blockNumber":
                return {"jsonrpc": "2.0", "id": 2, "result": "0x100"}, 12.0
            return {"jsonrpc": "2.0", "id": 3, "error": {"code": -32601, "message": "Method not found"}}, 9.0

        with mock.patch("dashboard.rpc_call", side_effect=fake_rpc):
            report = run_checks({"eth": "https://eth.api.pocket.network"})
        self.assertEqual(report["services"]["eth"]["status"], "ok")
        self.assertEqual(report["services"]["eth"]["chain_id"], "0x1")
        self.assertEqual(report["services"]["eth"]["block_height_int"], 256)
        self.assertIn("checked_at", report)


if __name__ == "__main__":
    unittest.main()
