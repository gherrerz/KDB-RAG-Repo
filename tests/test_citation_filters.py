"""Unit tests for reusable citation filtering helpers."""

from coderag.api.citation_filters import (
    build_inventory_citations,
    is_noisy_path,
    select_high_signal_citations,
)
from coderag.core.models import Citation
from coderag.core.models import InventoryItem


def test_is_noisy_path_detects_known_noise_tokens() -> None:
    """Mark empty and documentation placeholders as noisy."""
    assert is_noisy_path("") is True
    assert is_noisy_path("docs") is True
    assert is_noisy_path("document") is True
    assert is_noisy_path("document/readme.md") is True
    assert is_noisy_path("src/main.py") is False


def test_build_inventory_citations_filters_noise_and_maps_fields() -> None:
    """Generate inventory citations only for informative file paths."""
    items = [
        InventoryItem(
            label="Settings",
            path="src/coderag/core/settings.py",
            kind="file",
            start_line=1,
            end_line=22,
        ),
        InventoryItem(
            label="Docs",
            path="docs",
            kind="file",
            start_line=1,
            end_line=1,
        ),
    ]

    citations = build_inventory_citations(items)

    assert len(citations) == 1
    assert citations[0].path == "src/coderag/core/settings.py"
    assert citations[0].start_line == 1
    assert citations[0].end_line == 22
    assert citations[0].reason == "inventory_graph_match"
    assert citations[0].score == 1.0


def test_select_high_signal_citations_prefers_top_reranked_path() -> None:
    """Keep the strongest citation aligned with the first reranked path."""
    citations = [
        Citation(
            path="tests/test_api.py",
            start_line=10,
            end_line=12,
            score=0.95,
            reason="hybrid_rag_match",
        ),
        Citation(
            path="src/coderag/api/query_service.py",
            start_line=100,
            end_line=110,
            score=0.80,
            reason="hybrid_rag_match",
        ),
    ]

    selected = select_high_signal_citations(
        citations,
        preferred_paths=["src/coderag/api/query_service.py", "tests/test_api.py"],
        max_total=1,
    )

    assert len(selected) == 1
    assert selected[0].path == "src/coderag/api/query_service.py"


def test_select_high_signal_citations_falls_back_when_no_preferred_path_matches() -> None:
    """Fallback to the first deduplicated citation when no path aligns."""
    citations = [
        Citation(
            path="docs/API_REFERENCE.md",
            start_line=1,
            end_line=10,
            score=0.70,
            reason="hybrid_rag_match",
        ),
        Citation(
            path="docs/API_REFERENCE.md",
            start_line=1,
            end_line=10,
            score=0.65,
            reason="hybrid_rag_match",
        ),
    ]

    selected = select_high_signal_citations(
        citations,
        preferred_paths=["src/coderag/api/query_service.py"],
        max_total=1,
    )

    assert len(selected) == 1
    assert selected[0].path == "docs/API_REFERENCE.md"
