"""Resolución interna de respuesta final entre LLM y fallback extractivo."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Protocol

from coderag.core.models import Citation


class QueryAnswerClient(Protocol):
    """Contrato mínimo del cliente LLM requerido por la resolución final."""

    enabled: bool

    def answer(
        self,
        *,
        query: str,
        context: str,
        timeout_seconds: float,
    ) -> str:
        """Genera una respuesta final a partir de query y contexto."""

    def verify(
        self,
        *,
        answer: str,
        context: str,
        timeout_seconds: float,
    ) -> bool:
        """Valida la respuesta generada contra el contexto recuperado."""


class QueryAnswerSettings(Protocol):
    """Contrato mínimo de settings usado por la resolución de respuesta."""

    openai_timeout_seconds: float


class ContextSufficiencyEvaluator(Protocol):
    """Callable que decide si el contexto es suficiente para sintetizar."""

    def __call__(self, *, context: str, reranked_count: int) -> bool:
        """Evalúa si el contexto recuperado permite responder con seguridad."""


class ExtractiveFallbackBuilder(Protocol):
    """Callable que construye un fallback extractivo a partir de citas."""

    def __call__(
        self,
        citations: list[Citation],
        inventory_mode: bool = False,
        inventory_target: str | None = None,
        query: str = "",
        fallback_reason: str = "not_configured",
        component_purposes: list[tuple[str, str]] | None = None,
    ) -> str:
        """Construye una respuesta extractiva basada en evidencia."""


@dataclass(frozen=True)
class QueryAnswerResolution:
    """Representa la resolución final entre síntesis LLM y fallback."""

    answer: str
    context_sufficient: bool
    fallback_reason: str | None
    verify_valid: bool | None
    verify_skipped: bool
    llm_error: str | None


@dataclass(frozen=True)
class QueryAnswerResolutionHooks:
    """Colaboradores inyectados para resolver la respuesta final."""

    is_context_sufficient: ContextSufficiencyEvaluator
    build_extractive_fallback: ExtractiveFallbackBuilder
    remaining_budget_seconds: Callable[[float, float], float]
    elapsed_milliseconds: Callable[[float], float]


def resolve_query_answer(
    *,
    client: QueryAnswerClient,
    settings: QueryAnswerSettings,
    query: str,
    citations: list[Citation],
    context: str,
    reranked_count: int,
    verify_enabled: bool,
    pipeline_started_at: float,
    budget_seconds: float,
    stage_timings: dict[str, float],
    hooks: QueryAnswerResolutionHooks,
) -> QueryAnswerResolution:
    """Resuelve la respuesta final para query entre LLM y fallback extractivo."""
    context_sufficient = hooks.is_context_sufficient(
        context=context,
        reranked_count=reranked_count,
    )
    if not context_sufficient:
        fallback_reason = "insufficient_context"
        return QueryAnswerResolution(
            answer=hooks.build_extractive_fallback(
                citations,
                query=query,
                fallback_reason=fallback_reason,
            ),
            context_sufficient=False,
            fallback_reason=fallback_reason,
            verify_valid=None,
            verify_skipped=False,
            llm_error=None,
        )

    if not client.enabled:
        fallback_reason = "not_configured"
        return QueryAnswerResolution(
            answer=hooks.build_extractive_fallback(
                citations,
                query=query,
                fallback_reason=fallback_reason,
            ),
            context_sufficient=True,
            fallback_reason=fallback_reason,
            verify_valid=None,
            verify_skipped=False,
            llm_error=None,
        )

    if hooks.remaining_budget_seconds(pipeline_started_at, budget_seconds) <= 0:
        fallback_reason = "time_budget_exhausted"
        return QueryAnswerResolution(
            answer=hooks.build_extractive_fallback(
                citations,
                query=query,
                fallback_reason=fallback_reason,
            ),
            context_sufficient=True,
            fallback_reason=fallback_reason,
            verify_valid=None,
            verify_skipped=False,
            llm_error=None,
        )

    try:
        answer_started_at = monotonic()
        answer_timeout = min(
            float(settings.openai_timeout_seconds),
            hooks.remaining_budget_seconds(pipeline_started_at, budget_seconds),
        )
        if answer_timeout <= 0:
            fallback_reason = "time_budget_exhausted"
            return QueryAnswerResolution(
                answer=hooks.build_extractive_fallback(
                    citations,
                    query=query,
                    fallback_reason=fallback_reason,
                ),
                context_sufficient=True,
                fallback_reason=fallback_reason,
                verify_valid=None,
                verify_skipped=False,
                llm_error=None,
            )

        answer = client.answer(
            query=query,
            context=context,
            timeout_seconds=answer_timeout,
        )
        stage_timings["llm_answer_ms"] = hooks.elapsed_milliseconds(
            answer_started_at
        )

        if not verify_enabled:
            return QueryAnswerResolution(
                answer=answer,
                context_sufficient=True,
                fallback_reason=None,
                verify_valid=None,
                verify_skipped=True,
                llm_error=None,
            )

        verify_timeout = min(
            float(settings.openai_timeout_seconds),
            hooks.remaining_budget_seconds(pipeline_started_at, budget_seconds),
        )
        if verify_timeout <= 0:
            return QueryAnswerResolution(
                answer=answer,
                context_sufficient=True,
                fallback_reason=None,
                verify_valid=None,
                verify_skipped=True,
                llm_error=None,
            )

        verify_started_at = monotonic()
        verify_valid = client.verify(
            answer=answer,
            context=context,
            timeout_seconds=verify_timeout,
        )
        stage_timings["llm_verify_ms"] = hooks.elapsed_milliseconds(
            verify_started_at
        )
        if not verify_valid:
            fallback_reason = "verification_failed"
            answer = hooks.build_extractive_fallback(
                citations,
                query=query,
                fallback_reason=fallback_reason,
            )
            return QueryAnswerResolution(
                answer=answer,
                context_sufficient=True,
                fallback_reason=fallback_reason,
                verify_valid=False,
                verify_skipped=False,
                llm_error=None,
            )

        return QueryAnswerResolution(
            answer=answer,
            context_sufficient=True,
            fallback_reason=None,
            verify_valid=True,
            verify_skipped=False,
            llm_error=None,
        )
    except Exception as exc:
        fallback_reason = "generation_error"
        return QueryAnswerResolution(
            answer=hooks.build_extractive_fallback(
                citations,
                query=query,
                fallback_reason=fallback_reason,
            ),
            context_sufficient=True,
            fallback_reason=fallback_reason,
            verify_valid=None,
            verify_skipped=False,
            llm_error=str(exc),
        )