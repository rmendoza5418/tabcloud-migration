"""
inventory.py — Full asset discovery from the source Tableau Server.

build_inventory() returns a list of AssetRecord objects covering all
workbooks and published datasources, enriched with connection metadata.

This is the first step in the migration pipeline and is called by both
the assessment (read-only) and the planner.
"""

from __future__ import annotations

from client import TableauClient
from models import AssetRecord, ContentType


def build_inventory(
    client: TableauClient,
    project_filter: list[str] | None = None,
    verbose: bool = True,
) -> list[AssetRecord]:
    """
    Retrieve all workbooks and datasources from the source server.

    Args:
        client:         Authenticated TableauClient (must be in context manager).
        project_filter: If provided, only include assets in these projects.
                        Empty list or None includes everything.
        verbose:        Print progress to stdout.

    Returns:
        List of AssetRecord, datasources first (preserves publish order).
    """
    if verbose:
        print("Discovering datasources…")
    datasources = client.get_all_datasources()

    if verbose:
        print(f"  {len(datasources)} published datasources found")
        print("Discovering workbooks…")

    workbooks = client.get_all_workbooks()

    if verbose:
        print(f"  {len(workbooks)} workbooks found")

    all_assets: list[AssetRecord] = datasources + workbooks

    # Apply project filter
    if project_filter:
        filter_set = {p.lower() for p in project_filter}
        all_assets = [
            a for a in all_assets
            if a.project_name.lower() in filter_set
        ]
        if verbose:
            print(f"  {len(all_assets)} assets in filtered projects: {project_filter}")

    return all_assets


def summarize_inventory(assets: list[AssetRecord]) -> dict:
    """Return a summary dict suitable for logging or reporting."""
    workbooks    = [a for a in assets if a.content_type == ContentType.WORKBOOK]
    datasources  = [a for a in assets if a.content_type == ContentType.DATASOURCE]
    projects     = {a.project_name for a in assets}
    with_extract = [a for a in assets if a.has_extract]

    connection_types: dict[str, int] = {}
    for asset in assets:
        for conn in asset.connections:
            ct = conn.connection_type or "unknown"
            connection_types[ct] = connection_types.get(ct, 0) + 1

    return {
        "total":            len(assets),
        "workbooks":        len(workbooks),
        "datasources":      len(datasources),
        "projects":         len(projects),
        "with_extracts":    len(with_extract),
        "connection_types": dict(sorted(connection_types.items(),
                                        key=lambda x: -x[1])),
    }
