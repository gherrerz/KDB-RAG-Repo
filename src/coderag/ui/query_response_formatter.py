"""Formateadores de mensajes de consulta para UI."""

from collections.abc import Sequence
from typing import Any


def _normalize_text(value: Any) -> str:
    """Normaliza valores a texto en minúsculas para clasificación robusta."""
    return str(value or "").strip().lower()


def _humanize_fallback_reason(reason: str) -> str:
    """Convierte códigos técnicos de fallback en mensajes comprensibles."""
    mapping = {
        "generation_error": "error al generar con el modelo",
        "verification_failed": "verificación de respuesta fallida",
        "insufficient_context": "contexto insuficiente",
        "not_configured": "provider no configurado",
        "time_budget_exhausted": "tiempo de consulta agotado",
        "inventory_target_missing": "no se pudo identificar el objetivo de inventario",
    }
    normalized = _normalize_text(reason)
    return mapping.get(normalized, normalized or "sin detalle")


def _classify_llm_error(llm_error: str) -> str | None:
    """Clasifica llm_error a una causa corta y accionable para la UI."""
    normalized = _normalize_text(llm_error)
    if not normalized:
        return None

    if "credit balance" in normalized or "insufficient credit" in normalized:
        return "provider llm sin créditos suficientes"
    if "rate limit" in normalized or "too many requests" in normalized:
        return "límite de tasa del provider llm alcanzado"
    if "unauthorized" in normalized or "authentication" in normalized or "api key" in normalized:
        return "credenciales del provider llm inválidas o faltantes"
    if "model" in normalized and (
        "not available" in normalized
        or "not found" in normalized
        or "does not exist" in normalized
        or "not enabled" in normalized
        or "unsupported" in normalized
    ):
        return "modelo llm no disponible para la cuenta"
    if "timeout" in normalized or "timed out" in normalized:
        return "timeout del provider llm"
    return None


def build_query_answer_text(answer: str, diagnostics: dict[str, Any] | None) -> str:
    """Construye texto final de respuesta anexando diagnóstico relevante."""
    base = (answer or "Sin respuesta.").strip() or "Sin respuesta."
    details = diagnostics or {}
    fallback_reason = str(details.get("fallback_reason") or "").strip()
    if fallback_reason:
        reason_human = _humanize_fallback_reason(fallback_reason)
        extra_cause = None
        if _normalize_text(fallback_reason) == "generation_error":
            extra_cause = _classify_llm_error(str(details.get("llm_error") or ""))

        if extra_cause:
            return (
                f"{base}\n\n"
                f"[diagnóstico: {fallback_reason} | {reason_human}: {extra_cause}]"
            )

        return f"{base}\n\n[diagnóstico: {fallback_reason} | {reason_human}]"
    return base


def build_repo_not_ready_message(warnings: Sequence[str] | None) -> str:
    """Construye mensaje UI cuando un repositorio no está listo para consultas."""
    intro = (
        "El repositorio no esta listo para consultas. "
        "Ejecuta una nueva ingesta o revisa el estado de indices."
    )
    if not warnings:
        return intro
    top_lines = [str(line) for line in warnings[:3]]
    hints = "\n" + "\n".join(f"- {line}" for line in top_lines)
    return f"{intro}{hints}"
