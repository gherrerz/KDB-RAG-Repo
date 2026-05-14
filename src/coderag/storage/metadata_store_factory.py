"""Factory compartida para seleccionar el backend de metadata operativo."""

from __future__ import annotations

from coderag.core.settings import resolve_postgres_dsn
from coderag.storage.base_metadata_store import BaseMetadataStore
from coderag.storage.metadata_store import MetadataStore


def build_metadata_store(settings: object) -> BaseMetadataStore:
    """Construye el store de metadata adecuado según la configuración."""
    postgres_dsn = resolve_postgres_dsn(settings)
    if postgres_dsn:
        from coderag.storage.postgres_metadata_store import PostgresMetadataStore

        return PostgresMetadataStore(postgres_dsn)

    return MetadataStore(settings.workspace_path.parent / "metadata.db")


def metadata_backend_label(settings: object) -> str:
    """Devuelve una etiqueta legible del backend de metadata activo."""
    return (
        "Metadata Postgres"
        if resolve_postgres_dsn(settings)
        else "Metadata SQLite"
    )