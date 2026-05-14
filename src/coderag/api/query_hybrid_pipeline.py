"""Helpers internos para preparar y enriquecer el pipeline híbrido."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic

from coderag.api.citation_filters import is_noisy_path
from coderag.core.models import Citation, RetrievalChunk


@dataclass
class HybridGraphSeedInput:
    """Agrupa el estado compartido antes de la expansión del grafo."""

    initial: list[RetrievalChunk]
    reranked: list[RetrievalChunk]
    graph_seed_input: list[RetrievalChunk]
    stage_timings: dict[str, float]
    reverse_import_seed_boosted_count: int
    reverse_import_seed_chunks_added_count: int
    reverse_import_target_paths: list[str]
    external_import_seed_boosted_count: int
    external_import_seed_chunks_added_count: int


@dataclass
class GraphEnrichmentResult:
    """Agrupa los resultados compartidos tras enriquecer con el grafo."""

    reranked: list[RetrievalChunk]
    semantic_expand_diagnostics: dict[str, object]
    raw_citations: list[Citation]
    filtered_citations: list[Citation]
    citations: list[Citation]


@dataclass(frozen=True)
class HybridSeedPreparationHooks:
    """Colaboradores inyectados para preparar el pipeline híbrido."""

    hybrid_search: Callable[..., list[RetrievalChunk]]
    elapsed_milliseconds: Callable[[float], float]
    apply_internal_file_importer_seed_boost: Callable[
        ..., tuple[list[RetrievalChunk], int, dict[str, int], list[str]]
    ]
    apply_external_import_seed_boost: Callable[
        ..., tuple[list[RetrievalChunk], int, dict[str, int]]
    ]
    rerank: Callable[..., list[RetrievalChunk]]
    build_internal_file_importer_seed_chunks: Callable[
        ..., tuple[list[RetrievalChunk], int]
    ]
    build_external_import_seed_chunks: Callable[
        ..., tuple[list[RetrievalChunk], int]
    ]


@dataclass(frozen=True)
class GraphEnrichmentHooks:
    """Colaboradores inyectados para enriquecer resultados con el grafo."""

    apply_graph_context_chunk_boost: Callable[
        [list[RetrievalChunk], list[dict]],
        tuple[list[RetrievalChunk], int],
    ]
    build_graph_context_citations: Callable[[list[dict]], list[Citation]]
    citation_priority: Callable[[Citation], tuple[int, float]]


def prepare_hybrid_graph_seed_input(
    repo_id: str,
    query: str,
    top_n: int,
    top_k: int,
    embedding_provider: str | None,
    embedding_model: str | None,
    *,
    hooks: HybridSeedPreparationHooks,
) -> HybridGraphSeedInput:
    """Ejecuta la preparación híbrida común hasta el input de expansión."""
    stage_timings: dict[str, float] = {}

    retrieval_started_at = monotonic()
    initial = hooks.hybrid_search(
        repo_id=repo_id,
        query=query,
        top_n=top_n,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
    )
    stage_timings["hybrid_search_ms"] = hooks.elapsed_milliseconds(
        retrieval_started_at
    )

    internal_seed_started_at = monotonic()
    (
        initial,
        reverse_import_seed_boosted_count,
        reverse_import_matched_paths,
        reverse_import_target_paths,
    ) = hooks.apply_internal_file_importer_seed_boost(
        repo_id=repo_id,
        query=query,
        chunks=initial,
    )
    stage_timings["reverse_import_seed_boost_ms"] = (
        hooks.elapsed_milliseconds(internal_seed_started_at)
    )

    external_seed_started_at = monotonic()
    (
        initial,
        external_import_seed_boosted_count,
        external_import_matched_paths,
    ) = hooks.apply_external_import_seed_boost(
        repo_id=repo_id,
        query=query,
        chunks=initial,
    )
    stage_timings["external_import_seed_boost_ms"] = (
        hooks.elapsed_milliseconds(external_seed_started_at)
    )

    rerank_started_at = monotonic()
    reranked = hooks.rerank(query=query, chunks=initial, top_k=top_k)
    stage_timings["rerank_ms"] = hooks.elapsed_milliseconds(rerank_started_at)

    (
        reverse_graph_seed_chunks,
        reverse_import_seed_chunks_added_count,
    ) = hooks.build_internal_file_importer_seed_chunks(
        repo_id=repo_id,
        matched_paths=reverse_import_matched_paths,
        chunks=reranked,
    )
    (
        graph_seed_chunks,
        external_import_seed_chunks_added_count,
    ) = hooks.build_external_import_seed_chunks(
        repo_id=repo_id,
        matched_paths=external_import_matched_paths,
        chunks=reranked,
    )
    graph_seed_input = reranked + reverse_graph_seed_chunks + graph_seed_chunks

    return HybridGraphSeedInput(
        initial=initial,
        reranked=reranked,
        graph_seed_input=graph_seed_input,
        stage_timings=stage_timings,
        reverse_import_seed_boosted_count=reverse_import_seed_boosted_count,
        reverse_import_seed_chunks_added_count=(
            reverse_import_seed_chunks_added_count
        ),
        reverse_import_target_paths=reverse_import_target_paths,
        external_import_seed_boosted_count=external_import_seed_boosted_count,
        external_import_seed_chunks_added_count=(
            external_import_seed_chunks_added_count
        ),
    )


def finalize_graph_enrichment(
    reranked: list[RetrievalChunk],
    graph_context: list[dict],
    semantic_expand_diagnostics: dict[str, object],
    reverse_import_seed_boosted_count: int,
    reverse_import_seed_chunks_added_count: int,
    reverse_import_target_paths: list[str],
    external_import_seed_boosted_count: int,
    external_import_seed_chunks_added_count: int,
    *,
    hooks: GraphEnrichmentHooks,
) -> GraphEnrichmentResult:
    """Aplica enriquecimiento final común de grafo, citas y diagnostics."""
    reranked, graph_chunk_boosted_count = hooks.apply_graph_context_chunk_boost(
        reranked,
        graph_context,
    )
    semantic_expand_diagnostics = dict(semantic_expand_diagnostics)
    semantic_expand_diagnostics["reverse_import_seed_boosted_count"] = (
        reverse_import_seed_boosted_count
    )
    semantic_expand_diagnostics["reverse_import_seed_chunks_added_count"] = (
        reverse_import_seed_chunks_added_count
    )
    semantic_expand_diagnostics["reverse_import_target_paths"] = (
        reverse_import_target_paths
    )
    semantic_expand_diagnostics["external_import_seed_boosted_count"] = (
        external_import_seed_boosted_count
    )
    semantic_expand_diagnostics["external_import_seed_chunks_added_count"] = (
        external_import_seed_chunks_added_count
    )
    semantic_expand_diagnostics["semantic_graph_chunk_boosted_count"] = (
        graph_chunk_boosted_count
    )

    graph_citations = hooks.build_graph_context_citations(graph_context)
    semantic_expand_diagnostics["semantic_graph_citations_count"] = len(
        graph_citations
    )
    raw_citations = [
        Citation(
            path=item.metadata.get("path", "unknown"),
            start_line=int(item.metadata.get("start_line", 0)),
            end_line=int(item.metadata.get("end_line", 0)),
            score=float(item.score),
            reason="hybrid_rag_match",
        )
        for item in reranked
    ] + graph_citations
    filtered_citations = [
        item for item in raw_citations if not is_noisy_path(item.path)
    ]
    citations_source = filtered_citations or raw_citations
    citations = sorted(citations_source, key=hooks.citation_priority)

    return GraphEnrichmentResult(
        reranked=reranked,
        semantic_expand_diagnostics=semantic_expand_diagnostics,
        raw_citations=raw_citations,
        filtered_citations=filtered_citations,
        citations=citations,
    )