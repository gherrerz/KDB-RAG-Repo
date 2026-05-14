"""Citation ordering and extractive fallback presentation helpers."""

from collections import Counter
from pathlib import Path, PurePosixPath

from coderag.core.models import Citation


def fallback_header(fallback_reason: str) -> str:
    """Return the extractive fallback header for a given root cause."""
    messages = {
        "not_configured": (
            "LLM no está configurado; respuesta extractiva basada en "
            "evidencia."
        ),
        "verification_failed": (
            "No se pudo validar completamente la respuesta generada; "
            "mostrando evidencia trazable."
        ),
        "generation_error": (
            "Ocurrió un error al generar respuesta con el modelo seleccionado; mostrando "
            "evidencia trazable."
        ),
        "time_budget_exhausted": (
            "Se alcanzó el presupuesto de tiempo de consulta; mostrando "
            "evidencia trazable disponible."
        ),
        "insufficient_context": (
            "No hubo contexto suficiente para una síntesis confiable; "
            "mostrando evidencia trazable disponible."
        ),
    }
    return messages.get(
        fallback_reason,
        "Mostrando evidencia trazable del repositorio.",
    )


def build_extractive_fallback(
    citations: list[Citation],
    inventory_mode: bool = False,
    inventory_target: str | None = None,
    query: str = "",
    fallback_reason: str = "not_configured",
    component_purposes: list[tuple[str, str]] | None = None,
) -> str:
    """Build a local evidence-only response when LLM output is unavailable."""
    if not citations:
        return "No se encontró información en el repositorio."

    if inventory_mode:
        unique_citations = deduplicate_citations_by_path(citations)
        file_paths = [item.path for item in unique_citations]
        component_names = [PurePosixPath(path).name for path in file_paths]
        purposes_by_name = dict(component_purposes or [])

        folders = [
            str(PurePosixPath(path).parent)
            for path in file_paths
            if str(PurePosixPath(path).parent) not in {"", "."}
        ]
        folder_counter = Counter(folders)
        top_folders = [folder for folder, _count in folder_counter.most_common(3)]

        target_label = inventory_target or "componentes"
        lines = [
            fallback_header(fallback_reason),
            "1) Respuesta principal:",
            (
                f"Se identificaron {len(unique_citations)} elementos para "
                f"'{target_label}' en el repositorio consultado."
            ),
            "",
            "2) Componentes/archivos clave:",
        ]
        lines.extend(f"- {name}" for name in component_names)

        if purposes_by_name:
            lines.extend([
                "",
                "3) Función probable de cada componente:",
            ])
            for name in component_names:
                purpose = purposes_by_name.get(name)
                if purpose:
                    lines.append(f"- {name}: {purpose}")

        if top_folders:
            section_number = "4" if purposes_by_name else "3"
            lines.extend([
                "",
                f"{section_number}) Organización observada en el contexto:",
            ])
            lines.extend(f"- {folder}" for folder in top_folders)

        citations_section_number = "5" if purposes_by_name else "4"
        lines.extend([
            "",
            f"{citations_section_number}) Citas de archivos con líneas:",
        ])
        lines.extend(
            (
                f"- {citation.path} "
                f"(líneas {citation.start_line}-{citation.end_line}, "
                f"score {citation.score:.4f})"
            )
            for citation in unique_citations
        )

        if query.strip():
            lines.extend([
                "",
                f"Consulta original: {query.strip()}",
            ])
        return "\n".join(lines)

    lines = [fallback_header(fallback_reason)]
    limit = len(citations) if inventory_mode else 5
    for index, citation in enumerate(citations[:limit], start=1):
        lines.append(
            (
                f"{index}. {citation.path} "
                f"(líneas {citation.start_line}-{citation.end_line}, "
                f"score {citation.score:.4f})"
            )
        )
    return "\n".join(lines)


def deduplicate_citations(citations: list[Citation]) -> list[Citation]:
    """Deduplicate citations while preserving first-seen order."""
    seen: set[tuple[str, int, int]] = set()
    deduplicated: list[Citation] = []
    for citation in citations:
        key = (
            citation.path,
            citation.start_line,
            citation.end_line,
        )
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(citation)
    return deduplicated


def deduplicate_citations_by_path(citations: list[Citation]) -> list[Citation]:
    """Deduplicate citations by path while preserving first-seen order."""
    seen_paths: set[str] = set()
    deduplicated: list[Citation] = []
    for citation in citations:
        key = citation.path.strip().lower()
        if key in seen_paths:
            continue
        seen_paths.add(key)
        deduplicated.append(citation)
    return deduplicated


def citation_priority(citation: Citation) -> tuple[int, float]:
    """Rank citations using generic path quality signals."""
    path = citation.path.strip().lower()
    suffix = Path(path).suffix
    code_like_suffixes = {
        ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".kt", ".go",
        ".rs", ".cs", ".cpp", ".cc", ".c", ".h", ".hpp", ".php",
        ".rb", ".swift", ".scala", ".sql", ".sh", ".ps1", ".yaml",
        ".yml", ".json", ".toml", ".md", ".xml",
    }
    if suffix in code_like_suffixes:
        rank = 0
    elif "/" in path or "\\" in path:
        rank = 1
    elif path:
        rank = 2
    else:
        rank = 3
    return (rank, -citation.score)