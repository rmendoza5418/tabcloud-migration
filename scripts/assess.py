#!/usr/bin/env python3
"""
assess.py — Pre-migration cloud compatibility assessment.

Connects to the SOURCE Tableau Server, enumerates all content,
runs compatibility checks, and produces an HTML report + CSV export.

Usage:
    python scripts/assess.py
    python scripts/assess.py --config config/config.yaml
    python scripts/assess.py --projects "Finance Dashboards" "Sales Analytics"
    python scripts/assess.py --output ./reports
"""

import argparse
import sys
from pathlib import Path

# Allow imports from src/
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from client import TableauClient, load_config
from inventory import build_inventory, summarize_inventory
from assessment import run_full_assessment
from report import generate_assessment_report, generate_csv_export


def main():
    parser = argparse.ArgumentParser(
        description="Tableau Cloud migration readiness assessment"
    )
    parser.add_argument(
        "--config", default="config/config.yaml",
        help="Path to config.yaml (default: config/config.yaml)"
    )
    parser.add_argument(
        "--projects", nargs="+", default=None,
        help="Only assess content in these projects (by name)"
    )
    parser.add_argument(
        "--output", default=None,
        help="Output directory for reports (default: from config)"
    )
    parser.add_argument(
        "--no-report", action="store_true",
        help="Skip HTML/CSV report generation (stdout only)"
    )
    args = parser.parse_args()

    # Load config
    config = load_config(args.config)
    output_dir = args.output or config.get("reporting", {}).get("output_dir", "./output")

    print("Tableau Cloud Migration — Readiness Assessment")
    print(f"Source: {config['source']['server_url']}")
    print(f"Site:   {config['source']['site_name'] or '(Default)'}")
    print()

    # Connect and inventory
    with TableauClient.from_config(config["source"]) as client:
        print("Building asset inventory…")
        project_filter = args.projects or config.get("migration", {}).get("project_filter") or None
        assets = build_inventory(client, project_filter=project_filter, verbose=True)

        inv_summary = summarize_inventory(assets)
        print(f"\nInventory Summary")
        print(f"  Workbooks      : {inv_summary['workbooks']}")
        print(f"  Datasources    : {inv_summary['datasources']}")
        print(f"  Projects       : {inv_summary['projects']}")
        print(f"  With extracts  : {inv_summary['with_extracts']}")
        if inv_summary["connection_types"]:
            print(f"  Connection types:")
            for ct, count in list(inv_summary["connection_types"].items())[:8]:
                print(f"    {ct:<30} {count}")

        print("\nRunning compatibility checks…")
        issues, summary = run_full_assessment(assets, config, verbose=True)

    print(f"\nReadiness Summary")
    print(f"  Ready to migrate  : {summary.assets_ready} / {summary.total_assets} "
          f"({summary.readiness_pct}%)")
    print(f"  Blocked           : {summary.assets_blocked}")
    print(f"  Blockers          : {summary.blocker_count}")
    print(f"  Warnings          : {summary.warning_count}")

    if not args.no_report:
        print("\nGenerating reports…")
        html_path = generate_assessment_report(summary, issues, output_dir)
        csv_path  = generate_csv_export(issues, output_dir)
        print(f"  HTML report : {html_path}")
        print(f"  CSV export  : {csv_path}")

    if summary.blocker_count > 0:
        print(f"\n⚠  {summary.blocker_count} blocker(s) must be resolved before migration.")
        print("   See the HTML report for details and remediation guidance.")
        return 1

    print(f"\n✓  All {summary.total_assets} assets pass compatibility checks.")
    print("   Run scripts/plan.py to generate the migration plan.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
