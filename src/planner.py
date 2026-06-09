"""
planner.py — Migration plan generator.

Takes the assessed inventory and config, resolves project mappings,
determines publish order, and returns a MigrationPlan ready for
either dry-run validation or live migration execution.

Key ordering rules:
  1. Published datasources must be migrated before workbooks that use them.
  2. Parent projects must be created before child projects.
  3. Assets with BLOCKERs are placed in the plan as "skip" entries.
"""

from __future__ import annotations

from models import (
    AssetRecord, ContentType, IssueSeverity,
    MigrationPlan, MigrationPlanEntry, ProjectMapping,
)


def build_plan(
    assets: list[AssetRecord],
    config: dict,
    verbose: bool = True,
) -> MigrationPlan:
    """
    Generate an ordered migration plan from assessed assets.

    Args:
        assets:   Assessed AssetRecord list (compatibility_issues populated).
        config:   Full config dict (migration.project_mapping, skip_stale_days, etc.).
        verbose:  Print plan summary to stdout.

    Returns:
        MigrationPlan with entries ordered for safe execution.
    """
    migration_cfg = config.get("migration", {})
    raw_mapping   = migration_cfg.get("project_mapping", {})
    skip_stale    = migration_cfg.get("skip_stale_days", 0)

    project_mappings = _build_project_mappings(assets, raw_mapping)
    mapping_lookup   = {m.source_name: m.destination_name for m in project_mappings}

    entries: list[MigrationPlanEntry] = []
    seq = 1

    # ---------------------------------------------------------------- #
    # Datasources first, then workbooks
    # ---------------------------------------------------------------- #
    ordered_assets = (
        [a for a in assets if a.content_type == ContentType.DATASOURCE]
        + [a for a in assets if a.content_type == ContentType.WORKBOOK]
    )

    for asset in ordered_assets:
        dest_project = mapping_lookup.get(asset.project_name, asset.project_name)

        # Determine action
        action, skip_reason = _classify_action(asset, skip_stale)

        entries.append(MigrationPlanEntry(
            sequence    = seq,
            asset       = asset,
            dest_project= dest_project,
            action      = action,
            skip_reason = skip_reason,
        ))
        seq += 1

    plan = MigrationPlan(
        entries          = entries,
        project_mappings = project_mappings,
        source_site      = config.get("source", {}).get("site_name", ""),
        destination_site = config.get("destination", {}).get("site_name", ""),
    )

    if verbose:
        _print_summary(plan)

    return plan


def _classify_action(
    asset: AssetRecord,
    skip_stale_days: int,
) -> tuple[str, str | None]:
    """Return (action, skip_reason) for one asset."""

    # Unresolved BLOCKERs → skip
    if asset.has_blockers:
        titles = "; ".join(
            i.title for i in asset.compatibility_issues
            if i.severity == IssueSeverity.BLOCKER
        )
        return "skip", f"Unresolved blockers: {titles}"

    # Stale content (if threshold set)
    if skip_stale_days > 0:
        age = asset.age_days
        if age is not None and age > skip_stale_days:
            return "skip", f"Not accessed in {age} days (threshold: {skip_stale_days})"

    return "publish", None


def _build_project_mappings(
    assets: list[AssetRecord],
    raw_mapping: dict,
) -> list[ProjectMapping]:
    """
    Build ProjectMapping list from config overrides + discovered projects.
    Any project not in the config mapping keeps its original name.
    """
    seen_projects: dict[str, str] = {}  # source_name → source_luid
    for asset in assets:
        seen_projects[asset.project_name] = asset.project_luid

    mappings = []
    for source_name, source_luid in sorted(seen_projects.items()):
        dest_name = raw_mapping.get(source_name, source_name)
        mappings.append(ProjectMapping(
            source_name      = source_name,
            source_luid      = source_luid,
            destination_name = dest_name,
        ))

    return mappings


def _print_summary(plan: MigrationPlan) -> None:
    total     = len(plan.entries)
    publish   = len(plan.publishable)
    skipped   = len(plan.skipped)
    ds_count  = sum(1 for e in plan.publishable
                    if e.asset.content_type == ContentType.DATASOURCE)
    wb_count  = sum(1 for e in plan.publishable
                    if e.asset.content_type == ContentType.WORKBOOK)

    print(f"\nMigration Plan Summary")
    print(f"  Total assets : {total}")
    print(f"  To publish   : {publish}  ({ds_count} datasources, {wb_count} workbooks)")
    print(f"  Skipped      : {skipped}")
    print(f"  Projects     : {len(plan.project_mappings)}")

    if skipped:
        print(f"\n  Skipped assets:")
        for e in plan.skipped:
            print(f"    [{e.asset.content_type.value:11}] {e.asset.name!r}  — {e.skip_reason}")
