"""
cloud_compatibility.py — Core feature compatibility checks.

Each function receives the full asset list and returns a list of
CloudCompatibilityIssue objects. Functions are pure (no side effects)
so they're easy to test and extend.
"""

from __future__ import annotations

from models import (
    AssetRecord, CloudCompatibilityIssue, ContentType,
    IssueSeverity, IssueCategory,
)


# ------------------------------------------------------------------ #
# Bridge / on-prem connectivity
# ------------------------------------------------------------------ #

def check_bridge_dependencies(
    assets: list[AssetRecord],
    on_prem_hosts: list[str],
) -> list[CloudCompatibilityIssue]:
    """
    Flag assets with live connections to known on-premises hosts.
    These require Tableau Bridge to function on Cloud.
    """
    issues = []
    host_set = {h.lower() for h in on_prem_hosts}

    for asset in assets:
        for conn in asset.connections:
            host = (conn.server_address or "").lower()
            if any(host.endswith(prem) for prem in host_set) and conn.connection_type != "hyper":
                issues.append(CloudCompatibilityIssue(
                    severity    = IssueSeverity.BLOCKER,
                    category    = IssueCategory.CONNECTIVITY,
                    asset_luid  = asset.luid,
                    asset_name  = asset.name,
                    asset_type  = asset.content_type,
                    title       = "On-premises data source requires Tableau Bridge",
                    detail      = (
                        f"Connection to '{conn.server_address}' ({conn.connection_type}) "
                        "cannot be reached directly from Tableau Cloud. "
                        "Tableau Bridge must be installed and configured on a machine "
                        "with network access to this host."
                    ),
                    remediation = (
                        "Install Tableau Bridge on an on-prem server, configure it to "
                        "connect to this Tableau Cloud site, then set the datasource "
                        "to use Bridge after migration."
                    ),
                ))
                break   # one issue per asset for this check

    return issues


def check_embedded_credentials(
    assets: list[AssetRecord],
) -> list[CloudCompatibilityIssue]:
    """
    Flag assets with embedded database credentials.
    These are not exported and must be re-entered post-migration.
    """
    issues = []
    for asset in assets:
        embedded = [c for c in asset.connections if c.is_embedded]
        if embedded:
            conn_list = ", ".join(
                f"{c.connection_type}@{c.server_address}" for c in embedded
            )
            issues.append(CloudCompatibilityIssue(
                severity    = IssueSeverity.WARNING,
                category    = IssueCategory.AUTHENTICATION,
                asset_luid  = asset.luid,
                asset_name  = asset.name,
                asset_type  = asset.content_type,
                title       = "Embedded credentials not exported",
                detail      = (
                    f"This {'workbook' if asset.content_type == ContentType.WORKBOOK else 'datasource'} "
                    f"has embedded credentials for: {conn_list}. "
                    "Credentials are not included in .twbx/.tdsx downloads and must "
                    "be re-entered in Tableau Cloud after publishing."
                ),
                remediation = (
                    "After migration, open Edit Connection for each affected datasource "
                    "in Tableau Cloud and re-enter credentials. "
                    "Consider switching to OAuth or stored credentials where available."
                ),
            ))
    return issues


# ------------------------------------------------------------------ #
# Analytics extensions
# ------------------------------------------------------------------ #

EXTENSION_INDICATORS = {
    "rserve":   ("R analytics extension (RServe)", "external"),
    "tabpy":    ("Python analytics extension (TabPy)", "external"),
    "einstein": ("Einstein Discovery integration", "salesforce"),
    "analytics.extension": ("Analytics extension", "external"),
}


def check_analytics_extensions(
    assets: list[AssetRecord],
) -> list[CloudCompatibilityIssue]:
    """
    Flag workbooks that use TabPy or RServe.
    Cloud requires these to point to a Cloud-accessible endpoint.
    """
    issues = []
    for asset in assets:
        if asset.content_type != ContentType.WORKBOOK:
            continue
        for conn in asset.connections:
            ct = (conn.connection_type or "").lower()
            for key, (label, _) in EXTENSION_INDICATORS.items():
                if key in ct:
                    issues.append(CloudCompatibilityIssue(
                        severity    = IssueSeverity.BLOCKER,
                        category    = IssueCategory.EXTENSIONS,
                        asset_luid  = asset.luid,
                        asset_name  = asset.name,
                        asset_type  = asset.content_type,
                        title       = f"{label} requires Cloud-accessible endpoint",
                        detail      = (
                            f"This workbook uses '{conn.server_address}' as an analytics "
                            f"extension endpoint ({label}). "
                            "Tableau Cloud can only reach publicly accessible HTTPS endpoints "
                            "with a valid SSL certificate."
                        ),
                        remediation = (
                            "Deploy the analytics extension server in a Cloud-accessible "
                            "environment (VPC with public endpoint, or a hosted TabPy/RServe). "
                            "Update the extension connection in Tableau Cloud Site Settings "
                            "before opening the migrated workbook."
                        ),
                    ))
                    break
    return issues


# ------------------------------------------------------------------ #
# Performance / scale
# ------------------------------------------------------------------ #

def check_large_workbooks(
    assets: list[AssetRecord],
    threshold_mb: float = 100,
) -> list[CloudCompatibilityIssue]:
    """
    Flag workbooks exceeding the size threshold.
    Tableau Cloud has a 2 GB publish limit; large files slow down publish.
    """
    issues = []
    for asset in assets:
        if asset.content_type != ContentType.WORKBOOK:
            continue
        if asset.size_mb and asset.size_mb > threshold_mb:
            issues.append(CloudCompatibilityIssue(
                severity    = IssueSeverity.WARNING,
                category    = IssueCategory.PERFORMANCE,
                asset_luid  = asset.luid,
                asset_name  = asset.name,
                asset_type  = asset.content_type,
                title       = f"Large workbook ({asset.size_mb} MB)",
                detail      = (
                    f"This workbook is {asset.size_mb} MB, exceeding the {threshold_mb} MB "
                    "threshold. While Tableau Cloud supports up to 2 GB, large workbooks "
                    "result in slow load times and may hit timeout limits during publish."
                ),
                remediation = (
                    "Reduce workbook size by removing unused sheets, extracting data sources "
                    "to standalone published datasources, or filtering data earlier in the pipeline."
                ),
            ))
    return issues


def check_large_extracts(
    assets: list[AssetRecord],
    threshold_rows: int = 5_000_000,
) -> list[CloudCompatibilityIssue]:
    """
    Flag assets with very large extracts.
    Cloud has tighter extract refresh time limits and storage quotas.
    """
    issues = []
    for asset in assets:
        if asset.extract_rows and asset.extract_rows > threshold_rows:
            issues.append(CloudCompatibilityIssue(
                severity    = IssueSeverity.WARNING,
                category    = IssueCategory.PERFORMANCE,
                asset_luid  = asset.luid,
                asset_name  = asset.name,
                asset_type  = asset.content_type,
                title       = f"Large extract ({asset.extract_rows:,} rows)",
                detail      = (
                    f"This datasource contains {asset.extract_rows:,} rows. "
                    "Tableau Cloud enforces extract refresh time limits (2 hours by default). "
                    "Very large extracts may fail to refresh within this window."
                ),
                remediation = (
                    "Consider partitioning the extract, using incremental refresh, "
                    "or moving to a live Hyper-optimized connection. "
                    "Work with Tableau Cloud admin to review extract capacity limits for your site."
                ),
            ))
    return issues


def check_stale_content(
    assets: list[AssetRecord],
    stale_days: int = 180,
) -> list[CloudCompatibilityIssue]:
    """
    Flag content not accessed recently — low-value migration candidates.
    """
    if stale_days <= 0:
        return []

    issues = []
    for asset in assets:
        age = asset.age_days
        if age is not None and age > stale_days:
            issues.append(CloudCompatibilityIssue(
                severity    = IssueSeverity.INFO,
                category    = IssueCategory.GOVERNANCE,
                asset_luid  = asset.luid,
                asset_name  = asset.name,
                asset_type  = asset.content_type,
                title       = f"Stale content — last accessed {age} days ago",
                detail      = (
                    f"This {'workbook' if asset.content_type == ContentType.WORKBOOK else 'datasource'} "
                    f"was last accessed {age} days ago (threshold: {stale_days} days). "
                    "Migrating stale content increases Cloud storage consumption without "
                    "business value."
                ),
                remediation = (
                    "Confirm with the content owner whether migration is needed. "
                    "Consider archiving or deleting rather than migrating."
                ),
            ))
    return issues


def check_personal_space(
    assets: list[AssetRecord],
) -> list[CloudCompatibilityIssue]:
    """
    Flag content in Personal Space projects.
    Personal Space content is not migrated by default migration tools.
    """
    issues = []
    personal_indicators = {"personal space", "my content", "personal"}

    for asset in assets:
        if asset.project_name.lower() in personal_indicators:
            issues.append(CloudCompatibilityIssue(
                severity    = IssueSeverity.INFO,
                category    = IssueCategory.GOVERNANCE,
                asset_luid  = asset.luid,
                asset_name  = asset.name,
                asset_type  = asset.content_type,
                title       = "Content in Personal Space — requires manual migration",
                detail      = (
                    f"'{asset.name}' is in the '{asset.project_name}' project. "
                    "Content in Personal Space must be migrated by the individual owner; "
                    "admin bulk-migration tools do not transfer personal space content."
                ),
                remediation = (
                    "Notify content owners to download and republish their Personal Space "
                    "content to the destination Cloud site, or move content to a shared "
                    "project before running the bulk migration."
                ),
            ))
    return issues
