#!/usr/bin/env python3
import argparse
import json
import sys
import time
from pathlib import Path

from dashboard import build_evidence, render_html, run_checks

parser = argparse.ArgumentParser(description="Check Pocket public RPC services")
parser.add_argument("--json", action="store_true", help="print JSON report")
parser.add_argument("--html", metavar="PATH", help="write an HTML dashboard")
parser.add_argument("--runs", type=int, default=1, help="number of live checks per service")
parser.add_argument("--pause", type=float, default=0.2, help="seconds between repeated checks")
args = parser.parse_args()

if args.runs < 1:
    parser.error("--runs must be at least 1")

reports = []
for index in range(args.runs):
    reports.append(run_checks())
    if index + 1 < args.runs:
        time.sleep(args.pause)
evidence = build_evidence(reports)
report = reports[-1]
report["evidence"] = evidence

if args.html:
    Path(args.html).write_text(render_html(report), encoding="utf-8")
if args.json or not args.html:
    print(json.dumps(report, indent=2))
