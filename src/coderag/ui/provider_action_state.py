"""Evaluadores puros para decidir estado de acciones en la UI."""

from dataclasses import dataclass

from src.coderag.ui.provider_messages import (
    ingest_provider_not_ready_message,
    ingest_ready_message,
    query_blocked_by_ingestion_message,
    query_provider_not_ready_message,
    query_ready_message,
    query_requires_question_message,
    query_requires_repo_message,
)


@dataclass(frozen=True)
class ActionState:
    """Resultado de evaluación de habilitación y mensaje asociado."""

    enabled: bool
    message: str


def evaluate_ingest_action(
    embedding_ready: bool,
    embedding_reason: str,
    force_fallback: bool,
) -> ActionState:
    """Decide si la acción de ingesta debe estar habilitada."""
    if embedding_ready or force_fallback:
        return ActionState(enabled=True, message=ingest_ready_message())
    return ActionState(
        enabled=False,
        message=ingest_provider_not_ready_message(embedding_reason),
    )


def evaluate_query_action(
    controls_enabled: bool,
    has_repo: bool,
    has_question: bool,
    embedding_ready: bool,
    embedding_reason: str,
    llm_ready: bool,
    llm_reason: str,
    force_fallback: bool,
    retrieval_only_mode: bool = False,
) -> ActionState:
    """Decide si la acción de consulta debe estar habilitada."""
    if not controls_enabled:
        return ActionState(enabled=False, message=query_blocked_by_ingestion_message())

    if not has_repo:
        return ActionState(enabled=False, message=query_requires_repo_message())

    if not has_question:
        return ActionState(enabled=False, message=query_requires_question_message())

    llm_required = not retrieval_only_mode
    providers_ready = embedding_ready and (llm_ready or not llm_required)
    if not providers_ready and not force_fallback:
        details: list[str] = []
        if not embedding_ready:
            details.append(f"embeddings={embedding_reason}")
        if llm_required and not llm_ready:
            details.append(f"llm={llm_reason}")
        detail_text = ", ".join(details)
        return ActionState(
            enabled=False,
            message=query_provider_not_ready_message(detail_text),
        )

    return ActionState(enabled=True, message=query_ready_message())