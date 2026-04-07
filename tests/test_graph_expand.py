"""Tests for graph expansion semantic query budgets and diagnostics."""

import pytest

from coderag.core.models import RetrievalChunk
from coderag.retrieval import graph_expand


def test_expand_with_graph_with_diagnostics_semantic_budgets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Aplica budgets semánticos y reporta aristas podadas correctamente."""

    class _Settings:
        graph_hops = 2
        semantic_graph_query_enabled = True
        semantic_graph_query_max_edges = 2
        semantic_graph_query_max_nodes = 10
        semantic_graph_query_max_ms = 1000.0

        @staticmethod
        def resolve_semantic_relation_types(_override=None) -> list[str]:
            return ["CALLS", "IMPORTS"]

    class _Graph:
        def expand_symbols(
            self,
            symbol_ids,
            hops,
            relation_types=None,
            limit=200,
        ):
            assert relation_types == ["CALLS", "IMPORTS"]
            return [
                {
                    "seed": "s1",
                    "labels": ["Symbol"],
                    "props": {"id": "n1"},
                    "edge_count": 1,
                },
                {
                    "seed": "s1",
                    "labels": ["Symbol"],
                    "props": {"id": "n2"},
                    "edge_count": 2,
                },
            ]

        def close(self) -> None:
            return None

    monkeypatch.setattr(graph_expand, "get_settings", lambda: _Settings())
    monkeypatch.setattr(graph_expand, "GraphBuilder", _Graph)

    chunks = [
        RetrievalChunk(
            id="s1",
            text="x",
            score=1.0,
            metadata={"path": "src/a.py", "start_line": 1, "end_line": 1},
        )
    ]
    records, diagnostics = graph_expand.expand_with_graph_with_diagnostics(chunks)

    assert len(records) == 1
    assert diagnostics["semantic_query_enabled"] is True
    assert diagnostics["semantic_edges_used"] == 2
    assert diagnostics["semantic_pruned_edges"] == 1


def test_expand_with_graph_with_diagnostics_disabled_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mantiene campos semánticos en cero cuando la ruta está deshabilitada."""

    class _Settings:
        graph_hops = 2
        semantic_graph_query_enabled = False

    class _Graph:
        def expand_symbols(self, symbol_ids, hops, relation_types=None, limit=200):
            assert relation_types is None
            return [
                {
                    "seed": "s1",
                    "labels": ["Symbol"],
                    "props": {"id": "n1"},
                    "edge_count": 1,
                }
            ]

        def close(self) -> None:
            return None

    monkeypatch.setattr(graph_expand, "get_settings", lambda: _Settings())
    monkeypatch.setattr(graph_expand, "GraphBuilder", _Graph)

    chunks = [
        RetrievalChunk(
            id="s1",
            text="x",
            score=1.0,
            metadata={"path": "src/a.py", "start_line": 1, "end_line": 1},
        )
    ]
    records, diagnostics = graph_expand.expand_with_graph_with_diagnostics(chunks)

    assert len(records) == 1
    assert diagnostics["semantic_query_enabled"] is False
    assert diagnostics["semantic_edges_used"] == 0
    assert diagnostics["semantic_nodes_used"] == 0


def test_expand_with_graph_prioritizes_relation_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prioriza nodos por score semántico según tipo de relación y confianza."""

    class _Settings:
        graph_hops = 2
        semantic_graph_query_enabled = True
        semantic_graph_query_max_edges = 10
        semantic_graph_query_max_nodes = 1
        semantic_graph_query_max_ms = 1000.0
        semantic_graph_query_fallback_to_structural = True

        @staticmethod
        def resolve_semantic_relation_types(_override=None) -> list[str]:
            return ["CALLS", "IMPORTS", "EXTENDS", "IMPLEMENTS"]

        @staticmethod
        def resolve_semantic_relation_weights(_override=None) -> dict[str, float]:
            return {
                "CALLS": 1.0,
                "IMPORTS": 0.4,
                "EXTENDS": 1.6,
                "IMPLEMENTS": 1.0,
            }

    class _Graph:
        def expand_symbols(self, symbol_ids, hops, relation_types=None, limit=200):
            return [
                {
                    "seed": "s1",
                    "labels": ["Symbol"],
                    "props": {"id": "import-node"},
                    "edge_count": 1,
                    "relation_types": ["IMPORTS"],
                    "relation_confidence_avg": 1.0,
                },
                {
                    "seed": "s1",
                    "labels": ["Symbol"],
                    "props": {"id": "extends-node"},
                    "edge_count": 1,
                    "relation_types": ["EXTENDS"],
                    "relation_confidence_avg": 1.0,
                },
            ]

        def close(self) -> None:
            return None

    monkeypatch.setattr(graph_expand, "get_settings", lambda: _Settings())
    monkeypatch.setattr(graph_expand, "GraphBuilder", _Graph)

    chunks = [
        RetrievalChunk(
            id="s1",
            text="x",
            score=1.0,
            metadata={"path": "src/a.py", "start_line": 1, "end_line": 1},
        )
    ]
    records, diagnostics = graph_expand.expand_with_graph_with_diagnostics(chunks)

    assert len(records) == 1
    assert records[0]["props"]["id"] == "extends-node"
    assert diagnostics["semantic_pruned_edges"] == 1
    assert diagnostics["semantic_noise_ratio"] == 0.5


def test_expand_with_graph_uses_structural_fallback_when_semantic_pruned_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Si budgets podan todo, aplica fallback estructural automáticamente."""

    class _Settings:
        graph_hops = 2
        semantic_graph_query_enabled = True
        semantic_graph_query_max_edges = 1
        semantic_graph_query_max_nodes = 1
        semantic_graph_query_max_ms = 1000.0
        semantic_graph_query_fallback_to_structural = True

        @staticmethod
        def resolve_semantic_relation_types(_override=None) -> list[str]:
            return ["CALLS"]

        @staticmethod
        def resolve_semantic_relation_weights(_override=None) -> dict[str, float]:
            return {"CALLS": 1.0}

    class _Graph:
        def __init__(self):
            self.calls = 0

        def expand_symbols(self, symbol_ids, hops, relation_types=None, limit=200):
            self.calls += 1
            if relation_types:
                return [
                    {
                        "seed": "s1",
                        "labels": ["Symbol"],
                        "props": {"id": "semantic-node"},
                        "edge_count": 5,
                        "relation_types": ["CALLS"],
                        "relation_confidence_avg": 1.0,
                    }
                ]
            return [
                {
                    "seed": "s1",
                    "labels": ["Symbol"],
                    "props": {"id": "fallback-node"},
                    "edge_count": 1,
                }
            ]

        def close(self) -> None:
            return None

    monkeypatch.setattr(graph_expand, "get_settings", lambda: _Settings())
    monkeypatch.setattr(graph_expand, "GraphBuilder", _Graph)

    chunks = [
        RetrievalChunk(
            id="s1",
            text="x",
            score=1.0,
            metadata={"path": "src/a.py", "start_line": 1, "end_line": 1},
        )
    ]
    records, diagnostics = graph_expand.expand_with_graph_with_diagnostics(chunks)

    assert len(records) == 1
    assert records[0]["props"]["id"] == "fallback-node"
    assert diagnostics["semantic_fallback_used"] is True
    assert diagnostics["semantic_fallback_reason"] == "semantic_budget_pruned_all"
