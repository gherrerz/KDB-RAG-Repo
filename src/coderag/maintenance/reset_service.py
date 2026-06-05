"""Utilidades de reinicio del sistema para borrar datos indexados y persistentes."""

import os
import shutil
import stat
import time
from pathlib import Path

from coderag.core.settings import get_settings, resolve_postgres_dsn
from coderag.core.vector_index import (
    build_managed_vector_index,
    delete_repository_vector_documents,
    reset_managed_vector_storage,
)
from coderag.ingestion.graph_builder import GraphBuilder
from coderag.ingestion.index_chroma import COLLECTIONS
from coderag.storage.base_metadata_store import BaseMetadataStore
from coderag.storage.metadata_store_factory import (
    build_metadata_store,
    metadata_backend_label,
)


def _on_remove_error(func, path: str, exc_info) -> None:
    """Gestiona archivos de solo lectura durante limpieza de directorios en Windows."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _remove_path(path: Path, retries: int = 3) -> None:
    """Elimine un archivo o directorio con reintentos para bloqueos de archivos transitorios."""
    if not path.exists():
        return

    last_error: Exception | None = None
    for _ in range(retries):
        try:
            if path.is_dir():
                shutil.rmtree(path, onerror=_on_remove_error)
            else:
                path.unlink()
            return
        except Exception as exc:  # pragma: no cover - depends on OS lock timing
            last_error = exc
            time.sleep(0.35)

    if last_error is not None:
        raise RuntimeError(f"No se pudo eliminar {path}: {last_error}") from last_error


def _reset_postgres_lexical_storage(settings: object) -> tuple[list[str], list[str]]:
    """Limpia el corpus léxico en Postgres cuando ese backend existe."""
    cleared: list[str] = []
    warnings: list[str] = []
    postgres_dsn = resolve_postgres_dsn(settings)
    if not postgres_dsn:
        return cleared, warnings

    try:
        from coderag.storage.lexical_store import LexicalStore
        from coderag.storage.postgres_session import PostgresSessionFactory

        LexicalStore(
            postgres_dsn,
            getattr(settings, "lexical_fts_language", "english"),
            session_factory=PostgresSessionFactory.from_settings(settings),
        ).delete_all()
        cleared.append("LexicalStore Postgres")
    except Exception as exc:
        warnings.append(f"No se pudo limpiar LexicalStore Postgres: {exc}")

    return cleared, warnings


def _delete_repo_postgres_lexical_storage(
    settings: object,
    repo_id: str,
) -> tuple[list[str], list[str], dict[str, int]]:
    """Elimina el corpus léxico en Postgres de un repositorio puntual."""
    cleared: list[str] = []
    warnings: list[str] = []
    deleted_counts: dict[str, int] = {}
    postgres_dsn = resolve_postgres_dsn(settings)
    if not postgres_dsn:
        return cleared, warnings, deleted_counts

    try:
        from coderag.storage.lexical_store import LexicalStore
        from coderag.storage.postgres_session import PostgresSessionFactory

        lex_deleted = LexicalStore(
            postgres_dsn,
            getattr(settings, "lexical_fts_language", "english"),
            session_factory=PostgresSessionFactory.from_settings(settings),
        ).delete_repo(repo_id)
        deleted_counts["lexical_docs"] = int(
            lex_deleted.get("docs_removed", 0) or 0
        )
        cleared.append("LexicalStore")
    except Exception as exc:
        warnings.append(f"No se pudo limpiar LexicalStore para '{repo_id}': {exc}")

    return cleared, warnings, deleted_counts


def reset_all_storage() -> tuple[list[str], list[str]]:
    """Persistencia clara de vectores, léxicos, gráficos, espacios de trabajo y metadatos."""
    settings = get_settings()
    cleared: list[str] = []
    warnings: list[str] = []
    postgres_dsn = resolve_postgres_dsn(settings)

    lexical_cleared, lexical_warnings = _reset_postgres_lexical_storage(settings)
    cleared.extend(lexical_cleared)
    warnings.extend(lexical_warnings)

    chroma_reset_done, chroma_warnings = reset_managed_vector_storage(
        settings,
        remove_path=_remove_path,
    )
    warnings.extend(chroma_warnings)
    if chroma_reset_done:
        cleared.append("Chroma")

    _remove_path(settings.workspace_path)
    settings.workspace_path.mkdir(parents=True, exist_ok=True)
    cleared.append(f"Workspace ({settings.workspace_path})")

    if postgres_dsn:
        try:
            from coderag.storage.postgres_metadata_store import PostgresMetadataStore
            pg_store = PostgresMetadataStore(postgres_dsn)
            pg_store.reset_all()
            cleared.append("Metadata Postgres")
        except Exception as exc:
            warnings.append(f"No se pudo limpiar metadata Postgres: {exc}")
    else:
        warnings.append(
            "Metadata Postgres no está configurado; no se limpió metadata "
            "operativa durante el reset."
        )

    try:
        graph = GraphBuilder()
        try:
            graph.clear_graph()
            cleared.append("Grafo Neo4j")
        finally:
            graph.close()
    except Exception as exc:
        warnings.append(f"No se pudo limpiar Neo4j: {exc}")

    return cleared, warnings


def _workspace_repo_paths(workspace_root: Path, repo_id: str) -> list[Path]:
    """Lista rutas de workspace que corresponden al repo exacto o por sufijo."""
    if not workspace_root.exists() or not workspace_root.is_dir():
        return []

    matches: list[Path] = []
    exact_name = repo_id
    prefix_name = f"{repo_id}_"
    for child in workspace_root.iterdir():
        if not child.is_dir():
            continue
        if child.name == exact_name or child.name.startswith(prefix_name):
            matches.append(child)
    return matches


def delete_repo_storage(
    repo_id: str,
) -> tuple[list[str], list[str], dict[str, int]]:
    """Elimina un repositorio puntual en todas las capas de storage del RAG."""
    normalized_repo_id = repo_id.strip()
    if not normalized_repo_id:
        raise ValueError("repo_id no puede estar vacío")

    settings = get_settings()
    cleared: list[str] = []
    warnings: list[str] = []
    deleted_counts: dict[str, int] = {}

    try:
        chroma_deleted = delete_repository_vector_documents(
            build_managed_vector_index(),
            normalized_repo_id,
        )
        deleted_counts["chroma_total"] = int(chroma_deleted.get("total", 0) or 0)
        for collection_name in COLLECTIONS:
            value = int(chroma_deleted.get(collection_name, 0) or 0)
            deleted_counts[f"chroma_{collection_name}"] = value
        cleared.append("Chroma")
    except Exception as exc:
        warnings.append(f"No se pudo limpiar Chroma para '{normalized_repo_id}': {exc}")

    lexical_cleared, lexical_warnings, lexical_counts = (
        _delete_repo_postgres_lexical_storage(settings, normalized_repo_id)
    )
    cleared.extend(lexical_cleared)
    warnings.extend(lexical_warnings)
    deleted_counts.update(lexical_counts)

    try:
        graph = GraphBuilder()
        try:
            graph_nodes_deleted = graph.delete_repo_subgraph(normalized_repo_id)
        finally:
            graph.close()
        deleted_counts["neo4j_nodes"] = int(graph_nodes_deleted)
        cleared.append("Grafo Neo4j")
    except Exception as exc:
        warnings.append(f"No se pudo limpiar Neo4j para '{normalized_repo_id}': {exc}")

    workspace_removed = 0
    for path in _workspace_repo_paths(settings.workspace_path, normalized_repo_id):
        try:
            _remove_path(path)
            workspace_removed += 1
        except RuntimeError as exc:
            warnings.append(
                f"No se pudo eliminar workspace '{path.name}' por lock: {exc}"
            )
    deleted_counts["workspace_dirs"] = workspace_removed
    if workspace_removed > 0:
        cleared.append("Workspace")

    try:
        metadata_store = _build_metadata_store(settings)
        metadata_deleted = metadata_store.delete_repo_data(normalized_repo_id)
        deleted_counts["metadata_snapshots"] = int(
            metadata_deleted.get("snapshots_deleted", 0) or 0
        )
        deleted_counts["metadata_jobs"] = int(
            metadata_deleted.get("jobs_deleted", 0) or 0
        )
        deleted_counts["metadata_repos"] = int(
            metadata_deleted.get("repos_deleted", 0) or 0
        )
        deleted_counts["metadata_total"] = int(
            metadata_deleted.get("total", 0) or 0
        )
        cleared.append(metadata_backend_label(settings))
    except Exception as exc:
        warnings.append(
            "No se pudo limpiar metadata operativa para "
            f"'{normalized_repo_id}': {exc}"
        )

    return cleared, warnings, deleted_counts


def _build_metadata_store(settings: object) -> BaseMetadataStore:
    """Devuelve el store de metadatos apropiado según la configuración."""
    return build_metadata_store(settings)
