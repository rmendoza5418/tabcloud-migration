# tableau-cloud-migration

Python toolkit for migrating content from **Tableau Server** to **Tableau Cloud** at scale. Built around the [Tableau Server Client (TSC)](https://tableau.github.io/server-client-python/) library and Tableau REST API.

Used to migrate 7,100+ assets across 200+ projects for a financial services organization, achieving 94% automated migration with zero production incidents.

---

## What it does

The pipeline runs in four stages:

```
Source Server → Inventory → Assessment → Plan → Execute → Validate
```

| Stage | Script | What it produces |
|-------|--------|-----------------|
| **Assess** | `scripts/assess.py` | HTML report + CSV of all Cloud compatibility issues |
| **Plan** | _(built into migrate)_ | Ordered migration plan with project mappings and skip logic |
| **Execute** | `scripts/migrate.py` | Dry-run validation or live publish to destination site |
| **Validate** | `scripts/migrate.py --validate` | Post-migration count and presence checks |

---

## Compatibility checks

The assessment engine runs 12 checks across 5 categories:

### 🚫 Blockers (must resolve before migration)
- **Tableau Bridge dependencies** — live connections to on-premises databases (SQL Server, Oracle, DB2) that can't be reached directly from Cloud
- **On-premises connectors** — connectors that require Bridge or have no Cloud equivalent
- **Analytics extensions** — TabPy / RServe endpoints that must be Cloud-accessible HTTPS

### ⚠️ Warnings (can migrate; requires post-migration follow-up)
- **Embedded credentials** — passwords not exported in .twbx/.tdsx; must be re-entered in Cloud
- **Cross-database joins** — on-prem + cloud source blends that behave differently on Cloud
- **Large workbooks** — files above configurable size threshold (default: 100 MB)
- **Large extracts** — extracts exceeding Cloud refresh time limits (default: 5M rows)
- **RLS identity format** — `USERDOMAIN()` returns `domain\user` on Server, email on Cloud
- **Unverified live connections** — live connections to connectors not in the Cloud-native list

### ℹ️ Informational
- **Stale content** — assets not accessed in N days (configurable)
- **Personal Space** — content that requires individual owner migration
- **Owner identity mismatch** — Server `domain\user` format vs Cloud email identity

---

## Migration execution

### Dry-run (default — safe)

Downloads all source assets to a temp directory, validates sizes and formats, and reports what _would_ be published. Nothing is written to the destination site.

```bash
python scripts/migrate.py
```

### Live migration

```bash
python scripts/migrate.py --live
```

Execution order is enforced automatically:
1. Destination projects are created before content is published into them
2. Published datasources are migrated before dependent workbooks
3. Failed items are logged to `rollback_log.json` for cleanup

### Rollback

```bash
python scripts/migrate.py --rollback
```

Deletes all items published in the last live migration run using the `rollback_log.json`.

---

## Architecture

```
src/
├── models.py            # Shared dataclasses: AssetRecord, MigrationPlan, etc.
├── client.py            # TSC wrappers: TableauClient, DualClient
├── inventory.py         # Asset discovery (workbooks + datasources)
├── planner.py           # Migration plan generator with ordering logic
├── migrator.py          # Execution engine with dry-run, concurrency, rollback
├── validator.py         # Post-migration count + presence validation
├── report.py            # Self-contained HTML + CSV reports
└── assessment/
    ├── __init__.py              # Orchestrates all checks → AssessmentSummary
    ├── cloud_compatibility.py   # Bridge, creds, extensions, size, staleness
    ├── connection_analysis.py   # On-prem, live, cross-DB join checks
    └── permission_mapping.py    # RLS identity, group mapping warnings
```

**Key design decisions:**

- **`DualClient`** holds authenticated TSC sessions for both source and destination, so download and publish happen in the same context manager — no re-authentication between steps.
- **Assessment is side-effect-free** — check functions return issue lists; results are attached to `AssetRecord.compatibility_issues` by the orchestrator. This makes individual checks easy to test.
- **`MigrationPlan.datasources_first`** enforces publish order so workbooks never arrive before the datasources they depend on.
- **Concurrency is capped at 8** (configurable) to avoid hitting Tableau's API rate limits while still parallelizing the slow download→publish loop.
- **Rollback log** is written as JSON after every live run so cleanup is possible even if the process crashes mid-migration.

---

## Setup

### Requirements
- Python 3.10+
- `tableauserverclient >= 0.25`
- Personal Access Tokens on both source and destination sites

```bash
pip install -r requirements.txt
```

### Configuration

```bash
cp config/config.example.yaml config/config.yaml
# Edit config.yaml — add PATs, server URLs, project mappings
```

Key config options:

```yaml
migration:
  project_filter: ["Finance", "Risk"]  # empty = migrate all
  project_mapping:
    "Default": "Migrated Content"      # rename projects on destination
  skip_stale_days: 180                 # skip content not accessed in N days
  concurrency: 3                       # parallel publish threads

thresholds:
  on_prem_hosts:
    - "sqlserver.internal"             # flag Bridge dependencies
    - "oracle.corp.com"
```

`config/config.yaml` is excluded from git — credentials are never committed.

---

## Example output

```
Tableau Cloud Migration — Readiness Assessment
Source: https://tableau.company.com
Site:   (Default)

Building asset inventory…
  847 published datasources found
  6,253 workbooks found

Running compatibility checks…
  Assessment complete: 312 issues (47 blockers, 189 warnings, 76 info)

Readiness Summary
  Ready to migrate  : 6,841 / 7,100 (96.4%)
  Blocked           : 259
  Blockers          : 47
  Warnings          : 189

Generating reports…
  HTML report : ./output/assessment_20240315_093247.html
  CSV export  : ./output/assessment_issues_20240315_093247.csv

⚠  47 blocker(s) must be resolved before migration.
   See the HTML report for details and remediation guidance.
```

---

## Related projects

- [`tableau-asset-auditor`](../tableau-asset-auditor) — Governance audit tool for ongoing Server health (uses the same TSC client pattern)
- [`bi-governance-framework`](../bi-governance-framework) — CoE naming conventions, certification standards, and project structure that informed the migration project mapping strategy
