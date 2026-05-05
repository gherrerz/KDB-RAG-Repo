"""Pruebas para el comportamiento de orquestación de la canalización de ingesta."""

from pathlib import Path

import pytest

from coderag.core.models import RepoAuthConfig
from coderag.core.models import ScannedFile, SemanticRelation, SymbolChunk
from coderag.ingestion import pipeline


def test_ingest_repository_continues_on_graph_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
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

    class _Settings:
        workspace_path = tmp_path
        scan_max_file_size_bytes = 12345
        scan_excluded_dirs = ".git,node_modules"
        scan_excluded_extensions = ".png,.zip"
        scan_excluded_files = ".gitignore,.env"

    received_scan_args: dict[str, object] = {}

    def _fake_scan_repository_with_stats(
        repo_path: Path,
        max_file_size: int = 200_000,
        excluded_dirs: set[str] | None = None,
        excluded_extensions: set[str] | None = None,
        excluded_files: set[str] | None = None,
    ) -> tuple[list[ScannedFile], dict[str, int]]:
        received_scan_args["repo_path"] = repo_path
        received_scan_args["max_file_size"] = max_file_size
        received_scan_args["excluded_dirs"] = excluded_dirs or set()
        received_scan_args["excluded_extensions"] = excluded_extensions or set()
        received_scan_args["excluded_files"] = excluded_files or set()
        return scanned, {
            "visited": 1,
            "scanned": 1,
            "excluded_dir": 0,
            "excluded_extension": 0,
            "excluded_file": 0,
            "excluded_size": 0,
            "excluded_decode": 0,
        }

    monkeypatch.setattr(pipeline, "get_settings", lambda: _Settings())
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
        "_index_bm25",
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
    ) -> None:
        raise RuntimeError("neo4j auth")

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
    assert any("Observabilidad símbolos:" in item for item in logs)
    assert any("Advertencia: grafo Neo4j no disponible" in item for item in logs)
    assert logs[-1] == "Ingesta finalizada"


def test_ingest_repository_purges_existing_repo_before_reindex(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
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

    class _Settings:
        workspace_path = tmp_path
        scan_max_file_size_bytes = 12345
        scan_excluded_dirs = ".git,node_modules"
        scan_excluded_extensions = ".png,.zip"
        scan_excluded_files = ".gitignore,.env"

    call_order: list[str] = []

    monkeypatch.setattr(pipeline, "get_settings", lambda: _Settings())
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
        "_index_bm25",
        lambda repo_id, scanned_files, chunks: call_order.append("index_bm25"),
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

    assert call_order == ["purge", "index_vectors", "index_bm25", "index_graph"]
    assert any("Repositorio existente detectado" in item for item in logs)
    assert any("Observabilidad símbolos:" in item for item in logs)


def test_ingest_repository_forwards_ssh_runtime_config_to_clone(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
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

    class _Settings:
        workspace_path = tmp_path
        scan_max_file_size_bytes = 12345
        scan_excluded_dirs = ".git,node_modules"
        scan_excluded_extensions = ".png,.zip"
        scan_excluded_files = ".gitignore,.env"
        git_ssh_key_content = "PRIVATE KEY FROM ENV"
        git_ssh_key_content_b64 = ""
        git_ssh_known_hosts_content = "bitbucket.example ssh-ed25519 AAAA"
        git_ssh_known_hosts_content_b64 = ""
        git_ssh_strict_host_key_checking = "yes"

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

    monkeypatch.setattr(pipeline, "get_settings", lambda: _Settings())
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
        "_index_bm25",
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

    class _Settings:
        workspace_path = tmp_path
        scan_max_file_size_bytes = 12345
        scan_excluded_dirs = ".git,node_modules"
        scan_excluded_extensions = ".png,.zip"
        scan_excluded_files = ".gitignore,.env"
        git_ssh_key_content = ""
        git_ssh_key_content_b64 = ""
        git_ssh_known_hosts_content = ""
        git_ssh_known_hosts_content_b64 = ""
        git_ssh_strict_host_key_checking = "yes"

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

    monkeypatch.setattr(pipeline, "get_settings", lambda: _Settings())
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
        "_index_bm25",
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

    class _Settings:
        semantic_graph_enabled = True

    class _FakeGraphBuilder:
        def upsert_repo_graph(
            self,
            repo_id: str,
            scanned_files: list[ScannedFile],
            symbols: list[SymbolChunk],
            semantic_relations: list[SemanticRelation] | None = None,
        ) -> None:
            captured["repo_id"] = repo_id
            captured["semantic_relations"] = semantic_relations or []

        def close(self) -> None:
            return None

    monkeypatch.setattr(pipeline, "get_settings", lambda: _Settings())
    monkeypatch.setattr(pipeline, "GraphBuilder", _FakeGraphBuilder)
    monkeypatch.setattr(
        pipeline,
        "extract_python_semantic_relations",
        lambda repo_id, scanned_files, symbols: [
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
    assert diagnostics["semantic_graph"]["java_cross_file_resolved_count"] == 0
    assert diagnostics["semantic_graph"]["java_cross_file_resolved_by_type"] == {}
    assert diagnostics["semantic_graph"]["java_resolution_source_counts"] == {}
    assert diagnostics["semantic_graph"]["unresolved_count"] == 1
    assert diagnostics["semantic_graph"]["unresolved_by_type"] == {
        "CALLS": 1
    }


def test_index_graph_falls_back_when_semantic_extraction_fails(
    monkeypatch: pytest.MonkeyPatch,
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

    class _Settings:
        semantic_graph_enabled = True

    class _FakeGraphBuilder:
        def upsert_repo_graph(
            self,
            repo_id: str,
            scanned_files: list[ScannedFile],
            symbols: list[SymbolChunk],
            semantic_relations: list[SemanticRelation] | None = None,
        ) -> None:
            captured["semantic_relations"] = semantic_relations or []

        def close(self) -> None:
            return None

    monkeypatch.setattr(pipeline, "get_settings", lambda: _Settings())
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

    class _SettingsDisabled:
        semantic_graph_enabled = True
        semantic_graph_java_enabled = False

    class _SettingsEnabled:
        semantic_graph_enabled = True
        semantic_graph_java_enabled = True

    class _FakeGraphBuilder:
        def upsert_repo_graph(
            self,
            repo_id: str,
            scanned_files: list[ScannedFile],
            symbols: list[SymbolChunk],
            semantic_relations: list[SemanticRelation] | None = None,
        ) -> None:
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr(pipeline, "GraphBuilder", _FakeGraphBuilder)
    monkeypatch.setattr(
        pipeline,
        "extract_python_semantic_relations",
        lambda repo_id, scanned_files, symbols: [],
    )

    def _raise_if_called(*args, **kwargs):
        raise RuntimeError("java extractor should not run")

    monkeypatch.setattr(
        pipeline,
        "extract_java_semantic_relations",
        _raise_if_called,
    )
    monkeypatch.setattr(pipeline, "get_settings", lambda: _SettingsDisabled())
    pipeline._index_graph(
        repo_id="rj",
        scanned_files=scanned,
        symbols=symbols,
        diagnostics_sink=diagnostics,
    )

    monkeypatch.setattr(pipeline, "get_settings", lambda: _SettingsEnabled())
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

    class _Settings:
        semantic_graph_enabled = True
        semantic_graph_java_enabled = True

    class _FakeGraphBuilder:
        def upsert_repo_graph(
            self,
            repo_id: str,
            scanned_files: list[ScannedFile],
            symbols: list[SymbolChunk],
            semantic_relations: list[SemanticRelation] | None = None,
        ) -> None:
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr(pipeline, "get_settings", lambda: _Settings())
    monkeypatch.setattr(pipeline, "GraphBuilder", _FakeGraphBuilder)
    monkeypatch.setattr(
        pipeline,
        "extract_python_semantic_relations",
        lambda repo_id, scanned_files, symbols: [],
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
    assert diagnostics["semantic_graph"]["java_cross_file_resolved_by_type"] == {
        "IMPLEMENTS": 1
    }
    assert diagnostics["semantic_graph"]["java_resolution_source_counts"] == {}


def test_index_graph_reports_java_resolution_source_counts(
    monkeypatch: pytest.MonkeyPatch,
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

    class _Settings:
        semantic_graph_enabled = True
        semantic_graph_java_enabled = True

    class _FakeGraphBuilder:
        def upsert_repo_graph(
            self,
            repo_id: str,
            scanned_files: list[ScannedFile],
            symbols: list[SymbolChunk],
            semantic_relations: list[SemanticRelation] | None = None,
        ) -> None:
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr(pipeline, "get_settings", lambda: _Settings())
    monkeypatch.setattr(pipeline, "GraphBuilder", _FakeGraphBuilder)
    monkeypatch.setattr(
        pipeline,
        "extract_python_semantic_relations",
        lambda repo_id, scanned_files, symbols: [],
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


def test_index_graph_runs_typescript_semantics_only_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
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

    class _SettingsDisabled:
        semantic_graph_enabled = True
        semantic_graph_java_enabled = False
        semantic_graph_typescript_enabled = False

    class _SettingsEnabled:
        semantic_graph_enabled = True
        semantic_graph_java_enabled = False
        semantic_graph_typescript_enabled = True

    class _FakeGraphBuilder:
        def upsert_repo_graph(
            self,
            repo_id: str,
            scanned_files: list[ScannedFile],
            symbols: list[SymbolChunk],
            semantic_relations: list[SemanticRelation] | None = None,
        ) -> None:
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr(pipeline, "GraphBuilder", _FakeGraphBuilder)
    monkeypatch.setattr(
        pipeline,
        "extract_python_semantic_relations",
        lambda repo_id, scanned_files, symbols: [],
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
    monkeypatch.setattr(pipeline, "get_settings", lambda: _SettingsDisabled())
    pipeline._index_graph(
        repo_id="rt",
        scanned_files=scanned,
        symbols=symbols,
        diagnostics_sink=diagnostics,
    )

    monkeypatch.setattr(pipeline, "get_settings", lambda: _SettingsEnabled())
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
    assert diagnostics["semantic_graph"]["typescript_resolution_source_counts"] == {}


def test_index_graph_runs_javascript_semantics_only_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
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

    class _SettingsDisabled:
        semantic_graph_enabled = True
        semantic_graph_java_enabled = False
        semantic_graph_javascript_enabled = False
        semantic_graph_typescript_enabled = False

    class _SettingsEnabled:
        semantic_graph_enabled = True
        semantic_graph_java_enabled = False
        semantic_graph_javascript_enabled = True
        semantic_graph_typescript_enabled = False

    class _FakeGraphBuilder:
        def upsert_repo_graph(
            self,
            repo_id: str,
            scanned_files: list[ScannedFile],
            symbols: list[SymbolChunk],
            semantic_relations: list[SemanticRelation] | None = None,
        ) -> None:
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr(pipeline, "GraphBuilder", _FakeGraphBuilder)
    monkeypatch.setattr(
        pipeline,
        "extract_python_semantic_relations",
        lambda repo_id, scanned_files, symbols: [],
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
    monkeypatch.setattr(pipeline, "get_settings", lambda: _SettingsDisabled())
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
    monkeypatch.setattr(pipeline, "get_settings", lambda: _SettingsEnabled())
    pipeline._index_graph(
        repo_id="rj",
        scanned_files=scanned,
        symbols=symbols,
        diagnostics_sink=diagnostics,
    )

    assert called["value"] is True


def test_ingest_repository_fails_when_purge_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Aborta la ingesta si el purge previo falla para evitar índices mezclados."""

    class _Settings:
        workspace_path = tmp_path
        scan_max_file_size_bytes = 12345
        scan_excluded_dirs = ".git,node_modules"
        scan_excluded_extensions = ".png,.zip"
        scan_excluded_files = ".gitignore,.env"

    monkeypatch.setattr(pipeline, "get_settings", lambda: _Settings())
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
