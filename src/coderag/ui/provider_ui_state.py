"""Resolucion compartida del estado UI de providers para Ingesta/Consulta."""

from dataclasses import dataclass

from coderag.core.provider_model_catalog import normalize_provider_name
from coderag.ui.provider_capabilities import (
    readiness,
    resolve_embedding_capability,
    resolve_llm_capability,
)
from coderag.ui.provider_defaults import default_embedding_model, default_llm_model
from coderag.ui.provider_feedback import (
    embedding_feedback_from_capability,
    llm_feedback_from_capability,
)


@dataclass(frozen=True)
class ProviderUIState:
    """Estado calculado para sincronizar inputs y chips de provider en UI."""

    default_model: str
    warning: str
    chip_state: str
    chip_text: str
    ready: bool
    reason: str


def resolve_embedding_ui_state(
    settings: object,
    provider: str,
    *,
    context: str,
) -> ProviderUIState:
    """Resuelve estado UI de embeddings para el provider seleccionado."""
    normalized = normalize_provider_name(provider)
    capability = resolve_embedding_capability(settings, normalized)
    warning, chip_state, chip_text = embedding_feedback_from_capability(
        capability,
        context=context,
    )
    ready, reason = readiness(capability)
    return ProviderUIState(
        default_model=default_embedding_model(normalized),
        warning=warning,
        chip_state=chip_state,
        chip_text=chip_text,
        ready=ready,
        reason=reason,
    )


def resolve_llm_ui_state(settings: object, provider: str) -> ProviderUIState:
    """Resuelve estado UI de LLM para el provider seleccionado."""
    normalized = normalize_provider_name(provider)
    capability = resolve_llm_capability(settings, normalized)
    warning, chip_state, chip_text = llm_feedback_from_capability(capability)
    ready, reason = readiness(capability)
    return ProviderUIState(
        default_model=default_llm_model(normalized),
        warning=warning,
        chip_state=chip_state,
        chip_text=chip_text,
        ready=ready,
        reason=reason,
    )
