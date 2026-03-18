"""Pruebas unitarias para operaciones de limpieza en GraphBuilder."""

from typing import Any

import pytest

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

    def run(self, query: str, **kwargs: Any) -> _FakeResult:
        """Retorna resultados simulados según la consulta."""
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

    def session(self) -> _FakeSession:
        """Crea una sesión simulada."""
        return _FakeSession(
            nodes_deleted=self.nodes_deleted,
            total_nodes=self.total_nodes,
        )


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
