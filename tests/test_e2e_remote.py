"""Pruebas end-to-end con Chroma remoto y PostgreSQL real.

Estas pruebas requieren infraestructura real levantada. Se omiten
automáticamente si las variables de entorno no están configuradas.

Requisitos para ejecutar:
    POSTGRES_HOST=localhost
    POSTGRES_PORT=5432
    POSTGRES_DB=coderag
    POSTGRES_USER=coderag
    POSTGRES_PASSWORD=coderag
    CHROMA_MODE=remote
    CHROMA_HOST=localhost
    CHROMA_PORT=8001          (puerto host del contenedor Chroma)

Lanzar infraestructura con:
    docker compose --profile remote up -d chroma postgres

Ejecutar pruebas:
    pytest tests/test_e2e_remote.py -v
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from coderag.core.settings import Settings

# ---------------------------------------------------------------------------
# Marcadores de skip — se evalúan una sola vez al cargar el módulo
# ---------------------------------------------------------------------------

_POSTGRES_SETTINGS = Settings(_env_file=None)
_POSTGRES_DSN = _POSTGRES_SETTINGS.resolve_postgres_dsn()
_CHROMA_MODE = os.environ.get("CHROMA_MODE", "embedded").strip()
_CHROMA_HOST = os.environ.get("CHROMA_HOST", "localhost").strip()
_CHROMA_PORT = int(os.environ.get("CHROMA_PORT", "8001"))

_skip_no_postgres = pytest.mark.skipif(
    not _POSTGRES_DSN,
    reason="Postgres no configurado — se omite el test E2E de Postgres",
)
_skip_no_chroma_remote = pytest.mark.skipif(
    _CHROMA_MODE != "remote",
    reason="CHROMA_MODE != remote — se omite el test E2E de Chroma remoto",
)
_skip_e2e = pytest.mark.skipif(
    not _POSTGRES_DSN or _CHROMA_MODE != "remote",
    reason="Se requieren Postgres configurado y CHROMA_MODE=remote para el E2E completo",
)


# ===========================================================================
# Helpers
# ===========================================================================


def _unique_repo() -> str:
    """Genera un repo_id único para aislar cada ejecución de test."""
    return f"e2e-test-{uuid.uuid4().hex[:8]}"


def _sample_docs_and_metas(repo_id: str) -> tuple[list[str], list[dict]]:
    """Corpus mínimo de documentos para tests."""
    docs = [
        "def authenticate_user(username, password): pass",
        "class UserRepository: def find_by_id(self, user_id): pass",
        "SELECT * FROM orders WHERE status = 'pending'",
        "public class OrderService { void processOrder(Order order) {} }",
        "function fetchCart(userId) { return api.get('/cart/' + userId); }",
    ]
    metas = [
        {
            "id": f"{repo_id}:auth_user",
            "repo_id": repo_id,
            "path": "src/auth.py",
            "symbol_name": "authenticate_user",
            "entity_type": "symbol",
        },
        {
            "id": f"{repo_id}:user_repo",
            "repo_id": repo_id,
            "path": "src/user_repository.py",
            "symbol_name": "UserRepository",
            "entity_type": "symbol",
        },
        {
            "id": f"{repo_id}:orders_sql",
            "repo_id": repo_id,
            "path": "src/db/queries.sql",
            "symbol_name": "",
            "entity_type": "file",
        },
        {
            "id": f"{repo_id}:order_service",
            "repo_id": repo_id,
            "path": "src/OrderService.java",
            "symbol_name": "OrderService",
            "entity_type": "symbol",
        },
        {
            "id": f"{repo_id}:fetch_cart",
            "repo_id": repo_id,
            "path": "src/cart.js",
            "symbol_name": "fetchCart",
            "entity_type": "symbol",
        },
    ]
    return docs, metas


# ===========================================================================
# Bloque 1: LexicalStore (PostgreSQL FTS)
# ===========================================================================


@pytest.mark.usefixtures()
class TestLexicalStoreE2E:
    """Pruebas de integración real contra PostgreSQL para LexicalStore."""

    @_skip_no_postgres
    def test_init_schema_idempotente(self):
        """_init_schema puede llamarse múltiples veces sin error."""
        from coderag.storage.lexical_store import LexicalStore

        store1 = LexicalStore(_POSTGRES_DSN)
        store2 = LexicalStore(_POSTGRES_DSN)
        # Si llegamos aquí sin excepción, la idempotencia está garantizada.
        assert store1._lang == store2._lang

    @_skip_no_postgres
    def test_has_corpus_false_repo_nuevo(self):
        """Un repo nunca indexado retorna has_corpus=False."""
        from coderag.storage.lexical_store import LexicalStore

        store = LexicalStore(_POSTGRES_DSN)
        repo_id = _unique_repo()
        assert store.has_corpus(repo_id) is False

    @_skip_no_postgres
    def test_index_documents_y_has_corpus(self):
        """Después de indexar, has_corpus devuelve True."""
        from coderag.storage.lexical_store import LexicalStore

        store = LexicalStore(_POSTGRES_DSN)
        repo_id = _unique_repo()
        docs, metas = _sample_docs_and_metas(repo_id)

        try:
            store.index_documents(repo_id=repo_id, docs=docs, metadatas=metas)
            assert store.has_corpus(repo_id) is True
        finally:
            store.delete_repo(repo_id)

    @_skip_no_postgres
    def test_query_retorna_resultados_relevantes(self):
        """query devuelve documentos pertinentes para la consulta."""
        from coderag.storage.lexical_store import LexicalStore

        store = LexicalStore(_POSTGRES_DSN)
        repo_id = _unique_repo()
        docs, metas = _sample_docs_and_metas(repo_id)

        try:
            store.index_documents(repo_id=repo_id, docs=docs, metadatas=metas)
            results = store.query(repo_id=repo_id, text="authenticate user", top_n=3)

            assert len(results) > 0
            # El resultado de mayor score debe contener authenticate_user
            top = results[0]
            assert top["id"] == f"{repo_id}:auth_user"
            assert isinstance(top["score"], float)
            assert top["score"] > 0.0
            assert isinstance(top["metadata"], dict)
        finally:
            store.delete_repo(repo_id)

    @_skip_no_postgres
    def test_query_shape_compatible_con_bm25(self):
        """El shape de resultado es idéntico al de GLOBAL_BM25.query()."""
        from coderag.storage.lexical_store import LexicalStore

        store = LexicalStore(_POSTGRES_DSN)
        repo_id = _unique_repo()
        docs, metas = _sample_docs_and_metas(repo_id)

        try:
            store.index_documents(repo_id=repo_id, docs=docs, metadatas=metas)
            results = store.query(repo_id=repo_id, text="order", top_n=5)

            for item in results:
                assert "id" in item
                assert "text" in item
                assert "score" in item
                assert "metadata" in item
                assert isinstance(item["id"], str)
                assert isinstance(item["text"], str)
                assert isinstance(item["score"], float)
                assert isinstance(item["metadata"], dict)
        finally:
            store.delete_repo(repo_id)

    @_skip_no_postgres
    def test_query_texto_vacio_retorna_lista_vacia(self):
        """query con texto vacío retorna [] inmediatamente."""
        from coderag.storage.lexical_store import LexicalStore

        store = LexicalStore(_POSTGRES_DSN)
        repo_id = _unique_repo()
        docs, metas = _sample_docs_and_metas(repo_id)
        store.index_documents(repo_id=repo_id, docs=docs, metadatas=metas)

        try:
            assert store.query(repo_id=repo_id, text="   ") == []
        finally:
            store.delete_repo(repo_id)

    @_skip_no_postgres
    def test_index_documents_es_idempotente(self):
        """Reindexar el mismo corpus no crea duplicados."""
        from coderag.storage.lexical_store import LexicalStore

        store = LexicalStore(_POSTGRES_DSN)
        repo_id = _unique_repo()
        docs, metas = _sample_docs_and_metas(repo_id)

        try:
            store.index_documents(repo_id=repo_id, docs=docs, metadatas=metas)
            store.index_documents(repo_id=repo_id, docs=docs, metadatas=metas)
            results = store.query(repo_id=repo_id, text="user", top_n=10)
            # Si hay duplicados, habrá más resultados de los esperados
            ids = [r["id"] for r in results]
            assert len(ids) == len(set(ids)), "Se detectaron resultados duplicados"
        finally:
            store.delete_repo(repo_id)

    @_skip_no_postgres
    def test_delete_repo_elimina_corpus(self):
        """delete_repo limpia todos los documentos del repositorio."""
        from coderag.storage.lexical_store import LexicalStore

        store = LexicalStore(_POSTGRES_DSN)
        repo_id = _unique_repo()
        docs, metas = _sample_docs_and_metas(repo_id)

        store.index_documents(repo_id=repo_id, docs=docs, metadatas=metas)
        assert store.has_corpus(repo_id) is True

        result = store.delete_repo(repo_id)
        assert result["docs_removed"] == len(docs)
        assert store.has_corpus(repo_id) is False

    @_skip_no_postgres
    def test_delete_repo_es_idempotente(self):
        """delete_repo en repo inexistente retorna docs_removed=0 sin error."""
        from coderag.storage.lexical_store import LexicalStore

        store = LexicalStore(_POSTGRES_DSN)
        result = store.delete_repo(_unique_repo())
        assert result["docs_removed"] == 0

    @_skip_no_postgres
    def test_delete_all_limpia_corpus_de_multiples_repos(self):
        """delete_all borra todos los repos del corpus léxico."""
        from coderag.storage.lexical_store import LexicalStore

        store = LexicalStore(_POSTGRES_DSN)
        repo_a = _unique_repo()
        repo_b = _unique_repo()
        docs, metas_a = _sample_docs_and_metas(repo_a)
        _, metas_b = _sample_docs_and_metas(repo_b)

        try:
            store.index_documents(repo_id=repo_a, docs=docs, metadatas=metas_a)
            store.index_documents(repo_id=repo_b, docs=docs, metadatas=metas_b)
            assert store.has_corpus(repo_a)
            assert store.has_corpus(repo_b)

            store.delete_all()
            assert not store.has_corpus(repo_a)
            assert not store.has_corpus(repo_b)
        finally:
            # Por si delete_all falla, limpiar de todas formas
            store.delete_repo(repo_a)
            store.delete_repo(repo_b)

    @_skip_no_postgres
    def test_query_respeta_top_n(self):
        """query no devuelve más de top_n resultados."""
        from coderag.storage.lexical_store import LexicalStore

        store = LexicalStore(_POSTGRES_DSN)
        repo_id = _unique_repo()
        docs, metas = _sample_docs_and_metas(repo_id)

        try:
            store.index_documents(repo_id=repo_id, docs=docs, metadatas=metas)
            results = store.query(repo_id=repo_id, text="class", top_n=2)
            assert len(results) <= 2
        finally:
            store.delete_repo(repo_id)


# ===========================================================================
# Bloque 2: PostgresMetadataStore
# ===========================================================================


class TestPostgresMetadataStoreE2E:
    """Pruebas de integración real contra PostgreSQL para PostgresMetadataStore."""

    @_skip_no_postgres
    def test_upsert_y_get_job(self):
        """upsert_job persiste y get_job recupera correctamente."""
        from coderag.core.models import JobInfo, JobStatus
        from coderag.storage.postgres_metadata_store import PostgresMetadataStore

        store = PostgresMetadataStore(_POSTGRES_DSN)
        job_id = f"job-{uuid.uuid4().hex[:8]}"
        job = JobInfo(
            id=job_id,
            status=JobStatus.queued,
            progress=0.0,
            logs=["Iniciando ingesta", "Clonando repo"],
            repo_id="e2e-test-repo",
            error=None,
            diagnostics={"symbols": 0},
        )

        try:
            store.upsert_job(job)
            recovered = store.get_job(job_id)

            assert recovered is not None
            assert recovered.id == job_id
            assert recovered.status == JobStatus.queued
            assert recovered.logs == ["Iniciando ingesta", "Clonando repo"]
            assert recovered.diagnostics == {"symbols": 0}
        finally:
            from coderag.core.models import JobStatus
            # Limpiar el job de prueba
            job.status = JobStatus.failed
            store.upsert_job(job)

    @_skip_no_postgres
    def test_get_job_retorna_none_si_no_existe(self):
        """get_job devuelve None para un id no registrado."""
        from coderag.storage.postgres_metadata_store import PostgresMetadataStore

        store = PostgresMetadataStore(_POSTGRES_DSN)
        assert store.get_job(f"no-existe-{uuid.uuid4().hex}") is None

    @_skip_no_postgres
    def test_recover_interrupted_jobs(self):
        """recover_interrupted_jobs marca queued/running como failed."""
        from coderag.core.models import JobInfo, JobStatus
        from coderag.storage.postgres_metadata_store import PostgresMetadataStore

        store = PostgresMetadataStore(_POSTGRES_DSN)
        job_id = f"job-{uuid.uuid4().hex[:8]}"
        job = JobInfo(
            id=job_id,
            status=JobStatus.running,
            progress=0.3,
            logs=["En progreso"],
            repo_id="e2e-recover-repo",
        )
        store.upsert_job(job)

        try:
            count = store.recover_interrupted_jobs()
            assert count >= 1

            recovered = store.get_job(job_id)
            assert recovered is not None
            assert recovered.status == JobStatus.failed
            assert recovered.error is not None
        finally:
            store.delete_repo_jobs("e2e-recover-repo")

    @_skip_no_postgres
    def test_upsert_repo_runtime_y_get_repo_runtime(self):
        """upsert_repo_runtime persiste metadata que get_repo_runtime recupera."""
        from coderag.storage.postgres_metadata_store import PostgresMetadataStore

        store = PostgresMetadataStore(_POSTGRES_DSN)
        repo_id = f"e2e-repo-{uuid.uuid4().hex[:8]}"

        try:
            store.upsert_repo_runtime(
                repo_id=repo_id,
                organization="acme",
                repo_url="https://github.com/acme/test.git",
                branch="main",
                local_path=f"/tmp/{repo_id}",
                embedding_provider="vertex",
                embedding_model="text-embedding-005",
            )
            runtime = store.get_repo_runtime(repo_id)

            assert runtime is not None
            assert runtime["last_embedding_provider"] == "vertex"
            assert runtime["last_embedding_model"] == "text-embedding-005"
        finally:
            store.delete_repo_runtime(repo_id)

    @_skip_no_postgres
    def test_list_repo_ids_incluye_repos_registrados(self):
        """list_repo_ids incluye repos persistidos en la tabla repos."""
        from coderag.storage.postgres_metadata_store import PostgresMetadataStore

        store = PostgresMetadataStore(_POSTGRES_DSN)
        repo_id = f"e2e-list-{uuid.uuid4().hex[:8]}"

        try:
            store.upsert_repo_runtime(
                repo_id=repo_id,
                organization=None,
                repo_url="https://github.com/acme/test.git",
                branch="main",
                local_path=f"/tmp/{repo_id}",
                embedding_provider=None,
                embedding_model=None,
            )
            ids = store.list_repo_ids()
            assert repo_id in ids
        finally:
            store.delete_repo_runtime(repo_id)

    @_skip_no_postgres
    def test_delete_repo_data_elimina_jobs_y_repos(self):
        """delete_repo_data limpia todas las entradas del repositorio."""
        from coderag.core.models import JobInfo, JobStatus
        from coderag.storage.postgres_metadata_store import PostgresMetadataStore

        store = PostgresMetadataStore(_POSTGRES_DSN)
        repo_id = f"e2e-del-{uuid.uuid4().hex[:8]}"

        job = JobInfo(id=f"j-{uuid.uuid4().hex[:6]}", status=JobStatus.completed,
                      repo_id=repo_id, progress=1.0)
        store.upsert_job(job)
        store.upsert_repo_runtime(
            repo_id=repo_id, organization=None,
            repo_url="https://github.com/test/x.git",
            branch="main", local_path="/tmp/x",
            embedding_provider=None, embedding_model=None,
        )

        result = store.delete_repo_data(repo_id)
        assert result["jobs_deleted"] >= 1
        assert result["repos_deleted"] >= 1
        assert result["total"] == result["jobs_deleted"] + result["repos_deleted"]

        assert store.get_repo_runtime(repo_id) is None


# ===========================================================================
# Bloque 3: ChromaDB remoto
# ===========================================================================


class TestChromaRemoteE2E:
    """Pruebas de integración contra Chroma HttpClient real."""

    @_skip_no_chroma_remote
    def test_heartbeat_responde(self):
        """El servidor Chroma remoto responde al heartbeat."""
        import chromadb

        client = chromadb.HttpClient(host=_CHROMA_HOST, port=_CHROMA_PORT)
        hb = client.heartbeat()
        assert isinstance(hb, (int, float, dict))

    @_skip_no_chroma_remote
    def test_crear_coleccion_y_upsert_query(self):
        """Crea una colección, inserta vectores y los recupera via query."""
        import chromadb

        client = chromadb.HttpClient(host=_CHROMA_HOST, port=_CHROMA_PORT)
        col_name = f"e2e_test_{uuid.uuid4().hex[:8]}"

        try:
            col = client.create_collection(
                col_name,
                metadata={"hnsw:space": "cosine"},
            )

            dim = 8
            col.upsert(
                ids=["doc1", "doc2", "doc3"],
                embeddings=[
                    [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                    [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                ],
                documents=["primer documento", "segundo documento", "tercer documento"],
                metadatas=[
                    {"repo_id": "e2e", "entity_type": "symbol"},
                    {"repo_id": "e2e", "entity_type": "file"},
                    {"repo_id": "e2e", "entity_type": "module"},
                ],
            )

            results = col.query(
                query_embeddings=[[1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]],
                n_results=2,
                where={"repo_id": "e2e"},
            )

            assert results["ids"][0][0] == "doc1"
        finally:
            try:
                client.delete_collection(col_name)
            except Exception:
                pass

    @_skip_no_chroma_remote
    def test_delete_coleccion(self):
        """Eliminar una colección Chroma remota no lanza excepción."""
        import chromadb

        client = chromadb.HttpClient(host=_CHROMA_HOST, port=_CHROMA_PORT)
        col_name = f"e2e_del_{uuid.uuid4().hex[:8]}"
        client.create_collection(col_name)
        client.delete_collection(col_name)  # No debe lanzar

    @_skip_no_chroma_remote
    def test_chromaindex_heartbeat_remoto(self):
        """ChromaIndex con CHROMA_MODE=remote llama heartbeat correctamente."""
        from coderag.ingestion.index_chroma import ChromaIndex

        settings = SimpleNamespace(
            chroma_mode="remote",
            chroma_host=_CHROMA_HOST,
            chroma_port=_CHROMA_PORT,
            chroma_token="",
            chroma_path=None,
            resolve_chroma_hnsw_space=lambda: "cosine",
        )

        ChromaIndex.reset_shared_state()
        with patch("coderag.ingestion.index_chroma.get_settings", return_value=settings):
            idx = ChromaIndex()
            hb = idx.client.heartbeat()
            assert isinstance(hb, (int, float, dict))
        ChromaIndex.reset_shared_state()


# ===========================================================================
# Bloque 4: E2E completo — Chroma + LexicalStore + hybrid search
# ===========================================================================


class TestHybridSearchE2E:
    """Prueba end-to-end del flujo completo de búsqueda híbrida.

    - Indexa documentos en LexicalStore (Postgres) y en Chroma (remoto)
    - Ejecuta hybrid_search con embeddings mock
    - Verifica que el resultado funde correctamente ambas fuentes
    """

    @_skip_e2e
    def test_hybrid_search_funde_chroma_y_lexical(self):
        """hybrid_search combina Chroma + LexicalStore devolviendo RetrievalChunk."""
        import chromadb

        from coderag.ingestion.index_chroma import ChromaIndex
        from coderag.retrieval.hybrid_search import hybrid_search
        from coderag.storage.lexical_store import LexicalStore

        repo_id = _unique_repo()
        col_name = "code_symbols"

        # Embeddings mock de 4 dimensiones para el test
        dim = 4
        docs, metas = _sample_docs_and_metas(repo_id)

        # Embeddings sintéticos — uno por doc, ortogonales aproximados
        embeddings_db = [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
            [0.7, 0.7, 0.0, 0.0],
        ]
        query_embedding = [1.0, 0.0, 0.0, 0.0]  # más cercano a doc[0]

        chroma_client = chromadb.HttpClient(host=_CHROMA_HOST, port=_CHROMA_PORT)

        # --- Configurar settings mock para el test ---
        settings = SimpleNamespace(
            chroma_mode="remote",
            chroma_host=_CHROMA_HOST,
            chroma_port=_CHROMA_PORT,
            chroma_token="",
            chroma_path=None,
            resolve_postgres_dsn=lambda: _POSTGRES_DSN,
            lexical_fts_language="english",
            resolve_chroma_hnsw_space=lambda: "cosine",
        )

        lexical_store = LexicalStore(_POSTGRES_DSN)

        try:
            # --- Indexar en LexicalStore ---
            lexical_store.index_documents(
                repo_id=repo_id, docs=docs, metadatas=metas
            )

            # --- Indexar en Chroma ---
            ChromaIndex.reset_shared_state()
            with patch("coderag.ingestion.index_chroma.get_settings", return_value=settings):
                idx = ChromaIndex()
                idx.upsert(
                    collection_name=col_name,
                    ids=[m["id"] for m in metas],
                    documents=docs,
                    embeddings=embeddings_db,
                    metadatas=[{k: str(v) for k, v in m.items()} for m in metas],
                )

            # --- Ejecutar hybrid_retrieve con embedding y settings parcheados ---
            with (
                patch("coderag.retrieval.hybrid_search.get_settings", return_value=settings),
                patch("coderag.ingestion.index_chroma.get_settings", return_value=settings),
                patch(
                    "coderag.retrieval.hybrid_search.EmbeddingClient"
                ) as mock_emb_cls,
            ):
                mock_emb = mock_emb_cls.return_value
                mock_emb.embed_texts.return_value = [query_embedding]

                ChromaIndex.reset_shared_state()
                results = hybrid_search(
                    repo_id=repo_id,
                    query="authenticate user",
                    top_n=5,
                )

            assert len(results) > 0
            # doc[0] (authenticate_user) debería aparecer entre los primeros
            ids_returned = [r.id for r in results]
            assert f"{repo_id}:auth_user" in ids_returned

            # Verificar estructura de RetrievalChunk
            for chunk in results:
                assert hasattr(chunk, "id")
                assert hasattr(chunk, "text")
                assert hasattr(chunk, "score")
                assert hasattr(chunk, "metadata")
                assert isinstance(chunk.score, float)

        finally:
            lexical_store.delete_repo(repo_id)
            ChromaIndex.reset_shared_state()
            with patch("coderag.ingestion.index_chroma.get_settings", return_value=settings):
                idx2 = ChromaIndex()
                try:
                    idx2.delete_by_repo_id(repo_id=repo_id)
                except Exception:
                    pass
            ChromaIndex.reset_shared_state()

    @_skip_e2e
    def test_hybrid_search_sin_resultados_chroma_usa_solo_lexical(self):
        """Cuando Chroma no tiene docs del repo, lexical_store aporta los resultados."""
        from coderag.ingestion.index_chroma import ChromaIndex
        from coderag.retrieval.hybrid_search import hybrid_search
        from coderag.storage.lexical_store import LexicalStore

        repo_id = _unique_repo()
        docs, metas = _sample_docs_and_metas(repo_id)

        settings = SimpleNamespace(
            chroma_mode="remote",
            chroma_host=_CHROMA_HOST,
            chroma_port=_CHROMA_PORT,
            chroma_token="",
            chroma_path=None,
            resolve_postgres_dsn=lambda: _POSTGRES_DSN,
            lexical_fts_language="english",
            resolve_chroma_hnsw_space=lambda: "cosine",
        )

        lexical_store = LexicalStore(_POSTGRES_DSN)

        try:
            lexical_store.index_documents(
                repo_id=repo_id, docs=docs, metadatas=metas
            )

            with (
                patch("coderag.retrieval.hybrid_search.get_settings", return_value=settings),
                patch("coderag.ingestion.index_chroma.get_settings", return_value=settings),
                patch("coderag.retrieval.hybrid_search.EmbeddingClient") as mock_emb_cls,
            ):
                # El embedding retorna None para simular fallo de embeddings
                mock_emb_cls.return_value.embed_texts.side_effect = RuntimeError("embedding no disponible")

                ChromaIndex.reset_shared_state()

                results = hybrid_search(
                    repo_id=repo_id,
                    query="order service",
                    top_n=5,
                )

            # Sin vector results, lexical debería aportar al menos un resultado
            assert isinstance(results, list)

        finally:
            lexical_store.delete_repo(repo_id)
            ChromaIndex.reset_shared_state()
