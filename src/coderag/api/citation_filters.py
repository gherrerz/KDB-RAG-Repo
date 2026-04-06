"""Helpers to filter noisy paths and build inventory citations."""

from collections.abc import Sequence

from src.coderag.core.models import Citation, InventoryItem


def is_noisy_path(path: str) -> bool:
    """Return whether a citation path is likely non-informative noise."""
    normalized = path.strip().lower()
    if not normalized:
        return True
    if normalized in {".", "..", "document", "docs"}:
        return True
    if normalized.startswith("document/"):
        return True
    return False


def build_inventory_citations(items: Sequence[InventoryItem]) -> list[Citation]:
    """Build inventory citations from paged items, filtering noisy paths."""
    citations: list[Citation] = []
    for item in items:
        if is_noisy_path(item.path):
            continue
        citations.append(
            Citation(
                path=item.path,
                start_line=item.start_line,
                end_line=item.end_line,
                score=1.0,
                reason="inventory_graph_match",
            )
        )
    return citations