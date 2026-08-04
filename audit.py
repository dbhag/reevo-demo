#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os

from dryrun.config import load_config
from dryrun.gates import check_gates
from dryrun.parse import parse_history, parse_opportunities, parse_users
from dryrun.report import render_report
from dryrun.rules import ambiguous_date_findings, check_rules
from dryrun.stages import resolve_stages


def main() -> None:
    ap = argparse.ArgumentParser(description="Reevo Import Dry Run")
    ap.add_argument(
        "--export-dir",
        required=True,
        help="Directory containing opportunities.csv, opportunity_history.csv, users.csv",
    )
    ap.add_argument("--config", required=True, help="Target pipeline config YAML")
    ap.add_argument("--out", required=True, help="Output report path (markdown)")
    ap.add_argument(
        "--manifest",
        default=None,
        help="Ground-truth manifest CSV (synthetic runs only) — enables the "
        "threshold-sensitivity table in section 5. Omit for a real export.",
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

    report = render_report(opps, coercions, findings, resolutions, config, args.export_dir, args.manifest)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"{len(opps)} records parsed, {len(findings)} findings, {len(coercions)} coercions logged.")
    print(f"Report written to {args.out}")


if __name__ == "__main__":
    main()
