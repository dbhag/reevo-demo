#!/usr/bin/env python3
"""Renders dashboard.html: a read-only view over report.py's own output.

No new analysis logic — this script calls the exact same dryrun functions
audit.py calls (parse, rules, stages, gates), then hands the results to
report_data.build_dashboard_data() to shape them for display. All numbers
in the page are computed by the existing pipeline; this file only formats
and embeds them.
"""
from __future__ import annotations

import argparse
import json
import os

from dryrun.config import load_config
from dryrun.gates import check_gates
from dryrun.parse import parse_history, parse_opportunities, parse_users
from dryrun.report_data import build_dashboard_data
from dryrun.rules import ambiguous_date_findings, check_rules
from dryrun.stages import resolve_stages

TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "dashboard_template.html")


def main() -> None:
    ap = argparse.ArgumentParser(description="Render the Import Dry Run dashboard")
    ap.add_argument("--export-dir", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", default="dashboard.html")
    ap.add_argument(
        "--manifest",
        default=None,
        help="Ground-truth manifest CSV (synthetic runs only) — enables the "
        "F3/deterministic calibration stats in 'Where this is wrong'. Omit for a real export.",
    )
    args = ap.parse_args()

    config = load_config(args.config)
    opps, coercions = parse_opportunities(os.path.join(args.export_dir, "opportunities.csv"))
    history = parse_history(os.path.join(args.export_dir, "opportunity_history.csv"))
    users = parse_users(os.path.join(args.export_dir, "users.csv"))
    resolutions = resolve_stages([o.stage_raw for o in opps], config)

    findings = check_rules(opps, history, users, config)
    findings += ambiguous_date_findings(coercions)
    findings += check_gates(opps, resolutions, config)

    data = build_dashboard_data(
        opps, findings, resolutions, config, args.export_dir, manifest_path=args.manifest
    )

    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        template = f.read()

    # Defensive: a business string containing "</script" would otherwise
    # prematurely close the embedding tag and break the page.
    json_blob = json.dumps(data).replace("</script", "<\\/script")
    html = template.replace("__DATA_JSON__", json_blob)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Dashboard written to {args.out} ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
