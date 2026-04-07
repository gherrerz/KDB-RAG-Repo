"""Pruebas para filtrado y priorización de la calidad de las citas."""

from coderag.api.citation_filters import is_noisy_path
from coderag.api.query_service import _citation_priority
from coderag.core.models import Citation


def test_is_noisy_path_filters_non_informative_paths() -> None:
    """Marks known noisy pseudo-paths as non-informative."""
    assert is_noisy_path(".")
    assert is_noisy_path("document")
    assert is_noisy_path("document/reference/file.md")
    assert not is_noisy_path("services/api/index.ts")


def test_citation_priority_prefers_code_files_and_structured_paths() -> None:
    """Prioriza las rutas de los archivos por encima de las etiquetas de solo módulo de forma genérica."""
    file_path = Citation(
        path="services/api/index.ts",
        start_line=1,
        end_line=10,
        score=0.5,
        reason="x",
    )
    structured_path = Citation(
        path="services/api",
        start_line=1,
        end_line=10,
        score=0.6,
        reason="x",
    )
    module = Citation(
        path="api",
        start_line=1,
        end_line=1,
        score=0.9,
        reason="x",
    )

    ordered = sorted([module, structured_path, file_path], key=_citation_priority)
    assert ordered[0].path == "services/api/index.ts"
    assert ordered[1].path == "services/api"
