"""Pruebas unitarias para operaciones de limpieza en GraphBuilder."""

from typing import Any

import pytest

from coderag.core.models import ScannedFile, SemanticRelation, SymbolChunk
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
