"""
connection_analysis.py — Data connection compatibility checks.

Covers: on-premises live connections, Cloud-native connector support,
cross-database joins that behave differently on Cloud.
"""

from __future__ import annotations

from models import (
    AssetRecord, CloudCompatibilityIssue,
    IssueSeverity, IssueCategory,
)


# Connectors that are fully supported on Tableau Cloud without Bridge
CLOUD_NATIVE_CONNECTORS = {
    "hyper", "snowflake", "bigquery", "redshift", "databricks",
    "azure_sql", "azure_synapse", "salesforce", "google_sheets",
    "google_analytics", "s3", "athena", "postgresql", "mysql",
    "amazon_aurora", "teradata",   # direct Cloud versions
}

# Connectors that require Bridge or are unsupported on Cloud
REQUIRES_BRIDGE_CONNECTORS = {
    "sqlserver",        # on-prem SQL Server (not Azure)
    "oracle",
    "db2",
    "sybase",
    "actian_matrix",
    "aster",
    "greenplum",
    "netezza",
    "pivotal_greenplum",
    "sap_hana",
    "sap_sybase_ase",
    "teradata_olap",
}

# Connectors fully unsupported on Tableau Cloud (no Bridge path)
UNSUPPORTED_ON_CLOUD = {
    "r",
    "python",
    "actian_vector",
    "kognitio",
    "esri",
}


def check_on_prem_connections(
    assets: list[AssetRecord],
) -> list[CloudCompatibilityIssue]:
    """
    Flag connections using connectors that require Tableau Bridge on Cloud.
    """
    issues = []
    for asset in assets:
        bridge_conns = [
            c for c in asset.connections
            if (c.connection_type or "").lower() in REQUIRES_BRIDGE_CONNECTORS
        ]
        if bridge_conns:
            conn_detail = "; ".join(
                f"{c.connection_type} → {c.server_address}" for c in bridge_conns
            )
            issues.append(CloudCompatibilityIssue(
                severity    = IssueSeverity.BLOCKER,
                category    = IssueCategory.CONNECTIVITY,
                asset_luid  = asset.luid,
                asset_name  = asset.name,
                asset_type  = asset.content_type,
                title       = "Connector requires Tableau Bridge on Cloud",
                detail      = (
                    f"The following connections use connectors that require Tableau Bridge: "
                    f"{conn_detail}. Without Bridge, these connections will fail on Cloud."
                ),
                remediation = (
                    "Option 1: Deploy Tableau Bridge on a server with access to these databases. "
                    "Option 2: Migrate the database to a cloud-native service (e.g., SQL Server → Azure SQL). "
                    "Option 3: Pre-build extracts (.hyper) so live connectivity is not required."
                ),
            ))
    return issues


def check_live_to_cloud_connections(
    assets: list[AssetRecord],
) -> list[CloudCompatibilityIssue]:
    """
    Flag assets with live (non-extract) connections to confirm Cloud reachability.
    """
    issues = []
    for asset in assets:
        live_conns = [
            c for c in asset.connections
            if c.connection_type not in ("hyper",)
        ]
        if not asset.has_extract and live_conns:
            cloud_supported = all(
                (c.connection_type or "").lower() in CLOUD_NATIVE_CONNECTORS
                for c in live_conns
            )
            if not cloud_supported:
                conn_list = ", ".join(
                    f"{c.connection_type}" for c in live_conns
                    if (c.connection_type or "").lower() not in CLOUD_NATIVE_CONNECTORS
                )
                issues.append(CloudCompatibilityIssue(
                    severity    = IssueSeverity.WARNING,
                    category    = IssueCategory.CONNECTIVITY,
                    asset_luid  = asset.luid,
                    asset_name  = asset.name,
                    asset_type  = asset.content_type,
                    title       = "Live connection to unverified Cloud connector",
                    detail      = (
                        f"This asset uses live connections via: {conn_list}. "
                        "These connector types are not in the verified Cloud-native list. "
                        "Verify that these connections are reachable from Tableau Cloud "
                        "before migration."
                    ),
                    remediation = (
                        "Test the connection from a Tableau Desktop pointed at Tableau Cloud "
                        "before migrating. If the connection fails, consider switching to "
                        "an extract-based approach or using Tableau Bridge."
                    ),
                ))
    return issues


def check_cross_database_joins(
    assets: list[AssetRecord],
) -> list[CloudCompatibilityIssue]:
    """
    Flag workbooks with cross-database joins between on-prem and Cloud sources.
    These work differently on Cloud (no local federation support).
    """
    issues = []
    for asset in assets:
        if len(asset.connections) < 2:
            continue

        conn_types = {(c.connection_type or "").lower() for c in asset.connections}
        has_onprem = bool(conn_types & REQUIRES_BRIDGE_CONNECTORS)
        has_cloud  = bool(conn_types & CLOUD_NATIVE_CONNECTORS)

        if has_onprem and has_cloud:
            issues.append(CloudCompatibilityIssue(
                severity    = IssueSeverity.WARNING,
                category    = IssueCategory.CONNECTIVITY,
                asset_luid  = asset.luid,
                asset_name  = asset.name,
                asset_type  = asset.content_type,
                title       = "Cross-database join: on-prem + Cloud sources",
                detail      = (
                    "This asset blends data from both on-premises and cloud-native sources. "
                    "Tableau Cloud does not support federated queries spanning local and "
                    "remote sources in the same way Tableau Server does."
                ),
                remediation = (
                    "Consolidate the data in a cloud data warehouse (e.g., Snowflake, "
                    "BigQuery) before publishing, or split into separate datasources "
                    "and use Tableau relationships instead of joins."
                ),
            ))
    return issues
