"""Pruebas unitarias para operaciones de limpieza en GraphBuilder."""

from typing import Any

import pytest

from coderag.core.models import SemanticRelation
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
