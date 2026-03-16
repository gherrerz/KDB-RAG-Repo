"""Utilidades compartidas para feedback visual de providers en UI."""

from PySide6.QtWidgets import QLabel

from coderag.ui.provider_capabilities import EmbeddingCapability, LLMCapability
from coderag.ui.provider_messages import (
    embedding_warning_not_configured,
    embedding_warning_unsupported,
    llm_warning_not_configured,
    llm_warning_unsupported,
)


def apply_status_chip(chip: QLabel, state: str, text: str) -> None:
    """Aplica estado y texto normalizados a un chip de readiness."""
    valid_states = {"ready", "warning", "blocked"}
    selected_state = state if state in valid_states else "ready"
    chip.setProperty("state", selected_state)
    chip.setText(text)
    chip.style().unpolish(chip)
    chip.style().polish(chip)


def embedding_feedback_from_capability(
    capability: EmbeddingCapability,
    context: str,
) -> tuple[str, str, str]:
    """Resuelve warning y estado visual para providers de embeddings."""
    supported = bool(capability["supported"])
    configured = bool(capability["configured"])
    reason = str(capability["reason"])

    if not supported:
        warning = embedding_warning_unsupported(context)
        return warning, "warning", "Embeddings: No listo (fallback disponible)"

    if not configured:
        warning = embedding_warning_not_configured(context, reason)
        return warning, "blocked", "Embeddings: No listo"

    return "", "ready", "Embeddings: Listo"


def llm_feedback_from_capability(capability: LLMCapability) -> tuple[str, str, str]:
    """Resuelve warning y estado visual para providers LLM."""
    supported = bool(capability["supported"])
    configured = bool(capability["configured"])
    reason = str(capability["reason"])

    if not supported:
        warning = llm_warning_unsupported()
        return warning, "blocked", "LLM: No listo"

    if not configured:
        warning = llm_warning_not_configured(reason)
        return warning, "blocked", "LLM: No listo"

    return "", "ready", "LLM: Listo"