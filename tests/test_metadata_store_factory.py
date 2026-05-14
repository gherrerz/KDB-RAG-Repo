"""Pruebas unitarias para la factory compartida de metadata stores."""

from __future__ import annotations

from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys

import pytest

from coderag.storage.base_metadata_store import BaseMetadataStore
from coderag.storage.metadata_store import MetadataStore
from coderag.storage.metadata_store_factory import (
    build_metadata_store,
    metadata_backend_label,
)


def test_build_metadata_store_returns_sqlite_store_by_default(
    tmp_path: Path,
) -> None:
    """Sin DSN de Postgres, la factory debe devolver MetadataStore."""
    settings = SimpleNamespace(workspace_path=tmp_path / "workspace")

    store = build_metadata_store(settings)

    assert isinstance(store, BaseMetadataStore)
    assert isinstance(store, MetadataStore)
    assert metadata_backend_label(settings) == "Metadata SQLite"


def test_build_metadata_store_returns_postgres_store_when_dsn_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Con DSN resuelto, la factory debe construir PostgresMetadataStore."""

    class FakePostgresMetadataStore(BaseMetadataStore):
        """Stub mínimo para verificar selección de backend Postgres."""

        def __init__(self, dsn: str) -> None:
            """Guarda la DSN recibida para inspección del test."""
            self.dsn = dsn

        def upsert_job(self, job) -> None:
            del job

        def recover_interrupted_jobs(self) -> int:
            return 0

        def get_job(self, job_id: str):
            del job_id
            return None

        def list_repo_ids(self) -> list[str]:
            return []

        def list_repo_catalog(self) -> list[dict[str, str | None]]:
            return []

        def list_active_job_ids(self, repo_id: str | None = None) -> list[str]:
            del repo_id
            return []

        def upsert_repo_runtime(
            self,
            *,
            repo_id: str,
            organization: str | None,
            repo_url: str,
            branch: str,
            local_path: str,
            embedding_provider: str | None,
            embedding_model: str | None,
        ) -> None:
            del (
                repo_id,
                organization,
                repo_url,
                branch,
                local_path,
                embedding_provider,
                embedding_model,
            )

        def get_repo_runtime(self, repo_id: str):
            del repo_id
            return None

        def delete_repo_runtime(self, repo_id: str) -> int:
            del repo_id
            return 0

        def delete_repo_jobs(self, repo_id: str) -> int:
            del repo_id
            return 0

        def delete_repo_data(self, repo_id: str) -> dict[str, int]:
            del repo_id
            return {"jobs_deleted": 0, "repos_deleted": 0, "total": 0}

    fake_module = ModuleType("coderag.storage.postgres_metadata_store")
    fake_module.PostgresMetadataStore = FakePostgresMetadataStore
    monkeypatch.setitem(
        sys.modules,
        "coderag.storage.postgres_metadata_store",
        fake_module,
    )

    settings = SimpleNamespace(
        workspace_path=tmp_path / "workspace",
        resolve_postgres_dsn=lambda: "postgresql://fake/db",
    )

    store = build_metadata_store(settings)

    assert isinstance(store, FakePostgresMetadataStore)
    assert store.dsn == "postgresql://fake/db"
    assert metadata_backend_label(settings) == "Metadata Postgres"