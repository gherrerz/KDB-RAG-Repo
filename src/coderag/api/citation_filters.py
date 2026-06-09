"""Helpers to filter noisy paths and select returned citations."""

from collections.abc import Sequence

from coderag.core.models import Citation, InventoryItem


def _normalize_path(path: str) -> str:
    """Normalize citation paths for stable generic matching."""
    return path.strip().replace("\\", "/").lower()


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


def select_high_signal_citations(
    citations: Sequence[Citation],
    preferred_paths: Sequence[str],
    *,
    max_total: int = 1,
) -> list[Citation]:
    """Return the strongest evidence anchors aligned with reranked paths."""
    if max_total <= 0:
        return []
    if not citations:
        return []

    unique_citations: list[Citation] = []
    seen_spans: set[tuple[str, int, int]] = set()
    for citation in citations:
        normalized_path = _normalize_path(citation.path)
        key = (normalized_path, citation.start_line, citation.end_line)
        if key in seen_spans:
            continue
        seen_spans.add(key)
        unique_citations.append(citation)

    normalized_preferred_paths = []
    seen_paths: set[str] = set()
    for path in preferred_paths:
        normalized = _normalize_path(path)
        if not normalized or normalized in seen_paths:
            continue
        seen_paths.add(normalized)
        normalized_preferred_paths.append(normalized)

    selected: list[Citation] = []
    for preferred_path in normalized_preferred_paths:
        for citation in unique_citations:
            if _normalize_path(citation.path) != preferred_path:
                continue
            selected.append(citation)
            if len(selected) >= max_total:
                return selected
            break

    if selected:
        return selected[:max_total]
    return unique_citations[:max_total]


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