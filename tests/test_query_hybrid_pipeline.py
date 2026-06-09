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