"""Tipos y normalizadores para capacidades de providers en la UI."""

from typing import Mapping, TypedDict


class EmbeddingCapability(TypedDict):
    """Capacidades mínimas de un provider de embeddings."""

    provider: str
    supported: bool
    configured: bool
    reason: str


class LLMCapability(TypedDict):
    """Capacidades mínimas de un provider LLM."""

    provider: str
    supported: bool
    configured: bool
    answer: bool
    verify: bool
    reason: str


def normalize_embedding_capability(
    provider: str,
    raw: Mapping[str, object] | None,
) -> EmbeddingCapability:
    """Normaliza payload de capacidades de embeddings a shape tipado."""
    source = raw or {}
    normalized_provider = str(source.get("provider", provider))
    supported = bool(source.get("supported", True))
    configured = bool(source.get("configured", True))
    reason = str(source.get("reason", "ok" if configured else "not_configured"))
    return {
        "provider": normalized_provider,
        "supported": supported,
        "configured": configured,
        "reason": reason,
    }


def normalize_llm_capability(
    provider: str,
    raw: Mapping[str, object] | None,
) -> LLMCapability:
    """Normaliza payload de capacidades LLM a shape tipado."""
    source = raw or {}
    normalized_provider = str(source.get("provider", provider))
    supported = bool(source.get("supported", True))
    configured = bool(source.get("configured", True))
    reason = str(source.get("reason", "ok" if configured else "not_configured"))
    answer = bool(source.get("answer", True))
    verify = bool(source.get("verify", True))
    return {
        "provider": normalized_provider,
        "supported": supported,
        "configured": configured,
        "answer": answer,
        "verify": verify,
        "reason": reason,
    }


def resolve_embedding_capability(settings: object, provider: str) -> EmbeddingCapability:
    """Obtiene capacidades de embeddings desde settings de forma segura."""
    method = getattr(settings, "embedding_provider_capabilities", None)
    raw: Mapping[str, object] | None = None
    if callable(method):
        value = method(provider)
        if isinstance(value, Mapping):
            raw = value
    return normalize_embedding_capability(provider, raw)


def resolve_llm_capability(settings: object, provider: str) -> LLMCapability:
    """Obtiene capacidades LLM desde settings de forma segura."""
    method = getattr(settings, "llm_provider_capabilities", None)
    raw: Mapping[str, object] | None = None
    if callable(method):
        value = method(provider)
        if isinstance(value, Mapping):
            raw = value
    return normalize_llm_capability(provider, raw)


def readiness(capability: EmbeddingCapability | LLMCapability) -> tuple[bool, str]:
    """Evalúa readiness estándar y reason a partir de capacidades normalizadas."""
    ready = bool(capability["supported"]) and bool(capability["configured"])
    reason = str(capability["reason"])
    return ready, reason