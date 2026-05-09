"""Almacén léxico remoto en PostgreSQL con full-text search (tsvector + pg_trgm)."""

from __future__ import annotations

import json
from typing import Any

import psycopg
from psycopg.rows import dict_row


class LexicalStore:
    """Indexación y búsqueda léxica de corpus de código usando PostgreSQL FTS."""

    def __init__(self, postgres_url: str, fts_language: str = "english") -> None:
        """Inicializa el store y garantiza que el esquema existe."""
        self._url = postgres_url
        self._lang = fts_language
        self._init_schema()

    def _connect(self) -> psycopg.Connection[dict[str, Any]]:
        """Abre conexión a Postgres con row_factory dict_row."""
        return psycopg.connect(self._url, row_factory=dict_row)

    def _init_schema(self) -> None:
        """Crea tabla lexical_corpus e índices si no existen."""
        with self._connect() as conn:
            # pg_trgm opcional: se activa si la extensión está disponible
            try:
                conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
            except Exception:
                pass

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS lexical_corpus (
                    id TEXT NOT NULL,
                    repo_id TEXT NOT NULL,
                    doc TEXT NOT NULL,
                    path TEXT,
                    symbol_name TEXT,
                    entity_type TEXT,
                    metadata TEXT,
                    fts_vector tsvector,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    PRIMARY KEY (repo_id, id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_lexical_fts "
                "ON lexical_corpus USING GIN (fts_vector)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_lexical_repo "
                "ON lexical_corpus (repo_id)"
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
        lang = self._lang
        rows = []
        for doc, meta in zip(docs, metadatas):
            doc_id = str(meta.get("id", ""))
            path = str(meta.get("path", "") or "")
            symbol_name = str(meta.get("symbol_name", "") or "")
            entity_type = str(meta.get("entity_type", "") or "")
            rows.append((
                doc_id,
                repo_id,
                doc,
                path,
                symbol_name,
                entity_type,
                json.dumps(meta, ensure_ascii=True),
                lang,
                symbol_name,
                lang,
                path,
                lang,
                doc,
            ))

        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO lexical_corpus (
                        id, repo_id, doc, path, symbol_name, entity_type,
                        metadata, fts_vector
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s,
                        setweight(to_tsvector(%s, coalesce(%s, '')), 'A') ||
                        setweight(to_tsvector(%s, coalesce(%s, '')), 'B') ||
                        setweight(to_tsvector(%s, coalesce(%s, '')), 'C')
                    )
                    ON CONFLICT (repo_id, id) DO UPDATE SET
                        doc = EXCLUDED.doc,
                        path = EXCLUDED.path,
                        symbol_name = EXCLUDED.symbol_name,
                        entity_type = EXCLUDED.entity_type,
                        metadata = EXCLUDED.metadata,
                        fts_vector = EXCLUDED.fts_vector
                    """,
                    rows,
                )

    def query(
        self,
        repo_id: str,
        text: str,
        top_n: int = 50,
    ) -> list[dict]:
        """Devuelve los documentos más relevantes para la consulta usando FTS.

        El shape de retorno es compatible con GLOBAL_BM25.query():
        [{"id": ..., "text": ..., "score": ..., "metadata": {...}}]
        """
        if not text.strip():
            return []
        lang = self._lang
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    id,
                    doc,
                    path,
                    symbol_name,
                    entity_type,
                    metadata,
                    ts_rank_cd(
                        fts_vector,
                        plainto_tsquery(%s, %s)
                    ) AS score
                FROM lexical_corpus
                WHERE
                    repo_id = %s
                    AND fts_vector @@ plainto_tsquery(%s, %s)
                ORDER BY score DESC
                LIMIT %s
                """,
                (lang, text, repo_id, lang, text, top_n),
            ).fetchall()

        results: list[dict] = []
        for row in rows:
            meta: dict = {}
            if row.get("metadata"):
                try:
                    loaded = json.loads(row["metadata"])
                    if isinstance(loaded, dict):
                        meta = loaded
                except Exception:
                    meta = {}
            results.append(
                {
                    "id": row["id"],
                    "text": row["doc"],
                    "score": float(row["score"]),
                    "metadata": meta,
                }
            )
        return results

    def has_corpus(self, repo_id: str) -> bool:
        """Indica si el repositorio tiene documentos indexados."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM lexical_corpus WHERE repo_id = %s LIMIT 1",
                (repo_id,),
            ).fetchone()
        return row is not None

    def delete_repo(self, repo_id: str) -> dict[str, int]:
        """Elimina todos los documentos del repositorio y retorna conteo."""
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM lexical_corpus WHERE repo_id = %s",
                (repo_id,),
            )
            deleted = int(cursor.rowcount or 0)
        return {"docs_removed": deleted}

    def delete_all(self) -> None:
        """Elimina todo el corpus léxico. Usar solo en reset global."""
        with self._connect() as conn:
            conn.execute("DELETE FROM lexical_corpus")
