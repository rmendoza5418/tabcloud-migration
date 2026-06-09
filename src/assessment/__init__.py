"""
assessment — Pre-migration cloud compatibility checks.

Each module exports one or more check functions that accept a list of
AssetRecord objects and return a list of CloudCompatibilityIssue objects.

run_full_assessment() orchestrates all checks and populates each
AssetRecord's .compatibility_issues list in place.
"""

from __future__ import annotations

from models import AssetRecord, AssessmentSummary, CloudCompatibilityIssue, IssueSeverity
from datetime import datetime

from .cloud_compatibility import (
    check_bridge_dependencies,
    check_embedded_credentials,
    check_analytics_extensions,
    check_large_workbooks,
    check_large_extracts,
    check_stale_content,
    check_personal_space,
)
from .connection_analysis import (
    check_on_prem_connections,
    check_live_to_cloud_connections,
    check_cross_database_joins,
)
from .permission_mapping import (
    check_missing_group_mapping,
    check_row_level_security,
)


def run_full_assessment(
    assets: list[AssetRecord],
    config: dict,
    verbose: bool = True,
) -> tuple[list[CloudCompatibilityIssue], AssessmentSummary]:
    """
    Run all checks against the inventory.

    Issues are appended to each AssetRecord.compatibility_issues in place.
    Returns (all_issues, summary).
    """
    thresholds = config.get("thresholds", {})
    on_prem_hosts = thresholds.get("on_prem_hosts", [])
    large_wb_mb   = thresholds.get("large_workbook_mb", 100)
    large_extract = thresholds.get("large_extract_rows", 5_000_000)
    stale_days    = thresholds.get("stale_workbook_days", 180)

    all_checks = [
        # Connectivity — most likely to be BLOCKERs
        lambda a: check_bridge_dependencies(a, on_prem_hosts),
        check_on_prem_connections,
        check_live_to_cloud_connections,
        check_cross_database_joins,
        # Authentication
        check_embedded_credentials,
        # Extensions
        check_analytics_extensions,
        # Permissions / governance
        check_row_level_security,
        lambda a: check_missing_group_mapping(a, config),
        # Performance / scale
        lambda a: check_large_workbooks(a, large_wb_mb),
        lambda a: check_large_extracts(a, large_extract),
        lambda a: check_stale_content(a, stale_days),
        check_personal_space,
    ]

    all_issues: list[CloudCompatibilityIssue] = []

    for check_fn in all_checks:
        issues = check_fn(assets)
        all_issues.extend(issues)

    # Attach issues back to their assets
    issue_map: dict[str, list[CloudCompatibilityIssue]] = {}
    for issue in all_issues:
        issue_map.setdefault(issue.asset_luid, []).append(issue)

    for asset in assets:
        asset.compatibility_issues = issue_map.get(asset.luid, [])

    if verbose:
        blockers = sum(1 for i in all_issues if i.severity == IssueSeverity.BLOCKER)
        warnings = sum(1 for i in all_issues if i.severity == IssueSeverity.WARNING)
        print(f"  Assessment complete: {len(all_issues)} issues "
              f"({blockers} blockers, {warnings} warnings)")

    summary = _build_summary(assets, all_issues)
    return all_issues, summary


def _build_summary(
    assets: list[AssetRecord],
    issues: list[CloudCompatibilityIssue],
) -> AssessmentSummary:
    from models import ContentType
    return AssessmentSummary(
        total_assets  = len(assets),
        workbooks     = sum(1 for a in assets if a.content_type == ContentType.WORKBOOK),
        datasources   = sum(1 for a in assets if a.content_type == ContentType.DATASOURCE),
        blocker_count = sum(1 for i in issues if i.severity == IssueSeverity.BLOCKER),
        warning_count = sum(1 for i in issues if i.severity == IssueSeverity.WARNING),
        info_count    = sum(1 for i in issues if i.severity == IssueSeverity.INFO),
        assets_ready  = sum(1 for a in assets if not a.has_blockers),
        assets_blocked= sum(1 for a in assets if a.has_blockers),
        assessed_at   = datetime.now(),
    )
