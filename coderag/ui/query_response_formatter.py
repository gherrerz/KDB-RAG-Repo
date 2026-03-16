"""Formateadores de mensajes de consulta para UI."""

from collections.abc import Sequence
from typing import Any


def build_query_answer_text(answer: str, diagnostics: dict[str, Any] | None) -> str:
    """Construye texto final de respuesta anexando diagnóstico relevante."""
    base = (answer or "Sin respuesta.").strip() or "Sin respuesta."
    details = diagnostics or {}
    fallback_reason = details.get("fallback_reason")
    if fallback_reason:
        return f"{base}\n\n[diagnóstico: {fallback_reason}]"
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
