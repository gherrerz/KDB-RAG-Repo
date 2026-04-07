"""Módulo de expansión GraphRAG que utiliza vecinos Neo4j."""

import logging
from time import monotonic

from coderag.core.models import RetrievalChunk
from coderag.core.settings import get_settings
from coderag.ingestion.graph_builder import GraphBuilder


LOGGER = logging.getLogger(__name__)


def _relation_score(record: dict, weights: dict[str, float]) -> float:
    """Calcula score semántico por nodo expandido usando tipos y confianza."""
    relation_types = record.get("relation_types") or []
    if not isinstance(relation_types, list):
        relation_types = []

    confidence = float(record.get("relation_confidence_avg", 1.0) or 1.0)
    if confidence <= 0:
        confidence = 0.01

    if relation_types:
        base = sum(weights.get(str(item).upper(), 1.0) for item in relation_types)
    else:
        edge_count = max(1, int(record.get("edge_count", 1) or 1))
        base = float(edge_count)

    return round(base * confidence, 4)


def expand_with_graph(chunks: list[RetrievalChunk]) -> list[dict]:
    """Mantiene compatibilidad: devuelve solo nodos expandidos."""
    records, _diagnostics = expand_with_graph_with_diagnostics(chunks)
    return records


def expand_with_graph_with_diagnostics(
    chunks: list[RetrievalChunk],
) -> tuple[list[dict], dict[str, object]]:
    """Amplía grafo aplicando budgets semánticos y retorna diagnostics."""
    symbol_ids = [item.id for item in chunks]
    diagnostics: dict[str, object] = {
        "semantic_query_enabled": False,
        "semantic_relation_types": [],
        "semantic_edges_used": 0,
        "semantic_nodes_used": 0,
        "semantic_expand_ms": 0.0,
        "semantic_pruned_edges": 0,
        "semantic_noise_ratio": 0.0,
        "semantic_fallback_used": False,
        "semantic_fallback_reason": None,
    }
    if not symbol_ids:
        return [], diagnostics

    settings = get_settings()
    semantic_query_enabled = bool(
        getattr(settings, "semantic_graph_query_enabled", False)
    )
    relation_types = []
    relation_weights: dict[str, float] = {}
    if semantic_query_enabled and hasattr(settings, "resolve_semantic_relation_types"):
        relation_types = settings.resolve_semantic_relation_types(None)
    if semantic_query_enabled and hasattr(settings, "resolve_semantic_relation_weights"):
        relation_weights = settings.resolve_semantic_relation_weights(None)
    elif semantic_query_enabled:
        relation_weights = {
            "CALLS": 1.0,
            "IMPORTS": 0.7,
            "EXTENDS": 1.1,
            "IMPLEMENTS": 1.0,
        }

    diagnostics["semantic_query_enabled"] = semantic_query_enabled
    diagnostics["semantic_relation_types"] = relation_types

    max_nodes = max(1, int(getattr(settings, "semantic_graph_query_max_nodes", 200)))
    max_edges = max(1, int(getattr(settings, "semantic_graph_query_max_edges", 400)))
    max_ms = max(1.0, float(getattr(settings, "semantic_graph_query_max_ms", 120.0)))
    allow_structural_fallback = bool(
        getattr(settings, "semantic_graph_query_fallback_to_structural", True)
    )

    started_at = monotonic()
    graph = GraphBuilder()
    try:
        raw_records = graph.expand_symbols(
            symbol_ids=symbol_ids,
            hops=settings.graph_hops,
            relation_types=relation_types if semantic_query_enabled else None,
            limit=max_nodes * 3,
        )
        if semantic_query_enabled:
            for record in raw_records:
                record["semantic_score"] = _relation_score(record, relation_weights)
            raw_records.sort(
                key=lambda item: (
                    float(item.get("semantic_score", 0.0)),
                    -int(item.get("edge_count", 1) or 1),
                ),
                reverse=True,
            )

        selected: list[dict] = []
        edges_used = 0
        pruned_edges = 0
        for record in raw_records:
            elapsed_ms = (monotonic() - started_at) * 1000.0
            edge_count = int(record.get("edge_count", 1) or 1)
            if semantic_query_enabled and elapsed_ms > max_ms:
                pruned_edges += edge_count
                continue
            if semantic_query_enabled and len(selected) >= max_nodes:
                pruned_edges += edge_count
                continue
            if semantic_query_enabled and edges_used + edge_count > max_edges:
                pruned_edges += edge_count
                continue
            selected.append(record)
            edges_used += edge_count

        if (
            semantic_query_enabled
            and not selected
            and raw_records
            and allow_structural_fallback
        ):
            fallback_records = graph.expand_symbols(
                symbol_ids=symbol_ids,
                hops=settings.graph_hops,
                relation_types=None,
                limit=max_nodes,
            )
            selected = fallback_records[:max_nodes]
            edges_used = sum(
                max(1, int(item.get("edge_count", 1) or 1)) for item in selected
            )
            diagnostics["semantic_fallback_used"] = True
            diagnostics["semantic_fallback_reason"] = "semantic_budget_pruned_all"

        diagnostics["semantic_edges_used"] = edges_used if semantic_query_enabled else 0
        diagnostics["semantic_nodes_used"] = len(selected) if semantic_query_enabled else 0
        diagnostics["semantic_pruned_edges"] = (
            pruned_edges if semantic_query_enabled else 0
        )
        if semantic_query_enabled:
            total_edges = edges_used + pruned_edges
            diagnostics["semantic_noise_ratio"] = (
                round(pruned_edges / total_edges, 4) if total_edges > 0 else 0.0
            )
        diagnostics["semantic_expand_ms"] = round(
            (monotonic() - started_at) * 1000.0,
            2,
        )
        return selected, diagnostics
    except Exception as exc:
        LOGGER.warning("Graph expansion falló; se usará contexto sin grafo: %s", exc)
        if semantic_query_enabled and allow_structural_fallback:
            try:
                fallback_records = graph.expand_symbols(
                    symbol_ids=symbol_ids,
                    hops=settings.graph_hops,
                    relation_types=None,
                    limit=max_nodes,
                )
                diagnostics["semantic_fallback_used"] = True
                diagnostics["semantic_fallback_reason"] = "semantic_exception"
                diagnostics["semantic_edges_used"] = sum(
                    max(1, int(item.get("edge_count", 1) or 1))
                    for item in fallback_records[:max_nodes]
                )
                diagnostics["semantic_nodes_used"] = len(fallback_records[:max_nodes])
                diagnostics["semantic_expand_ms"] = round(
                    (monotonic() - started_at) * 1000.0,
                    2,
                )
                return fallback_records[:max_nodes], diagnostics
            except Exception as fallback_exc:
                LOGGER.warning(
                    "Fallback estructural también falló en expansión de grafo: %s",
                    fallback_exc,
                )
        diagnostics["semantic_expand_ms"] = round(
            (monotonic() - started_at) * 1000.0,
            2,
        )
        return [], diagnostics
    finally:
        graph.close()
