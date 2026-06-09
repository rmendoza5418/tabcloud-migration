"""
models.py — Shared data models for the migration pipeline.

All dataclasses are deliberately plain (no ORM, no external deps) so they
serialize cleanly to JSON/CSV for reporting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


# ------------------------------------------------------------------ #
# Enumerations
# ------------------------------------------------------------------ #

class ContentType(str, Enum):
    WORKBOOK = "workbook"
    DATASOURCE = "datasource"


class IssueSeverity(str, Enum):
    """Migration readiness issue severity."""
    BLOCKER = "BLOCKER"   # Must be resolved before migration can proceed
    WARNING = "WARNING"   # Can proceed but requires manual follow-up post-migration
    INFO    = "INFO"      # Informational; no action required


class IssueCategory(str, Enum):
    CONNECTIVITY    = "connectivity"      # Bridge, on-prem, live connections
    AUTHENTICATION  = "authentication"    # Embedded creds, OAuth, SAML
    EXTENSIONS      = "extensions"        # TabPy, RServer, JavaScript API
    PERMISSIONS     = "permissions"       # Groups, RLS, site-level roles
    PERFORMANCE     = "performance"       # Size, extract scale
    GOVERNANCE      = "governance"        # Certification, metadata, ownership
    COMPATIBILITY   = "compatibility"     # Features not available on Cloud


class MigrationStatus(str, Enum):
    PENDING   = "pending"
    SKIPPED   = "skipped"    # Blocked by unresolved BLOCKER issues
    DRY_RUN   = "dry_run"    # Validated but not published
    SUCCESS   = "success"
    FAILED    = "failed"
    ROLLED_BACK = "rolled_back"


# ------------------------------------------------------------------ #
# Asset inventory
# ------------------------------------------------------------------ #

@dataclass
class ConnectionInfo:
    """One data connection within a workbook or datasource."""
    server_address:  str
    server_port:     Optional[str]
    connection_type: str           # e.g. "sqlserver", "oracle", "hyper"
    database_name:   Optional[str]
    username:        Optional[str]
    is_embedded:     bool = False  # credentials embedded in the content


@dataclass
class AssetRecord:
    """
    Unified representation of a workbook or published datasource on the
    source Tableau Server, enriched with metadata needed for migration
    planning.
    """
    content_type:    ContentType
    luid:            str
    name:            str
    project_name:    str
    project_luid:    str
    owner_name:      str
    owner_email:     Optional[str]

    created_at:      Optional[datetime]
    updated_at:      Optional[datetime]
    last_accessed:   Optional[datetime]

    view_count:      int = 0           # total lifetime views (workbooks)
    size_bytes:      Optional[int] = None
    has_extract:     bool = False
    extract_rows:    Optional[int] = None

    connections:     list[ConnectionInfo] = field(default_factory=list)
    tags:            list[str]          = field(default_factory=list)
    is_certified:    bool = False
    description:     str = ""

    # Populated during assessment
    compatibility_issues: list[CloudCompatibilityIssue] = field(default_factory=list)

    @property
    def age_days(self) -> Optional[int]:
        if self.last_accessed:
            return (datetime.now() - self.last_accessed).days
        return None

    @property
    def size_mb(self) -> Optional[float]:
        if self.size_bytes:
            return round(self.size_bytes / 1_048_576, 1)
        return None

    @property
    def has_blockers(self) -> bool:
        return any(i.severity == IssueSeverity.BLOCKER
                   for i in self.compatibility_issues)

    @property
    def blocker_count(self) -> int:
        return sum(1 for i in self.compatibility_issues
                   if i.severity == IssueSeverity.BLOCKER)

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.compatibility_issues
                   if i.severity == IssueSeverity.WARNING)


# ------------------------------------------------------------------ #
# Assessment
# ------------------------------------------------------------------ #

@dataclass
class CloudCompatibilityIssue:
    """One issue identified during the pre-migration assessment."""
    severity:    IssueSeverity
    category:    IssueCategory
    asset_luid:  str
    asset_name:  str
    asset_type:  ContentType
    title:       str
    detail:      str
    remediation: str

    @property
    def priority_score(self) -> int:
        return {
            IssueSeverity.BLOCKER: 100,
            IssueSeverity.WARNING: 10,
            IssueSeverity.INFO:    1,
        }[self.severity]


@dataclass
class AssessmentSummary:
    total_assets:        int
    workbooks:           int
    datasources:         int
    blocker_count:       int
    warning_count:       int
    info_count:          int
    assets_ready:        int    # no blockers
    assets_blocked:      int    # ≥1 blocker
    assessed_at:         datetime = field(default_factory=datetime.now)

    @property
    def readiness_pct(self) -> float:
        if self.total_assets == 0:
            return 0.0
        return round(self.assets_ready / self.total_assets * 100, 1)


# ------------------------------------------------------------------ #
# Migration plan
# ------------------------------------------------------------------ #

@dataclass
class ProjectMapping:
    """Maps a source project to a destination project (by name)."""
    source_name:      str
    source_luid:      str
    destination_name: str
    destination_luid: Optional[str] = None   # filled after dest project is created
    parent_name:      Optional[str] = None


@dataclass
class MigrationPlanEntry:
    """One line item in the ordered migration plan."""
    sequence:       int              # execution order (datasources before workbooks)
    asset:          AssetRecord
    dest_project:   str              # destination project name
    action:         str              # "publish" | "skip" | "review"
    skip_reason:    Optional[str] = None

    # Populated after execution
    status:           MigrationStatus = MigrationStatus.PENDING
    destination_luid: Optional[str]   = None
    error_message:    Optional[str]   = None
    duration_secs:    Optional[float] = None


@dataclass
class MigrationPlan:
    entries:           list[MigrationPlanEntry]
    project_mappings:  list[ProjectMapping]
    created_at:        datetime = field(default_factory=datetime.now)
    source_site:       str = ""
    destination_site:  str = ""

    @property
    def publishable(self) -> list[MigrationPlanEntry]:
        return [e for e in self.entries if e.action == "publish"]

    @property
    def skipped(self) -> list[MigrationPlanEntry]:
        return [e for e in self.entries if e.action == "skip"]

    @property
    def datasources_first(self) -> list[MigrationPlanEntry]:
        """Datasources must be published before dependent workbooks."""
        return sorted(
            [e for e in self.publishable],
            key=lambda e: (0 if e.asset.content_type == ContentType.DATASOURCE else 1,
                           e.sequence),
        )


# ------------------------------------------------------------------ #
# Migration results
# ------------------------------------------------------------------ #

@dataclass
class MigrationResult:
    plan:              MigrationPlan
    dry_run:           bool
    started_at:        datetime
    completed_at:      Optional[datetime] = None

    @property
    def duration_secs(self) -> Optional[float]:
        if self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None

    @property
    def succeeded(self) -> list[MigrationPlanEntry]:
        target = MigrationStatus.DRY_RUN if self.dry_run else MigrationStatus.SUCCESS
        return [e for e in self.plan.entries if e.status == target]

    @property
    def failed(self) -> list[MigrationPlanEntry]:
        return [e for e in self.plan.entries if e.status == MigrationStatus.FAILED]

    @property
    def success_rate(self) -> float:
        publishable = self.plan.publishable
        if not publishable:
            return 0.0
        return round(len(self.succeeded) / len(publishable) * 100, 1)


# ------------------------------------------------------------------ #
# Post-migration validation
# ------------------------------------------------------------------ #

@dataclass
class ValidationCheck:
    check_name:  str
    passed:      bool
    detail:      str
    asset_luid:  Optional[str] = None
    asset_name:  Optional[str] = None


@dataclass
class ValidationReport:
    checks:      list[ValidationCheck]
    source_counts: dict   # {"workbooks": N, "datasources": N}
    dest_counts:   dict
    run_at:      datetime = field(default_factory=datetime.now)

    @property
    def passed(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def failed(self) -> int:
        return sum(1 for c in self.checks if not c.passed)

    @property
    def all_passed(self) -> bool:
        return self.failed == 0
