"""Almacén léxico remoto en PostgreSQL con full-text search."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import bindparam, delete, func, literal, literal_column
from sqlalchemy import select, text as sql_text
from sqlalchemy.dialects.postgresql import JSONB, insert

from coderag.storage.postgres_schema import (
    POSTGRES_LEXICAL_CORPUS_TABLE_NAME,
    lexical_corpus_table,
)
from coderag.storage.postgres_session import PostgresSessionFactory


def _build_weighted_fts_vector() -> Any:
    """Compone el tsvector pesado que prioriza símbolo, path y contenido."""
    return (
        func.setweight(
            func.to_tsvector(
                bindparam("lang"),
                func.coalesce(bindparam("symbol_name"), literal("")),
            ),
            literal_column("'A'"),
        )
        .op("||")(
            func.setweight(
                func.to_tsvector(
                    bindparam("lang"),
                    func.coalesce(bindparam("path"), literal("")),
                ),
                literal_column("'B'"),
            )
        )
        .op("||")(
            func.setweight(
                func.to_tsvector(
                    bindparam("lang"),
                    func.coalesce(bindparam("doc"), literal("")),
                ),
                literal_column("'C'"),
            )
        )
    )


def _build_upsert_statement() -> Any:
    """Construye el upsert batch para el corpus léxico."""
    insert_stmt = insert(lexical_corpus_table).values(
        {
            "id": bindparam("id"),
            "repo_id": bindparam("repo_id"),
            "doc": bindparam("doc"),
            "path": bindparam("path"),
            "symbol_name": bindparam("symbol_name"),
            "entity_type": bindparam("entity_type"),
            "metadata": bindparam("metadata", type_=JSONB),
            "fts_vector": _build_weighted_fts_vector(),
        }
    )
    return insert_stmt.on_conflict_do_update(
        index_elements=[
            lexical_corpus_table.c.repo_id,
            lexical_corpus_table.c.id,
        ],
        set_={
            "doc": insert_stmt.excluded.doc,
            "path": insert_stmt.excluded.path,
            "symbol_name": insert_stmt.excluded.symbol_name,
            "entity_type": insert_stmt.excluded.entity_type,
            "metadata": insert_stmt.excluded["metadata"],
            "fts_vector": insert_stmt.excluded.fts_vector,
        },
    )


def _coerce_query_metadata(value: Any) -> dict[str, Any]:
    """Normaliza metadata leída desde JSONB o filas legacy serializadas."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            loaded = json.loads(value)
        except Exception:
            return {}
        return loaded if isinstance(loaded, dict) else {}
    return {}


_UPSERT_LEXICAL_DOCUMENTS = _build_upsert_statement()

_QUERY_LEXICAL_DOCUMENTS = sql_text(
    f"""
    SELECT
        id,
        doc,
        path,
        symbol_name,
        entity_type,
        metadata,
        ts_rank_cd(
            fts_vector,
            plainto_tsquery(:lang, :text)
        ) AS score
    FROM {POSTGRES_LEXICAL_CORPUS_TABLE_NAME}
    WHERE
        repo_id = :repo_id
        AND entity_type <> 'file_full'
        AND fts_vector @@ plainto_tsquery(:lang, :text)
    ORDER BY score DESC
    LIMIT :top_n
    """
)

_GET_LEXICAL_DOCUMENT_BY_PATH = sql_text(
    f"""
    SELECT id, doc, path, symbol_name, entity_type, metadata
    FROM {POSTGRES_LEXICAL_CORPUS_TABLE_NAME}
    WHERE repo_id = :repo_id AND path = :path AND entity_type = :entity_type
    LIMIT 1
    """
)

_GET_LEXICAL_SYMBOL_DOCUMENT = sql_text(
    f"""
    SELECT id, doc, path, symbol_name, entity_type, metadata
    FROM {POSTGRES_LEXICAL_CORPUS_TABLE_NAME}
    WHERE
        repo_id = :repo_id
        AND path = :path
        AND symbol_name = :symbol_name
        AND entity_type = 'symbol'
    LIMIT 1
    """
)


class LexicalStore:
    """Indexación y búsqueda léxica de corpus de código usando PostgreSQL FTS."""

    def __init__(
        self,
        postgres_dsn: str,
        fts_language: str = "english",
        *,
        session_factory: PostgresSessionFactory | None = None,
    ) -> None:
        """Inicializa el store usando la infraestructura compartida."""
        self._url = postgres_dsn
        self._lang = fts_language
        self._session_factory = session_factory or PostgresSessionFactory(
            postgres_dsn
        )

    def index_documents(
        self,
        repo_id: str,
        docs: list[str],
        metadatas: list[dict],
    ) -> None:
        """Indexa un corpus de documentos para un repositorio.

        Cada doc se almacena con su metadata y un vector tsvector pesado:
        - 'A' (mayor peso): symbol_name
        - 'B': path
        - 'C': contenido del documento
        """
        if not docs:
            return
        rows: list[dict[str, Any]] = []
        for doc, meta in zip(docs, metadatas):
            doc_id = str(meta.get("id", ""))
            path = str(meta.get("path", "") or "")
            symbol_name = str(meta.get("symbol_name", "") or "")
            entity_type = str(meta.get("entity_type", "") or "")
            rows.append(
                {
                    "id": doc_id,
                    "repo_id": repo_id,
                    "doc": doc,
                    "path": path,
                    "symbol_name": symbol_name,
                    "entity_type": entity_type,
                    "metadata": dict(meta),
                    "lang": self._lang,
                }
            )

        with self._session_factory.get_connection() as connection:
            connection.execute(_UPSERT_LEXICAL_DOCUMENTS, rows)

    def query(
        self,
        repo_id: str,
        text: str,
        top_n: int = 50,
    ) -> list[dict]:
        """Devuelve los documentos más relevantes para la consulta usando FTS.

        El shape de retorno es compatible con el contrato léxico legacy:
        [{"id": ..., "text": ..., "score": ..., "metadata": {...}}]
        """
        if not text.strip():
            return []
        with self._session_factory.get_connection() as connection:
            rows = connection.execute(
                _QUERY_LEXICAL_DOCUMENTS,
                {
                    "lang": self._lang,
                    "text": text,
                    "repo_id": repo_id,
                    "top_n": top_n,
                },
            ).mappings().all()

        results: list[dict] = []
        for row in rows:
            meta = _coerce_query_metadata(row.get("metadata"))
            results.append(
                {
                    "id": row["id"],
                    "text": row["doc"],
                    "score": float(row["score"]),
                    "metadata": meta,
                }
            )
        return results

    def get_file_document(self, repo_id: str, path: str) -> dict | None:
        """Recupera el contenido íntegro persistido de un archivo (sin FTS ranking)."""
        with self._session_factory.get_connection() as connection:
            row = connection.execute(
                _GET_LEXICAL_DOCUMENT_BY_PATH,
                {"repo_id": repo_id, "path": path, "entity_type": "file_full"},
            ).mappings().first()
        if row is None:
            return None
        return {
            "id": row["id"],
            "text": row["doc"],
            "metadata": _coerce_query_metadata(row.get("metadata")),
        }

    def get_symbol_document(
        self,
        repo_id: str,
        path: str,
        symbol_name: str,
    ) -> dict | None:
        """Recupera el snippet persistido de un símbolo exacto (sin FTS ranking)."""
        with self._session_factory.get_connection() as connection:
            row = connection.execute(
                _GET_LEXICAL_SYMBOL_DOCUMENT,
                {"repo_id": repo_id, "path": path, "symbol_name": symbol_name},
            ).mappings().first()
        if row is None:
            return None
        return {
            "id": row["id"],
            "text": row["doc"],
            "metadata": _coerce_query_metadata(row.get("metadata")),
        }

    def has_corpus(self, repo_id: str) -> bool:
        """Indica si el repositorio tiene documentos indexados."""
        statement = (
            select(literal(1))
            .select_from(lexical_corpus_table)
            .where(lexical_corpus_table.c.repo_id == repo_id)
            .limit(1)
        )
        with self._session_factory.get_connection() as connection:
            row = connection.execute(statement).first()
        return row is not None

    def delete_repo(self, repo_id: str) -> dict[str, int]:
        """Elimina todos los documentos del repositorio y retorna conteo."""
        statement = delete(lexical_corpus_table).where(
            lexical_corpus_table.c.repo_id == repo_id
        )
        with self._session_factory.get_connection() as connection:
            result = connection.execute(statement)
            deleted = int(result.rowcount or 0)
        return {"docs_removed": deleted}

    def delete_by_repo_and_paths(
        self,
        repo_id: str,
        paths: list[str],
    ) -> dict[str, int]:
        """Elimina documentos del repo acotados a un set de paths y retorna conteo.

        Solo afecta filas cuya columna ``path`` coincide con los paths dados
        (símbolos y archivos). Los docs de módulo tienen ``path`` con el nombre de
        módulo y se gestionan por separado en el pipeline (recompute).
        """
        unique_paths = list(dict.fromkeys(p for p in paths if p))
        if not unique_paths:
            return {"docs_removed": 0}
        statement = delete(lexical_corpus_table).where(
            lexical_corpus_table.c.repo_id == repo_id,
            lexical_corpus_table.c.path.in_(unique_paths),
        )
        with self._session_factory.get_connection() as connection:
            result = connection.execute(statement)
            deleted = int(result.rowcount or 0)
        return {"docs_removed": deleted}

    def delete_all(self) -> None:
        """Elimina todo el corpus léxico. Usar solo en reset global."""
        with self._session_factory.get_connection() as connection:
            connection.execute(delete(lexical_corpus_table))
