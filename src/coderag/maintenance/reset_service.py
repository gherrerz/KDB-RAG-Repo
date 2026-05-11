"""Utilidades de reinicio del sistema para borrar datos indexados y persistentes."""

import gc
import os
import shutil
import stat
import time
from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings

from coderag.core.settings import get_settings
from coderag.ingestion.graph_builder import GraphBuilder
from coderag.ingestion.index_bm25 import GLOBAL_BM25
from coderag.ingestion.index_chroma import (
    COLLECTIONS,
    ChromaIndex,
    build_remote_chroma_client,
)
from coderag.storage.metadata_store import MetadataStore


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


def reset_all_storage() -> tuple[list[str], list[str]]:
    """Persistencia clara de vectores, léxicos, gráficos, espacios de trabajo y metadatos."""
    settings = get_settings()
    cleared: list[str] = []
    warnings: list[str] = []
    postgres_url = (settings.postgres_url or "").strip()

    ChromaIndex.reset_shared_state()

    GLOBAL_BM25.clear()
    cleared.append("BM25 en memoria")

    bm25_path = settings.workspace_path.parent / "bm25"
    try:
        _remove_path(bm25_path)
        bm25_path.mkdir(parents=True, exist_ok=True)
        cleared.append(f"BM25 snapshots ({bm25_path})")
    except RuntimeError as exc:
        warnings.append(
            "No se pudo vaciar carpeta BM25 por lock de archivos: "
            f"{exc}"
        )

    if postgres_url:
        try:
            from coderag.storage.lexical_store import LexicalStore
            LexicalStore(postgres_url).delete_all()
            cleared.append("LexicalStore Postgres")
        except Exception as exc:
            warnings.append(f"No se pudo limpiar LexicalStore Postgres: {exc}")

    chroma_reset_done = False
    if settings.chroma_mode == "remote":
        try:
            client = build_remote_chroma_client(settings)
            for collection_name in COLLECTIONS:
                try:
                    client.delete_collection(collection_name)
                except Exception:
                    continue
            chroma_reset_done = True
        except Exception as exc:
            warnings.append(f"No se pudieron limpiar colecciones Chroma remoto: {exc}")
        finally:
            try:
                del client
            except Exception:
                pass
            gc.collect()
    else:
        try:
            client = chromadb.PersistentClient(
                path=str(settings.chroma_path),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            for collection_name in COLLECTIONS:
                try:
                    client.delete_collection(collection_name)
                except Exception:
                    continue
            chroma_reset_done = True
        except Exception as exc:
            warnings.append(f"No se pudieron limpiar colecciones Chroma por API: {exc}")
        finally:
            try:
                del client
            except Exception:
                pass
            gc.collect()

        ChromaIndex.reset_shared_state()

        try:
            _remove_path(settings.chroma_path)
        except RuntimeError as exc:
            warnings.append(
                "No se pudo vaciar carpeta Chroma por lock de archivos: "
                f"{exc}"
            )
        settings.chroma_path.mkdir(parents=True, exist_ok=True)

    ChromaIndex.reset_shared_state()
    if chroma_reset_done:
        cleared.append("Chroma")

    _remove_path(settings.workspace_path)
    settings.workspace_path.mkdir(parents=True, exist_ok=True)
    cleared.append(f"Workspace ({settings.workspace_path})")

    if postgres_url:
        try:
            from coderag.storage.postgres_metadata_store import PostgresMetadataStore
            pg_store = PostgresMetadataStore(postgres_url)
            pg_store.reset_all()
            cleared.append("Metadata Postgres")
        except Exception as exc:
            warnings.append(f"No se pudo limpiar metadata Postgres: {exc}")
    else:
        metadata_db = settings.workspace_path.parent / "metadata.db"
        _remove_path(metadata_db)
        metadata_db.parent.mkdir(parents=True, exist_ok=True)
        metadata_db.touch(exist_ok=True)
        cleared.append(f"Metadata ({metadata_db})")

    graph = GraphBuilder()
    try:
        with graph.driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")
        cleared.append("Grafo Neo4j")
    finally:
        graph.close()

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
        chroma = ChromaIndex()
        chroma_deleted = chroma.delete_by_repo_id(normalized_repo_id)
        deleted_counts["chroma_total"] = int(chroma_deleted.get("total", 0) or 0)
        for collection_name in COLLECTIONS:
            value = int(chroma_deleted.get(collection_name, 0) or 0)
            deleted_counts[f"chroma_{collection_name}"] = value
        cleared.append("Chroma")
    except Exception as exc:
        warnings.append(f"No se pudo limpiar Chroma para '{normalized_repo_id}': {exc}")

    try:
        bm25_deleted = GLOBAL_BM25.delete_repo(normalized_repo_id)
        deleted_counts["bm25_docs"] = int(
            bm25_deleted.get("docs_removed", 0) or 0
        )
        deleted_counts["bm25_snapshots"] = int(
            bm25_deleted.get("snapshot_removed", 0) or 0
        )
        cleared.append("BM25")
    except Exception as exc:
        warnings.append(f"No se pudo limpiar BM25 para '{normalized_repo_id}': {exc}")

    postgres_url_del = (settings.postgres_url or "").strip()
    if postgres_url_del:
        try:
            from coderag.storage.lexical_store import LexicalStore
            lex_deleted = LexicalStore(postgres_url_del, settings.lexical_fts_language).delete_repo(normalized_repo_id)
            deleted_counts["lexical_docs"] = int(lex_deleted.get("docs_removed", 0) or 0)
            cleared.append("LexicalStore")
        except Exception as exc:
            warnings.append(f"No se pudo limpiar LexicalStore para '{normalized_repo_id}': {exc}")

    graph = GraphBuilder()
    try:
        graph_nodes_deleted = graph.delete_repo_subgraph(normalized_repo_id)
        deleted_counts["neo4j_nodes"] = int(graph_nodes_deleted)
        cleared.append("Grafo Neo4j")
    except Exception as exc:
        warnings.append(f"No se pudo limpiar Neo4j para '{normalized_repo_id}': {exc}")
    finally:
        graph.close()

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

    metadata_store = _build_metadata_store(settings)
    try:
        metadata_deleted = metadata_store.delete_repo_data(normalized_repo_id)
        deleted_counts["metadata_jobs"] = int(
            metadata_deleted.get("jobs_deleted", 0) or 0
        )
        deleted_counts["metadata_repos"] = int(
            metadata_deleted.get("repos_deleted", 0) or 0
        )
        deleted_counts["metadata_total"] = int(
            metadata_deleted.get("total", 0) or 0
        )
        cleared.append("Metadata SQLite" if not (settings.postgres_url or "").strip() else "Metadata Postgres")
    except Exception as exc:
        warnings.append(
            "No se pudo limpiar metadata para "
            f"'{normalized_repo_id}': {exc}"
        )

    return cleared, warnings, deleted_counts


def _build_metadata_store(settings):
    """Devuelve el store de metadatos apropiado según la configuración."""
    postgres_url = (settings.postgres_url or "").strip()
    if postgres_url:
        from coderag.storage.postgres_metadata_store import PostgresMetadataStore
        return PostgresMetadataStore(postgres_url)
    return MetadataStore(settings.workspace_path.parent / "metadata.db")
