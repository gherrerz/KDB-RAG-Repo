"""Tests for citation quality filtering and prioritization."""

from coderag.api.query_service import _citation_priority, _is_noisy_path
from coderag.core.models import Citation


def test_is_noisy_path_filters_non_informative_paths() -> None:
    """Marks known noisy pseudo-paths as non-informative."""
    assert _is_noisy_path(".")
    assert _is_noisy_path("document")
    assert _is_noisy_path("document/reference/file.md")
    assert not _is_noisy_path("mall-admin/pom.xml")


def test_citation_priority_prefers_pom_and_source_paths() -> None:
    """Prioritizes pom and source code paths above generic module names."""
    pom = Citation(
        path="pom.xml",
        start_line=1,
        end_line=10,
        score=0.5,
        reason="x",
    )
    source = Citation(
        path="mall-admin/src/main/java/App.java",
        start_line=1,
        end_line=10,
        score=0.6,
        reason="x",
    )
    module = Citation(
        path="mall-admin",
        start_line=1,
        end_line=1,
        score=0.9,
        reason="x",
    )

    ordered = sorted([module, source, pom], key=_citation_priority)
    assert ordered[0].path == "pom.xml"
    assert ordered[1].path == "mall-admin/src/main/java/App.java"
