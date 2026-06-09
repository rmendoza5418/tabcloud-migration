"""
permission_mapping.py — Permission and security compatibility checks.

Covers: AD group mapping, row-level security patterns, and site-role
differences between Tableau Server and Tableau Cloud.
"""

from __future__ import annotations

from models import (
    AssetRecord, CloudCompatibilityIssue,
    IssueSeverity, IssueCategory,
)


def check_row_level_security(
    assets: list[AssetRecord],
) -> list[CloudCompatibilityIssue]:
    """
    Flag datasources that likely use user-based row-level security.
    RLS patterns using USERNAME() / USERDOMAIN() must be verified post-migration
    because Cloud uses email addresses as user identities, not domain\\user format.
    """
    issues = []
    rls_indicators = ["rls", "row level", "row_level", "user_filter", "userdomain"]

    for asset in assets:
        name_lower = asset.name.lower()
        desc_lower = asset.description.lower()

        if any(ind in name_lower or ind in desc_lower for ind in rls_indicators):
            issues.append(CloudCompatibilityIssue(
                severity    = IssueSeverity.WARNING,
                category    = IssueCategory.PERMISSIONS,
                asset_luid  = asset.luid,
                asset_name  = asset.name,
                asset_type  = asset.content_type,
                title       = "Possible row-level security — user identity format change",
                detail      = (
                    "This datasource name or description suggests it uses row-level security. "
                    "Tableau Server USERNAME() returns 'DOMAIN\\\\username' format. "
                    "Tableau Cloud USERNAME() returns the user's email address. "
                    "Any RLS calculations using USERDOMAIN() or domain-prefixed usernames "
                    "will break after migration."
                ),
                remediation = (
                    "Audit USERNAME()/USERDOMAIN() calculated fields in this datasource. "
                    "Update user filter logic to use email-format identities. "
                    "Test with at least 3 users spanning different access levels before "
                    "promoting to production on Cloud."
                ),
            ))
    return issues


def check_missing_group_mapping(
    assets: list[AssetRecord],
    config: dict,
) -> list[CloudCompatibilityIssue]:
    """
    Warn when content is owned by users with no obvious Cloud counterpart.
    (Full permission-to-group mapping requires the group list from both servers.)
    """
    issues = []
    migration_cfg = config.get("migration", {})
    migrate_permissions = migration_cfg.get("migrate_permissions", True)

    if not migrate_permissions:
        return issues

    # Without the actual group list from both servers we can only warn broadly.
    # A production implementation would cross-reference source groups vs dest groups.
    for asset in assets:
        if asset.owner_name and "@" not in asset.owner_name:
            # Server users often stored as domain\\user; Cloud expects email
            issues.append(CloudCompatibilityIssue(
                severity    = IssueSeverity.INFO,
                category    = IssueCategory.PERMISSIONS,
                asset_luid  = asset.luid,
                asset_name  = asset.name,
                asset_type  = asset.content_type,
                title       = "Owner identity may not match Cloud user",
                detail      = (
                    f"Owner '{asset.owner_name}' appears to be in domain\\\\user format. "
                    "Tableau Cloud identifies users by email address. "
                    "Ownership assignment requires a username→email mapping."
                ),
                remediation = (
                    "Provide a user mapping file (domain_user → email) via --user-map flag "
                    "so ownership can be re-assigned after publish. "
                    "Content without a matched owner will be assigned to the migration service account."
                ),
            ))
    return issues
