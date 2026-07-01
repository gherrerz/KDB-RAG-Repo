"""Pruebas unitarias para LexicalStore sobre SQLAlchemy compartido."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from sqlalchemy.dialects import postgresql

from coderag.storage.postgres_schema import POSTGRES_LEXICAL_CORPUS_TABLE_NAME


def _result(*, rows=None, first=None, rowcount: int = 0) -> MagicMock:
    """Construye un resultado mock compatible con query/select/delete."""
    result = MagicMock()
    result.mappings.return_value.all.return_value = list(rows or [])
    result.first.return_value = first
    result.rowcount = rowcount
    return result


def _session_factory_mock(connection: MagicMock) -> MagicMock:
    """Construye un session factory mock con context manager de conexión."""
    factory = MagicMock()
    factory.get_connection.return_value.__enter__.return_value = connection
    factory.get_connection.return_value.__exit__.return_value = False
    return factory


def _make_store(language: str = "english"):
    """Crea LexicalStore con session factory mock para aislar el test."""
    from coderag.storage.lexical_store import LexicalStore

    connection = MagicMock()
    session_factory = _session_factory_mock(connection)
    store = LexicalStore(
        "postgresql://fake/db",
        fts_language=language,
        session_factory=session_factory,
    )
    return store, connection, session_factory


# ===========================================================================
# __init__
# ===========================================================================


class TestInitSchema:
    def test_constructor_builds_default_session_factory(self):
        """Sin inyección explícita, el store crea su factory por defecto."""
        from coderag.storage.lexical_store import LexicalStore

        factory_instance = MagicMock()
        factory_instance.get_connection.return_value.__enter__.return_value = (
            MagicMock()
        )
        factory_instance.get_connection.return_value.__exit__.return_value = (
            False
        )

        with patch(
            "coderag.storage.lexical_store.PostgresSessionFactory"
        ) as factory_class:
            factory_class.return_value = factory_instance
            store = LexicalStore("postgresql://fake/db")

        factory_class.assert_called_once_with("postgresql://fake/db")
        assert store._session_factory is factory_instance


# ===========================================================================
# index_documents
# ===========================================================================


class TestIndexDocuments:
    def test_indexa_documentos_con_upsert_batch(self):
        """index_documents ejecuta un upsert batch con SQLAlchemy."""
        store, connection, _ = _make_store()

        docs = ["def foo(): pass", "class Bar: pass"]
        metas = [
            {"id": "r1:foo", "repo_id": "r1", "path": "src/foo.py",
             "symbol_name": "foo", "entity_type": "symbol"},
            {"id": "r1:Bar", "repo_id": "r1", "path": "src/bar.py",
             "symbol_name": "Bar", "entity_type": "symbol"},
        ]

        store.index_documents(repo_id="r1", docs=docs, metadatas=metas)

        statement, rows = connection.execute.call_args.args
        compiled = statement.compile(dialect=postgresql.dialect())
        assert "ON CONFLICT (repo_id, id) DO UPDATE" in str(compiled)
        assert "to_tsvector" in str(compiled)
        assert len(rows) == 2
        assert rows[0]["id"] == "r1:foo"
        assert rows[0]["repo_id"] == "r1"

    def test_no_ejecuta_sql_con_lista_vacia(self):
        """index_documents es no-op cuando docs está vacío."""
        store, connection, _ = _make_store()

        store.index_documents(repo_id="r1", docs=[], metadatas=[])

        connection.execute.assert_not_called()

    def test_metadata_dict_se_envia_como_jsonb_tipado(self):
        """La metadata se envía como dict para persistencia JSONB."""
        store, connection, _ = _make_store()

        meta = {"id": "r1:x", "path": "x.py", "symbol_name": "x",
                "entity_type": "file", "custom_key": "valor"}
        store.index_documents(repo_id="r1", docs=["texto"], metadatas=[meta])

        _, rows = connection.execute.call_args.args
        assert rows[0]["metadata"]["custom_key"] == "valor"

    def test_campos_opcionales_faltantes_usan_string_vacio(self):
        """Metadata sin path/symbol_name no lanza KeyError."""
        store, connection, _ = _make_store()

        meta = {"id": "r1:m"}  # sin path ni symbol_name
        store.index_documents(repo_id="r1", docs=["modulo"], metadatas=[meta])

        _, rows = connection.execute.call_args.args
        assert rows[0]["path"] == ""
        assert rows[0]["symbol_name"] == ""


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
        store, connection, _ = _make_store()
        connection.execute.return_value = _result(rows=[row])

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
        store, connection, _ = _make_store()

        results = store.query(repo_id="r1", text="   ")

        assert results == []
        connection.execute.assert_not_called()

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
        store, connection, _ = _make_store()
        connection.execute.return_value = _result(rows=[row])

        results = store.query(repo_id="r1", text="texto")

        assert results[0]["metadata"] == {}

    def test_sin_resultados_retorna_lista_vacia(self):
        """query con 0 resultados devuelve []."""
        store, connection, _ = _make_store()
        connection.execute.return_value = _result(rows=[])

        results = store.query(repo_id="r1", text="xyz irrelevante")

        assert results == []

    def test_pasa_top_n_a_sql(self):
        """El parámetro top_n se pasa como LIMIT en la consulta SQL."""
        store, connection, _ = _make_store()
        connection.execute.return_value = _result(rows=[])

        store.query(repo_id="r1", text="algo", top_n=7)

        _, params = connection.execute.call_args.args
        assert params["top_n"] == 7

    def test_pasa_repo_id_a_sql(self):
        """El repo_id se incluye en los parámetros de la consulta."""
        store, connection, _ = _make_store()
        connection.execute.return_value = _result(rows=[])

        store.query(repo_id="mi-repo", text="algo")

        _, params = connection.execute.call_args.args
        assert params["repo_id"] == "mi-repo"


# ===========================================================================
# query excluye entity_type=file_full del ranking general
# ===========================================================================


class TestQueryExcludesFileFull:
    def test_sql_excluye_entity_type_file_full(self):
        """El FTS general no debe rankear documentos de archivo completo."""
        from coderag.storage.lexical_store import _QUERY_LEXICAL_DOCUMENTS

        assert "entity_type <> 'file_full'" in str(_QUERY_LEXICAL_DOCUMENTS)


# ===========================================================================
# get_file_document / get_symbol_document
# ===========================================================================


class TestGetFileDocument:
    def test_recupera_contenido_integro_por_path_exacto(self):
        """get_file_document recupera el documento file_full por path exacto."""
        store, connection, _ = _make_store()
        row = {
            "id": "r1:file_full:src/foo.py",
            "doc": "print('hola')\n",
            "path": "src/foo.py",
            "symbol_name": "",
            "entity_type": "file_full",
            "metadata": {"path": "src/foo.py", "start_line": 1, "end_line": 1},
        }
        result = MagicMock()
        result.mappings.return_value.first.return_value = row
        connection.execute.return_value = result

        document = store.get_file_document("r1", "src/foo.py")

        assert document == {
            "id": "r1:file_full:src/foo.py",
            "text": "print('hola')\n",
            "metadata": {"path": "src/foo.py", "start_line": 1, "end_line": 1},
        }
        _, params = connection.execute.call_args.args
        assert params == {
            "repo_id": "r1",
            "path": "src/foo.py",
            "entity_type": "file_full",
        }

    def test_retorna_none_si_no_existe(self):
        """get_file_document retorna None cuando no hay fila."""
        store, connection, _ = _make_store()
        result = MagicMock()
        result.mappings.return_value.first.return_value = None
        connection.execute.return_value = result

        assert store.get_file_document("r1", "src/missing.py") is None


class TestGetSymbolDocument:
    def test_recupera_snippet_exacto_de_simbolo(self):
        """get_symbol_document filtra por path y symbol_name exactos."""
        store, connection, _ = _make_store()
        row = {
            "id": "r1:sym",
            "doc": "def foo(): pass",
            "path": "src/foo.py",
            "symbol_name": "foo",
            "entity_type": "symbol",
            "metadata": {"symbol_name": "foo", "start_line": 1, "end_line": 1},
        }
        result = MagicMock()
        result.mappings.return_value.first.return_value = row
        connection.execute.return_value = result

        document = store.get_symbol_document("r1", "src/foo.py", "foo")

        assert document["text"] == "def foo(): pass"
        _, params = connection.execute.call_args.args
        assert params == {
            "repo_id": "r1",
            "path": "src/foo.py",
            "symbol_name": "foo",
        }

    def test_retorna_none_si_no_existe(self):
        """get_symbol_document retorna None cuando no hay fila."""
        store, connection, _ = _make_store()
        result = MagicMock()
        result.mappings.return_value.first.return_value = None
        connection.execute.return_value = result

        assert store.get_symbol_document("r1", "src/foo.py", "missing") is None


# ===========================================================================
# has_corpus
# ===========================================================================


class TestHasCorpus:
    def test_retorna_true_cuando_hay_fila(self):
        """has_corpus devuelve True cuando fetchone retorna un resultado."""
        store, connection, _ = _make_store()
        connection.execute.return_value = _result(first=object())

        assert store.has_corpus("r1") is True

    def test_retorna_false_cuando_no_hay_fila(self):
        """has_corpus devuelve False cuando fetchone retorna None."""
        store, connection, _ = _make_store()
        connection.execute.return_value = _result(first=None)

        assert store.has_corpus("inexistente") is False


# ===========================================================================
# delete_repo
# ===========================================================================


class TestDeleteRepo:
    def test_retorna_conteo_de_filas_eliminadas(self):
        """delete_repo retorna {'docs_removed': N} según rowcount."""
        store, connection, _ = _make_store()
        connection.execute.return_value = _result(rowcount=5)

        result = store.delete_repo("r1")

        assert result == {"docs_removed": 5}

    def test_pasa_repo_id_a_delete(self):
        """delete_repo incluye el repo_id en el WHERE de la sentencia SQL."""
        store, connection, _ = _make_store()
        connection.execute.return_value = _result(rowcount=0)

        store.delete_repo("mi-repo")

        statement = connection.execute.call_args.args[0]
        compiled = statement.compile(dialect=postgresql.dialect())
        assert "mi-repo" in compiled.params.values()

    def test_retorna_cero_si_rowcount_es_none(self):
        """delete_repo maneja rowcount=None devolviendo 0."""
        store, connection, _ = _make_store()
        connection.execute.return_value = _result(rowcount=None)

        result = store.delete_repo("vacio")

        assert result["docs_removed"] == 0


# ===========================================================================
# delete_all
# ===========================================================================


class TestDeleteAll:
    def test_ejecuta_delete_sin_where(self):
        """delete_all ejecuta DELETE FROM lexical_corpus sin WHERE."""
        store, connection, _ = _make_store()
        connection.execute.return_value = _result()

        store.delete_all()

        statement = connection.execute.call_args.args[0]
        compiled = statement.compile(dialect=postgresql.dialect())
        assert f"DELETE FROM {POSTGRES_LEXICAL_CORPUS_TABLE_NAME}" in str(compiled)
        assert "WHERE" not in str(compiled)


# ===========================================================================
# Idioma FTS
# ===========================================================================


class TestFtsLanguage:
    def test_idioma_personalizado_se_pasa_a_query(self):
        """El fts_language configurable se usa en plainto_tsquery."""
        store, connection, _ = _make_store(language="spanish")
        connection.execute.return_value = _result(rows=[])

        store.query(repo_id="r1", text="función")

        _, params = connection.execute.call_args.args
        assert params["lang"] == "spanish"

    def test_idioma_personalizado_se_pasa_a_index_documents(self):
        """El fts_language se pasa como argumento a to_tsvector en INSERT."""
        store, connection, _ = _make_store(language="spanish")

        meta = {"id": "r:1", "path": "a.py", "symbol_name": "fn",
                "entity_type": "symbol"}
        store.index_documents(repo_id="r", docs=["código"], metadatas=[meta])

        _, rows = connection.execute.call_args.args
        assert rows[0]["lang"] == "spanish"
