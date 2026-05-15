"""Pruebas unitarias para LexicalStore (PostgreSQL FTS).

No requieren base de datos real: psycopg.connect se parchea con MagicMock.
Si psycopg no está instalado en el entorno de prueba (solo disponible en
Docker/CI), se inyecta un stub en sys.modules antes de importar el módulo.
"""

from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock, patch

import pytest

from coderag.storage.postgres_table_names import (
    POSTGRES_LEXICAL_CORPUS_TABLE,
)

# ---------------------------------------------------------------------------
# Stub psycopg solo si el import real no está disponible en el entorno
# ---------------------------------------------------------------------------
try:
    import psycopg  # noqa: F401
except ModuleNotFoundError:
    _psycopg_stub = MagicMock()
    _psycopg_rows_stub = MagicMock()
    _psycopg_rows_stub.dict_row = MagicMock()
    _psycopg_stub.rows = _psycopg_rows_stub
    sys.modules["psycopg"] = _psycopg_stub
    sys.modules["psycopg.rows"] = _psycopg_rows_stub
    sys.modules["psycopg.rows.dict_row"] = _psycopg_rows_stub.dict_row

# ---------------------------------------------------------------------------
# Helpers para construir mocks de conexión psycopg
# ---------------------------------------------------------------------------

_PATCH_CONNECT = "coderag.storage.lexical_store.psycopg.connect"


def _cursor(rows=None, rowcount: int = 0) -> MagicMock:
    """Devuelve un cursor mock con fetchall/fetchone configurados."""
    c = MagicMock()
    c.fetchall.return_value = list(rows or [])
    c.fetchone.return_value = (rows[0] if rows else None)
    c.rowcount = rowcount
    return c


def _conn(cursor: MagicMock | None = None) -> MagicMock:
    """Devuelve un objeto conexión mock que soporta context manager.

    MagicMock gestiona dunders en la clase del objeto. La forma correcta
    de configurar el context manager es via .return_value.
    """
    conn = MagicMock()
    # with conn as c: → c = conn.__enter__() → necesitamos que retorne conn
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    if cursor is not None:
        conn.execute.return_value = cursor
        conn.cursor.return_value.__enter__.return_value = cursor
        conn.cursor.return_value.__exit__.return_value = False
    return conn


def _make_store(language: str = "english") -> tuple:
    """Crea LexicalStore con psycopg parcheado; retorna (store, init_conn)."""
    from coderag.storage.lexical_store import LexicalStore

    init_conn = _conn(_cursor())
    with patch(_PATCH_CONNECT, return_value=init_conn):
        store = LexicalStore("postgresql://fake/db", fts_language=language)
    return store, init_conn


# ===========================================================================
# __init__ / _init_schema
# ===========================================================================


class TestInitSchema:
    def test_crea_tabla_e_indices(self):
        """_init_schema ejecuta CREATE TABLE y CREATE INDEX correctamente."""
        store, init_conn = _make_store()

        calls_sql = [str(c.args[0]) for c in init_conn.execute.call_args_list]
        assert any(POSTGRES_LEXICAL_CORPUS_TABLE in s for s in calls_sql)
        assert any("idx_lexical_fts" in s for s in calls_sql)
        assert any("idx_lexical_repo" in s for s in calls_sql)

    def test_intenta_crear_extension_pg_trgm(self):
        """_init_schema intenta activar pg_trgm (lo ignora si falla)."""
        store, init_conn = _make_store()

        first_call_sql = init_conn.execute.call_args_list[0].args[0]
        assert "pg_trgm" in str(first_call_sql)

    def test_no_falla_si_pg_trgm_no_disponible(self):
        """_init_schema continúa aunque pg_trgm lance excepción."""
        from coderag.storage.lexical_store import LexicalStore

        init_conn = _conn(_cursor())

        def execute_side_effect(sql, *args):
            if "pg_trgm" in str(sql):
                raise Exception("extension not available")
            return _cursor()

        init_conn.execute.side_effect = execute_side_effect
        # No debe propagar excepción
        with patch(_PATCH_CONNECT, return_value=init_conn):
            LexicalStore("postgresql://fake/db")


# ===========================================================================
# index_documents
# ===========================================================================


class TestIndexDocuments:
    def test_indexa_documentos_llamando_executemany(self):
        """index_documents llama a executemany con las filas correctas."""
        store, _ = _make_store()
        test_cursor = _cursor()
        test_conn = _conn(test_cursor)

        docs = ["def foo(): pass", "class Bar: pass"]
        metas = [
            {"id": "r1:foo", "repo_id": "r1", "path": "src/foo.py",
             "symbol_name": "foo", "entity_type": "symbol"},
            {"id": "r1:Bar", "repo_id": "r1", "path": "src/bar.py",
             "symbol_name": "Bar", "entity_type": "symbol"},
        ]

        with patch(_PATCH_CONNECT, return_value=test_conn):
            store.index_documents(repo_id="r1", docs=docs, metadatas=metas)

        test_cursor.executemany.assert_called_once()
        _, rows = test_cursor.executemany.call_args.args
        assert len(rows) == 2
        assert rows[0][0] == "r1:foo"
        assert rows[0][1] == "r1"

    def test_no_llama_executemany_con_lista_vacia(self):
        """index_documents es no-op cuando docs está vacío."""
        store, _ = _make_store()
        test_cursor = _cursor()
        test_conn = _conn(test_cursor)

        with patch(_PATCH_CONNECT, return_value=test_conn):
            store.index_documents(repo_id="r1", docs=[], metadatas=[])

        test_cursor.executemany.assert_not_called()

    def test_metadata_json_serializada_correctamente(self):
        """El campo metadata se serializa como JSON string."""
        store, _ = _make_store()
        test_cursor = _cursor()
        test_conn = _conn(test_cursor)

        meta = {"id": "r1:x", "path": "x.py", "symbol_name": "x",
                "entity_type": "file", "custom_key": "valor"}
        with patch(_PATCH_CONNECT, return_value=test_conn):
            store.index_documents(repo_id="r1", docs=["texto"], metadatas=[meta])

        _, rows = test_cursor.executemany.call_args.args
        json_str = rows[0][6]  # posición 6 = metadata
        parsed = json.loads(json_str)
        assert parsed["custom_key"] == "valor"

    def test_campos_opcionales_faltantes_usan_string_vacio(self):
        """Metadata sin path/symbol_name no lanza KeyError."""
        store, _ = _make_store()
        test_cursor = _cursor()
        test_conn = _conn(test_cursor)

        meta = {"id": "r1:m"}  # sin path ni symbol_name
        with patch(_PATCH_CONNECT, return_value=test_conn):
            store.index_documents(repo_id="r1", docs=["modulo"], metadatas=[meta])

        _, rows = test_cursor.executemany.call_args.args
        assert rows[0][3] == ""   # path vacío
        assert rows[0][4] == ""   # symbol_name vacío


# ===========================================================================
# query
# ===========================================================================


def _make_row(doc_id: str, doc: str, score: float, meta: dict) -> dict:
    return {
        "id": doc_id,
        "doc": doc,
        "path": meta.get("path", ""),
        "symbol_name": meta.get("symbol_name", ""),
        "entity_type": meta.get("entity_type", ""),
        "metadata": json.dumps(meta),
        "score": score,
    }


class TestQuery:
    def test_devuelve_lista_con_shape_compatible_bm25(self):
        """query retorna lista de dicts con keys id, text, score, metadata."""
        meta = {"id": "r1:foo", "path": "foo.py"}
        row = _make_row("r1:foo", "def foo(): pass", 0.85, meta)
        store, _ = _make_store()
        test_conn = _conn(_cursor(rows=[row]))

        with patch(_PATCH_CONNECT, return_value=test_conn):
            results = store.query(repo_id="r1", text="foo")

        assert len(results) == 1
        item = results[0]
        assert item["id"] == "r1:foo"
        assert item["text"] == "def foo(): pass"
        assert isinstance(item["score"], float)
        assert isinstance(item["metadata"], dict)
        assert item["metadata"]["path"] == "foo.py"

    def test_texto_vacio_retorna_lista_vacia_sin_consulta(self):
        """query con texto vacío retorna [] sin llamar a execute."""
        store, _ = _make_store()
        test_conn = _conn(_cursor())

        with patch(_PATCH_CONNECT, return_value=test_conn):
            results = store.query(repo_id="r1", text="   ")

        assert results == []
        test_conn.execute.assert_not_called()

    def test_metadata_invalida_retorna_dict_vacio(self):
        """Metadata con JSON corrupto se trata como dict vacío."""
        row = {
            "id": "r1:bad",
            "doc": "texto",
            "path": "",
            "symbol_name": "",
            "entity_type": "",
            "metadata": "NO_JSON{{{",
            "score": 0.5,
        }
        store, _ = _make_store()
        test_conn = _conn(_cursor(rows=[row]))

        with patch(_PATCH_CONNECT, return_value=test_conn):
            results = store.query(repo_id="r1", text="texto")

        assert results[0]["metadata"] == {}

    def test_sin_resultados_retorna_lista_vacia(self):
        """query con 0 resultados devuelve []."""
        store, _ = _make_store()
        test_conn = _conn(_cursor(rows=[]))

        with patch(_PATCH_CONNECT, return_value=test_conn):
            results = store.query(repo_id="r1", text="xyz irrelevante")

        assert results == []

    def test_pasa_top_n_a_sql(self):
        """El parámetro top_n se pasa como LIMIT en la consulta SQL."""
        store, _ = _make_store()
        test_conn = _conn(_cursor(rows=[]))

        with patch(_PATCH_CONNECT, return_value=test_conn):
            store.query(repo_id="r1", text="algo", top_n=7)

        sql, params = test_conn.execute.call_args.args
        assert 7 in params

    def test_pasa_repo_id_a_sql(self):
        """El repo_id se incluye en los parámetros de la consulta."""
        store, _ = _make_store()
        test_conn = _conn(_cursor(rows=[]))

        with patch(_PATCH_CONNECT, return_value=test_conn):
            store.query(repo_id="mi-repo", text="algo")

        _, params = test_conn.execute.call_args.args
        assert "mi-repo" in params


# ===========================================================================
# has_corpus
# ===========================================================================


class TestHasCorpus:
    def test_retorna_true_cuando_hay_fila(self):
        """has_corpus devuelve True cuando fetchone retorna un resultado."""
        store, _ = _make_store()
        test_conn = _conn(_cursor(rows=[{"id": "r1:x"}]))

        with patch(_PATCH_CONNECT, return_value=test_conn):
            assert store.has_corpus("r1") is True

    def test_retorna_false_cuando_no_hay_fila(self):
        """has_corpus devuelve False cuando fetchone retorna None."""
        store, _ = _make_store()
        test_conn = _conn(_cursor(rows=[]))

        with patch(_PATCH_CONNECT, return_value=test_conn):
            assert store.has_corpus("inexistente") is False


# ===========================================================================
# delete_repo
# ===========================================================================


class TestDeleteRepo:
    def test_retorna_conteo_de_filas_eliminadas(self):
        """delete_repo retorna {'docs_removed': N} según rowcount."""
        store, _ = _make_store()
        test_conn = _conn(_cursor(rowcount=5))

        with patch(_PATCH_CONNECT, return_value=test_conn):
            result = store.delete_repo("r1")

        assert result == {"docs_removed": 5}

    def test_pasa_repo_id_a_delete(self):
        """delete_repo incluye el repo_id en el WHERE de la sentencia SQL."""
        store, _ = _make_store()
        test_conn = _conn(_cursor(rowcount=0))

        with patch(_PATCH_CONNECT, return_value=test_conn):
            store.delete_repo("mi-repo")

        delete_call_params = test_conn.execute.call_args.args[1]
        assert "mi-repo" in delete_call_params

    def test_retorna_cero_si_rowcount_es_none(self):
        """delete_repo maneja rowcount=None devolviendo 0."""
        store, _ = _make_store()
        test_conn = _conn(_cursor(rowcount=None))

        with patch(_PATCH_CONNECT, return_value=test_conn):
            result = store.delete_repo("vacio")

        assert result["docs_removed"] == 0


# ===========================================================================
# delete_all
# ===========================================================================


class TestDeleteAll:
    def test_ejecuta_delete_sin_where(self):
        """delete_all ejecuta DELETE FROM lexical_corpus sin WHERE."""
        store, _ = _make_store()
        test_conn = _conn(_cursor())

        with patch(_PATCH_CONNECT, return_value=test_conn):
            store.delete_all()

        sql = test_conn.execute.call_args.args[0]
        assert f"DELETE FROM {POSTGRES_LEXICAL_CORPUS_TABLE}" in str(sql)
        assert "WHERE" not in str(sql)


# ===========================================================================
# Idioma FTS
# ===========================================================================


class TestFtsLanguage:
    def test_idioma_personalizado_se_pasa_a_query(self):
        """El fts_language configurable se usa en plainto_tsquery."""
        store, _ = _make_store(language="spanish")
        test_conn = _conn(_cursor(rows=[]))

        with patch(_PATCH_CONNECT, return_value=test_conn):
            store.query(repo_id="r1", text="función")

        _, params = test_conn.execute.call_args.args
        assert "spanish" in params

    def test_idioma_personalizado_se_pasa_a_index_documents(self):
        """El fts_language se pasa como argumento a to_tsvector en INSERT."""
        store, _ = _make_store(language="spanish")
        test_cursor = _cursor()
        test_conn = _conn(test_cursor)

        meta = {"id": "r:1", "path": "a.py", "symbol_name": "fn",
                "entity_type": "symbol"}
        with patch(_PATCH_CONNECT, return_value=test_conn):
            store.index_documents(repo_id="r", docs=["código"], metadatas=[meta])

        _, rows = test_cursor.executemany.call_args.args
        # lang aparece 3 veces (para A, B, C weight) en la tupla de params
        lang_count = sum(1 for v in rows[0] if v == "spanish")
        assert lang_count == 3
