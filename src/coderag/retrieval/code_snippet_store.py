"""Lectura de snippets de código desde el índice persistido (sin filesystem).

Estas funciones recuperan el texto fuente exacto de un símbolo o de un
archivo completo directamente desde Chroma/Postgres, sin depender de un
workspace local del clone del repositorio (que puede no existir en un
worker distribuido o tras la purga del workspace).
"""

from dataclasses import dataclass

from coderag.core.settings import get_settings, resolve_postgres_dsn
from coderag.ingestion.index_chroma import ChromaIndex
from coderag.storage.lexical_store import LexicalStore
from coderag.storage.postgres_session import PostgresSessionFactory


@dataclass(frozen=True)
class SnippetRecord:
    """Snippet de código resuelto desde el índice persistido."""

    text: str
    path: str
    start_line: int
    end_line: int
    symbol_name: str | None
    symbol_type: str | None
    source: str


def _lexical_store() -> LexicalStore:
    """Construye un LexicalStore contra el Postgres configurado."""
    settings = get_settings()
    postgres_dsn = resolve_postgres_dsn(settings)
    if not postgres_dsn:
        raise RuntimeError(
            "LexicalStore Postgres es obligatorio para leer snippets "
            "persistidos. Configure POSTGRES_* antes de consultar."
        )
    return LexicalStore(
        postgres_dsn,
        settings.lexical_fts_language,
        session_factory=PostgresSessionFactory.from_settings(settings),
    )


def get_symbol_snippet(
    repo_id: str,
    path: str,
    symbol_name: str,
) -> SnippetRecord | None:
    """Recupera el snippet completo de un símbolo por path+nombre exacto.

    Intenta primero Chroma (``code_symbols``, incluye ``snippet``/metadata
    completa) y usa Postgres ``lexical_corpus`` como respaldo si el registro
    no está disponible en Chroma (p. ej. purga parcial incremental).
    """
    chroma = ChromaIndex()
    try:
        result = chroma.get_collection(
            "code_symbols",
            where={
                "$and": [
                    {"repo_id": repo_id},
                    {"path": path},
                    {"symbol_name": symbol_name},
                ]
            },
            include=["documents", "metadatas"],
            limit=1,
        )
    except Exception:
        result = None

    if result:
        documents = result.get("documents") or []
        metadatas = result.get("metadatas") or []
        if documents and metadatas:
            metadata = metadatas[0] or {}
            return SnippetRecord(
                text=documents[0],
                path=str(metadata.get("path", path)),
                start_line=int(metadata.get("start_line", 1) or 1),
                end_line=int(metadata.get("end_line", 1) or 1),
                symbol_name=str(metadata.get("symbol_name") or symbol_name),
                symbol_type=metadata.get("symbol_type"),
                source="chroma_symbol",
            )

    row = _lexical_store().get_symbol_document(repo_id, path, symbol_name)
    if row is None:
        return None
    metadata = row.get("metadata") or {}
    return SnippetRecord(
        text=row["text"],
        path=str(metadata.get("path", path)),
        start_line=int(metadata.get("start_line", 1) or 1),
        end_line=int(metadata.get("end_line", 1) or 1),
        symbol_name=str(metadata.get("symbol_name") or symbol_name),
        symbol_type=metadata.get("symbol_type"),
        source="lexical_symbol",
    )


def get_file_snippet(repo_id: str, path: str) -> SnippetRecord | None:
    """Recupera el contenido íntegro persistido de un archivo (entity_type=file_full)."""
    row = _lexical_store().get_file_document(repo_id, path)
    if row is None:
        return None
    metadata = row.get("metadata") or {}
    end_line = int(metadata.get("end_line", 1) or 1)
    return SnippetRecord(
        text=row["text"],
        path=str(metadata.get("path", path)),
        start_line=1,
        end_line=end_line,
        symbol_name=None,
        symbol_type=None,
        source="lexical_file_full",
    )
