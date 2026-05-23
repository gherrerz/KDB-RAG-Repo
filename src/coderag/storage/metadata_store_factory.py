"""Factory compartida para seleccionar el backend de metadata operativo."""

from __future__ import annotations

from coderag.core.settings import resolve_postgres_dsn
from coderag.storage.base_metadata_store import BaseMetadataStore
from coderag.storage.postgres_session import PostgresSessionFactory


def build_metadata_store(settings: object) -> BaseMetadataStore:
    """Construye el store de metadata operativo soportado."""
    postgres_dsn = resolve_postgres_dsn(settings)
    if postgres_dsn:
        from coderag.storage.postgres_metadata_store import PostgresMetadataStore

        return PostgresMetadataStore(
            postgres_dsn,
            session_factory=PostgresSessionFactory.from_settings(settings),
        )

    raise RuntimeError(
        "Metadata Postgres es obligatorio en el runtime actual. "
        "Configure POSTGRES_*; SQLite legacy ya no esta soportado como "
        "backend operativo."
    )


def metadata_backend_label(settings: object) -> str:
    """Devuelve una etiqueta legible del backend de metadata activo."""
    if resolve_postgres_dsn(settings):
        return "Metadata Postgres"
    return "Metadata unavailable"