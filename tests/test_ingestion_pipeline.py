"""Pruebas para el comportamiento de orquestación de la canalización de ingesta."""

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from coderag.core.models import FileImportRelation, RepoAuthConfig
from coderag.core.models import ScannedFile, SemanticRelation, SymbolChunk
from coderag.ingestion import pipeline


def test_index_lexical_backend_uses_postgres_when_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Con Postgres activo, indexa solo el backend léxico soportado."""
    scanned = [ScannedFile(path="a.py", language="python", content="print('ok')")]
    symbols = [
        SymbolChunk(
            id="s1",
            repo_id="r1",
            path="a.py",
            language="python",
            symbol_name="main",
            symbol_type="function",
            start_line=1,
            end_line=1,
            snippet="print('ok')",
        )
    ]
    lexical_calls: list[dict[str, object]] = []
    class FakeLexicalStore:
        def __init__(self, dsn: str, language: str, session_factory=None) -> None:
            self.dsn = dsn
            self.language = language
            self.session_factory = session_factory

        def index_documents(
            self,
            *,
            repo_id: str,
            docs: list[str],
            metadatas: list[dict],
        ) -> None:
            lexical_calls.append(
                {
                    "repo_id": repo_id,
                    "docs": docs,
                    "metadatas": metadatas,
                }
            )

    fake_module = ModuleType("coderag.storage.lexical_store")
    fake_module.LexicalStore = FakeLexicalStore
    monkeypatch.setitem(sys.modules, "coderag.storage.lexical_store", fake_module)

    import coderag.storage.postgres_session as postgres_session

    monkeypatch.setattr(
        postgres_session.PostgresSessionFactory,
        "from_settings",
        lambda settings: "fake-session-factory",
    )
    monkeypatch.setattr(
        pipeline,
        "get_settings",
        lambda: SimpleNamespace(
            lexical_fts_language="spanish",
            resolve_postgres_dsn=lambda: "postgresql://fake/db",
        ),
    )

    pipeline._index_lexical_backend("r1", scanned, symbols)

    assert len(lexical_calls) == 1
    assert lexical_calls[0]["repo_id"] == "r1"


def test_index_lexical_backend_requires_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sin Postgres configurado la ingesta debe fallar antes de indexar."""
    scanned = [ScannedFile(path="a.py", language="python", content="print('ok')")]
    symbols = [
        SymbolChunk(
            id="s1",
            repo_id="r1",
            path="a.py",
            language="python",
            symbol_name="main",
            symbol_type="function",
            start_line=1,
            end_line=1,
            snippet="print('ok')",
        )
    ]
    monkeypatch.setattr(
        pipeline,
        "get_settings",
        lambda: SimpleNamespace(
            lexical_fts_language="spanish",
            resolve_postgres_dsn=lambda: "",
        ),
    )

    with pytest.raises(RuntimeError, match="LexicalStore Postgres es obligatorio"):
        pipeline._index_lexical_backend("r1", scanned, symbols)


def test_index_vectors_publishes_vector_metrics_into_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agrega métricas vectoriales por colección al diagnostics sink."""
    scanned = [ScannedFile(path="a.py", language="python", content="def a():\n pass")]
    symbols = [
        SymbolChunk(
            id="s1",
            repo_id="r1",
            path="a.py",
            language="python",
            symbol_name="a",
            symbol_type="function",
            start_line=1,
            end_line=2,
            snippet="def a():\n pass",
        )
    ]
    diagnostics: dict[str, object] = {}

    class _FakeEmbedder:
        def __init__(self, provider=None, model=None) -> None:
            del provider, model

        def embed_texts(
            self,
            texts: list[str],
            progress_callback=None,
        ) -> list[list[float]]:
            del progress_callback
            return [[0.1, 0.2] for _ in texts]

    class _FakeChroma:
        def upsert(
            self,
            collection_name: str,
            ids: list[str],
            documents: list[str],
            embeddings: list[list[float]],
            metadatas: list[dict[str, object]],
        ) -> dict[str, int | str | None]:
            del ids, documents, embeddings, metadatas
            if collection_name == "code_symbols":
                return {
                    "collection_name": collection_name,
                    "requested_batch_size": 100,
                    "effective_batch_size": 50,
                    "split_count": 1,
                    "recovered_retry_count": 1,
                    "payload_too_large_events": 1,
                    "proxy_reset_events": 0,
                    "upstream_restarting_events": 0,
                    "documents_written": 1,
                }
            return {
                "collection_name": collection_name,
                "requested_batch_size": 100,
                "effective_batch_size": 100,
                "split_count": 0,
                "recovered_retry_count": 0,
                "payload_too_large_events": 0,
                "proxy_reset_events": 0,
                "upstream_restarting_events": 0,
                "documents_written": 1,
            }

    monkeypatch.setattr(pipeline, "EmbeddingClient", _FakeEmbedder)
    monkeypatch.setattr(pipeline, "ChromaIndex", _FakeChroma)

    pipeline._index_vectors(
        repo_id="r1",
        scanned_files=scanned,
        symbols=symbols,
        diagnostics_sink=diagnostics,
    )

    vector_index = diagnostics["vector_index"]
    assert vector_index["collections_written"] == 3
    assert vector_index["initial_batch_size"] == 100
    assert vector_index["effective_batch_size"] == 50
    assert vector_index["split_count"] == 1
    assert vector_index["recovered_retry_count"] == 1
    assert vector_index["payload_too_large_events"] == 1
    expected_tokens = pipeline._estimate_embedding_tokens_read(
        ["def a():\n pass"]
        + ["Archivo: a.py\nLenguaje: python\nLineas: 2\nExtracto:\ndef a():\n pass"]
        + ["Modulo: .\nArchivos: 1\nLenguajes: python"]
    )
    assert vector_index["embedding_tokens_read_estimated"] == expected_tokens
    assert vector_index["documents_written"] == 3


def test_ingest_repository_publishes_repo_size_mb_into_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    patch_module_settings,
) -> None:
    """Publica repo_size_mb usando solo el contenido realmente leído."""
    scanned = [
        ScannedFile(path="a.py", language="python", content="hola"),
        ScannedFile(path="b.py", language="python", content="mundo"),
    ]
    symbols = [
        SymbolChunk(
            id="s1",
            repo_id="r1",
            path="a.py",
            language="python",
            symbol_name="a",
            symbol_type="function",
            start_line=1,
            end_line=1,
            snippet="hola",
        )
    ]
    diagnostics: dict[str, object] = {}

    _patch_pipeline_settings(patch_module_settings, tmp_path)
    monkeypatch.setattr(
        pipeline,
        "clone_repository",
        lambda repo_url, destination_root, branch, commit, **kwargs: (
            "r1",
            tmp_path,
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "scan_repository_with_stats",
        lambda *args, **kwargs: (
            scanned,
            {
                "visited": 2,
                "visited_dirs": 1,
                "scanned": 2,
                "excluded_dir": 0,
                "excluded_extension": 0,
                "excluded_file": 0,
                "excluded_pattern": 0,
                "excluded_size": 0,
                "excluded_decode": 0,
                "pruned_dirs": 0,
            },
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "extract_symbol_chunks",
        lambda repo_id, scanned_files: symbols,
    )
    monkeypatch.setattr(
        pipeline,
        "_index_vectors",
        lambda repo_id, s, c, **kwargs: None,
    )
    monkeypatch.setattr(
        pipeline,
        "_index_lexical_backend",
        lambda repo_id, scanned_files, chunks: None,
    )
    monkeypatch.setattr(
        pipeline,
        "_index_graph",
        lambda repo_id, scanned_files, chunks, logger=None, **kwargs: None,
    )
    monkeypatch.setattr(
        pipeline,
        "_repo_has_existing_index_data",
        lambda repo_id, logger: False,
    )

    pipeline.ingest_repository(
        provider="github",
        repo_url="https://example.com/repo.git",
        branch="main",
        commit=None,
        token=None,
        logger=lambda message: None,
        diagnostics_sink=diagnostics,
    )

    assert diagnostics["repo_size_mb"] == 0.0


def _patch_pipeline_settings(
    patch_module_settings,
    tmp_path: Path,
    **overrides: object,
) -> object:
    """Aplica settings de prueba comunes para orquestación de ingesta."""
    defaults = {
        "workspace_path": tmp_path,
        "resolve_postgres_dsn": lambda: "postgresql://fake/db",
        "scan_max_file_size_bytes": 12345,
        "scan_excluded_dirs": ".git,node_modules",
        "scan_excluded_extensions": ".png,.zip",
        "scan_excluded_files": ".gitignore,.env",
        "git_ssh_key_content": "",
        "git_ssh_key_content_b64": "",
        "git_ssh_known_hosts_content": "",
        "git_ssh_known_hosts_content_b64": "",
        "git_ssh_strict_host_key_checking": "yes",
    }
    defaults.update(overrides)
    return patch_module_settings(pipeline, **defaults)


def test_ingest_repository_continues_on_graph_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    patch_module_settings,
) -> None:
    """La canalización registra una advertencia y se completa incluso cuando falla la indexación de Neo4j."""
    scanned = [ScannedFile(path="a.py", language="python", content="def a():\n pass")]
    symbols = [
        SymbolChunk(
            id="s1",
            repo_id="r1",
            path="a.py",
            language="python",
            symbol_name="a",
            symbol_type="function",
            start_line=1,
            end_line=2,
            snippet="def a():\n pass",
        )
    ]

    received_scan_args: dict[str, object] = {}

    def _fake_scan_repository_with_stats(
        repo_path: Path,
        max_file_size: int = 200_000,
        excluded_dirs: set[str] | None = None,
        excluded_extensions: set[str] | None = None,
        excluded_files: set[str] | None = None,
        excluded_patterns: set[str] | None = None,
    ) -> tuple[list[ScannedFile], dict[str, int]]:
        received_scan_args["repo_path"] = repo_path
        received_scan_args["max_file_size"] = max_file_size
        received_scan_args["excluded_dirs"] = excluded_dirs or set()
        received_scan_args["excluded_extensions"] = excluded_extensions or set()
        received_scan_args["excluded_files"] = excluded_files or set()
        received_scan_args["excluded_patterns"] = excluded_patterns or set()
        return scanned, {
            "visited": 1,
            "visited_dirs": 1,
            "scanned": 1,
            "excluded_dir": 0,
            "excluded_extension": 0,
            "excluded_file": 0,
            "excluded_pattern": 0,
            "excluded_size": 0,
            "excluded_decode": 0,
            "pruned_dirs": 0,
        }

    patch_module_settings(
        pipeline,
        workspace_path=tmp_path,
        scan_max_file_size_bytes=12345,
        scan_excluded_dirs=".git,node_modules",
        scan_excluded_extensions=".png,.zip",
        scan_excluded_files=".gitignore,.env",
        scan_excluded_patterns="",
    )
    monkeypatch.setattr(
        pipeline,
        "clone_repository",
        lambda repo_url, destination_root, branch, commit, **kwargs: (
            "r1",
            tmp_path,
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "scan_repository_with_stats",
        _fake_scan_repository_with_stats,
    )
    monkeypatch.setattr(
        pipeline,
        "extract_symbol_chunks",
        lambda repo_id, scanned_files: symbols,
    )
    monkeypatch.setattr(
        pipeline,
        "_index_vectors",
        lambda repo_id, s, c, **kwargs: None,
    )
    monkeypatch.setattr(
        pipeline,
        "_index_lexical_backend",
        lambda repo_id, scanned_files, chunks: None,
    )
    monkeypatch.setattr(
        pipeline,
        "_repo_has_existing_index_data",
        lambda repo_id, logger: False,
    )

    def fail_graph(
        repo_id: str,
        scanned_files: list[ScannedFile],
        chunks: list[SymbolChunk],
        logger=None,
        **kwargs,
    ) -> None:
        del repo_id, scanned_files, chunks, logger, kwargs
        raise RuntimeError(
            "No se pudo completar la operación de Neo4j 'upsert de grafo por "
            "repositorio' en neo4j:7687 (auth=basic). Error original: neo4j auth"
        )

    monkeypatch.setattr(pipeline, "_index_graph", fail_graph)

    logs: list[str] = []
    repo_id = pipeline.ingest_repository(
        provider="github",
        repo_url="https://example.com/repo.git",
        branch="main",
        commit=None,
        token=None,
        logger=logs.append,
    )

    assert repo_id == "r1"
    assert received_scan_args["repo_path"] == tmp_path
    assert received_scan_args["max_file_size"] == 12345
    assert ".git" in received_scan_args["excluded_dirs"]
    assert "node_modules" in received_scan_args["excluded_dirs"]
    assert ".png" in received_scan_args["excluded_extensions"]
    assert ".zip" in received_scan_args["excluded_extensions"]
    assert ".gitignore" in received_scan_args["excluded_files"]
    assert ".env" in received_scan_args["excluded_files"]
    assert received_scan_args["excluded_patterns"] == set()
    assert any("Observabilidad símbolos:" in item for item in logs)
    assert any("Advertencia: no se pudo indexar grafo Neo4j" in item for item in logs)
    assert any("neo4j:7687" in item for item in logs)
    assert logs[-1] == "Ingesta finalizada"


def test_ingest_repository_purges_existing_repo_before_reindex(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    patch_module_settings,
) -> None:
    """Ejecuta purge por repo_id antes de indexar cuando detecta datos previos."""
    scanned = [ScannedFile(path="a.py", language="python", content="def a():\n pass")]
    symbols = [
        SymbolChunk(
            id="s1",
            repo_id="r1",
            path="a.py",
            language="python",
            symbol_name="a",
            symbol_type="function",
            start_line=1,
            end_line=2,
            snippet="def a():\n pass",
        )
    ]

    call_order: list[str] = []

    patch_module_settings(
        pipeline,
        workspace_path=tmp_path,
        scan_max_file_size_bytes=12345,
        scan_excluded_dirs=".git,node_modules",
        scan_excluded_extensions=".png,.zip",
        scan_excluded_files=".gitignore,.env",
    )
    monkeypatch.setattr(
        pipeline,
        "clone_repository",
        lambda repo_url, destination_root, branch, commit, **kwargs: (
            "r1",
            tmp_path,
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "_repo_has_existing_index_data",
        lambda repo_id, logger: True,
    )
    monkeypatch.setattr(
        pipeline,
        "_purge_repo_indices",
        lambda repo_id, logger: call_order.append("purge"),
    )
    monkeypatch.setattr(
        pipeline,
        "scan_repository_with_stats",
        lambda *args, **kwargs: (
            scanned,
            {
                "visited": 1,
                "scanned": 1,
                "excluded_dir": 0,
                "excluded_extension": 0,
                "excluded_file": 0,
                "excluded_size": 0,
                "excluded_decode": 0,
            },
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "extract_symbol_chunks",
        lambda repo_id, scanned_files: symbols,
    )
    monkeypatch.setattr(
        pipeline,
        "_index_vectors",
        lambda repo_id, s, c, **kwargs: call_order.append("index_vectors"),
    )
    monkeypatch.setattr(
        pipeline,
        "_index_lexical_backend",
        lambda repo_id, scanned_files, chunks: call_order.append("index_lexical"),
    )
    monkeypatch.setattr(
        pipeline,
        "_index_graph",
        lambda repo_id, scanned_files, chunks, logger=None, **kwargs: (
            call_order.append("index_graph")
        ),
    )

    logs: list[str] = []
    pipeline.ingest_repository(
        provider="github",
        repo_url="https://example.com/repo.git",
        branch="main",
        commit=None,
        token=None,
        logger=logs.append,
    )

    assert call_order == ["purge", "index_vectors", "index_lexical", "index_graph"]
    assert any("Repositorio existente detectado" in item for item in logs)
    assert any("Observabilidad símbolos:" in item for item in logs)


def test_repo_has_existing_index_data_uses_active_lexical_helper(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    patch_module_settings,
) -> None:
    """Consulta el backend léxico activo a través del helper compartido."""

    class FakeChromaIndex:
        def count_by_repo_id(self, collection_name: str, repo_id: str) -> int:
            del collection_name, repo_id
            return 0

    class FakeGraphBuilder:
        def has_repo_data(self, repo_id: str) -> bool:
            del repo_id
            return False

        def close(self) -> None:
            return None

    _patch_pipeline_settings(patch_module_settings, tmp_path)
    monkeypatch.setattr(
        pipeline,
        "build_managed_vector_index",
        lambda: FakeChromaIndex(),
    )
    monkeypatch.setattr(pipeline, "GraphBuilder", FakeGraphBuilder)

    captured: dict[str, str] = {}

    def _fake_repository_has_active_lexical_data(
        settings: object,
        repo_id: str,
    ) -> bool:
        del settings
        captured["repo_id"] = repo_id
        return True

    monkeypatch.setattr(
        pipeline,
        "repository_has_active_lexical_data",
        _fake_repository_has_active_lexical_data,
    )

    assert pipeline._repo_has_existing_index_data("r1", lambda message: None) is True
    assert captured["repo_id"] == "r1"


def test_purge_repo_indices_formats_lexical_counts_from_active_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    patch_module_settings,
) -> None:
    """Reporta conteos lexicales del backend activo soportado."""

    class FakeChromaIndex:
        def delete_by_repo_id(self, repo_id: str) -> dict[str, int]:
            del repo_id
            return {"total": 5}

    class FakeGraphBuilder:
        def delete_repo_subgraph(self, repo_id: str) -> int:
            del repo_id
            return 4

        def close(self) -> None:
            return None

    _patch_pipeline_settings(patch_module_settings, tmp_path)
    monkeypatch.setattr(
        pipeline,
        "build_managed_vector_index",
        lambda: FakeChromaIndex(),
    )
    monkeypatch.setattr(pipeline, "GraphBuilder", FakeGraphBuilder)
    monkeypatch.setattr(
        pipeline,
        "delete_active_repository_lexical_data",
        lambda settings, repo_id: {"docs_removed": 2, "snapshot_removed": 1},
    )

    logs: list[str] = []
    pipeline._purge_repo_indices("r1", logs.append)

    assert "lexical_docs=2" in logs[0]
    assert "chroma_total=5" in logs[0]
    assert "neo4j_nodes=4" in logs[0]


def test_purge_repo_indices_formats_lexical_counts_from_active_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    patch_module_settings,
) -> None:
    """Reporta conteos LexicalStore cuando ese es el backend activo."""

    class FakeChromaIndex:
        def delete_by_repo_id(self, repo_id: str) -> dict[str, int]:
            del repo_id
            return {"total": 1}

    class FakeGraphBuilder:
        def delete_repo_subgraph(self, repo_id: str) -> int:
            del repo_id
            return 2

        def close(self) -> None:
            return None

    _patch_pipeline_settings(patch_module_settings, tmp_path)
    monkeypatch.setattr(
        pipeline,
        "build_managed_vector_index",
        lambda: FakeChromaIndex(),
    )
    monkeypatch.setattr(pipeline, "GraphBuilder", FakeGraphBuilder)
    monkeypatch.setattr(
        pipeline,
        "delete_active_repository_lexical_data",
        lambda settings, repo_id: {"docs_removed": 3},
    )
    monkeypatch.setattr(
        pipeline,
        "repository_lexical_backend_label",
        lambda settings: "lexical",
    )

    logs: list[str] = []
    pipeline._purge_repo_indices("r1", logs.append)

    assert "lexical_docs=3" in logs[0]
    assert "chroma_total=1" in logs[0]
    assert "neo4j_nodes=2" in logs[0]


def test_ingest_repository_forwards_ssh_runtime_config_to_clone(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    patch_module_settings,
) -> None:
    """Propaga configuración SSH de runtime al clonador para repos privados."""
    scanned = [ScannedFile(path="a.py", language="python", content="print('ok')")]
    symbols = [
        SymbolChunk(
            id="s1",
            repo_id="r1",
            path="a.py",
            language="python",
            symbol_name="main",
            symbol_type="function",
            start_line=1,
            end_line=1,
            snippet="print('ok')",
        )
    ]

    captured: dict[str, object] = {}

    def _fake_clone(
        repo_url: str,
        destination_root: Path,
        branch: str,
        commit: str | None,
        provider: str = "github",
        token: str | None = None,
        auth: RepoAuthConfig | None = None,
        ssh_key_content: str | None = None,
        ssh_key_content_b64: str | None = None,
        ssh_known_hosts_content: str | None = None,
        ssh_known_hosts_content_b64: str | None = None,
        ssh_strict_host_key_checking: str = "yes",
    ) -> tuple[str, Path]:
        captured["repo_url"] = repo_url
        captured["destination_root"] = destination_root
        captured["branch"] = branch
        captured["commit"] = commit
        captured["provider"] = provider
        captured["token"] = token
        captured["auth"] = auth
        captured["ssh_key_content"] = ssh_key_content
        captured["ssh_key_content_b64"] = ssh_key_content_b64
        captured["ssh_known_hosts_content"] = ssh_known_hosts_content
        captured["ssh_known_hosts_content_b64"] = ssh_known_hosts_content_b64
        captured["ssh_strict_host_key_checking"] = ssh_strict_host_key_checking
        return "r1", tmp_path

    _patch_pipeline_settings(
        patch_module_settings,
        tmp_path,
        git_ssh_key_content="PRIVATE KEY FROM ENV",
        git_ssh_known_hosts_content="bitbucket.example ssh-ed25519 AAAA",
    )
    monkeypatch.setattr(pipeline, "clone_repository", _fake_clone)
    monkeypatch.setattr(
        pipeline,
        "_repo_has_existing_index_data",
        lambda repo_id, logger: False,
    )
    monkeypatch.setattr(
        pipeline,
        "scan_repository_with_stats",
        lambda *args, **kwargs: (
            scanned,
            {
                "visited": 1,
                "scanned": 1,
                "excluded_dir": 0,
                "excluded_extension": 0,
                "excluded_file": 0,
                "excluded_size": 0,
                "excluded_decode": 0,
            },
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "extract_symbol_chunks",
        lambda repo_id, scanned_files: symbols,
    )
    monkeypatch.setattr(
        pipeline,
        "_index_vectors",
        lambda repo_id, s, c, **kwargs: None,
    )
    monkeypatch.setattr(
        pipeline,
        "_index_lexical_backend",
        lambda repo_id, scanned_files, chunks: None,
    )
    monkeypatch.setattr(
        pipeline,
        "_index_graph",
        lambda repo_id, scanned_files, chunks, logger=None, **kwargs: None,
    )

    logs: list[str] = []
    pipeline.ingest_repository(
        provider="bitbucket",
        repo_url="https://bitbucket.example/scm/acme/repo.git",
        branch="master",
        commit=None,
        token=None,
        logger=logs.append,
    )

    assert captured["provider"] == "bitbucket"
    assert captured["token"] is None
    assert isinstance(captured["auth"], RepoAuthConfig)
    assert captured["auth"].deployment == "auto"
    assert captured["ssh_key_content"] == "PRIVATE KEY FROM ENV"
    assert captured["ssh_key_content_b64"] == ""
    assert captured["ssh_known_hosts_content"] == (
        "bitbucket.example ssh-ed25519 AAAA"
    )
    assert captured["ssh_known_hosts_content_b64"] == ""
    assert captured["ssh_strict_host_key_checking"] == "yes"


def test_ingest_repository_forwards_explicit_auth_payload_to_clone(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    patch_module_settings,
) -> None:
    """Pasa el bloque auth explícito al clonador para HTTPS Bitbucket."""
    scanned = [ScannedFile(path="a.py", language="python", content="print('ok')")]
    symbols = [
        SymbolChunk(
            id="s1",
            repo_id="r1",
            path="a.py",
            language="python",
            symbol_name="main",
            symbol_type="function",
            start_line=1,
            end_line=1,
            snippet="print('ok')",
        )
    ]

    captured: dict[str, object] = {}

    def _fake_clone(
        repo_url: str,
        destination_root: Path,
        branch: str,
        commit: str | None,
        provider: str = "github",
        token: str | None = None,
        auth: RepoAuthConfig | None = None,
        ssh_key_content: str | None = None,
        ssh_key_content_b64: str | None = None,
        ssh_known_hosts_content: str | None = None,
        ssh_known_hosts_content_b64: str | None = None,
        ssh_strict_host_key_checking: str = "yes",
    ) -> tuple[str, Path]:
        captured["provider"] = provider
        captured["token"] = token
        captured["auth"] = auth
        return "r1", tmp_path

    _patch_pipeline_settings(patch_module_settings, tmp_path)
    monkeypatch.setattr(pipeline, "clone_repository", _fake_clone)
    monkeypatch.setattr(
        pipeline,
        "_repo_has_existing_index_data",
        lambda repo_id, logger: False,
    )
    monkeypatch.setattr(
        pipeline,
        "scan_repository_with_stats",
        lambda *args, **kwargs: (
            scanned,
            {
                "visited": 1,
                "scanned": 1,
                "excluded_dir": 0,
                "excluded_extension": 0,
                "excluded_file": 0,
                "excluded_size": 0,
                "excluded_decode": 0,
            },
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "extract_symbol_chunks",
        lambda repo_id, scanned_files: symbols,
    )
    monkeypatch.setattr(
        pipeline,
        "_index_vectors",
        lambda repo_id, s, c, **kwargs: None,
    )
    monkeypatch.setattr(
        pipeline,
        "_index_lexical_backend",
        lambda repo_id, scanned_files, chunks: None,
    )
    monkeypatch.setattr(
        pipeline,
        "_index_graph",
        lambda repo_id, scanned_files, chunks, logger=None, **kwargs: None,
    )

    logs: list[str] = []
    pipeline.ingest_repository(
        provider="bitbucket",
        repo_url="https://bitbucket.org/acme/private-repo.git",
        branch="main",
        commit=None,
        token=None,
        auth=RepoAuthConfig(
            deployment="cloud",
            transport="https",
            method="http_basic",
            username="acme-user",
            secret="app-password",
        ),
        logger=logs.append,
    )

    assert captured["provider"] == "bitbucket"
    assert captured["token"] is None
    assert isinstance(captured["auth"], RepoAuthConfig)
    assert captured["auth"].method == "http_basic"
    assert captured["auth"].username == "acme-user"


def test_index_graph_adds_semantic_relations_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
    patch_module_settings,
    tmp_path: Path,
) -> None:
    """Incluye relaciones semánticas cuando el flag está habilitado."""
    scanned = [ScannedFile(path="a.py", language="python", content="def a():\n pass")]
    symbols = [
        SymbolChunk(
            id="s1",
            repo_id="r1",
            path="a.py",
            language="python",
            symbol_name="a",
            symbol_type="function",
            start_line=1,
            end_line=2,
            snippet="def a():\n pass",
        )
    ]
    captured: dict[str, object] = {}
    diagnostics: dict[str, object] = {}

    class _FakeGraphBuilder:
        def upsert_repo_graph(
            self,
            repo_id: str,
            scanned_files: list[ScannedFile],
            symbols: list[SymbolChunk],
            semantic_relations: list[SemanticRelation] | None = None,
            file_import_relations=None,
        ) -> None:
            captured["repo_id"] = repo_id
            captured["semantic_relations"] = semantic_relations or []

        def close(self) -> None:
            return None

    _patch_pipeline_settings(
        patch_module_settings,
        tmp_path,
        semantic_graph_enabled=True,
    )
    monkeypatch.setattr(pipeline, "GraphBuilder", _FakeGraphBuilder)
    monkeypatch.setattr(
        pipeline,
        "extract_python_semantic_relations",
        lambda repo_id, scanned_files, symbols, resolution_stats_sink=None, file_imports_sink=None: [
            SemanticRelation(
                repo_id=repo_id,
                source_symbol_id="s1",
                relation_type="CALLS",
                target_symbol_id=None,
                target_ref="print",
                target_kind="external",
                path="a.py",
                line=1,
                confidence=0.9,
                language="python",
            )
        ],
    )

    pipeline._index_graph(
        repo_id="r1",
        scanned_files=scanned,
        symbols=symbols,
        diagnostics_sink=diagnostics,
    )

    assert captured["repo_id"] == "r1"
    assert len(captured["semantic_relations"]) == 1
    assert diagnostics["semantic_graph"]["enabled"] is True
    assert diagnostics["semantic_graph"]["status"] == "ok"
    assert diagnostics["semantic_graph"]["relation_counts_by_type"] == {
        "CALLS": 1
    }
    assert diagnostics["semantic_graph"]["python_resolution_source_counts"] == {}
    assert diagnostics["semantic_graph"]["java_cross_file_resolved_count"] == 0
    assert diagnostics["semantic_graph"]["java_cross_file_resolved_by_type"] == {}
    assert diagnostics["semantic_graph"]["java_resolution_source_counts"] == {}
    assert diagnostics["semantic_graph"]["unresolved_count"] == 1
    assert diagnostics["semantic_graph"]["unresolved_by_type"] == {
        "CALLS": 1
    }


def test_index_graph_falls_back_when_semantic_extraction_fails(
    monkeypatch: pytest.MonkeyPatch,
    patch_module_settings,
    tmp_path: Path,
) -> None:
    """Si falla extracción semántica, conserva indexación estructural."""
    scanned = [ScannedFile(path="a.py", language="python", content="def a():\n pass")]
    symbols = [
        SymbolChunk(
            id="s1",
            repo_id="r1",
            path="a.py",
            language="python",
            symbol_name="a",
            symbol_type="function",
            start_line=1,
            end_line=2,
            snippet="def a():\n pass",
        )
    ]
    captured: dict[str, object] = {}
    diagnostics: dict[str, object] = {}

    class _FakeGraphBuilder:
        def upsert_repo_graph(
            self,
            repo_id: str,
            scanned_files: list[ScannedFile],
            symbols: list[SymbolChunk],
            semantic_relations: list[SemanticRelation] | None = None,
            file_import_relations=None,
        ) -> None:
            captured["semantic_relations"] = semantic_relations or []

        def close(self) -> None:
            return None

    _patch_pipeline_settings(
        patch_module_settings,
        tmp_path,
        semantic_graph_enabled=True,
    )
    monkeypatch.setattr(pipeline, "GraphBuilder", _FakeGraphBuilder)

    def _raise_semantic_error(*args, **kwargs):
        raise RuntimeError("semantic error")

    monkeypatch.setattr(
        pipeline,
        "extract_python_semantic_relations",
        _raise_semantic_error,
    )

    pipeline._index_graph(
        repo_id="r1",
        scanned_files=scanned,
        symbols=symbols,
        diagnostics_sink=diagnostics,
    )

    assert captured["semantic_relations"] == []
    assert diagnostics["semantic_graph"]["enabled"] is True
    assert diagnostics["semantic_graph"]["status"] == "fallback"
    assert diagnostics["semantic_graph"]["relation_counts_by_type"] == {}
    assert diagnostics["semantic_graph"]["java_cross_file_resolved_count"] == 0
    assert diagnostics["semantic_graph"]["java_cross_file_resolved_by_type"] == {}
    assert diagnostics["semantic_graph"]["java_resolution_source_counts"] == {}
    assert diagnostics["semantic_graph"]["unresolved_count"] == 0
    assert diagnostics["semantic_graph"]["unresolved_by_type"] == {}
    assert diagnostics["semantic_graph"]["error"] == "semantic error"


def test_index_graph_runs_java_semantics_only_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
    patch_module_settings,
    tmp_path: Path,
) -> None:
    """Ejecuta extractor Java solo cuando el flag dedicado está activo."""
    scanned = [
        ScannedFile(
            path="src/A.java",
            language="java",
            content="public class A { public void x() { y(); } }",
        )
    ]
    symbols = [
        SymbolChunk(
            id="sj1",
            repo_id="rj",
            path="src/A.java",
            language="java",
            symbol_name="x",
            symbol_type="method",
            start_line=1,
            end_line=1,
            snippet="public void x() { y(); }",
        )
    ]
    diagnostics: dict[str, object] = {}

    class _FakeGraphBuilder:
        def upsert_repo_graph(
            self,
            repo_id: str,
            scanned_files: list[ScannedFile],
            symbols: list[SymbolChunk],
            semantic_relations: list[SemanticRelation] | None = None,
            file_import_relations=None,
        ) -> None:
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr(pipeline, "GraphBuilder", _FakeGraphBuilder)
    monkeypatch.setattr(
        pipeline,
        "extract_python_semantic_relations",
        lambda repo_id, scanned_files, symbols, resolution_stats_sink=None, file_imports_sink=None: [],
    )

    def _raise_if_called(*args, **kwargs):
        raise RuntimeError("java extractor should not run")

    monkeypatch.setattr(
        pipeline,
        "extract_java_semantic_relations",
        _raise_if_called,
    )
    _patch_pipeline_settings(
        patch_module_settings,
        tmp_path,
        semantic_graph_enabled=True,
        semantic_graph_java_enabled=False,
    )
    pipeline._index_graph(
        repo_id="rj",
        scanned_files=scanned,
        symbols=symbols,
        diagnostics_sink=diagnostics,
    )

    _patch_pipeline_settings(
        patch_module_settings,
        tmp_path,
        semantic_graph_enabled=True,
        semantic_graph_java_enabled=True,
    )
    monkeypatch.setattr(
        pipeline,
        "extract_java_semantic_relations",
        lambda repo_id, scanned_files, symbols, resolution_stats_sink=None: [
            SemanticRelation(
                repo_id=repo_id,
                source_symbol_id="sj1",
                relation_type="CALLS",
                target_symbol_id=None,
                target_ref="y",
                target_kind="external",
                path="src/A.java",
                line=1,
                confidence=0.75,
                language="java",
            )
        ],
    )
    pipeline._index_graph(
        repo_id="rj",
        scanned_files=scanned,
        symbols=symbols,
        diagnostics_sink=diagnostics,
    )

    assert diagnostics["semantic_graph"]["relation_counts"] == 1
    assert diagnostics["semantic_graph"]["relation_counts_by_type"] == {
        "CALLS": 1
    }
    assert diagnostics["semantic_graph"]["java_cross_file_resolved_count"] == 0
    assert diagnostics["semantic_graph"]["java_cross_file_resolved_by_type"] == {}
    assert diagnostics["semantic_graph"]["java_resolution_source_counts"] == {}


def test_index_graph_reports_java_cross_file_resolved_count(
    monkeypatch: pytest.MonkeyPatch,
    patch_module_settings,
    tmp_path: Path,
) -> None:
    """Cuenta relaciones Java resueltas hacia símbolos en archivos distintos."""
    scanned = [
        ScannedFile(path="src/A.java", language="java", content="class A {}"),
        ScannedFile(path="src/B.java", language="java", content="class B {}"),
    ]
    symbols = [
        SymbolChunk(
            id="sa",
            repo_id="rj",
            path="src/A.java",
            language="java",
            symbol_name="A",
            symbol_type="class",
            start_line=1,
            end_line=1,
            snippet="class A {}",
        ),
        SymbolChunk(
            id="sb",
            repo_id="rj",
            path="src/B.java",
            language="java",
            symbol_name="B",
            symbol_type="class",
            start_line=1,
            end_line=1,
            snippet="class B {}",
        ),
    ]
    diagnostics: dict[str, object] = {}

    class _FakeGraphBuilder:
        def upsert_repo_graph(
            self,
            repo_id: str,
            scanned_files: list[ScannedFile],
            symbols: list[SymbolChunk],
            semantic_relations: list[SemanticRelation] | None = None,
            file_import_relations=None,
        ) -> None:
            return None

        def close(self) -> None:
            return None

    _patch_pipeline_settings(
        patch_module_settings,
        tmp_path,
        semantic_graph_enabled=True,
        semantic_graph_java_enabled=True,
    )
    monkeypatch.setattr(pipeline, "GraphBuilder", _FakeGraphBuilder)
    monkeypatch.setattr(
        pipeline,
        "extract_python_semantic_relations",
        lambda repo_id, scanned_files, symbols, resolution_stats_sink=None, file_imports_sink=None: [],
    )
    monkeypatch.setattr(
        pipeline,
        "extract_java_semantic_relations",
        lambda repo_id, scanned_files, symbols, resolution_stats_sink=None: [
            SemanticRelation(
                repo_id=repo_id,
                source_symbol_id="sa",
                relation_type="IMPLEMENTS",
                target_symbol_id="sb",
                target_ref="B",
                target_kind="symbol",
                path="src/A.java",
                line=1,
                confidence=0.95,
                language="java",
            )
        ],
    )

    pipeline._index_graph(
        repo_id="rj",
        scanned_files=scanned,
        symbols=symbols,
        diagnostics_sink=diagnostics,
    )

    assert diagnostics["semantic_graph"]["java_cross_file_resolved_count"] == 1


def test_index_graph_respects_kotlin_semantic_flag(
    monkeypatch: pytest.MonkeyPatch,
    patch_module_settings,
    tmp_path: Path,
) -> None:
    """Ejecuta extracción semántica Kotlin solo cuando el flag está activo."""
    scanned = [ScannedFile(path="src/A.kt", language="kotlin", content="class A")]
    symbols = [
        SymbolChunk(
            id="sk1",
            repo_id="rk",
            path="src/A.kt",
            language="kotlin",
            symbol_name="A",
            symbol_type="class",
            start_line=1,
            end_line=1,
            snippet="class A",
        )
    ]
    diagnostics: dict[str, object] = {}

    class _FakeGraphBuilder:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def upsert_repo_graph(
            self,
            repo_id: str,
            scanned_files: list[ScannedFile],
            symbols: list[SymbolChunk],
            semantic_relations: list[SemanticRelation] | None = None,
            file_import_relations=None,
        ) -> None:
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr(pipeline, "GraphBuilder", _FakeGraphBuilder)
    monkeypatch.setattr(
        pipeline,
        "extract_python_semantic_relations",
        lambda repo_id, scanned_files, symbols, resolution_stats_sink=None, file_imports_sink=None: [],
    )
    monkeypatch.setattr(
        pipeline,
        "extract_kotlin_semantic_relations",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("kotlin extractor should not run")
        ),
    )
    _patch_pipeline_settings(
        patch_module_settings,
        tmp_path,
        semantic_graph_enabled=True,
        semantic_graph_java_enabled=False,
        semantic_graph_javascript_enabled=False,
        semantic_graph_typescript_enabled=False,
        semantic_graph_kotlin_enabled=False,
        semantic_graph_swift_enabled=False,
    )
    pipeline._index_graph(
        repo_id="rk",
        scanned_files=scanned,
        symbols=symbols,
        diagnostics_sink=diagnostics,
    )

    _patch_pipeline_settings(
        patch_module_settings,
        tmp_path,
        semantic_graph_enabled=True,
        semantic_graph_java_enabled=False,
        semantic_graph_javascript_enabled=False,
        semantic_graph_typescript_enabled=False,
        semantic_graph_kotlin_enabled=True,
        semantic_graph_swift_enabled=False,
    )
    monkeypatch.setattr(
        pipeline,
        "extract_kotlin_semantic_relations",
        lambda repo_id, scanned_files, symbols, resolution_stats_sink=None: [
            SemanticRelation(
                repo_id=repo_id,
                source_symbol_id="sk1",
                relation_type="CALLS",
                target_symbol_id=None,
                target_ref="println",
                target_kind="external",
                path="src/A.kt",
                line=1,
                confidence=0.75,
                language="kotlin",
            )
        ],
    )
    pipeline._index_graph(
        repo_id="rk",
        scanned_files=scanned,
        symbols=symbols,
        diagnostics_sink=diagnostics,
    )

    assert diagnostics["semantic_graph"]["relation_counts"] == 1
    assert diagnostics["semantic_graph"]["relation_counts_by_type"] == {
        "CALLS": 1
    }
    assert diagnostics["semantic_graph"]["kotlin_cross_file_resolved_count"] == 0
    assert diagnostics["semantic_graph"]["kotlin_resolution_source_counts"] == {}


def test_index_graph_respects_swift_semantic_flag(
    monkeypatch: pytest.MonkeyPatch,
    patch_module_settings,
    tmp_path: Path,
) -> None:
    """Ejecuta extracción semántica Swift solo cuando el flag está activo."""
    scanned = [
        ScannedFile(path="src/A.swift", language="swift", content="class A {}")
    ]
    symbols = [
        SymbolChunk(
            id="ss1",
            repo_id="rs",
            path="src/A.swift",
            language="swift",
            symbol_name="A",
            symbol_type="class",
            start_line=1,
            end_line=1,
            snippet="class A {}",
        )
    ]
    diagnostics: dict[str, object] = {}

    class _FakeGraphBuilder:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def upsert_repo_graph(
            self,
            repo_id: str,
            scanned_files: list[ScannedFile],
            symbols: list[SymbolChunk],
            semantic_relations: list[SemanticRelation] | None = None,
            file_import_relations=None,
        ) -> None:
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr(pipeline, "GraphBuilder", _FakeGraphBuilder)
    monkeypatch.setattr(
        pipeline,
        "extract_python_semantic_relations",
        lambda repo_id, scanned_files, symbols, resolution_stats_sink=None, file_imports_sink=None: [],
    )
    monkeypatch.setattr(
        pipeline,
        "extract_swift_semantic_relations",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("swift extractor should not run")
        ),
    )
    _patch_pipeline_settings(
        patch_module_settings,
        tmp_path,
        semantic_graph_enabled=True,
        semantic_graph_java_enabled=False,
        semantic_graph_javascript_enabled=False,
        semantic_graph_typescript_enabled=False,
        semantic_graph_kotlin_enabled=False,
        semantic_graph_swift_enabled=False,
    )
    pipeline._index_graph(
        repo_id="rs",
        scanned_files=scanned,
        symbols=symbols,
        diagnostics_sink=diagnostics,
    )

    _patch_pipeline_settings(
        patch_module_settings,
        tmp_path,
        semantic_graph_enabled=True,
        semantic_graph_java_enabled=False,
        semantic_graph_javascript_enabled=False,
        semantic_graph_typescript_enabled=False,
        semantic_graph_kotlin_enabled=False,
        semantic_graph_swift_enabled=True,
    )
    monkeypatch.setattr(
        pipeline,
        "extract_swift_semantic_relations",
        lambda repo_id, scanned_files, symbols, resolution_stats_sink=None: [
            SemanticRelation(
                repo_id=repo_id,
                source_symbol_id="ss1",
                relation_type="CALLS",
                target_symbol_id=None,
                target_ref="print",
                target_kind="external",
                path="src/A.swift",
                line=1,
                confidence=0.75,
                language="swift",
            )
        ],
    )
    pipeline._index_graph(
        repo_id="rs",
        scanned_files=scanned,
        symbols=symbols,
        diagnostics_sink=diagnostics,
    )

    assert diagnostics["semantic_graph"]["relation_counts"] == 1
    assert diagnostics["semantic_graph"]["relation_counts_by_type"] == {
        "CALLS": 1
    }
    assert diagnostics["semantic_graph"]["swift_cross_file_resolved_count"] == 0
    assert diagnostics["semantic_graph"]["swift_resolution_source_counts"] == {}


def test_index_graph_mixed_kotlin_swift_preserves_python_outputs(
    monkeypatch: pytest.MonkeyPatch,
    patch_module_settings,
    tmp_path: Path,
) -> None:
    """No altera símbolos ni semántica Python al coexistir con Kotlin y Swift."""
    python_scanned = [
        ScannedFile(
            path="pkg/shared.py",
            language="python",
            content="def helper():\n    return 1\n",
        ),
        ScannedFile(
            path="pkg/consumer.py",
            language="python",
            content=(
                "def run():\n"
                "    from pkg.shared import helper\n"
                "    helper()\n"
            ),
        ),
    ]
    mixed_scanned = python_scanned + [
        ScannedFile(
            path="src/com/acme/api/Service.kt",
            language="kotlin",
            content="package com.acme.api\n\ninterface Service\n",
        ),
        ScannedFile(
            path="src/com/acme/impl/Base.kt",
            language="kotlin",
            content=(
                "package com.acme.impl\n\n"
                "open class Base {\n"
                "    fun helper() {}\n"
                "}\n"
            ),
        ),
        ScannedFile(
            path="src/com/acme/impl/Impl.kt",
            language="kotlin",
            content=(
                "package com.acme.impl\n\n"
                "import com.acme.api.Service\n\n"
                "class Impl: Base(), Service {\n"
                "    fun run() {\n"
                "        helper()\n"
                "    }\n"
                "}\n"
            ),
        ),
        ScannedFile(
            path="src/Base.swift",
            language="swift",
            content="class Base {\n    func helper() {}\n}\n",
        ),
        ScannedFile(
            path="src/Service.swift",
            language="swift",
            content="protocol Service {\n    func run()\n}\n",
        ),
        ScannedFile(
            path="src/Impl.swift",
            language="swift",
            content=(
                "class Impl: Base, Service {\n"
                "    func run() {\n"
                "        helper()\n"
                "    }\n"
                "}\n"
            ),
        ),
    ]
    diagnostics: dict[str, object] = {}
    captured: dict[str, object] = {}

    class _FakeGraphBuilder:
        def upsert_repo_graph(
            self,
            repo_id: str,
            scanned_files: list[ScannedFile],
            symbols: list[SymbolChunk],
            semantic_relations: list[SemanticRelation] | None = None,
            file_import_relations: list[FileImportRelation] | None = None,
        ) -> None:
            captured["repo_id"] = repo_id
            captured["scanned_files"] = scanned_files
            captured["symbols"] = symbols
            captured["semantic_relations"] = semantic_relations or []
            captured["file_import_relations"] = file_import_relations or []

        def close(self) -> None:
            return None

    def _symbol_key(symbol: SymbolChunk) -> tuple[str, str, str, int, int]:
        return (
            symbol.path,
            symbol.symbol_name,
            symbol.symbol_type,
            symbol.start_line,
            symbol.end_line,
        )

    def _relation_key(
        relation: SemanticRelation,
    ) -> tuple[str, str, str | None, str | None, str | None]:
        return (
            relation.path,
            relation.relation_type,
            relation.target_ref,
            relation.target_symbol_id,
            relation.resolution_method,
        )

    def _file_import_key(
        relation: FileImportRelation,
    ) -> tuple[str, str | None, str, str, str | None]:
        return (
            relation.source_path,
            relation.target_path,
            relation.target_ref,
            relation.target_kind,
            relation.resolution_method,
        )

    baseline_python_symbols = pipeline.extract_symbol_chunks(
        repo_id="rmix",
        scanned_files=python_scanned,
    )
    baseline_python_resolution_stats: dict[str, int] = {}
    baseline_python_file_imports: list[FileImportRelation] = []
    baseline_python_relations = pipeline.extract_python_semantic_relations(
        repo_id="rmix",
        scanned_files=python_scanned,
        symbols=baseline_python_symbols,
        resolution_stats_sink=baseline_python_resolution_stats,
        file_imports_sink=baseline_python_file_imports,
    )

    mixed_symbols = pipeline.extract_symbol_chunks(
        repo_id="rmix",
        scanned_files=mixed_scanned,
    )

    _patch_pipeline_settings(
        patch_module_settings,
        tmp_path,
        semantic_graph_enabled=True,
        semantic_graph_java_enabled=False,
        semantic_graph_javascript_enabled=False,
        semantic_graph_typescript_enabled=False,
        semantic_graph_kotlin_enabled=True,
        semantic_graph_swift_enabled=True,
    )
    monkeypatch.setattr(pipeline, "GraphBuilder", _FakeGraphBuilder)

    pipeline._index_graph(
        repo_id="rmix",
        scanned_files=mixed_scanned,
        symbols=mixed_symbols,
        diagnostics_sink=diagnostics,
    )

    python_symbols_from_mixed = [
        item for item in mixed_symbols if item.language == "python"
    ]
    python_relations_from_mixed = [
        item
        for item in captured["semantic_relations"]
        if item.language == "python"
    ]
    python_file_imports_from_mixed = [
        item
        for item in captured["file_import_relations"]
        if item.language == "python"
    ]

    assert {
        _symbol_key(item) for item in python_symbols_from_mixed
    } == {_symbol_key(item) for item in baseline_python_symbols}
    assert {
        _relation_key(item) for item in python_relations_from_mixed
    } == {_relation_key(item) for item in baseline_python_relations}
    assert {
        _file_import_key(item) for item in python_file_imports_from_mixed
    } == {_file_import_key(item) for item in baseline_python_file_imports}
    assert diagnostics["semantic_graph"]["python_resolution_source_counts"] == (
        baseline_python_resolution_stats
    )
    assert any(
        item.language == "kotlin" for item in captured["semantic_relations"]
    )
    assert any(item.language == "swift" for item in captured["semantic_relations"])


def test_index_graph_multilanguage_file_imports_feed_graph_edges(
    monkeypatch: pytest.MonkeyPatch,
    patch_module_settings,
    tmp_path: Path,
) -> None:
    """Integra Python, TypeScript, Kotlin y Swift en el mismo sink de file imports."""
    scanned = [
        ScannedFile(
            path="pkg/shared.py",
            language="python",
            content="def helper():\n    return 1\n",
        ),
        ScannedFile(
            path="pkg/consumer.py",
            language="python",
            content="from pkg.shared import helper\n",
        ),
        ScannedFile(
            path="web/button.tsx",
            language="typescript",
            content="export function Button(): JSX.Element { return <button /> }\n",
        ),
        ScannedFile(
            path="web/page.tsx",
            language="typescript",
            content="import { Button } from './button';\nexport function Page(): JSX.Element { return <Button /> }\n",
        ),
        ScannedFile(
            path="src/com/acme/api/Service.kt",
            language="kotlin",
            content="package com.acme.api\n\ninterface Service\n",
        ),
        ScannedFile(
            path="src/com/acme/app/Impl.kt",
            language="kotlin",
            content="package com.acme.app\n\nimport com.acme.api.Service\n\nclass Impl: Service\n",
        ),
        ScannedFile(
            path="src/App/Impl.swift",
            language="swift",
            content="import Foundation\n\nclass Impl {}\n",
        ),
    ]
    diagnostics: dict[str, object] = {}
    captured: dict[str, object] = {}

    class _FakeGraphBuilder:
        def upsert_repo_graph(
            self,
            repo_id: str,
            scanned_files: list[ScannedFile],
            symbols: list[SymbolChunk],
            semantic_relations: list[SemanticRelation] | None = None,
            file_import_relations: list[FileImportRelation] | None = None,
        ) -> None:
            captured["file_import_relations"] = file_import_relations or []

        def close(self) -> None:
            return None

    symbols = pipeline.extract_symbol_chunks(repo_id="rmulti", scanned_files=scanned)

    _patch_pipeline_settings(
        patch_module_settings,
        tmp_path,
        semantic_graph_enabled=True,
        semantic_graph_java_enabled=False,
        semantic_graph_javascript_enabled=False,
        semantic_graph_typescript_enabled=True,
        semantic_graph_kotlin_enabled=True,
        semantic_graph_swift_enabled=True,
    )
    monkeypatch.setattr(pipeline, "GraphBuilder", _FakeGraphBuilder)

    pipeline._index_graph(
        repo_id="rmulti",
        scanned_files=scanned,
        symbols=symbols,
        diagnostics_sink=diagnostics,
    )

    file_imports = captured["file_import_relations"]
    assert {item.language for item in file_imports} == {
        "python",
        "typescript",
        "kotlin",
        "swift",
    }
    assert any(
        item.language == "python"
        and item.target_path == "pkg/shared.py"
        and item.target_kind == "file"
        for item in file_imports
    )
    assert any(
        item.language == "typescript"
        and item.target_path == "web/button.tsx"
        and item.target_kind == "file"
        for item in file_imports
    )
    assert any(
        item.language == "kotlin"
        and item.target_path == "src/com/acme/api/Service.kt"
        and item.target_kind == "file"
        for item in file_imports
    )
    assert any(
        item.language == "swift"
        and item.target_path is None
        and item.target_ref == "Foundation"
        and item.target_kind == "external"
        for item in file_imports
    )
    assert diagnostics["semantic_graph"]["file_import_counts_by_language"] == {
        "kotlin": {"total": 1, "internal": 1, "external": 0},
        "python": {"total": 1, "internal": 1, "external": 0},
        "swift": {"total": 1, "internal": 0, "external": 1},
        "typescript": {"total": 1, "internal": 1, "external": 0},
    }


def test_index_graph_reports_java_resolution_source_counts(
    monkeypatch: pytest.MonkeyPatch,
    patch_module_settings,
    tmp_path: Path,
) -> None:
    """Incluye desglose de origen de resolución Java en diagnostics."""
    scanned = [ScannedFile(path="src/A.java", language="java", content="class A {}")]
    symbols = [
        SymbolChunk(
            id="sa",
            repo_id="rj",
            path="src/A.java",
            language="java",
            symbol_name="A",
            symbol_type="class",
            start_line=1,
            end_line=1,
            snippet="class A {}",
        )
    ]
    diagnostics: dict[str, object] = {}

    class _FakeGraphBuilder:
        def upsert_repo_graph(
            self,
            repo_id: str,
            scanned_files: list[ScannedFile],
            symbols: list[SymbolChunk],
            semantic_relations: list[SemanticRelation] | None = None,
            file_import_relations=None,
        ) -> None:
            return None

        def close(self) -> None:
            return None

    _patch_pipeline_settings(
        patch_module_settings,
        tmp_path,
        semantic_graph_enabled=True,
        semantic_graph_java_enabled=True,
    )
    monkeypatch.setattr(pipeline, "GraphBuilder", _FakeGraphBuilder)
    monkeypatch.setattr(
        pipeline,
        "extract_python_semantic_relations",
        lambda repo_id, scanned_files, symbols, resolution_stats_sink=None, file_imports_sink=None: [],
    )

    def _fake_java_extract(
        repo_id: str,
        scanned_files: list[ScannedFile],
        symbols: list[SymbolChunk],
        resolution_stats_sink: dict[str, int] | None = None,
    ) -> list[SemanticRelation]:
        if resolution_stats_sink is not None:
            resolution_stats_sink.update({"import": 2, "same_package": 1})
        return []

    monkeypatch.setattr(
        pipeline,
        "extract_java_semantic_relations",
        _fake_java_extract,
    )

    pipeline._index_graph(
        repo_id="rj",
        scanned_files=scanned,
        symbols=symbols,
        diagnostics_sink=diagnostics,
    )

    assert diagnostics["semantic_graph"]["java_resolution_source_counts"] == {
        "import": 2,
        "same_package": 1,
    }


def test_index_graph_reports_python_resolution_source_counts(
    monkeypatch: pytest.MonkeyPatch,
    patch_module_settings,
    tmp_path: Path,
) -> None:
    """Incluye desglose de origen de resolución Python en diagnostics."""
    scanned = [ScannedFile(path="src/a.py", language="python", content="def a():\n    pass")]
    symbols = [
        SymbolChunk(
            id="sp1",
            repo_id="rp",
            path="src/a.py",
            language="python",
            symbol_name="a",
            symbol_type="function",
            start_line=1,
            end_line=2,
            snippet="def a():\n    pass",
        )
    ]
    diagnostics: dict[str, object] = {}

    class _FakeGraphBuilder:
        def upsert_repo_graph(
            self,
            repo_id: str,
            scanned_files: list[ScannedFile],
            symbols: list[SymbolChunk],
            semantic_relations: list[SemanticRelation] | None = None,
            file_import_relations=None,
        ) -> None:
            return None

        def close(self) -> None:
            return None

    def _fake_python_extract(
        repo_id: str,
        scanned_files: list[ScannedFile],
        symbols: list[SymbolChunk],
        resolution_stats_sink: dict[str, int] | None = None,
        file_imports_sink=None,
    ) -> list[SemanticRelation]:
        if resolution_stats_sink is not None:
            resolution_stats_sink.update({"alias": 2, "local": 1})
        return []

    _patch_pipeline_settings(
        patch_module_settings,
        tmp_path,
        semantic_graph_enabled=True,
        semantic_graph_java_enabled=False,
        semantic_graph_javascript_enabled=False,
        semantic_graph_typescript_enabled=False,
    )
    monkeypatch.setattr(pipeline, "GraphBuilder", _FakeGraphBuilder)
    monkeypatch.setattr(
        pipeline,
        "extract_python_semantic_relations",
        _fake_python_extract,
    )
    monkeypatch.setattr(
        pipeline,
        "extract_java_semantic_relations",
        lambda repo_id, scanned_files, symbols, resolution_stats_sink=None: [],
    )
    monkeypatch.setattr(
        pipeline,
        "extract_javascript_semantic_relations",
        lambda repo_id, scanned_files, symbols, resolution_stats_sink=None: [],
    )
    monkeypatch.setattr(
        pipeline,
        "extract_typescript_semantic_relations",
        lambda repo_id, scanned_files, symbols, resolution_stats_sink=None: [],
    )

    pipeline._index_graph(
        repo_id="rp",
        scanned_files=scanned,
        symbols=symbols,
        diagnostics_sink=diagnostics,
    )

    assert diagnostics["semantic_graph"]["python_resolution_source_counts"] == {
        "alias": 2,
        "local": 1,
    }


def test_index_graph_reports_python_top_level_file_import_counts(
    monkeypatch: pytest.MonkeyPatch,
    patch_module_settings,
    tmp_path: Path,
) -> None:
    """Incluye conteos de imports top-level Python a nivel archivo."""
    scanned = [
        ScannedFile(
            path="src/a.py",
            language="python",
            content="from pkg import mod\nimport requests\n",
        )
    ]
    symbols = [
        SymbolChunk(
            id="sp1",
            repo_id="rp",
            path="src/a.py",
            language="python",
            symbol_name="a",
            symbol_type="function",
            start_line=1,
            end_line=1,
            snippet="def a():\n    pass",
        )
    ]
    diagnostics: dict[str, object] = {}

    class _FakeGraphBuilder:
        def upsert_repo_graph(
            self,
            repo_id: str,
            scanned_files: list[ScannedFile],
            symbols: list[SymbolChunk],
            semantic_relations: list[SemanticRelation] | None = None,
            file_import_relations=None,
        ) -> None:
            return None

        def close(self) -> None:
            return None

    def _fake_python_extract(
        repo_id: str,
        scanned_files: list[ScannedFile],
        symbols: list[SymbolChunk],
        resolution_stats_sink: dict[str, int] | None = None,
        file_imports_sink: list[FileImportRelation] | None = None,
    ) -> list[SemanticRelation]:
        if file_imports_sink is not None:
            file_imports_sink.extend(
                [
                    FileImportRelation(
                        repo_id=repo_id,
                        source_path="src/a.py",
                        target_path="src/pkg/mod.py",
                        target_ref="pkg.mod",
                        target_kind="file",
                        path="src/a.py",
                        line=1,
                        language="python",
                        resolution_method="import_from",
                    ),
                    FileImportRelation(
                        repo_id=repo_id,
                        source_path="src/a.py",
                        target_ref="requests",
                        target_kind="external",
                        path="src/a.py",
                        line=2,
                        language="python",
                        resolution_method="import",
                    ),
                ]
            )
        return []

    _patch_pipeline_settings(
        patch_module_settings,
        tmp_path,
        semantic_graph_enabled=True,
        semantic_graph_java_enabled=False,
        semantic_graph_javascript_enabled=False,
        semantic_graph_typescript_enabled=False,
    )
    monkeypatch.setattr(pipeline, "GraphBuilder", _FakeGraphBuilder)
    monkeypatch.setattr(
        pipeline,
        "extract_python_semantic_relations",
        _fake_python_extract,
    )
    monkeypatch.setattr(
        pipeline,
        "extract_java_semantic_relations",
        lambda repo_id, scanned_files, symbols, resolution_stats_sink=None: [],
    )
    monkeypatch.setattr(
        pipeline,
        "extract_javascript_semantic_relations",
        lambda repo_id, scanned_files, symbols, resolution_stats_sink=None: [],
    )
    monkeypatch.setattr(
        pipeline,
        "extract_typescript_semantic_relations",
        lambda repo_id, scanned_files, symbols, resolution_stats_sink=None: [],
    )

    pipeline._index_graph(
        repo_id="rp",
        scanned_files=scanned,
        symbols=symbols,
        diagnostics_sink=diagnostics,
    )

    assert diagnostics["semantic_graph"]["python_top_level_file_import_count"] == 2
    assert (
        diagnostics["semantic_graph"]["python_top_level_file_import_internal_count"]
        == 1
    )
    assert (
        diagnostics["semantic_graph"]["python_top_level_file_import_external_count"]
        == 1
    )


def test_index_graph_normalizes_file_import_resolution_methods_and_reports_counts(
    monkeypatch: pytest.MonkeyPatch,
    patch_module_settings,
    tmp_path: Path,
) -> None:
    """Normaliza resolution_method de file imports y expone conteos canónicos."""
    scanned = [
        ScannedFile(
            path="src/a.py",
            language="python",
            content="from pkg import mod\n",
        ),
        ScannedFile(
            path="src/a.js",
            language="javascript",
            content="import Base from './base';\n",
        ),
    ]
    symbols = [
        SymbolChunk(
            id="sp1",
            repo_id="rp",
            path="src/a.py",
            language="python",
            symbol_name="a",
            symbol_type="function",
            start_line=1,
            end_line=1,
            snippet="def a():\n    pass",
        )
    ]
    diagnostics: dict[str, object] = {}
    captured: dict[str, object] = {}

    class _FakeGraphBuilder:
        def upsert_repo_graph(
            self,
            repo_id: str,
            scanned_files: list[ScannedFile],
            symbols: list[SymbolChunk],
            semantic_relations: list[SemanticRelation] | None = None,
            file_import_relations=None,
        ) -> None:
            captured["file_import_relations"] = file_import_relations or []

        def close(self) -> None:
            return None

    def _fake_python_extract(
        repo_id: str,
        scanned_files: list[ScannedFile],
        symbols: list[SymbolChunk],
        resolution_stats_sink: dict[str, int] | None = None,
        file_imports_sink: list[FileImportRelation] | None = None,
    ) -> list[SemanticRelation]:
        if file_imports_sink is not None:
            file_imports_sink.extend(
                [
                    FileImportRelation(
                        repo_id=repo_id,
                        source_path="src/a.py",
                        target_path="src/pkg/mod.py",
                        target_ref="pkg.mod",
                        target_kind="file",
                        path="src/a.py",
                        line=1,
                        language="python",
                        resolution_method="import_from",
                    ),
                    FileImportRelation(
                        repo_id=repo_id,
                        source_path="src/a.py",
                        target_path=None,
                        target_ref="requests",
                        target_kind="external",
                        path="src/a.py",
                        line=2,
                        language="python",
                        resolution_method="import",
                    ),
                ]
            )
        return []

    def _fake_javascript_extract(
        repo_id: str,
        scanned_files: list[ScannedFile],
        symbols: list[SymbolChunk],
        resolution_stats_sink: dict[str, int] | None = None,
        file_imports_sink: list[FileImportRelation] | None = None,
    ) -> list[SemanticRelation]:
        if file_imports_sink is not None:
            file_imports_sink.extend(
                [
                    FileImportRelation(
                        repo_id=repo_id,
                        source_path="src/a.js",
                        target_path="src/base.js",
                        target_ref="./base",
                        target_kind="file",
                        path="src/a.js",
                        line=1,
                        language="javascript",
                        resolution_method="path",
                    )
                ]
            )
        return []

    _patch_pipeline_settings(
        patch_module_settings,
        tmp_path,
        semantic_graph_enabled=True,
        semantic_graph_java_enabled=False,
        semantic_graph_javascript_enabled=True,
        semantic_graph_typescript_enabled=False,
    )
    monkeypatch.setattr(pipeline, "GraphBuilder", _FakeGraphBuilder)
    monkeypatch.setattr(pipeline, "extract_python_semantic_relations", _fake_python_extract)
    monkeypatch.setattr(
        pipeline,
        "extract_javascript_semantic_relations",
        _fake_javascript_extract,
    )
    monkeypatch.setattr(
        pipeline,
        "extract_java_semantic_relations",
        lambda repo_id, scanned_files, symbols, resolution_stats_sink=None: [],
    )
    monkeypatch.setattr(
        pipeline,
        "extract_typescript_semantic_relations",
        lambda repo_id, scanned_files, symbols, resolution_stats_sink=None: [],
    )

    pipeline._index_graph(
        repo_id="rp",
        scanned_files=scanned,
        symbols=symbols,
        diagnostics_sink=diagnostics,
    )

    normalized_methods = {
        (item.language, item.target_ref): item.resolution_method
        for item in captured["file_import_relations"]
    }
    assert normalized_methods == {
        ("python", "pkg.mod"): "import_path",
        ("python", "requests"): "import_path",
        ("javascript", "./base"): "import_path",
    }
    assert diagnostics["semantic_graph"]["file_import_resolution_counts"] == {
        "import_path": 3
    }
    assert diagnostics["semantic_graph"]["file_import_resolution_counts_by_language"] == {
        "javascript": {"import_path": 1},
        "python": {"import_path": 2},
    }
    assert diagnostics["semantic_graph"]["file_import_counts_by_language"] == {
        "javascript": {"total": 1, "internal": 1, "external": 0},
        "python": {"total": 2, "internal": 1, "external": 1},
    }


def test_index_graph_runs_typescript_semantics_only_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
    patch_module_settings,
    tmp_path: Path,
) -> None:
    """Ejecuta extractor TypeScript solo cuando el flag dedicado está activo."""
    scanned = [
        ScannedFile(
            path="src/a.ts",
            language="typescript",
            content="export function run() { helper(); }",
        )
    ]
    symbols = [
        SymbolChunk(
            id="st1",
            repo_id="rt",
            path="src/a.ts",
            language="typescript",
            symbol_name="run",
            symbol_type="function",
            start_line=1,
            end_line=1,
            snippet="export function run() { helper(); }",
        )
    ]
    diagnostics: dict[str, object] = {}

    class _FakeGraphBuilder:
        def upsert_repo_graph(
            self,
            repo_id: str,
            scanned_files: list[ScannedFile],
            symbols: list[SymbolChunk],
            semantic_relations: list[SemanticRelation] | None = None,
            file_import_relations=None,
        ) -> None:
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr(pipeline, "GraphBuilder", _FakeGraphBuilder)
    monkeypatch.setattr(
        pipeline,
        "extract_python_semantic_relations",
        lambda repo_id, scanned_files, symbols, resolution_stats_sink=None, file_imports_sink=None: [],
    )
    monkeypatch.setattr(
        pipeline,
        "extract_java_semantic_relations",
        lambda repo_id, scanned_files, symbols, resolution_stats_sink=None: [],
    )

    def _raise_if_called(*args, **kwargs):
        raise RuntimeError("typescript extractor should not run")

    monkeypatch.setattr(
        pipeline,
        "extract_typescript_semantic_relations",
        _raise_if_called,
    )
    _patch_pipeline_settings(
        patch_module_settings,
        tmp_path,
        semantic_graph_enabled=True,
        semantic_graph_java_enabled=False,
        semantic_graph_typescript_enabled=False,
    )
    pipeline._index_graph(
        repo_id="rt",
        scanned_files=scanned,
        symbols=symbols,
        diagnostics_sink=diagnostics,
    )

    _patch_pipeline_settings(
        patch_module_settings,
        tmp_path,
        semantic_graph_enabled=True,
        semantic_graph_java_enabled=False,
        semantic_graph_typescript_enabled=True,
    )
    monkeypatch.setattr(
        pipeline,
        "extract_typescript_semantic_relations",
        lambda repo_id, scanned_files, symbols, resolution_stats_sink=None: [
            SemanticRelation(
                repo_id=repo_id,
                source_symbol_id="st1",
                relation_type="CALLS",
                target_symbol_id=None,
                target_ref="helper",
                target_kind="external",
                path="src/a.ts",
                line=1,
                confidence=0.75,
                language="typescript",
            )
        ],
    )
    pipeline._index_graph(
        repo_id="rt",
        scanned_files=scanned,
        symbols=symbols,
        diagnostics_sink=diagnostics,
    )

    assert diagnostics["semantic_graph"]["relation_counts"] == 1
    assert diagnostics["semantic_graph"]["relation_counts_by_type"] == {
        "CALLS": 1
    }
    assert diagnostics["semantic_graph"]["typescript_cross_file_resolved_count"] == 0
    assert diagnostics["semantic_graph"]["typescript_resolution_source_counts"] == {}


def test_index_graph_reports_typescript_cross_file_resolved_count(
    monkeypatch: pytest.MonkeyPatch,
    patch_module_settings,
    tmp_path: Path,
) -> None:
    """Cuenta relaciones TypeScript resueltas hacia símbolos en archivos distintos."""
    scanned = [
        ScannedFile(path="src/base.ts", language="typescript", content="export class Base {}"),
        ScannedFile(path="src/service.ts", language="typescript", content="export class Service extends Base {}"),
    ]
    symbols = [
        SymbolChunk(
            id="st-base",
            repo_id="rt",
            path="src/base.ts",
            language="typescript",
            symbol_name="Base",
            symbol_type="class",
            start_line=1,
            end_line=1,
            snippet="export class Base {}",
        ),
        SymbolChunk(
            id="st-service",
            repo_id="rt",
            path="src/service.ts",
            language="typescript",
            symbol_name="Service",
            symbol_type="class",
            start_line=1,
            end_line=1,
            snippet="export class Service extends Base {}",
        ),
    ]
    diagnostics: dict[str, object] = {}

    class _FakeGraphBuilder:
        def upsert_repo_graph(
            self,
            repo_id: str,
            scanned_files: list[ScannedFile],
            symbols: list[SymbolChunk],
            semantic_relations: list[SemanticRelation] | None = None,
            file_import_relations=None,
        ) -> None:
            return None

        def close(self) -> None:
            return None

    _patch_pipeline_settings(
        patch_module_settings,
        tmp_path,
        semantic_graph_enabled=True,
        semantic_graph_java_enabled=False,
        semantic_graph_javascript_enabled=False,
        semantic_graph_typescript_enabled=True,
    )
    monkeypatch.setattr(pipeline, "GraphBuilder", _FakeGraphBuilder)
    monkeypatch.setattr(
        pipeline,
        "extract_python_semantic_relations",
        lambda repo_id, scanned_files, symbols, resolution_stats_sink=None, file_imports_sink=None: [],
    )
    monkeypatch.setattr(
        pipeline,
        "extract_java_semantic_relations",
        lambda repo_id, scanned_files, symbols, resolution_stats_sink=None: [],
    )
    monkeypatch.setattr(
        pipeline,
        "extract_javascript_semantic_relations",
        lambda repo_id, scanned_files, symbols, resolution_stats_sink=None: [],
    )
    monkeypatch.setattr(
        pipeline,
        "extract_typescript_semantic_relations",
        lambda repo_id, scanned_files, symbols, resolution_stats_sink=None: [
            SemanticRelation(
                repo_id=repo_id,
                source_symbol_id="st-service",
                relation_type="EXTENDS",
                target_symbol_id="st-base",
                target_ref="Base",
                target_kind="symbol",
                path="src/service.ts",
                line=1,
                confidence=0.95,
                language="typescript",
            )
        ],
    )

    pipeline._index_graph(
        repo_id="rt",
        scanned_files=scanned,
        symbols=symbols,
        diagnostics_sink=diagnostics,
    )

    assert diagnostics["semantic_graph"]["typescript_cross_file_resolved_count"] == 1


def test_index_graph_runs_javascript_semantics_only_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
    patch_module_settings,
    tmp_path: Path,
) -> None:
    """Ejecuta extractor JavaScript solo cuando el flag dedicado está activo."""
    scanned = [
        ScannedFile(
            path="src/a.js",
            language="javascript",
            content="export function run() { helper(); }",
        )
    ]
    symbols = [
        SymbolChunk(
            id="sj1",
            repo_id="rj",
            path="src/a.js",
            language="javascript",
            symbol_name="run",
            symbol_type="function",
            start_line=1,
            end_line=1,
            snippet="export function run() { helper(); }",
        )
    ]
    diagnostics: dict[str, object] = {}

    class _FakeGraphBuilder:
        def upsert_repo_graph(
            self,
            repo_id: str,
            scanned_files: list[ScannedFile],
            symbols: list[SymbolChunk],
            semantic_relations: list[SemanticRelation] | None = None,
            file_import_relations=None,
        ) -> None:
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr(pipeline, "GraphBuilder", _FakeGraphBuilder)
    monkeypatch.setattr(
        pipeline,
        "extract_python_semantic_relations",
        lambda repo_id, scanned_files, symbols, resolution_stats_sink=None, file_imports_sink=None: [],
    )
    monkeypatch.setattr(
        pipeline,
        "extract_java_semantic_relations",
        lambda repo_id, scanned_files, symbols, resolution_stats_sink=None: [],
    )
    monkeypatch.setattr(
        pipeline,
        "extract_typescript_semantic_relations",
        lambda repo_id, scanned_files, symbols, resolution_stats_sink=None: [],
    )

    def _raise_if_called(*args, **kwargs):
        raise RuntimeError("javascript extractor should not run")

    monkeypatch.setattr(
        pipeline,
        "extract_javascript_semantic_relations",
        _raise_if_called,
    )
    _patch_pipeline_settings(
        patch_module_settings,
        tmp_path,
        semantic_graph_enabled=True,
        semantic_graph_java_enabled=False,
        semantic_graph_javascript_enabled=False,
        semantic_graph_typescript_enabled=False,
    )
    pipeline._index_graph(
        repo_id="rj",
        scanned_files=scanned,
        symbols=symbols,
        diagnostics_sink=diagnostics,
    )

    called: dict[str, bool] = {"value": False}

    def _fake_extract_javascript(*args, **kwargs):
        called["value"] = True
        return []

    monkeypatch.setattr(
        pipeline,
        "extract_javascript_semantic_relations",
        _fake_extract_javascript,
    )
    _patch_pipeline_settings(
        patch_module_settings,
        tmp_path,
        semantic_graph_enabled=True,
        semantic_graph_java_enabled=False,
        semantic_graph_javascript_enabled=True,
        semantic_graph_typescript_enabled=False,
    )
    pipeline._index_graph(
        repo_id="rj",
        scanned_files=scanned,
        symbols=symbols,
        diagnostics_sink=diagnostics,
    )

    assert called["value"] is True


def test_index_graph_reports_javascript_cross_file_resolved_count(
    monkeypatch: pytest.MonkeyPatch,
    patch_module_settings,
    tmp_path: Path,
) -> None:
    """Cuenta relaciones JavaScript resueltas hacia símbolos en archivos distintos."""
    scanned = [
        ScannedFile(path="src/base.js", language="javascript", content="export class Base {}"),
        ScannedFile(path="src/service.js", language="javascript", content="export class Service extends Base {}"),
    ]
    symbols = [
        SymbolChunk(
            id="sj-base",
            repo_id="rj",
            path="src/base.js",
            language="javascript",
            symbol_name="Base",
            symbol_type="class",
            start_line=1,
            end_line=1,
            snippet="export class Base {}",
        ),
        SymbolChunk(
            id="sj-service",
            repo_id="rj",
            path="src/service.js",
            language="javascript",
            symbol_name="Service",
            symbol_type="class",
            start_line=1,
            end_line=1,
            snippet="export class Service extends Base {}",
        ),
    ]
    diagnostics: dict[str, object] = {}

    class _FakeGraphBuilder:
        def upsert_repo_graph(
            self,
            repo_id: str,
            scanned_files: list[ScannedFile],
            symbols: list[SymbolChunk],
            semantic_relations: list[SemanticRelation] | None = None,
            file_import_relations=None,
        ) -> None:
            return None

        def close(self) -> None:
            return None

    _patch_pipeline_settings(
        patch_module_settings,
        tmp_path,
        semantic_graph_enabled=True,
        semantic_graph_java_enabled=False,
        semantic_graph_javascript_enabled=True,
        semantic_graph_typescript_enabled=False,
    )
    monkeypatch.setattr(pipeline, "GraphBuilder", _FakeGraphBuilder)
    monkeypatch.setattr(
        pipeline,
        "extract_python_semantic_relations",
        lambda repo_id, scanned_files, symbols, resolution_stats_sink=None, file_imports_sink=None: [],
    )
    monkeypatch.setattr(
        pipeline,
        "extract_java_semantic_relations",
        lambda repo_id, scanned_files, symbols, resolution_stats_sink=None: [],
    )
    monkeypatch.setattr(
        pipeline,
        "extract_typescript_semantic_relations",
        lambda repo_id, scanned_files, symbols, resolution_stats_sink=None: [],
    )
    monkeypatch.setattr(
        pipeline,
        "extract_javascript_semantic_relations",
        lambda repo_id, scanned_files, symbols, resolution_stats_sink=None: [
            SemanticRelation(
                repo_id=repo_id,
                source_symbol_id="sj-service",
                relation_type="EXTENDS",
                target_symbol_id="sj-base",
                target_ref="Base",
                target_kind="symbol",
                path="src/service.js",
                line=1,
                confidence=0.95,
                language="javascript",
            )
        ],
    )

    pipeline._index_graph(
        repo_id="rj",
        scanned_files=scanned,
        symbols=symbols,
        diagnostics_sink=diagnostics,
    )

    assert diagnostics["semantic_graph"]["javascript_cross_file_resolved_count"] == 1


def test_ingest_repository_fails_when_purge_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    patch_module_settings,
) -> None:
    """Aborta la ingesta si el purge previo falla para evitar índices mezclados."""

    _patch_pipeline_settings(patch_module_settings, tmp_path)
    monkeypatch.setattr(
        pipeline,
        "clone_repository",
        lambda repo_url, destination_root, branch, commit, **kwargs: (
            "r1",
            tmp_path,
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "_repo_has_existing_index_data",
        lambda repo_id, logger: True,
    )

    def _fail_purge(repo_id: str, logger) -> None:
        raise RuntimeError("chroma lock")

    monkeypatch.setattr(pipeline, "_purge_repo_indices", _fail_purge)

    logs: list[str] = []
    with pytest.raises(RuntimeError) as exc_info:
        pipeline.ingest_repository(
            provider="github",
            repo_url="https://example.com/repo.git",
            branch="main",
            commit=None,
            token=None,
            logger=logs.append,
        )

    assert "No se pudo limpiar la data indexada previa" in str(exc_info.value)


