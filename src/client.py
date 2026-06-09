"""
client.py — Tableau Server Client wrappers for source and destination servers.

Provides:
  TableauClient    — single-server TSC wrapper with pagination and retry
  DualClient       — source + destination pair used by migrator
  load_config()    — YAML config loader
"""

from __future__ import annotations

import time
import yaml
from pathlib import Path
from typing import Generator

import tableauserverclient as TSC

from models import AssetRecord, ConnectionInfo, ContentType


# ------------------------------------------------------------------ #
# Config loader
# ------------------------------------------------------------------ #

def load_config(config_path: str | Path = "config/config.yaml") -> dict:
    """Load YAML config. Raises FileNotFoundError with a helpful message."""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Config not found at {path}. "
            "Copy config/config.example.yaml to config/config.yaml and fill in your credentials."
        )
    with open(path) as f:
        return yaml.safe_load(f)


# ------------------------------------------------------------------ #
# Single-server client
# ------------------------------------------------------------------ #

class TableauClient:
    """
    Thin TSC wrapper that handles:
      - PAT authentication
      - Automatic pagination via _paginate()
      - Rate-limit sleep between requests
      - Structured asset enrichment (connections, view counts)
    """

    RATE_LIMIT_SLEEP = 0.1   # seconds between paginated API calls

    def __init__(self, server_url: str, site_name: str,
                 token_name: str, token_value: str,
                 api_version: str = "3.19"):
        self.server_url  = server_url
        self.site_name   = site_name
        self.token_name  = token_name
        self.token_value = token_value
        self.api_version = api_version

        self._server: TSC.Server | None = None
        self._auth:   TSC.PersonalAccessTokenAuth | None = None

    @classmethod
    def from_config(cls, cfg: dict) -> "TableauClient":
        return cls(
            server_url  = cfg["server_url"],
            site_name   = cfg.get("site_name", ""),
            token_name  = cfg["token_name"],
            token_value = cfg["token_value"],
            api_version = cfg.get("api_version", "3.19"),
        )

    def __enter__(self) -> "TableauClient":
        self._server = TSC.Server(self.server_url, use_server_version=False)
        self._server.version = self.api_version
        self._auth = TSC.PersonalAccessTokenAuth(
            token_name  = self.token_name,
            personal_access_token = self.token_value,
            site_id     = self.site_name,
        )
        self._server.auth.sign_in(self._auth)
        return self

    def __exit__(self, *_):
        if self._server:
            try:
                self._server.auth.sign_out()
            except Exception:
                pass

    @property
    def server(self) -> TSC.Server:
        if not self._server:
            raise RuntimeError("Client not signed in — use as context manager.")
        return self._server

    # ---------------------------------------------------------------- #
    # Pagination
    # ---------------------------------------------------------------- #

    def _paginate(self, endpoint, req_options=None) -> Generator:
        """Yield all items from a paginated TSC endpoint."""
        opts = req_options or TSC.RequestOptions(pagesize=100)
        page = 1
        while True:
            opts.pagenumber = page
            items, pagination = endpoint.get(opts)
            yield from items
            if pagination.page_number * pagination.page_size >= pagination.total_available:
                break
            page += 1
            time.sleep(self.RATE_LIMIT_SLEEP)

    # ---------------------------------------------------------------- #
    # Asset retrieval
    # ---------------------------------------------------------------- #

    def get_all_workbooks(self) -> list[AssetRecord]:
        records = []
        for wb in self._paginate(self.server.workbooks):
            self.server.workbooks.populate_views(wb)
            self.server.workbooks.populate_connections(wb)
            records.append(self._workbook_to_record(wb))
        return records

    def get_all_datasources(self) -> list[AssetRecord]:
        records = []
        for ds in self._paginate(self.server.datasources):
            self.server.datasources.populate_connections(ds)
            records.append(self._datasource_to_record(ds))
        return records

    def get_all_projects(self) -> list[TSC.ProjectItem]:
        return list(self._paginate(self.server.projects))

    def get_all_groups(self) -> list[TSC.GroupItem]:
        return list(self._paginate(self.server.groups))

    def get_all_users(self) -> list[TSC.UserItem]:
        return list(self._paginate(self.server.users))

    def get_workbook_permissions(self, wb: TSC.WorkbookItem):
        self.server.workbooks.populate_permissions(wb)
        return wb.permissions

    def get_datasource_permissions(self, ds: TSC.DatasourceItem):
        self.server.datasources.populate_permissions(ds)
        return ds.permissions

    # ---------------------------------------------------------------- #
    # Publishing
    # ---------------------------------------------------------------- #

    def publish_workbook(
        self,
        twbx_path: str,
        project_id: str,
        name: str | None = None,
        mode: str = "CreateNew",
    ) -> TSC.WorkbookItem:
        item = TSC.WorkbookItem(project_id=project_id, name=name)
        publish_mode = getattr(TSC.Server.PublishMode, mode)
        return self.server.workbooks.publish(item, twbx_path, publish_mode)

    def publish_datasource(
        self,
        tdsx_path: str,
        project_id: str,
        name: str | None = None,
        mode: str = "CreateNew",
    ) -> TSC.DatasourceItem:
        item = TSC.DatasourceItem(project_id=project_id, name=name)
        publish_mode = getattr(TSC.Server.PublishMode, mode)
        return self.server.datasources.publish(item, tdsx_path, publish_mode)

    def create_project(self, name: str, description: str = "",
                       parent_id: str | None = None) -> TSC.ProjectItem:
        item = TSC.ProjectItem(name=name, description=description,
                               parent_id=parent_id)
        return self.server.projects.create(item)

    def download_workbook(self, luid: str, output_dir: str,
                          include_extract: bool = True) -> str:
        return self.server.workbooks.download(
            luid, filepath=output_dir, include_extract=include_extract
        )

    def download_datasource(self, luid: str, output_dir: str,
                            include_extract: bool = True) -> str:
        return self.server.datasources.download(
            luid, filepath=output_dir, include_extract=include_extract
        )

    # ---------------------------------------------------------------- #
    # Internal converters
    # ---------------------------------------------------------------- #

    def _workbook_to_record(self, wb: TSC.WorkbookItem) -> AssetRecord:
        connections = [
            ConnectionInfo(
                server_address  = c.server_address or "",
                server_port     = c.server_port,
                connection_type = c.connection_type or "",
                database_name   = c.datasource_name,
                username        = c.username,
                is_embedded     = bool(c.username),
            )
            for c in (wb.connections or [])
        ]
        return AssetRecord(
            content_type  = ContentType.WORKBOOK,
            luid          = wb.id,
            name          = wb.name,
            project_name  = wb.project_name or "",
            project_luid  = wb.project_id or "",
            owner_name    = wb.owner_id or "",   # populated separately if needed
            owner_email   = None,
            created_at    = wb.created_at,
            updated_at    = wb.updated_at,
            last_accessed = wb.updated_at,        # proxy; TSC doesn't expose last_viewed
            view_count    = len(wb.views) if wb.views else 0,
            size_bytes    = None,                 # not exposed via TSC
            has_extract   = any(c.connection_type == "hyper" for c in connections),
            connections   = connections,
            tags          = list(wb.tags or []),
            is_certified  = False,               # populated by governance checks
            description   = wb.description or "",
        )

    def _datasource_to_record(self, ds: TSC.DatasourceItem) -> AssetRecord:
        connections = [
            ConnectionInfo(
                server_address  = c.server_address or "",
                server_port     = c.server_port,
                connection_type = c.connection_type or "",
                database_name   = c.datasource_name,
                username        = c.username,
                is_embedded     = bool(c.username),
            )
            for c in (ds.connections or [])
        ]
        return AssetRecord(
            content_type  = ContentType.DATASOURCE,
            luid          = ds.id,
            name          = ds.name,
            project_name  = ds.project_name or "",
            project_luid  = ds.project_id or "",
            owner_name    = ds.owner_id or "",
            owner_email   = None,
            created_at    = ds.created_at,
            updated_at    = ds.updated_at,
            last_accessed = ds.updated_at,
            view_count    = 0,
            size_bytes    = None,
            has_extract   = getattr(ds, "is_published", False),
            connections   = connections,
            tags          = list(ds.tags or []),
            is_certified  = ds.certified if hasattr(ds, "certified") else False,
            description   = ds.description or "",
        )


# ------------------------------------------------------------------ #
# Dual client (source + destination)
# ------------------------------------------------------------------ #

class DualClient:
    """
    Convenience wrapper that holds authenticated clients for both
    source and destination servers. Used by Migrator and Validator.
    """

    def __init__(self, source: TableauClient, destination: TableauClient):
        self.source      = source
        self.destination = destination

    @classmethod
    def from_config(cls, cfg: dict) -> "DualClient":
        return cls(
            source      = TableauClient.from_config(cfg["source"]),
            destination = TableauClient.from_config(cfg["destination"]),
        )

    def __enter__(self) -> "DualClient":
        self.source.__enter__()
        self.destination.__enter__()
        return self

    def __exit__(self, *args):
        self.source.__exit__(*args)
        self.destination.__exit__(*args)
