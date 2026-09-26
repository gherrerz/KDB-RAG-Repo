"""Focused tests for final citation selection in graph enrichment."""

from coderag.api.query_hybrid_pipeline import finalize_graph_enrichment
from coderag.core.models import RetrievalChunk


def test_finalize_graph_enrichment_returns_primary_reranked_citation() -> None:
    """Return only the main evidence citation aligned with the top reranked path."""
    reranked = [
        RetrievalChunk(
            id="c1",
            text="def run_query():\n    return {}",
            score=0.84,
            metadata={
                "path": "src/coderag/api/query_service.py",
                "start_line": 100,
                "end_line": 120,
            },
        ),
        RetrievalChunk(
            id="c2",
            text="def fake_run_query():\n    return run_query()",
            score=0.98,
            metadata={
                "path": "tests/test_api.py",
                "start_line": 10,
                "end_line": 12,
            },
        ),
    ]

    result = finalize_graph_enrichment(
        reranked=reranked,
        graph_context=[],
        semantic_expand_diagnostics={},
        reverse_import_seed_boosted_count=0,
        reverse_import_seed_chunks_added_count=0,
        reverse_import_target_paths=[],
        external_import_seed_boosted_count=0,
        external_import_seed_chunks_added_count=0,
        hooks=_hooks(),
    )

    assert len(result.raw_citations) == 2
    assert len(result.filtered_citations) == 2
    assert len(result.citations) == 1
    assert result.citations[0].path == "src/coderag/api/query_service.py"


def test_finalize_graph_enrichment_falls_back_to_raw_when_filtered_empty() -> None:
    """Keep one raw citation when all filtered candidates are removed as noise."""
    reranked = [
        RetrievalChunk(
            id="c1",
            text="doc chunk",
            score=0.50,
            metadata={
                "path": "docs",
                "start_line": 1,
                "end_line": 5,
            },
        )
    ]

    result = finalize_graph_enrichment(
        reranked=reranked,
        graph_context=[],
        semantic_expand_diagnostics={},
        reverse_import_seed_boosted_count=0,
        reverse_import_seed_chunks_added_count=0,
        reverse_import_target_paths=[],
        external_import_seed_boosted_count=0,
        external_import_seed_chunks_added_count=0,
        hooks=_hooks(),
    )

    assert len(result.filtered_citations) == 0
    assert len(result.citations) == 1
    assert result.citations[0].path == "docs"


def _hooks():
    return _GraphEnrichmentHooks()


class _GraphEnrichmentHooks:
    """Minimal hook implementation for graph enrichment tests."""

    @staticmethod
    def apply_graph_context_chunk_boost(chunks, graph_records):
        _ = graph_records
        return chunks, 0

    @staticmethod
    def build_graph_context_citations(graph_records):
        _ = graph_records
        return []

    @staticmethod
    def citation_priority(citation):
        return (0, -citation.score)


def _seed_hooks(received: list[dict]):
    """Hooks mínimos: hybrid_search registra kwargs y devuelve un extra."""
    from coderag.api.query_hybrid_pipeline import HybridSeedPreparationHooks

    def _chunk(chunk_id: str, path: str) -> RetrievalChunk:
        return RetrievalChunk(
            id=chunk_id,
            text="x",
            score=0.5,
            metadata={"path": path, "start_line": 1, "end_line": 2},
        )

    def hybrid_search(**kwargs):
        received.append(kwargs)
        return [_chunk("in", "src/a.ts"), _chunk("out", "docs/a.md")]

    return HybridSeedPreparationHooks(
        hybrid_search=hybrid_search,
        elapsed_milliseconds=lambda started_at: 0.0,
        apply_internal_file_importer_seed_boost=(
            lambda repo_id, query, chunks: (chunks, 0, {}, [])
        ),
        apply_external_import_seed_boost=(
            lambda repo_id, query, chunks: (chunks, 0, {})
        ),
        rerank=lambda query, chunks, top_k: chunks,
        build_internal_file_importer_seed_chunks=(
            lambda repo_id, matched_paths, chunks: (
                [_chunk("seed-rev-out", "docs/b.md")],
                1,
            )
        ),
        build_external_import_seed_chunks=(
            lambda repo_id, matched_paths, chunks: (
                [_chunk("seed-ext-in", "src/c.ts")],
                1,
            )
        ),
    )


def test_prepare_hybrid_graph_seed_input_applies_filter_to_seeds() -> None:
    """Con filtro, ni los chunks ni los seeds de grafo lo incumplen."""
    from coderag.api.query_hybrid_pipeline import (
        prepare_hybrid_graph_seed_input,
    )
    from coderag.retrieval.retrieval_filter import RetrievalFilter

    received: list[dict] = []
    retrieval_filter = RetrievalFilter.from_request(["src/**"], None)

    result = prepare_hybrid_graph_seed_input(
        "repo1",
        "consulta",
        10,
        5,
        None,
        None,
        hooks=_seed_hooks(received),
        retrieval_filter=retrieval_filter,
    )

    assert received[0]["retrieval_filter"] is retrieval_filter
    assert [chunk.id for chunk in result.reranked] == ["in"]
    assert [chunk.id for chunk in result.graph_seed_input] == [
        "in",
        "seed-ext-in",
    ]


def test_prepare_hybrid_graph_seed_input_without_filter_is_unchanged() -> None:
    """Sin filtro no se envía el kwarg y no se descarta ningún chunk."""
    from coderag.api.query_hybrid_pipeline import (
        prepare_hybrid_graph_seed_input,
    )

    received: list[dict] = []

    result = prepare_hybrid_graph_seed_input(
        "repo1",
        "consulta",
        10,
        5,
        None,
        None,
        hooks=_seed_hooks(received),
    )

    assert "retrieval_filter" not in received[0]
    assert [chunk.id for chunk in result.graph_seed_input] == [
        "in",
        "out",
        "seed-rev-out",
        "seed-ext-in",
    ]
