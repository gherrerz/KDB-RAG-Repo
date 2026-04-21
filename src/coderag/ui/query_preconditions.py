"""Evaluación local de precondiciones antes de ejecutar consultas en UI."""

from dataclasses import dataclass


@dataclass(frozen=True)
class QueryPreconditionResult:
    """Resultado de validación local previo al request de consulta."""

    allowed: bool
    message: str


def evaluate_local_query_preconditions(
    *,
    repo_id: str,
    question: str,
    has_repo_in_catalog: bool,
    job_poll_enabled: bool,
    embedding_ready: bool,
    embedding_reason: str,
    llm_ready: bool,
    llm_reason: str,
    force_fallback: bool,
    retrieval_only_mode: bool = False,
) -> QueryPreconditionResult:
    """Valida precondiciones locales para consulta antes de llamar a la API."""
    if job_poll_enabled:
        return QueryPreconditionResult(
            allowed=False,
            message="La ingesta está en progreso. Espera a que finalice antes de consultar.",
        )

    llm_required = not retrieval_only_mode
    providers_ready = embedding_ready and (llm_ready or not llm_required)
    if not providers_ready and not force_fallback:
        details: list[str] = []
        if not embedding_ready:
            details.append(f"embeddings={embedding_reason}")
        if llm_required and not llm_ready:
            details.append(f"llm={llm_reason}")
        return QueryPreconditionResult(
            allowed=False,
            message=(
                "Provider no listo para consulta "
                f"({', '.join(details)}). Activa 'Forzar fallback' para continuar."
            ),
        )

    if not repo_id:
        return QueryPreconditionResult(
            allowed=False,
            message="Debes seleccionar un ID de repositorio del listado.",
        )

    if not has_repo_in_catalog:
        return QueryPreconditionResult(
            allowed=False,
            message=(
                "El ID seleccionado no existe en la base de conocimiento. "
                "Actualiza la lista e intenta nuevamente."
            ),
        )

    if not question:
        return QueryPreconditionResult(
            allowed=False,
            message="Debes escribir una pregunta para consultar.",
        )

    return QueryPreconditionResult(allowed=True, message="")
