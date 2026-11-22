# Pocket RPC Health Dashboard: Submission

## Quest

Integrate Pocket Into a Working Application (650 XP).

Proof required: demo, docs/repo, service identifier, test outcome.
Approval criterion: useful and reproducible, more than a copied single request.

## Ambassador preapproval: GRANTED 2026-11-22

Johnson submitted the proposal to the Pocket ambassador team in Discord,
tagging two ambassador team members, and they approved it.

Proposal as submitted:

> Target: a multi-chain RPC health dashboard monitoring Ethereum, Base,
> Polygon through Pocket public endpoints.
>
> Scope: structured JSON-RPC requests, chain identity, latest block height,
> response latency, repeated-request consistency, error responses, and a
> small dashboard showing the results.
>
> Process: confirm target and scope, implement, test each chain with repeated
> requests, document the integration and failure cases, and record a short
> demo.
>
> Proof: working demo, docs and repo, service identifiers, test outcomes.

Honest timeline note: the first version of the implementation was built
before the approval was received. The approval covers the proposal as
submitted, and this package was then audited and upgraded to implement the
full promised scope (block height, repeated-request consistency, and error
responses, plus two extra chains) before submission. Nothing about the
timeline is hidden.

## What the app does

A small Python app that checks Pocket public RPC services and reports
chain identity, latest block height, response latency, repeated-request
consistency, and error handling. No wallet, no API key, no POKT, no staking,
no paid relay. Every network is free and keyless at
`https://{slug}.api.pocket.network`.

Services checked (5): eth, base, poly, arb-one, op.

Four checks per service per round, 5 rounds by default:

1. Chain identity: `eth_chainId` against the documented chain ID.
2. Latest block height: `eth_blockNumber`, hex and decimal. The block height
   must advance across repeated checks and never go backwards.
3. Latency: chain and block calls, min / mean / max across the rounds.
4. Error case: unknown method `eth_nope`, which must come back as an RPC
   error (code -32601), proving the endpoint returns real JSON-RPC errors
   instead of silently failing.

## Live demo

- Dashboard: https://pocket-rpc-health.vercel.app/
- Repo: https://github.com/Jayanng/pocket-rpc-health-dashboard
- Demo video: `demo/pocket-demo-1min.mp4` (62 seconds, H.264 1280x800), script at `demo/script.md`.
- Run it yourself: `python3 src/main.py --runs 5 --pause 2 --json --html report.html > evidence.json`

## Live test outcome

Fresh live run, checked at 2026-11-22T15:02:46 UTC, 5 rounds per service.
All 5 services ok, 5/5 successful checks each.

| Service | Chain ID | Block height (first to last) | Latency min / mean / max (ms) | Error case |
| --- | --- | --- | --- | --- |
| eth | 0x1, matches | 26,471,420 to 26,471,425 | 667.7 / 701.2 / 742.4 | 5/5 returned error -32601 |
| base | 0x2105, matches | 60,846,673 to 60,846,918 | 660.47 / 677.12 / 702.22 | 5/5 returned error -32601 |
| poly | 0x89, matches | 97,769,496 to 97,769,528 | 679.79 / 701.04 / 728.36 | 5/5 returned error -32601 |
| arb-one | 0xa4b1, matches | 526,546,623 to 526,546,794 | 656.69 / 669.97 / 691.55 | 5/5 returned error -32601 |
| op | 0xa, matches | 159,880,469 to 159,880,494 | 651.27 / 678.91 / 700.8 | 5/5 returned error -32601 |

Chain IDs were stable across all 5 rounds for every service and matched the
documented IDs. Block heights advanced and never went backwards. Every
error case returned RPC error -32601 (eth and op: "Method not found"; poly
and arb-one: "the method eth_nope does not exist/is not available").

Unit tests: 12 passed, 0 failed (`python3 -m unittest discover -s tests -v`).

Latency note: this run measured about 650-740 ms per call, higher than the
30-80 ms in the first evidence file. That is an honest measurement from the
machine that ran it at that time; each call opens a fresh HTTPS connection,
so connection setup is included in the number. Latency is reported as
observed, not tuned to look good.

## Proof files in this repo

- `README.md`: what it does, how to run it, how to reproduce the evidence.
- `SUBMISSION.md`: this file.
- `src/dashboard.py`: services, checks, evidence aggregation, dashboard rendering.
- `src/main.py`: CLI entry point.
- `tests/test_dashboard.py`: 12 offline unit tests (mocked transport, no network).
- `evidence.json`: machine-readable output of the fresh live run above.
- `report.html`: the dashboard generated from that same run.
- `screenshots/report.png`: screenshot of the dashboard from that same run, plus `screenshots/report-mobile.png`: the same dashboard at phone width.
- `vercel.json`: rewrites `/` to `/report.html` for the git-linked Vercel project.
- `demo/pocket-demo-1min.mp4`: 1-minute demo video explaining the dashboard, plus `demo/script.md`: the video script with per-scene timings.
- `pocket-rpc-health-dashboard-proof.zip`: this whole package.

## Service identifiers

| Service | Pocket endpoint | Expected chain ID |
| --- | --- | --- |
| eth | https://eth.api.pocket.network | 0x1 |
| base | https://base.api.pocket.network | 0x2105 |
| poly | https://poly.api.pocket.network | 0x89 |
| arb-one | https://arb-one.api.pocket.network | 0xa4b1 |
| op | https://op.api.pocket.network | 0xa |

Endpoints per https://docs.pocket.network/developers/supported-chains/.
Public RPC is free and keyless, rate limit 2,000 requests per IP per minute.

## Notes

- No API keys or private data anywhere in this package. The evidence
  contains only public chain data (chain IDs, block heights, latencies,
  RPC error codes and messages).
- The dashboard is a static snapshot of one run, not a live service. To get
  fresh numbers, run the command above; the report and evidence are
  regenerated from that single run so they always match.
