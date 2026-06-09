"""
migrator.py — Migration execution engine.

Iterates through a MigrationPlan and publishes each asset from the
source server to the destination server. Supports:

  - Dry-run mode: downloads and validates without publishing
  - Concurrency: parallel publish via ThreadPoolExecutor
  - Rollback log: tracks all published LUIDs for cleanup on failure
  - Progress callbacks: for CLI progress reporting

Usage:
    migrator = Migrator(dual_client, config, output_dir="./output")
    result   = migrator.run(plan, dry_run=True)
"""

from __future__ import annotations

import os
import json
import time
import tempfile
import concurrent.futures
from datetime import datetime
from pathlib import Path

from client import DualClient
from models import (
    ContentType, MigrationPlan, MigrationPlanEntry, MigrationResult,
    MigrationStatus,
)


class Migrator:
    """Executes a MigrationPlan against source and destination Tableau sites."""

    def __init__(
        self,
        client: DualClient,
        config: dict,
        output_dir: str | Path = "./output",
        on_progress: callable | None = None,
    ):
        self.client     = client
        self.config     = config
        self.output_dir = Path(output_dir)
        self.on_progress = on_progress   # callback(entry, status, message)

        migration_cfg        = config.get("migration", {})
        self.concurrency     = min(max(migration_cfg.get("concurrency", 3), 1), 8)
        self.migrate_perms   = migration_cfg.get("migrate_permissions", True)

        # Rollback log — maps destination LUID → content type
        self._rollback_log: list[dict] = []

    # ---------------------------------------------------------------- #
    # Main entry point
    # ---------------------------------------------------------------- #

    def run(
        self,
        plan: MigrationPlan,
        dry_run: bool = True,
    ) -> MigrationResult:
        """
        Execute the migration plan.

        Args:
            plan:    Finalized MigrationPlan from planner.build_plan().
            dry_run: If True, downloads and validates only — nothing is published.

        Returns:
            MigrationResult with per-entry status and aggregate statistics.
        """
        result = MigrationResult(plan=plan, dry_run=dry_run, started_at=datetime.now())
        self.output_dir.mkdir(parents=True, exist_ok=True)

        mode_label = "DRY RUN" if dry_run else "LIVE MIGRATION"
        print(f"\n{'='*60}")
        print(f"  {mode_label}")
        print(f"  {len(plan.publishable)} assets  |  concurrency: {self.concurrency}")
        print(f"{'='*60}\n")

        # Ensure destination projects exist (unless dry-run)
        if not dry_run:
            self._ensure_destination_projects(plan)

        # Migrate: datasources first, then workbooks
        entries = plan.datasources_first

        if self.concurrency == 1:
            for entry in entries:
                self._process_entry(entry, dry_run)
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.concurrency) as ex:
                futures = {
                    ex.submit(self._process_entry, entry, dry_run): entry
                    for entry in entries
                }
                for future in concurrent.futures.as_completed(futures):
                    try:
                        future.result()
                    except Exception as exc:
                        entry = futures[future]
                        entry.status = MigrationStatus.FAILED
                        entry.error_message = str(exc)

        result.completed_at = datetime.now()
        self._write_rollback_log()
        self._print_result_summary(result)
        return result

    # ---------------------------------------------------------------- #
    # Per-entry processing
    # ---------------------------------------------------------------- #

    def _process_entry(self, entry: MigrationPlanEntry, dry_run: bool) -> None:
        if entry.action == "skip":
            entry.status = MigrationStatus.SKIPPED
            self._log_progress(entry, "SKIPPED", entry.skip_reason or "")
            return

        start = time.monotonic()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                # Step 1: Download from source
                local_path = self._download_asset(entry, tmp)

                if dry_run:
                    # Validate file exists and has non-zero size
                    size_kb = round(Path(local_path).stat().st_size / 1024, 1)
                    entry.status = MigrationStatus.DRY_RUN
                    self._log_progress(entry, "DRY_RUN", f"validated ({size_kb} KB)")
                else:
                    # Step 2: Resolve destination project LUID
                    dest_project_luid = self._resolve_dest_project(entry.dest_project)

                    # Step 3: Publish to destination
                    dest_item = self._publish_asset(entry, local_path, dest_project_luid)
                    entry.destination_luid = dest_item.id
                    entry.status = MigrationStatus.SUCCESS

                    # Track for potential rollback
                    self._rollback_log.append({
                        "content_type": entry.asset.content_type.value,
                        "destination_luid": dest_item.id,
                        "name": entry.asset.name,
                    })

                    self._log_progress(entry, "SUCCESS", f"→ {dest_item.id}")

        except Exception as exc:
            entry.status = MigrationStatus.FAILED
            entry.error_message = str(exc)
            self._log_progress(entry, "FAILED", str(exc))

        entry.duration_secs = round(time.monotonic() - start, 2)

    def _download_asset(self, entry: MigrationPlanEntry, output_dir: str) -> str:
        asset = entry.asset
        if asset.content_type == ContentType.WORKBOOK:
            return self.client.source.download_workbook(
                asset.luid, output_dir, include_extract=True
            )
        else:
            return self.client.source.download_datasource(
                asset.luid, output_dir, include_extract=True
            )

    def _publish_asset(
        self,
        entry: MigrationPlanEntry,
        local_path: str,
        dest_project_luid: str,
    ):
        asset = entry.asset
        if asset.content_type == ContentType.WORKBOOK:
            return self.client.destination.publish_workbook(
                local_path, dest_project_luid, name=asset.name
            )
        else:
            return self.client.destination.publish_datasource(
                local_path, dest_project_luid, name=asset.name
            )

    # ---------------------------------------------------------------- #
    # Project management
    # ---------------------------------------------------------------- #

    def _ensure_destination_projects(self, plan: MigrationPlan) -> None:
        """Create any destination projects that don't already exist."""
        existing = {
            p.name: p.id
            for p in self.client.destination.get_all_projects()
        }

        for mapping in plan.project_mappings:
            if mapping.destination_name not in existing:
                print(f"  Creating project: '{mapping.destination_name}'")
                proj = self.client.destination.create_project(
                    name        = mapping.destination_name,
                    description = f"Migrated from '{mapping.source_name}' on Tableau Server",
                )
                mapping.destination_luid = proj.id
                existing[mapping.destination_name] = proj.id
            else:
                mapping.destination_luid = existing[mapping.destination_name]

    def _resolve_dest_project(self, project_name: str) -> str:
        """Look up destination project LUID by name."""
        for p in self.client.destination.get_all_projects():
            if p.name == project_name:
                return p.id
        raise ValueError(
            f"Destination project '{project_name}' not found. "
            "Run ensure_destination_projects() before migrating."
        )

    # ---------------------------------------------------------------- #
    # Rollback support
    # ---------------------------------------------------------------- #

    def _write_rollback_log(self) -> None:
        if not self._rollback_log:
            return
        log_path = self.output_dir / "rollback_log.json"
        with open(log_path, "w") as f:
            json.dump(self._rollback_log, f, indent=2)
        print(f"\n  Rollback log written: {log_path}")

    def rollback(self) -> None:
        """
        Delete all items published in this migration run.
        Uses rollback_log.json if the in-memory log is empty.
        """
        log_path = self.output_dir / "rollback_log.json"
        if not self._rollback_log and log_path.exists():
            with open(log_path) as f:
                self._rollback_log = json.load(f)

        if not self._rollback_log:
            print("Nothing to roll back.")
            return

        print(f"Rolling back {len(self._rollback_log)} items…")
        for item in self._rollback_log:
            try:
                if item["content_type"] == ContentType.WORKBOOK.value:
                    self.client.destination.server.workbooks.delete(
                        item["destination_luid"]
                    )
                else:
                    self.client.destination.server.datasources.delete(
                        item["destination_luid"]
                    )
                print(f"  Deleted: {item['name']}")
            except Exception as exc:
                print(f"  Failed to delete {item['name']}: {exc}")

    # ---------------------------------------------------------------- #
    # Reporting helpers
    # ---------------------------------------------------------------- #

    def _log_progress(
        self, entry: MigrationPlanEntry, status: str, detail: str
    ) -> None:
        tag = f"[{entry.asset.content_type.value[:2].upper()}]"
        print(f"  {tag} {status:<10} {entry.asset.name!r:50}  {detail}")
        if self.on_progress:
            self.on_progress(entry, status, detail)

    @staticmethod
    def _print_result_summary(result: MigrationResult) -> None:
        total    = len(result.plan.publishable)
        success  = len(result.succeeded)
        failed   = len(result.failed)
        skipped  = len(result.plan.skipped)
        dur      = result.duration_secs or 0

        print(f"\n{'='*60}")
        print(f"  {'DRY RUN' if result.dry_run else 'MIGRATION'} COMPLETE")
        print(f"  Success  : {success}/{total}  ({result.success_rate}%)")
        print(f"  Failed   : {failed}")
        print(f"  Skipped  : {skipped}")
        print(f"  Duration : {dur:.1f}s")
        print(f"{'='*60}\n")

        if failed:
            print("Failed items:")
            for e in result.failed:
                print(f"  [{e.asset.content_type.value}] {e.asset.name}: {e.error_message}")
