"""Pocket RPC health dashboard.

Checks Pocket Network public RPC service endpoints with no wallet,
no API key, no POKT, and no paid relay. The Foundation's public RPC
(api.pocket.network) gives free, keyless access; see
https://docs.pocket.network/developers/supported-chains/.

For every service, each round runs four checks:

1. Chain identity: ``eth_chainId``, compared with the documented
   chain ID for that network.
2. Block height: ``eth_blockNumber``, read every round so repeated
   runs can show the height advancing.
3. Latency: response time of the chain and block calls.
4. Error handling: an unknown method (``eth_nope``) is sent on
   purpose. A healthy endpoint answers with an RPC error (code
   -32601); the code and message are recorded as the failure case.

Repeated runs are aggregated into evidence: successful vs failed
checks, chain ID stability and match, block height range and
advancement, latency min/mean/max, and the error-case outcome.
"""

import html
import json
import statistics
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

SERVICES = {
    "eth": "https://eth.api.pocket.network",
    "base": "https://base.api.pocket.network",
    "poly": "https://poly.api.pocket.network",
    "arb-one": "https://arb-one.api.pocket.network",
    "op": "https://op.api.pocket.network",
}

EXPECTED_CHAIN_IDS = {
    "eth": "0x1",
    "base": "0x2105",
    "poly": "0x89",
    "arb-one": "0xa4b1",
    "op": "0xa",
}

ERROR_PROBE_METHOD = "eth_nope"


def rpc_call(endpoint, method, params=None, timeout=15):
    """Send one JSON-RPC request. Returns (payload, latency_ms).

    The payload is the decoded JSON object. Transport failures and
    undecodable replies come back as {"error": {"message": ...}} so
    callers never have to catch.
    """
    body = json.dumps(
        {"jsonrpc": "2.0", "method": method, "params": params or [], "id": 1}
    ).encode()
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode())
        if not isinstance(payload, dict):
            payload = {
                "error": {"message": "unexpected non-object reply"},
                "_local_error": True,
            }
    except Exception as exc:  # transport error, timeout, bad JSON
        payload = {"error": {"message": str(exc)}, "_local_error": True}
    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    return payload, latency_ms


def _result_of(payload):
    """Split an RPC payload into (result, error_dict), tolerating junk."""
    if not isinstance(payload, dict):
        return None, {"message": "unexpected non-object reply"}
    error = payload.get("error")
    if isinstance(error, dict):
        return None, error
    if error is not None:
        return None, {"message": str(error)}
    if "result" not in payload:
        return None, {"message": "missing result"}
    return payload["result"], None


def check_service(service_id, endpoint, timeout=15):
    """Run the four checks for one service. Returns one result dict."""
    chain_payload, chain_latency = rpc_call(endpoint, "eth_chainId", timeout=timeout)
    chain_result, chain_error = _result_of(chain_payload)
    block_payload, block_latency = rpc_call(
        endpoint, "eth_blockNumber", timeout=timeout
    )
    block_result, block_error = _result_of(block_payload)
    error_payload, _error_latency = rpc_call(
        endpoint, ERROR_PROBE_METHOD, timeout=timeout
    )
    # A transport failure is not an RPC error: the endpoint never
    # answered, so the error case was not actually probed.
    local_error = bool(error_payload.get("_local_error"))
    _error_result, error_detail = _result_of(error_payload)
    if error_detail is not None and not local_error:
        error_case = {
            "method": ERROR_PROBE_METHOD,
            "code": error_detail.get("code"),
            "message": error_detail.get("message", "RPC error"),
            "received_error": True,
        }
    else:
        if local_error:
            message = (
                "no reply from endpoint ("
                f"{error_detail.get('message', 'transport error') if error_detail else 'transport error'})"
            )
        else:
            message = "endpoint returned a result for an unknown method"
        error_case = {
            "method": ERROR_PROBE_METHOD,
            "code": None,
            "message": message,
            "received_error": False,
        }
    expected = EXPECTED_CHAIN_IDS.get(service_id)
    block_int = None
    if isinstance(block_result, str):
        try:
            block_int = int(block_result, 16)
        except ValueError:
            block_int = None
    result = {
        "status": "ok",
        "chain_id": chain_result,
        "chain_id_expected": expected,
        "chain_id_match": chain_result == expected
        if chain_result is not None
        else False,
        "block_height": block_result,
        "block_height_int": block_int,
        "chain_latency_ms": chain_latency,
        "block_latency_ms": block_latency,
        "latency_ms": chain_latency,
        "error_case": error_case,
    }
    if chain_error is not None:
        result["status"] = "error"
        result["error"] = chain_error.get("message", "RPC error")
    elif block_error is not None:
        result["status"] = "error"
        result["error"] = block_error.get("message", "RPC error")
    return result


def run_checks(services=None):
    """One round of checks across all services. Returns a report dict."""
    services = services or SERVICES
    results = {}
    for service_id, endpoint in services.items():
        results[service_id] = check_service(service_id, endpoint)
    return build_report(results)


def build_report(service_results):
    """Assemble one round of per-service results into a report."""
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "services": dict(service_results),
    }


def build_evidence(reports):
    """Aggregate repeated rounds into per-service evidence."""
    if not reports:
        return {"checks_per_service": 0, "services": {}}
    service_ids = sorted({sid for report in reports for sid in report["services"]})
    services = {}
    for service_id in service_ids:
        results = [
            report["services"][service_id]
            for report in reports
            if service_id in report["services"]
        ]
        successful = [item for item in results if item.get("status") == "ok"]
        chain_ids = sorted(
            {item["chain_id"] for item in successful if item.get("chain_id") is not None}
        )
        heights = [
            item["block_height_int"]
            for item in results
            if item.get("block_height_int") is not None
        ]
        latencies = [
            item["latency_ms"] for item in results if item.get("latency_ms") is not None
        ]
        error_cases = [item.get("error_case") or {} for item in results]
        services[service_id] = {
            "checks": len(results),
            "successful_checks": len(successful),
            "failed_checks": len(results) - len(successful),
            "chain_ids": chain_ids,
            "chain_id_stable": len(chain_ids) == 1,
            "chain_id_expected": EXPECTED_CHAIN_IDS.get(service_id),
            "chain_id_match_all": bool(successful)
            and all(item.get("chain_id_match") for item in successful),
            "block_first": heights[0] if heights else None,
            "block_last": heights[-1] if heights else None,
            "block_min": min(heights) if heights else None,
            "block_max": max(heights) if heights else None,
            "block_advanced": bool(heights) and heights[-1] > heights[0],
            "block_monotonic": bool(heights)
            and all(b >= a for a, b in zip(heights, heights[1:])),
            "min_latency_ms": min(latencies) if latencies else None,
            "mean_latency_ms": round(statistics.mean(latencies), 2)
            if latencies
            else None,
            "max_latency_ms": max(latencies) if latencies else None,
            "error_case_method": ERROR_PROBE_METHOD,
            "error_case_received": sum(
                1 for case in error_cases if case.get("received_error")
            ),
            "error_case_codes": sorted(
                {case["code"] for case in error_cases if case.get("code") is not None}
            ),
            "error_case_messages": sorted(
                {case["message"] for case in error_cases if case.get("message")}
            ),
            "error_case_ok": bool(error_cases)
            and all(case.get("received_error") for case in error_cases),
        }
    return {"checks_per_service": len(reports), "services": services}


def _fmt(value, suffix=""):
    """Format a value for the dashboard, showing n/a for missing data."""
    if value is None:
        return "n/a"
    return f"{value}{suffix}"


def render_html(report):
    """Render the report as a standalone dashboard page.

    Design follows the warm-paper, electric-blue design language: warm paper
    background, electric blue accents, white cards with blue corner
    brackets, and blue letter squares. Only the presentation changed;
    the data and the checks are exactly the same as before.
    """
    ok_count = sum(
        1 for item in report["services"].values() if item.get("status") == "ok"
    )
    total = len(report["services"])
    cards = []
    for service_id, result in report["services"].items():
        status = html.escape(str(result.get("status", "")))
        status_label = "OK" if result.get("status") == "ok" else "ERROR"
        status_class = "ok" if result.get("status") == "ok" else "fail"
        letter = html.escape(service_id[:1].upper())
        chain_id = result.get("chain_id")
        expected = result.get("chain_id_expected")
        match = result.get("chain_id_match")
        if chain_id is None:
            chain_line = "n/a"
        elif match:
            chain_line = f"<code>{html.escape(str(chain_id))}</code> matches expected <code>{html.escape(str(expected))}</code>"
        else:
            chain_line = f"<code>{html.escape(str(chain_id))}</code> does NOT match expected <code>{html.escape(str(expected))}</code>"
        block = result.get("block_height")
        block_int = result.get("block_height_int")
        if block is None:
            block_line = "n/a"
        elif block_int is None:
            block_line = f"<code>{html.escape(str(block))}</code>"
        else:
            block_line = f"<code>{html.escape(str(block))}</code> ({block_int:,})"
        error_case = result.get("error_case") or {}
        if error_case.get("received_error"):
            error_line = (
                f"<code>{html.escape(str(error_case.get('method', '')))}</code> "
                f"returned RPC error <code>{html.escape(str(error_case.get('code')))}</code>: "
                f"{html.escape(str(error_case.get('message', '')))}"
            )
        else:
            error_line = "no RPC error returned for the unknown method"
        error_text = result.get("error")
        error_row = (
            f"<p class=\"card-error\">Error: <code>{html.escape(str(error_text))}</code></p>"
            if error_text
            else ""
        )
        cards.append(
            f'<article class="card"><span class="corner tl"></span><span class="corner tr"></span><span class="corner bl"></span><span class="corner br"></span>'
            f'<div class="card-top"><span class="letter">{letter}</span><span class="pill {status_class}">{status_label}</span></div>'
            f"<h3>{html.escape(service_id)}</h3>"
            f"<div class=\"fact\"><div class=\"k\">Chain ID</div><div class=\"v\">{chain_line}</div></div>"
            f"<div class=\"fact\"><div class=\"k\">Latest block</div><div class=\"v mono\">{block_line}</div></div>"
            f"<div class=\"fact\"><div class=\"k\">Latency</div><div class=\"v\">chain <strong>{_fmt(result.get('chain_latency_ms'), ' ms')}</strong>, "
            f"block <strong>{_fmt(result.get('block_latency_ms'), ' ms')}</strong></div></div>"
            f"<div class=\"fact\"><div class=\"k\">Error case</div><div class=\"v\">{error_line}</div></div>"
            f"{error_row}</article>"
        )
    evidence_html = ""
    if report.get("evidence"):
        rows = []
        for service_id, details in report["evidence"]["services"].items():
            block_range = (
                f"{details['block_first']:,} to {details['block_last']:,}"
                if details["block_first"] is not None
                else "n/a"
            )
            advanced = (
                "yes" if details["block_advanced"] else "no (same block across checks)"
            )
            rows.append(
                "<tr>"
                f"<td><strong>{html.escape(service_id)}</strong></td>"
                f"<td>{details['successful_checks']}/{details['checks']}</td>"
                f"<td>{', '.join(html.escape(str(c)) for c in details['chain_ids']) or 'n/a'}</td>"
                f"<td>{'yes' if details['chain_id_stable'] else 'no'}</td>"
                f"<td>{'yes' if details['chain_id_match_all'] else 'no'}</td>"
                f"<td>{block_range}</td>"
                f"<td>{advanced}</td>"
                f"<td>{_fmt(details['min_latency_ms'])} / {_fmt(details['mean_latency_ms'])} / {_fmt(details['max_latency_ms'])} ms</td>"
                f"<td>{details['error_case_received']}/{details['checks']} returned error "
                f"{', '.join(html.escape(str(c)) for c in details['error_case_codes']) or 'n/a'}</td>"
                "</tr>"
            )
        evidence_html = (
            "<section class=\"panel\"><span class=\"corner tl\"></span><span class=\"corner tr\"></span><span class=\"corner bl\"></span><span class=\"corner br\"></span>"
            "<h2>Repeated-check evidence</h2>"
            "<p class=\"sub\">Five rounds per service. Chain ID must stay stable and match the "
            "documented ID, block height must not go backwards, and the unknown "
            "method must come back as an RPC error.</p>"
            "<div class=\"table-wrap\"><table><thead><tr><th>Service</th><th>Successful</th><th>Chain IDs</th>"
            "<th>Chain stable</th><th>Chain match</th><th>Block height</th>"
            "<th>Block advanced</th><th>Latency min / mean / max</th>"
            "<th>Error case</th></tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table></div></section>"
        )
    state_label = "ALL OK" if ok_count == total else "ATTENTION"
    state_class = "state" if ok_count == total else "state attention"
    endpoints_html = ", ".join(
        f"<code>{html.escape(SERVICES[service_id])}</code>"
        for service_id in report["services"]
        if service_id in SERVICES
    )
    return (
        """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pocket RPC Health Dashboard</title><style>
:root{--primary:#0018ff;--bg:#f6f4ef;--card:#fff;--ink:#0a0a0a;--ink-2:#555;--muted:#737373;--border:#e5e5e5;--blue:#0018ff;--blue-soft:#0018ff14;--green:#15803d;--green-bg:#f0fdf4;--red:#dc2626;--red-bg:#fef2f2;--sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;--mono:ui-monospace,"SF Mono",SFMono-Regular,Menlo,Consolas,monospace}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--ink);font-family:var(--sans);-webkit-font-smoothing:antialiased;margin:0;font-size:16px;line-height:1.55}
code,.mono{font-family:var(--mono)}
a{color:inherit}
.blue{color:var(--blue)}
.wrap{max-width:1120px;margin:0 auto;padding:0 24px}
.topbar{padding:20px 0 0}
.brand-row{display:flex;align-items:center;gap:10px}
.brand-mark{background:var(--primary);color:#fff;border-radius:8px;display:flex;justify-content:center;align-items:center;width:30px;height:30px;font-size:17px;font-weight:800}
.brand{font-size:18px;font-weight:700;letter-spacing:-.02em}
.hero{background-image:radial-gradient(#0018ff14 1px,#0000 1px);background-size:22px 22px;padding:64px 0 48px}
.hero h1{letter-spacing:-.03em;margin:0 0 16px;font-size:68px;font-weight:800;line-height:1.02;max-width:720px}
.hero .lede{color:var(--muted);margin:0;font-size:18px;max-width:620px}
main{padding-bottom:72px}
.verdict{background:var(--card);border:1px solid var(--border);padding:32px;position:relative;box-shadow:0 1px 2px #0a0a0a0d;margin-bottom:16px}
.corner{border:0 solid var(--primary);width:8px;height:8px;position:absolute}
.corner.tl{border-top-width:2px;border-left-width:2px;top:-1px;left:-1px}
.corner.tr{border-top-width:2px;border-right-width:2px;top:-1px;right:-1px}
.corner.bl{border-bottom-width:2px;border-left-width:2px;bottom:-1px;left:-1px}
.corner.br{border-bottom-width:2px;border-right-width:2px;bottom:-1px;right:-1px}
.verdict-top{display:flex;justify-content:space-between;align-items:flex-start;gap:16px}
.verdict h2{letter-spacing:-.02em;margin:0;font-size:clamp(26px,3.2vw,36px);font-weight:800}
.verdict p{color:var(--ink-2);max-width:680px;margin:10px 0 0;font-size:15px}
.verdict .state{letter-spacing:.08em;text-transform:uppercase;background:var(--blue);color:#fff;white-space:nowrap;border-radius:999px;flex:none;padding:5px 10px;font-size:11px;font-weight:800}
.verdict .state.attention{background:var(--red)}
.grid{display:grid;grid-template-columns:repeat(5,1fr);gap:16px;margin-bottom:16px}
.card{background:var(--card);border:1px solid var(--border);padding:20px;position:relative;box-shadow:0 1px 2px #0a0a0a0d}
.card-top{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}
.letter{background:var(--primary);color:#fff;display:flex;justify-content:center;align-items:center;width:36px;height:36px;font-size:17px;font-weight:800}
.pill{letter-spacing:.08em;text-transform:uppercase;border:1px solid var(--blue);color:var(--blue);white-space:nowrap;background:0 0;border-radius:999px;padding:3px 8px;font-size:10px;font-weight:700}
.pill.ok{border-color:var(--green);color:var(--green);background:var(--green-bg)}
.pill.fail{border-color:var(--red);color:var(--red);background:var(--red-bg)}
.card h3{margin:14px 0 12px;font-size:17px;font-weight:800}
.fact{margin-bottom:12px}
.fact:last-child{margin-bottom:0}
.fact .k{color:var(--muted);margin-bottom:2px;font-size:12px;font-weight:600}
.fact .v{font-size:14px;line-height:1.6;overflow-wrap:anywhere}
.fact .v code{color:var(--blue)}
.fact .v strong{font-weight:700}
.card-error{color:var(--red);margin:12px 0 0;font-size:13px}
.card-error code{color:var(--red)}
.panel{background:var(--card);border:1px solid var(--border);padding:22px 26px;position:relative;box-shadow:0 1px 2px #0a0a0a0d}
.panel h2{letter-spacing:-.02em;margin:0 0 8px;font-size:24px;font-weight:800}
.panel .sub{color:var(--muted);margin:0 0 12px;font-size:14px}
.table-wrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;min-width:760px}
th{color:var(--muted);text-transform:uppercase;letter-spacing:.04em;font-size:12px;font-weight:600;text-align:left;padding:10px 12px;border-bottom:1px solid var(--border)}
td{padding:12px;border-bottom:1px solid var(--border);font-size:14px;vertical-align:top;line-height:1.5}
tr:last-child td{border-bottom:none}
td code{color:var(--blue)}
.site-footer{background:var(--blue);color:#fff;padding:48px 24px 32px}
.site-footer p{margin:0 0 8px;font-size:14px;line-height:1.6}
.site-footer p:last-child{margin-bottom:0}
.site-footer a{color:#fff;text-decoration:underline}
.site-footer code{color:#fff}
@media (width<=860px){.hero h1{font-size:40px}.grid{grid-template-columns:repeat(2,1fr)}.verdict{padding:24px}}
@media (width<=520px){.grid{grid-template-columns:1fr}.panel{padding:18px}.verdict-top{flex-direction:column}}
</style></head><body>
<header class="topbar"><div class="wrap"><div class="brand-row"><span class="brand-mark">P</span><span class="brand">Pocket RPC Health Dashboard</span></div></div></header>
<section class="hero"><div class="wrap"><h1>Pocket RPC health, <span class="blue">checked live.</span></h1>
<p class="lede">Five chains, four checks each, straight through Pocket's public RPC endpoints. No wallet, no API key, no POKT, no paid relay.</p></div></section>
<main class="wrap">
<section class="verdict"><span class="corner tl"></span><span class="corner tr"></span><span class="corner bl"></span><span class="corner br"></span>
<div class="verdict-top"><h2>SUMMARY of TOTAL services ok</h2><span class="STATECLASS">STATELABEL</span></div>
<p>Live checks through Pocket public RPC service endpoints. Checked at: CHECKED_AT</p>
<p>Each round checks chain identity, latest block height, response latency, and the error case, five rounds per service.</p>
</section>
<section class="grid">CARDS</section>
EVIDENCE
</main>
<footer class="site-footer"><div class="wrap">
<p>Endpoints: Pocket public RPC, free and keyless. No wallet, API key, POKT, or paid relay. Checked: ENDPOINTS</p>
<p>Docs: <a href="https://docs.pocket.network/developers/supported-chains/">docs.pocket.network/developers/supported-chains</a> &middot; Repo: <a href="https://github.com/Jayanng/pocket-rpc-health-dashboard">github.com/Jayanng/pocket-rpc-health-dashboard</a> &middot; Live demo: <a href="https://pocket-rpc-health-dashboard.vercel.app/">pocket-rpc-health-dashboard.vercel.app</a></p>
</div></footer>
</body></html>""".replace(
            "CHECKED_AT", html.escape(report["checked_at"])
        )
        .replace("SUMMARY", f"{ok_count}")
        .replace("TOTAL", str(total))
        .replace("STATELABEL", state_label)
        .replace("STATECLASS", state_class)
        .replace("ENDPOINTS", endpoints_html)
        .replace("CARDS", "".join(cards))
        .replace("EVIDENCE", evidence_html)
    )
