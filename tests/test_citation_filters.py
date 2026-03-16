"""Unit tests for reusable citation filtering helpers."""

from coderag.api.citation_filters import build_inventory_citations, is_noisy_path
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
            path="coderag/core/settings.py",
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
    assert citations[0].path == "coderag/core/settings.py"
    assert citations[0].start_line == 1
    assert citations[0].end_line == 22
    assert citations[0].reason == "inventory_graph_match"
    assert citations[0].score == 1.0
