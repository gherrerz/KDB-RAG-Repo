"""Pruebas unitarias para operaciones de limpieza en GraphBuilder."""

from typing import Any

import pytest

from coderag.core.models import (
    FileImportRelation,
    ScannedFile,
    SemanticRelation,
    SymbolChunk,
)
import coderag.ingestion.graph_builder as graph_builder_module
from coderag.ingestion.graph_builder import GraphBuilder


class _FakeCounters:
    """Contadores de resumen Neo4j simulados."""

    def __init__(self, nodes_deleted: int) -> None:
        """Inicializa el contador de nodos eliminados."""
        self.nodes_deleted = nodes_deleted


class _FakeResult:
    """Resultado Neo4j simulado para consume/single."""

    def __init__(self, nodes_deleted: int, total_nodes: int) -> None:
        """Guarda valores de conteo para pruebas."""
        self._nodes_deleted = nodes_deleted
        self._total_nodes = total_nodes

    def consume(self) -> Any:
        """Devuelve objeto con counters emulando Neo4j."""
        class _Summary:
            def __init__(self, counters: _FakeCounters) -> None:
                self.counters = counters

        return _Summary(_FakeCounters(self._nodes_deleted))

    def single(self) -> dict[str, int]:
        """Devuelve una fila con conteo total de nodos."""
        return {"total": self._total_nodes}

    def __iter__(self):
        """Permite iterar resultados vacíos en consultas de listado."""
        return iter(())


class _FakeSession:
    """Sesión Neo4j simulada para consultas de conteo y borrado."""

    def __init__(self, nodes_deleted: int, total_nodes: int) -> None:
        """Configura conteos predefinidos para resultados."""
        self.nodes_deleted = nodes_deleted
        self.total_nodes = total_nodes
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def run(self, query: str, **kwargs: Any) -> _FakeResult:
        """Retorna resultados simulados según la consulta."""
        self.calls.append((query, kwargs))
        if "DETACH DELETE" in query:
            return _FakeResult(nodes_deleted=self.nodes_deleted, total_nodes=0)
        return _FakeResult(nodes_deleted=0, total_nodes=self.total_nodes)

    def __enter__(self) -> "_FakeSession":
        """Soporte de context manager."""
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        """Soporte de context manager sin cleanup adicional."""
        return None


class _FakeDriver:
    """Driver Neo4j simulado que entrega sesiones de prueba."""

    def __init__(self, nodes_deleted: int, total_nodes: int) -> None:
        """Guarda conteos para inyectarlos en sesiones."""
        self.nodes_deleted = nodes_deleted
        self.total_nodes = total_nodes
        self.last_session: _FakeSession | None = None

    def session(self) -> _FakeSession:
        """Crea una sesión simulada."""
        self.last_session = _FakeSession(
            nodes_deleted=self.nodes_deleted,
            total_nodes=self.total_nodes,
        )
        return self.last_session


def test_delete_repo_subgraph_returns_deleted_nodes() -> None:
    """Devuelve la cantidad de nodos borrados por repo_id."""
    builder = GraphBuilder.__new__(GraphBuilder)
    builder.driver = _FakeDriver(nodes_deleted=7, total_nodes=7)

    deleted = builder.delete_repo_subgraph("repo-x")

    assert deleted == 7


def test_has_repo_data_reads_graph_count() -> None:
    """Informa True/False según el conteo de nodos por repo_id."""
    builder = GraphBuilder.__new__(GraphBuilder)
    builder.driver = _FakeDriver(nodes_deleted=0, total_nodes=3)
    assert builder.has_repo_data("repo-x") is True

    builder.driver = _FakeDriver(nodes_deleted=0, total_nodes=0)
    assert builder.has_repo_data("repo-x") is False


def test_upsert_repo_graph_persists_semantic_relations() -> None:
    """Inserta relaciones semánticas resueltas y externas por tipo."""
    builder = GraphBuilder.__new__(GraphBuilder)
    driver = _FakeDriver(nodes_deleted=0, total_nodes=0)
    builder.driver = driver

    relations = [
        SemanticRelation(
            repo_id="repo-x",
            source_symbol_id="source-1",
            relation_type="CALLS",
            target_symbol_id="target-1",
            target_ref="helper",
            target_kind="symbol",
            path="pkg/a.py",
            line=10,
            confidence=0.9,
            language="python",
            resolution_method="local",
        ),
        SemanticRelation(
            repo_id="repo-x",
            source_symbol_id="source-1",
            relation_type="IMPORTS",
            target_symbol_id=None,
            target_ref="json",
            target_kind="external",
            path="pkg/a.py",
            line=11,
            confidence=0.8,
            language="python",
            resolution_method="unresolved",
        ),
    ]

    builder.upsert_repo_graph(
        repo_id="repo-x",
        scanned_files=[],
        symbols=[],
        semantic_relations=relations,
    )

    session = driver.last_session
    assert session is not None
    assert any(":CALLS" in query for query, _ in session.calls)
    assert any(":IMPORTS" in query for query, _ in session.calls)
    import_call = next(kwargs for query, kwargs in session.calls if ":IMPORTS" in query)
    assert import_call["rows"][0]["resolution_method"] == "unresolved"
    assert import_call["rows"][0]["source_path"] == "pkg/a.py"
    import_query = next(query for query, _ in session.calls if ":IMPORTS" in query)
    assert "ref: row.target_ref" in import_query
    assert "source_path: row.source_path" not in import_query
    assert "r.source_path = row.source_path" in import_query
    assert "resolution_method = row.resolution_method" in import_query


def test_derive_file_dependency_edges_deduplicates_resolved_relations() -> None:
    """Colapsa relaciones resueltas entre símbolos a pares archivo->archivo."""
    symbols = [
        SymbolChunk(
            id="source-1",
            repo_id="repo-x",
            path="pkg/a.py",
            language="python",
            symbol_name="run",
            symbol_type="function",
            start_line=1,
            end_line=3,
            snippet="def run():\n    helper()",
        ),
        SymbolChunk(
            id="target-1",
            repo_id="repo-x",
            path="pkg/b.py",
            language="python",
            symbol_name="helper",
            symbol_type="function",
            start_line=1,
            end_line=2,
            snippet="def helper():\n    return 1",
        ),
    ]
    relations = [
        SemanticRelation(
            repo_id="repo-x",
            source_symbol_id="source-1",
            relation_type="CALLS",
            target_symbol_id="target-1",
            target_ref="helper",
            target_kind="symbol",
            path="pkg/a.py",
            line=2,
            confidence=0.9,
            language="python",
        ),
        SemanticRelation(
            repo_id="repo-x",
            source_symbol_id="source-1",
            relation_type="IMPORTS",
            target_symbol_id="target-1",
            target_ref="pkg.b.helper",
            target_kind="symbol",
            path="pkg/a.py",
            line=1,
            confidence=0.8,
            language="python",
        ),
    ]

    rows = GraphBuilder._derive_file_dependency_edges(relations, symbols)

    assert rows == [
        {
            "source_path": "pkg/a.py",
            "target_path": "pkg/b.py",
            "count": 2,
            "relation_types": ["CALLS", "IMPORTS"],
        }
    ]


def test_upsert_repo_graph_persists_file_dependency_edges_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persiste IMPORTS_FILE derivados solo cuando el flag dedicado está activo."""
    builder = GraphBuilder.__new__(GraphBuilder)
    driver = _FakeDriver(nodes_deleted=0, total_nodes=0)
    builder.driver = driver

    scanned_files = [
        ScannedFile(path="pkg/a.py", language="python", content="def run():\n    helper()\n"),
        ScannedFile(path="pkg/b.py", language="python", content="def helper():\n    return 1\n"),
    ]
    symbols = [
        SymbolChunk(
            id="source-1",
            repo_id="repo-x",
            path="pkg/a.py",
            language="python",
            symbol_name="run",
            symbol_type="function",
            start_line=1,
            end_line=2,
            snippet="def run():\n    helper()",
        ),
        SymbolChunk(
            id="target-1",
            repo_id="repo-x",
            path="pkg/b.py",
            language="python",
            symbol_name="helper",
            symbol_type="function",
            start_line=1,
            end_line=2,
            snippet="def helper():\n    return 1",
        ),
    ]
    relations = [
        SemanticRelation(
            repo_id="repo-x",
            source_symbol_id="source-1",
            relation_type="CALLS",
            target_symbol_id="target-1",
            target_ref="helper",
            target_kind="symbol",
            path="pkg/a.py",
            line=2,
            confidence=0.9,
            language="python",
        )
    ]

    class _SettingsEnabled:
        semantic_graph_file_edges_enabled = True

    monkeypatch.setattr(
        graph_builder_module,
        "get_settings",
        lambda: _SettingsEnabled(),
    )

    builder.upsert_repo_graph(
        repo_id="repo-x",
        scanned_files=scanned_files,
        symbols=symbols,
        semantic_relations=relations,
    )

    session = driver.last_session
    assert session is not None
    file_edge_calls = [
        kwargs for query, kwargs in session.calls if "IMPORTS_FILE" in query
    ]
    assert len(file_edge_calls) == 1
    assert file_edge_calls[0]["rows"] == [
        {
            "source_path": "pkg/a.py",
            "target_path": "pkg/b.py",
            "count": 1,
            "relation_types": ["CALLS"],
        }
    ]


def test_upsert_repo_graph_skips_file_dependency_edges_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No persiste IMPORTS_FILE cuando el flag de rollout está apagado."""
    builder = GraphBuilder.__new__(GraphBuilder)
    driver = _FakeDriver(nodes_deleted=0, total_nodes=0)
    builder.driver = driver

    class _SettingsDisabled:
        semantic_graph_file_edges_enabled = False

    monkeypatch.setattr(
        graph_builder_module,
        "get_settings",
        lambda: _SettingsDisabled(),
    )

    builder.upsert_repo_graph(
        repo_id="repo-x",
        scanned_files=[],
        symbols=[],
        semantic_relations=[],
    )

    session = driver.last_session
    assert session is not None
    assert not any("IMPORTS_FILE" in query for query, _ in session.calls)


def test_upsert_repo_graph_persists_top_level_python_file_imports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persiste imports top-level Python como aristas File->File y File->ExternalSymbol."""
    builder = GraphBuilder.__new__(GraphBuilder)
    driver = _FakeDriver(nodes_deleted=0, total_nodes=0)
    builder.driver = driver

    class _SettingsEnabled:
        semantic_graph_file_edges_enabled = True

    monkeypatch.setattr(
        graph_builder_module,
        "get_settings",
        lambda: _SettingsEnabled(),
    )

    builder.upsert_repo_graph(
        repo_id="repo-x",
        scanned_files=[
            ScannedFile(path="pkg/a.py", language="python", content="import json\n"),
            ScannedFile(path="pkg/b.py", language="python", content="def helper():\n    return 1\n"),
        ],
        symbols=[],
        semantic_relations=[],
        file_import_relations=[
            FileImportRelation(
                repo_id="repo-x",
                source_path="pkg/a.py",
                target_path="pkg/b.py",
                target_ref="pkg.b.helper",
                target_kind="file",
                path="pkg/a.py",
                line=1,
                language="python",
                resolution_method="qualified",
            ),
            FileImportRelation(
                repo_id="repo-x",
                source_path="pkg/a.py",
                target_path=None,
                target_ref="json",
                target_kind="external",
                path="pkg/a.py",
                line=2,
                language="python",
                resolution_method="unresolved",
            ),
        ],
    )

    session = driver.last_session
    assert session is not None
    file_edge_call = next(
        kwargs for query, kwargs in session.calls if "IMPORTS_FILE" in query
    )
    assert file_edge_call["rows"] == [
        {
            "source_path": "pkg/a.py",
            "target_path": "pkg/b.py",
            "count": 1,
            "relation_types": ["IMPORTS"],
        }
    ]
    external_call = next(
        kwargs for query, kwargs in session.calls if "IMPORTS_EXTERNAL_FILE" in query
    )
    assert external_call["rows"] == [
        {
            "source_path": "pkg/a.py",
            "target_ref": "json",
            "path": "pkg/a.py",
            "line": 2,
            "language": "python",
            "resolution_method": "unresolved",
        }
    ]
    external_query = next(
        query for query, _ in session.calls if "IMPORTS_EXTERNAL_FILE" in query
    )
    assert "ref: row.target_ref" in external_query
    assert "language: row.language\n            })" in external_query
    assert "source_path: row.source_path, target_ref: row.target_ref" in external_query


def test_expand_symbols_uses_literal_hops_in_query() -> None:
    """Evita usar parámetros en rango de hops para Cypher variable-length."""

    class _Record:
        def data(self) -> dict[str, object]:
            return {
                "seed": "s1",
                "labels": ["Symbol"],
                "props": {"name": "n"},
                "edge_count": 1,
                "relation_types": ["CALLS"],
                "relation_confidence_avg": 1.0,
            }

    class _ExpandSession:
        def __init__(self) -> None:
            self.query = ""
            self.kwargs: dict[str, Any] = {}

        def run(self, query: str, **kwargs: Any) -> list[_Record]:
            self.query = query
            self.kwargs = kwargs
            return [_Record()]

        def __enter__(self) -> "_ExpandSession":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    class _ExpandDriver:
        def __init__(self) -> None:
            self.last_session: _ExpandSession | None = None

        def session(self) -> _ExpandSession:
            self.last_session = _ExpandSession()
            return self.last_session

    builder = GraphBuilder.__new__(GraphBuilder)
    driver = _ExpandDriver()
    builder.driver = driver

    records = builder.expand_symbols(
        symbol_ids=["s1"],
        hops=3,
        relation_types=["CALLS"],
        limit=10,
    )

    assert len(records) == 1
    assert driver.last_session is not None
    assert "[*1..3]" in driver.last_session.query
    assert "$hops" not in driver.last_session.query
    assert "hops" not in driver.last_session.kwargs


def test_query_inventory_dependency_uses_file_edges() -> None:
    """Consulta dependencias usando aristas File -> File y File -> External."""

    class _DependencyRecord:
        def __init__(
            self,
            label: str,
            path: str,
            kind: str,
            start_line: int,
            end_line: int,
        ) -> None:
            self._payload = {
                "label": label,
                "path": path,
                "kind": kind,
                "start_line": start_line,
                "end_line": end_line,
            }

        def data(self) -> dict[str, object]:
            return self._payload

    class _DependencySession:
        def __init__(self) -> None:
            self.query = ""
            self.kwargs: dict[str, Any] = {}

        def run(self, query: str, **kwargs: Any) -> list[_DependencyRecord]:
            self.query = query
            self.kwargs = kwargs
            return [
                _DependencyRecord(
                    label="pkg/b.py",
                    path="pkg/b.py",
                    kind="file_dependency",
                    start_line=1,
                    end_line=1,
                ),
                _DependencyRecord(
                    label="requests",
                    path="pkg/a.py",
                    kind="external_dependency",
                    start_line=2,
                    end_line=2,
                ),
            ]

        def __enter__(self) -> "_DependencySession":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    class _DependencyDriver:
        def __init__(self) -> None:
            self.last_session: _DependencySession | None = None

        def session(self) -> _DependencySession:
            self.last_session = _DependencySession()
            return self.last_session

    builder = GraphBuilder.__new__(GraphBuilder)
    driver = _DependencyDriver()
    builder.driver = driver

    records = builder.query_inventory(
        repo_id="repo-x",
        target_term="dependencias",
        module_name="pkg",
        limit=10,
        offset=0,
    )

    assert [item["kind"] for item in records] == [
        "file_dependency",
        "external_dependency",
    ]
    assert driver.last_session is not None
    assert "CALL () {" in driver.last_session.query
    assert "IMPORTS_FILE" in driver.last_session.query
    assert "IMPORTS_EXTERNAL_FILE" in driver.last_session.query
    assert driver.last_session.kwargs == {
        "repo_id": "repo-x",
        "module_name": "pkg",
        "limit": 10,
        "offset": 0,
    }


def test_query_file_paths_by_suffix_prefers_exact_and_suffix_matches() -> None:
    """Resuelve archivos candidatos por path exacto, basename o sufijo."""

    class _FilePathRecord:
        def __init__(self, path: str, match_score: int) -> None:
            self._payload = {"path": path, "match_score": match_score}

        def data(self) -> dict[str, object]:
            return self._payload

    class _FilePathSession:
        def __init__(self) -> None:
            self.query = ""
            self.kwargs: dict[str, Any] = {}

        def run(self, query: str, **kwargs: Any) -> list[_FilePathRecord]:
            self.query = query
            self.kwargs = kwargs
            return [
                _FilePathRecord(
                    path="src/coderag/storage/metadata_store.py",
                    match_score=2,
                )
            ]

        def __enter__(self) -> "_FilePathSession":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    class _FilePathDriver:
        def __init__(self) -> None:
            self.last_session: _FilePathSession | None = None

        def session(self) -> _FilePathSession:
            self.last_session = _FilePathSession()
            return self.last_session

    builder = GraphBuilder.__new__(GraphBuilder)
    driver = _FilePathDriver()
    builder.driver = driver

    records = builder.query_file_paths_by_suffix(
        repo_id="repo-x",
        candidates=["metadata_store.py", "./src/coderag/storage/metadata_store.py"],
        limit=5,
    )

    assert records == [
        {"path": "src/coderag/storage/metadata_store.py", "match_score": 2}
    ]
    assert driver.last_session is not None
    assert "path_lower ENDS WITH '/' + candidate" in driver.last_session.query
    assert "split(path_lower, '/')[size(split(path_lower, '/')) - 1] = candidate" in driver.last_session.query
    assert driver.last_session.kwargs == {
        "repo_id": "repo-x",
        "candidates": ["metadata_store.py", "src/coderag/storage/metadata_store.py"],
        "limit": 5,
    }


def test_query_file_importers_returns_direct_importer_files() -> None:
    """Busca importadores directos de un archivo objetivo mediante IMPORTS_FILE."""

    class _ImporterRecord:
        def __init__(
            self,
            target_path: str,
            label: str,
            path: str,
            kind: str,
            start_line: int,
            end_line: int,
        ) -> None:
            self._payload = {
                "target_path": target_path,
                "label": label,
                "path": path,
                "kind": kind,
                "start_line": start_line,
                "end_line": end_line,
            }

        def data(self) -> dict[str, object]:
            return self._payload

    class _ImporterSession:
        def __init__(self) -> None:
            self.query = ""
            self.kwargs: dict[str, Any] = {}

        def run(self, query: str, **kwargs: Any) -> list[_ImporterRecord]:
            self.query = query
            self.kwargs = kwargs
            return [
                _ImporterRecord(
                    target_path="src/coderag/storage/metadata_store.py",
                    label="worker.py",
                    path="src/coderag/jobs/worker.py",
                    kind="file_importer",
                    start_line=1,
                    end_line=1,
                ),
                _ImporterRecord(
                    target_path="src/coderag/storage/metadata_store.py",
                    label="storage_health.py",
                    path="src/coderag/core/storage_health.py",
                    kind="file_importer",
                    start_line=1,
                    end_line=1,
                ),
            ]

        def __enter__(self) -> "_ImporterSession":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    class _ImporterDriver:
        def __init__(self) -> None:
            self.last_session: _ImporterSession | None = None

        def session(self) -> _ImporterSession:
            self.last_session = _ImporterSession()
            return self.last_session

    builder = GraphBuilder.__new__(GraphBuilder)
    driver = _ImporterDriver()
    builder.driver = driver

    records = builder.query_file_importers(
        repo_id="repo-x",
        target_paths=["src/coderag/storage/metadata_store.py"],
        limit=10,
    )

    assert [item["path"] for item in records] == [
        "src/coderag/jobs/worker.py",
        "src/coderag/core/storage_health.py",
    ]
    assert all(item["kind"] == "file_importer" for item in records)
    assert driver.last_session is not None
    assert "-[r:IMPORTS_FILE]->(target:File" in driver.last_session.query
    assert "WHERE target.path IN $target_paths" in driver.last_session.query
    assert driver.last_session.kwargs == {
        "repo_id": "repo-x",
        "target_paths": ["src/coderag/storage/metadata_store.py"],
        "limit": 10,
    }


def test_expand_symbol_file_context_uses_file_edges() -> None:
    """Expande contexto de archivo a partir de símbolos semilla mediante aristas File."""

    class _FileContextRecord:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def data(self) -> dict[str, object]:
            return self._payload

    class _FileContextSession:
        def __init__(self) -> None:
            self.query = ""
            self.kwargs: dict[str, Any] = {}

        def run(self, query: str, **kwargs: Any) -> list[_FileContextRecord]:
            self.query = query
            self.kwargs = kwargs
            return [
                _FileContextRecord(
                    {
                        "seed": "s1",
                        "labels": ["File"],
                        "props": {"path": "pkg/b.py"},
                        "edge_count": 1,
                        "relation_types": ["IMPORTS_FILE"],
                        "relation_confidence_avg": 1.0,
                    }
                )
            ]

        def __enter__(self) -> "_FileContextSession":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    class _FileContextDriver:
        def __init__(self) -> None:
            self.last_session: _FileContextSession | None = None

        def session(self) -> _FileContextSession:
            self.last_session = _FileContextSession()
            return self.last_session

    builder = GraphBuilder.__new__(GraphBuilder)
    driver = _FileContextDriver()
    builder.driver = driver

    records = builder.expand_symbol_file_context(symbol_ids=["s1"], limit=7)

    assert len(records) == 1
    assert records[0]["relation_types"] == ["IMPORTS_FILE"]
    assert driver.last_session is not None
    assert "CALL () {" in driver.last_session.query
    assert "IMPORTS_FILE" in driver.last_session.query
    assert "IMPORTS_EXTERNAL_FILE" in driver.last_session.query
    assert "coalesce(r.source_path, source.path, '') AS source_path" in driver.last_session.query
    assert "coalesce(r.line, 1) AS line" in driver.last_session.query
    assert driver.last_session.kwargs == {"symbol_ids": ["s1"], "limit": 7}


def test_expand_file_path_context_uses_file_edges() -> None:
    """Expande contexto de archivo a partir de paths semilla mediante aristas File."""

    class _FilePathContextRecord:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def data(self) -> dict[str, object]:
            return self._payload

    class _FilePathContextSession:
        def __init__(self) -> None:
            self.query = ""
            self.kwargs: dict[str, Any] = {}

        def run(self, query: str, **kwargs: Any) -> list[_FilePathContextRecord]:
            self.query = query
            self.kwargs = kwargs
            return [
                _FilePathContextRecord(
                    {
                        "seed": "src/a.py",
                        "labels": ["ExternalSymbol"],
                        "props": {"ref": "neo4j.GraphDatabase"},
                        "edge_count": 1,
                        "relation_types": ["IMPORTS_EXTERNAL_FILE"],
                        "relation_confidence_avg": 1.0,
                        "line": 4,
                        "source_path": "src/a.py",
                    }
                )
            ]

        def __enter__(self) -> "_FilePathContextSession":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    class _FilePathContextDriver:
        def __init__(self) -> None:
            self.last_session: _FilePathContextSession | None = None

        def session(self) -> _FilePathContextSession:
            self.last_session = _FilePathContextSession()
            return self.last_session

    builder = GraphBuilder.__new__(GraphBuilder)
    driver = _FilePathContextDriver()
    builder.driver = driver

    records = builder.expand_file_path_context(
        repo_id="repo-x",
        file_paths=["src/a.py", "src/a.py"],
        limit=5,
    )

    assert len(records) == 1
    assert records[0]["relation_types"] == ["IMPORTS_EXTERNAL_FILE"]
    assert driver.last_session is not None
    assert "IMPORTS_FILE" in driver.last_session.query
    assert "IMPORTS_EXTERNAL_FILE" in driver.last_session.query
    assert driver.last_session.kwargs == {
        "repo_id": "repo-x",
        "file_paths": ["src/a.py"],
        "limit": 5,
    }


def test_query_repo_modules_returns_sorted_module_paths() -> None:
    """Devuelve módulos distintos persistidos en Neo4j ordenados por path."""

    class _ModuleRecord:
        def __init__(self, module_path: str) -> None:
            self._module_path = module_path

        def get(self, key: str, default: object = None) -> object:
            if key == "module_path":
                return self._module_path
            return default

    class _ModuleSession:
        def __init__(self) -> None:
            self.query = ""
            self.kwargs: dict[str, Any] = {}

        def run(self, query: str, **kwargs: Any) -> list[_ModuleRecord]:
            self.query = query
            self.kwargs = kwargs
            return [
                _ModuleRecord("api"),
                _ModuleRecord("core"),
                _ModuleRecord("services/payments"),
            ]

        def __enter__(self) -> "_ModuleSession":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    class _ModuleDriver:
        def __init__(self) -> None:
            self.last_session: _ModuleSession | None = None

        def session(self) -> _ModuleSession:
            self.last_session = _ModuleSession()
            return self.last_session

    builder = GraphBuilder.__new__(GraphBuilder)
    driver = _ModuleDriver()
    builder.driver = driver

    modules = builder.query_repo_modules("repo-x")

    assert modules == ["api", "core", "services/payments"]
    assert driver.last_session is not None
    assert "HAS_MODULE" in driver.last_session.query
    assert driver.last_session.kwargs == {"repo_id": "repo-x"}


def test_upsert_repo_graph_persists_file_metadata() -> None:
    """Persiste metadata derivada de archivo para queries sin workspace."""
    builder = GraphBuilder.__new__(GraphBuilder)
    driver = _FakeDriver(nodes_deleted=0, total_nodes=0)
    builder.driver = driver

    scanned_files = [
        ScannedFile(
            path="src/coderag/core/settings.py",
            language="python",
            content='"""Orquesta validaciones de consultas."""\n',
        )
    ]
    symbols = [
        SymbolChunk(
            id="sym-1",
            repo_id="repo-x",
            path="src/coderag/core/settings.py",
            language="python",
            symbol_name="Settings",
            symbol_type="class",
            start_line=1,
            end_line=10,
            snippet="class Settings:\n    pass",
        )
    ]

    builder.upsert_repo_graph(
        repo_id="repo-x",
        scanned_files=scanned_files,
        symbols=symbols,
        semantic_relations=[],
    )

    session = driver.last_session
    assert session is not None
    file_call = next(kwargs for query, kwargs in session.calls if "MERGE (f:File" in query)
    assert file_call["file_name"] == "settings.py"
    assert file_call["module_path"] == "src"
    assert file_call["purpose_source"] == "module_docstring"
    assert "Orquesta validaciones" in file_call["purpose_summary"]
    assert file_call["top_level_symbol_names"] == ["Settings"]
    assert file_call["top_level_symbol_types"] == ["class"]


def test_query_file_purpose_summaries_returns_path_map() -> None:
    """Recupera propósito persistido por path desde Neo4j."""

    class _PurposeRecord:
        def __init__(self, path: str, purpose_summary: str, purpose_source: str) -> None:
            self._payload = {
                "path": path,
                "purpose_summary": purpose_summary,
                "purpose_source": purpose_source,
            }

        def get(self, key: str, default: object = None) -> object:
            return self._payload.get(key, default)

    class _PurposeSession:
        def __init__(self) -> None:
            self.query = ""
            self.kwargs: dict[str, Any] = {}

        def run(self, query: str, **kwargs: Any) -> list[_PurposeRecord]:
            self.query = query
            self.kwargs = kwargs
            return [
                _PurposeRecord(
                    "src/coderag/core/settings.py",
                    "Centraliza configuración y parámetros del módulo.",
                    "filename_heuristic",
                )
            ]

        def __enter__(self) -> "_PurposeSession":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    class _PurposeDriver:
        def __init__(self) -> None:
            self.last_session: _PurposeSession | None = None

        def session(self) -> _PurposeSession:
            self.last_session = _PurposeSession()
            return self.last_session

    builder = GraphBuilder.__new__(GraphBuilder)
    driver = _PurposeDriver()
    builder.driver = driver

    payload = builder.query_file_purpose_summaries(
        "repo-x",
        ["src/coderag/core/settings.py"],
    )

    assert payload == {
        "src/coderag/core/settings.py": {
            "purpose_summary": "Centraliza configuración y parámetros del módulo.",
            "purpose_source": "filename_heuristic",
        }
    }
    assert driver.last_session is not None
    assert "purpose_summary" in driver.last_session.query
    assert driver.last_session.kwargs == {
        "repo_id": "repo-x",
        "paths": ["src/coderag/core/settings.py"],
    }
