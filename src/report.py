"""
report.py — HTML and CSV report generation.

generate_assessment_report() — pre-migration readiness report
generate_migration_report()   — post-migration results report
generate_csv_export()         — flat CSV of all issues for stakeholders
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

from models import (
    AssessmentSummary, CloudCompatibilityIssue, ContentType,
    IssueSeverity, MigrationResult, ValidationReport,
)


# ------------------------------------------------------------------ #
# Assessment HTML report
# ------------------------------------------------------------------ #

def generate_assessment_report(
    summary: AssessmentSummary,
    issues: list[CloudCompatibilityIssue],
    output_dir: str | Path = "./output",
) -> Path:
    """Generate a self-contained HTML assessment report."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"assessment_{datetime.now():%Y%m%d_%H%M%S}.html"

    severity_color = {
        IssueSeverity.BLOCKER: "#dc2626",
        IssueSeverity.WARNING: "#d97706",
        IssueSeverity.INFO:    "#2563eb",
    }

    # Group issues by severity
    blockers = [i for i in issues if i.severity == IssueSeverity.BLOCKER]
    warnings = [i for i in issues if i.severity == IssueSeverity.WARNING]
    infos    = [i for i in issues if i.severity == IssueSeverity.INFO]

    def issue_rows(issue_list: list[CloudCompatibilityIssue]) -> str:
        if not issue_list:
            return "<tr><td colspan='5' style='color:#6b7280;font-style:italic'>No issues</td></tr>"
        rows = []
        for iss in sorted(issue_list, key=lambda x: x.asset_name):
            rows.append(
                f"<tr>"
                f"<td>{iss.asset_type.value}</td>"
                f"<td>{_esc(iss.asset_name)}</td>"
                f"<td>{iss.category.value}</td>"
                f"<td>{_esc(iss.title)}</td>"
                f"<td>{_esc(iss.remediation)}</td>"
                f"</tr>"
            )
        return "\n".join(rows)

    readiness_color = (
        "#16a34a" if summary.readiness_pct >= 90 else
        "#d97706" if summary.readiness_pct >= 60 else
        "#dc2626"
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Tableau Cloud Migration Assessment</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         margin: 0; padding: 24px; background: #f9fafb; color: #111; }}
  h1 {{ font-size: 1.5rem; margin-bottom: 4px; }}
  .subtitle {{ color: #6b7280; margin-bottom: 24px; font-size: 0.9rem; }}
  .kpi-row {{ display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }}
  .kpi {{ background: white; border-radius: 8px; padding: 16px 24px;
          min-width: 120px; box-shadow: 0 1px 3px rgba(0,0,0,.1); }}
  .kpi .value {{ font-size: 2rem; font-weight: 700; line-height: 1.1; }}
  .kpi .label {{ font-size: 0.8rem; color: #6b7280; margin-top: 4px; }}
  .section {{ background: white; border-radius: 8px; padding: 20px;
              margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,.1); }}
  .section h2 {{ margin: 0 0 12px; font-size: 1rem; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.875rem; }}
  th {{ text-align: left; padding: 8px 12px; background: #f3f4f6;
        border-bottom: 1px solid #e5e7eb; font-weight: 600; }}
  td {{ padding: 8px 12px; border-bottom: 1px solid #f3f4f6; vertical-align: top; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 9999px;
            font-size: 0.75rem; font-weight: 600; color: white; }}
</style>
</head>
<body>
<h1>Tableau Cloud Migration — Readiness Assessment</h1>
<div class="subtitle">Generated {summary.assessed_at:%Y-%m-%d %H:%M}</div>

<div class="kpi-row">
  <div class="kpi">
    <div class="value">{summary.total_assets:,}</div>
    <div class="label">Total Assets</div>
  </div>
  <div class="kpi">
    <div class="value">{summary.workbooks:,}</div>
    <div class="label">Workbooks</div>
  </div>
  <div class="kpi">
    <div class="value">{summary.datasources:,}</div>
    <div class="label">Datasources</div>
  </div>
  <div class="kpi">
    <div class="value" style="color:{readiness_color}">{summary.readiness_pct}%</div>
    <div class="label">Migration Ready</div>
  </div>
  <div class="kpi">
    <div class="value" style="color:#dc2626">{summary.blocker_count}</div>
    <div class="label">Blockers</div>
  </div>
  <div class="kpi">
    <div class="value" style="color:#d97706">{summary.warning_count}</div>
    <div class="label">Warnings</div>
  </div>
  <div class="kpi">
    <div class="value">{summary.assets_blocked}</div>
    <div class="label">Assets Blocked</div>
  </div>
</div>

<div class="section">
  <h2>🚫 Blockers <span class="badge" style="background:#dc2626">{len(blockers)}</span></h2>
  <table>
    <tr><th>Type</th><th>Asset</th><th>Category</th><th>Issue</th><th>Remediation</th></tr>
    {issue_rows(blockers)}
  </table>
</div>

<div class="section">
  <h2>⚠️ Warnings <span class="badge" style="background:#d97706">{len(warnings)}</span></h2>
  <table>
    <tr><th>Type</th><th>Asset</th><th>Category</th><th>Issue</th><th>Remediation</th></tr>
    {issue_rows(warnings)}
  </table>
</div>

<div class="section">
  <h2>ℹ️ Informational <span class="badge" style="background:#2563eb">{len(infos)}</span></h2>
  <table>
    <tr><th>Type</th><th>Asset</th><th>Category</th><th>Issue</th><th>Remediation</th></tr>
    {issue_rows(infos)}
  </table>
</div>
</body>
</html>"""

    path.write_text(html, encoding="utf-8")
    return path


# ------------------------------------------------------------------ #
# Migration results HTML report
# ------------------------------------------------------------------ #

def generate_migration_report(
    result: MigrationResult,
    validation: ValidationReport | None = None,
    output_dir: str | Path = "./output",
) -> Path:
    """Generate a self-contained HTML migration results report."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"migration_{datetime.now():%Y%m%d_%H%M%S}.html"

    def entry_rows() -> str:
        rows = []
        for e in result.plan.entries:
            status_color = {
                "success":    "#16a34a",
                "dry_run":    "#2563eb",
                "failed":     "#dc2626",
                "skipped":    "#6b7280",
                "pending":    "#9ca3af",
                "rolled_back":"#7c3aed",
            }.get(e.status.value, "#111")

            rows.append(
                f"<tr>"
                f"<td>{e.asset.content_type.value}</td>"
                f"<td>{_esc(e.asset.name)}</td>"
                f"<td>{_esc(e.asset.project_name)}</td>"
                f"<td>→ {_esc(e.dest_project)}</td>"
                f"<td style='color:{status_color};font-weight:600'>{e.status.value.upper()}</td>"
                f"<td>{_esc(e.error_message or '')}</td>"
                f"</tr>"
            )
        return "\n".join(rows)

    mode = "DRY RUN" if result.dry_run else "LIVE MIGRATION"
    dur  = f"{result.duration_secs:.1f}s" if result.duration_secs else "—"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Tableau Cloud Migration Results</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         margin: 0; padding: 24px; background: #f9fafb; color: #111; }}
  h1 {{ font-size: 1.5rem; margin-bottom: 4px; }}
  .subtitle {{ color: #6b7280; margin-bottom: 24px; font-size: 0.9rem; }}
  .kpi-row {{ display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }}
  .kpi {{ background: white; border-radius: 8px; padding: 16px 24px;
          min-width: 100px; box-shadow: 0 1px 3px rgba(0,0,0,.1); }}
  .kpi .value {{ font-size: 2rem; font-weight: 700; line-height: 1.1; }}
  .kpi .label {{ font-size: 0.8rem; color: #6b7280; margin-top: 4px; }}
  .section {{ background: white; border-radius: 8px; padding: 20px;
              margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,.1); }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.875rem; }}
  th {{ text-align: left; padding: 8px 12px; background: #f3f4f6;
        border-bottom: 1px solid #e5e7eb; font-weight: 600; }}
  td {{ padding: 8px 12px; border-bottom: 1px solid #f3f4f6; vertical-align: top; }}
</style>
</head>
<body>
<h1>Tableau Cloud Migration — {mode} Results</h1>
<div class="subtitle">Completed {result.completed_at:%Y-%m-%d %H:%M} · Duration: {dur}</div>

<div class="kpi-row">
  <div class="kpi">
    <div class="value" style="color:#16a34a">{len(result.succeeded)}</div>
    <div class="label">{'Validated' if result.dry_run else 'Succeeded'}</div>
  </div>
  <div class="kpi">
    <div class="value" style="color:#dc2626">{len(result.failed)}</div>
    <div class="label">Failed</div>
  </div>
  <div class="kpi">
    <div class="value" style="color:#6b7280">{len(result.plan.skipped)}</div>
    <div class="label">Skipped</div>
  </div>
  <div class="kpi">
    <div class="value">{result.success_rate}%</div>
    <div class="label">Success Rate</div>
  </div>
</div>

<div class="section">
  <h2>Migration Results</h2>
  <table>
    <tr><th>Type</th><th>Asset</th><th>Source Project</th><th>Dest Project</th>
        <th>Status</th><th>Error</th></tr>
    {entry_rows()}
  </table>
</div>
</body>
</html>"""

    path.write_text(html, encoding="utf-8")
    return path


# ------------------------------------------------------------------ #
# CSV export
# ------------------------------------------------------------------ #

def generate_csv_export(
    issues: list[CloudCompatibilityIssue],
    output_dir: str | Path = "./output",
) -> Path:
    """Export all assessment issues as a flat CSV for stakeholder review."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"assessment_issues_{datetime.now():%Y%m%d_%H%M%S}.csv"

    fields = ["severity", "category", "asset_type", "asset_name",
              "asset_luid", "title", "detail", "remediation"]

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for iss in sorted(issues, key=lambda x: (-x.priority_score, x.asset_name)):
            writer.writerow({
                "severity":    iss.severity.value,
                "category":    iss.category.value,
                "asset_type":  iss.asset_type.value,
                "asset_name":  iss.asset_name,
                "asset_luid":  iss.asset_luid,
                "title":       iss.title,
                "detail":      iss.detail,
                "remediation": iss.remediation,
            })

    return path


def _esc(s: str) -> str:
    """Minimal HTML escaping."""
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
