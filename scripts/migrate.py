#!/usr/bin/env python3
"""
migrate.py — Execute the migration plan.

By default runs in DRY-RUN mode: downloads all assets from source,
validates them, and reports what would be published — without writing
anything to the destination site.

Pass --live to execute a real migration.

Usage:
    # Dry run (safe, default)
    python scripts/migrate.py

    # Live migration
    python scripts/migrate.py --live

    # Single project, live
    python scripts/migrate.py --live --projects "Finance Dashboards"

    # Rollback the last live migration
    python scripts/migrate.py --rollback
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from client import DualClient, load_config
from inventory import build_inventory
from assessment import run_full_assessment
from planner import build_plan
from migrator import Migrator
from validator import PostMigrationValidator
from report import generate_migration_report


def main():
    parser = argparse.ArgumentParser(
        description="Tableau Server → Cloud migration executor"
    )
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument(
        "--live", action="store_true",
        help="Execute a real migration (default: dry-run only)"
    )
    parser.add_argument(
        "--projects", nargs="+", default=None,
        help="Limit to specific source projects"
    )
    parser.add_argument(
        "--validate", action="store_true",
        help="Run post-migration validation after a live migration"
    )
    parser.add_argument(
        "--rollback", action="store_true",
        help="Delete all items published in the last migration run"
    )
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    config = load_config(args.config)
    output_dir = args.output or config.get("reporting", {}).get("output_dir", "./output")
    dry_run = not args.live

    if dry_run:
        print("Tableau Cloud Migration — DRY RUN")
        print("(Pass --live to execute a real migration)")
    else:
        print("Tableau Cloud Migration — LIVE MIGRATION")
        print("WARNING: This will publish content to the destination site.")
        confirm = input("Type 'yes' to proceed: ").strip().lower()
        if confirm != "yes":
            print("Aborted.")
            return 0

    print(f"\nSource : {config['source']['server_url']}")
    print(f"Dest   : {config['destination']['server_url']}")
    print()

    with DualClient.from_config(config) as dual:

        # Rollback mode: just undo the last run
        if args.rollback:
            migrator = Migrator(dual, config, output_dir)
            migrator.rollback()
            return 0

        # Step 1: Inventory
        print("Building inventory…")
        project_filter = args.projects or config.get("migration", {}).get("project_filter") or None
        assets = build_inventory(dual.source, project_filter=project_filter, verbose=True)

        # Step 2: Assess
        print("\nRunning compatibility checks…")
        issues, summary = run_full_assessment(assets, config, verbose=True)

        if summary.blocker_count > 0 and args.live:
            print(f"\n✗  {summary.blocker_count} unresolved blocker(s) found.")
            print("   Resolve blockers before running a live migration.")
            print("   Run scripts/assess.py for the full report.")
            return 1

        # Step 3: Plan
        print("\nBuilding migration plan…")
        plan = build_plan(assets, config, verbose=True)

        # Step 4: Execute (dry-run or live)
        migrator = Migrator(dual, config, output_dir)
        result = migrator.run(plan, dry_run=dry_run)

        # Step 5: Post-migration validation (live only, if requested)
        if args.validate and not dry_run:
            print("\nRunning post-migration validation…")
            validator = PostMigrationValidator(dual)
            validator.run(result.plan, verbose=True)

        # Step 6: Reports
        report_path = generate_migration_report(result, output_dir=output_dir)
        print(f"\nMigration report: {report_path}")

        return 0 if result.success_rate == 100 else 1


if __name__ == "__main__":
    sys.exit(main())
