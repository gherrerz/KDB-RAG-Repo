"""Módulo de expansión GraphRAG que utiliza vecinos Neo4j."""

import logging
import re
from time import monotonic

from coderag.core.models import RetrievalChunk
from coderag.core.settings import get_settings
from coderag.ingestion.graph_builder import GraphBuilder


LOGGER = logging.getLogger(__name__)


def _record_identity(record: dict) -> tuple[str, tuple[str, ...], str]:
    """Construye una clave estable para deduplicar nodos expandidos."""
    labels = tuple(sorted(str(label) for label in (record.get("labels") or [])))
    props = record.get("props") or {}
    anchor = str(
        props.get("id")
        or props.get("path")
        or props.get("ref")
        or props.get("name")
        or ""
    )
    return (str(record.get("seed", "")), labels, anchor)


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


def _tokenize_query(query: str) -> tuple[str, ...]:
    """Tokeniza la query para priorizar contexto de archivo por solape textual."""
    normalized = query.strip().lower()
    tokens = re.findall(r"[a-z0-9_]+", normalized)
    return tuple(token for token in tokens if len(token) >= 2)


def _file_context_query_score(record: dict, query_tokens: tuple[str, ...]) -> float:
    """Calcula un score ligero de relevancia de file-context contra la query."""
    if not query_tokens:
        return 0.0

    props = record.get("props") or {}
    haystack_parts = [
        str(props.get("path", "") or ""),
        str(props.get("module_path", "") or ""),
        str(props.get("file_name", "") or ""),
        str(props.get("ref", "") or ""),
        str(props.get("language", "") or ""),
        " ".join(str(item) for item in (record.get("relation_types") or [])),
    ]
    haystack = " ".join(part.lower() for part in haystack_parts if part)
    if not haystack:
        return 0.0

    matches = sum(1 for token in query_tokens if token in haystack)
    if matches <= 0:
        return 0.0

    base = matches / max(1, len(query_tokens))
    if "ExternalSymbol" in (record.get("labels") or []):
        base += 0.05
    return round(base, 4)


def expand_with_graph(chunks: list[RetrievalChunk]) -> list[dict]:
    """Mantiene compatibilidad: devuelve solo nodos expandidos."""
    records, _diagnostics = expand_with_graph_with_diagnostics(chunks)
    return records


def expand_with_graph_with_diagnostics(
    chunks: list[RetrievalChunk],
    query: str | None = None,
) -> tuple[list[dict], dict[str, object]]:
    """Amplía grafo aplicando budgets semánticos y retorna diagnostics."""
    symbol_ids = [item.id for item in chunks]
    file_paths_by_repo: dict[str, set[str]] = {}
    for item in chunks:
        metadata = item.metadata or {}
        repo_id = str(metadata.get("repo_id", "") or "").strip()
        path = str(metadata.get("path", "") or "").strip()
        if repo_id and path:
            file_paths_by_repo.setdefault(repo_id, set()).add(path)
    query_tokens = _tokenize_query(query or "")
    diagnostics: dict[str, object] = {
        "semantic_query_enabled": False,
        "semantic_relation_types": [],
        "semantic_edges_used": 0,
        "semantic_nodes_used": 0,
        "semantic_file_context_used": 0,
        "semantic_file_context_pruned": 0,
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
        expand_symbol_file_context = getattr(
            graph,
            "expand_symbol_file_context",
            None,
        )
        file_context_records = []
        if callable(expand_symbol_file_context):
            file_context_records = expand_symbol_file_context(
                symbol_ids=symbol_ids,
                limit=max_nodes,
            )
        expand_file_path_context = getattr(graph, "expand_file_path_context", None)
        if callable(expand_file_path_context):
            for repo_id, file_paths in file_paths_by_repo.items():
                file_context_records.extend(
                    expand_file_path_context(
                        repo_id=repo_id,
                        file_paths=sorted(file_paths),
                        limit=max_nodes,
                    )
                )
        if file_context_records:
            for record in file_context_records:
                record["file_context_query_score"] = _file_context_query_score(
                    record,
                    query_tokens,
                )
            file_context_records.sort(
                key=lambda item: (
                    float(item.get("file_context_query_score", 0.0)),
                    int(item.get("edge_count", 1) or 1),
                ),
                reverse=True,
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
        selected_keys: set[tuple[str, tuple[str, ...], str]] = set()
        edges_used = 0
        pruned_edges = 0
        file_context_used = 0
        file_context_pruned = 0
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
            record_key = _record_identity(record)
            if record_key in selected_keys:
                continue
            selected.append(record)
            selected_keys.add(record_key)
            edges_used += edge_count

        for record in file_context_records:
            elapsed_ms = (monotonic() - started_at) * 1000.0
            if semantic_query_enabled and elapsed_ms > max_ms:
                file_context_pruned += 1
                break
            if semantic_query_enabled and len(selected) >= max_nodes:
                file_context_pruned += 1
                break
            record_key = _record_identity(record)
            if record_key in selected_keys:
                continue
            selected.append(record)
            selected_keys.add(record_key)
            file_context_used += 1

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
        diagnostics["semantic_file_context_used"] = file_context_used
        diagnostics["semantic_file_context_pruned"] = file_context_pruned
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
