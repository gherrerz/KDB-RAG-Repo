"""Utilidades de reinicio del sistema para borrar datos indexados y persistentes."""

import gc
import os
import shutil
import sqlite3
import stat
import time
from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings

from coderag.core.settings import get_settings
from coderag.ingestion.graph_builder import GraphBuilder
from coderag.ingestion.index_bm25 import GLOBAL_BM25
from coderag.ingestion.index_chroma import COLLECTIONS, ChromaIndex


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


def _compact_chroma_sqlite(chroma_path: Path) -> None:
    """Fuerza la compactación del archivo SQLite tras borrar colecciones lógicamente."""
    db_file = chroma_path / "chroma.sqlite3"
    if not db_file.exists():
        return

    connection = sqlite3.connect(str(db_file), timeout=8)
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("VACUUM")
        connection.execute("PRAGMA optimize")
        connection.commit()
    finally:
        connection.close()


def reset_all_storage() -> tuple[list[str], list[str]]:
    """Persistencia clara de vectores, léxicos, gráficos, espacios de trabajo y metadatos."""
    settings = get_settings()
    cleared: list[str] = []
    warnings: list[str] = []

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

    chroma_reset_done = False
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
        _compact_chroma_sqlite(settings.chroma_path)
        cleared.append("Chroma SQLite compactado")
    except sqlite3.Error as exc:
        warnings.append(
            "No se pudo compactar chroma.sqlite3 tras borrado lógico: "
            f"{exc}"
        )

    try:
        _remove_path(settings.chroma_path)
    except RuntimeError as exc:
        warnings.append(
            "No se pudo vaciar carpeta Chroma por lock de archivos: "
            f"{exc}"
        )
    settings.chroma_path.mkdir(parents=True, exist_ok=True)
    if chroma_reset_done:
        cleared.append(f"Chroma ({settings.chroma_path})")

    _remove_path(settings.workspace_path)
    settings.workspace_path.mkdir(parents=True, exist_ok=True)
    cleared.append(f"Workspace ({settings.workspace_path})")

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
