"""Contratos y selección de backend para búsqueda léxica por repositorio."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from coderag.core.settings import resolve_postgres_dsn
from coderag.ingestion.index_bm25 import GLOBAL_BM25


@runtime_checkable
class RepositoryLexicalIndex(Protocol):
    """Contrato mínimo compartido por backends léxicos consultables."""

    def query(self, repo_id: str, text: str, top_n: int = 50) -> list[dict]:
        """Retorna resultados compatibles con la fusión léxica del retrieval."""

    def delete_repo(self, repo_id: str) -> dict[str, int]:
        """Elimina datos indexados del repositorio y retorna conteos."""


def build_repository_lexical_index(settings: object) -> RepositoryLexicalIndex:
    """Selecciona el backend léxico activo según configuración."""
    postgres_dsn = resolve_postgres_dsn(settings)
    if postgres_dsn:
        from coderag.storage.lexical_store import LexicalStore

        return LexicalStore(
            postgres_dsn,
            getattr(settings, "lexical_fts_language", "english"),
        )

    return GLOBAL_BM25


def repository_lexical_backend_label(settings: object) -> str:
    """Devuelve la etiqueta del backend léxico activo."""
    return "lexical" if resolve_postgres_dsn(settings) else "bm25"


def repository_lexical_index_has_data(index: object, repo_id: str) -> bool:
    """Indica si el backend léxico activo tiene datos para un repositorio."""
    has_corpus = getattr(index, "has_corpus", None)
    if callable(has_corpus):
        return bool(has_corpus(repo_id))

    has_repo = getattr(index, "has_repo", None)
    has_repo_snapshot = getattr(index, "has_repo_snapshot", None)
    return (
        bool(has_repo(repo_id)) if callable(has_repo) else False
    ) or (
        bool(has_repo_snapshot(repo_id))
        if callable(has_repo_snapshot)
        else False
    )


def repository_has_active_lexical_data(settings: object, repo_id: str) -> bool:
    """Consulta si el backend léxico activo ya contiene el repositorio."""
    index = build_repository_lexical_index(settings)
    return repository_lexical_index_has_data(index, repo_id)


def repository_has_query_ready_lexical_data(
    settings: object,
    repo_id: str,
) -> bool:
    """Indica si el backend léxico activo está listo para consultas."""
    index = build_repository_lexical_index(settings)
    ensure_loaded = getattr(index, "ensure_repo_loaded", None)
    if callable(ensure_loaded):
        return bool(ensure_loaded(repo_id))
    return repository_lexical_index_has_data(index, repo_id)


def delete_active_repository_lexical_data(
    settings: object,
    repo_id: str,
) -> dict[str, int]:
    """Borra datos del repositorio usando el backend léxico activo."""
    index = build_repository_lexical_index(settings)
    return index.delete_repo(repo_id)


def ensure_repository_lexical_index_loaded(
    index: RepositoryLexicalIndex,
    repo_id: str,
) -> None:
    """Carga en memoria backends que lo requieren antes de consultar."""
    ensure_loaded = getattr(index, "ensure_repo_loaded", None)
    if callable(ensure_loaded):
        ensure_loaded(repo_id)