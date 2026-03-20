"""Cliente UI para obtener catálogos de modelos desde la API con fallback local."""

from __future__ import annotations

from dataclasses import dataclass
import os

import requests

from coderag.core.provider_model_catalog import (
    embedding_models_for_provider,
    llm_models_for_provider,
)
from coderag.core.settings import get_settings

API_BASE = os.getenv("CODERAG_API_BASE", "http://127.0.0.1:8000")

_NON_REMOTE_FALLBACK_WARNINGS = {
    "catalog_service_unavailable",
    "model_kind_invalid",
    "missing_openai_api_key",
    "missing_anthropic_api_key",
    "missing_gemini_api_key",
    "missing_vertex_ai_api_key_or_project",
    "provider_without_embedding_backend",
    "anthropic_embedding_unsupported",
    "gemini_sdk_discovery_disabled",
    "unknown_provider_catalog_fallback",
    "anthropic_catalog_fallback",
}


@dataclass(frozen=True)
class UIModelCatalogResult:
    """Resultado de catálogo de modelos consumible por vistas Qt."""

    models: list[str]
    source: str
    warning: str | None = None


def should_show_remote_catalog_fallback_hint(warning: str | None) -> bool:
    """Indica si conviene mostrar aviso genérico de fallo remoto."""
    normalized = (warning or "").strip().lower()
    if not normalized or normalized in _NON_REMOTE_FALLBACK_WARNINGS:
        return False
    if "remote_catalog" in normalized:
        return True
    if normalized.endswith("_catalog_failed") or normalized.endswith("_catalog_empty"):
        return True
    if normalized == "empty_remote_models":
        return True
    return False


def fetch_models_for_provider(
    provider: str,
    kind: str,
    *,
    force_refresh: bool = False,
) -> UIModelCatalogResult:
    """Obtiene modelos de la API y usa fallback local si la API no responde."""
    normalized_provider = provider.strip().lower()
    normalized_kind = kind.strip().lower()

    settings = get_settings()
    timeout = max(1.0, float(settings.discovery_timeout_seconds))

    try:
        response = requests.get(
            f"{API_BASE}/providers/models",
            params={
                "provider": normalized_provider,
                "kind": normalized_kind,
                "force_refresh": force_refresh,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        models = [
            str(item).strip()
            for item in (payload.get("models") or [])
            if str(item).strip()
        ]
        source = str(payload.get("source") or "remote").strip().lower() or "remote"
        warning = payload.get("warning")
        if models:
            return UIModelCatalogResult(models=models, source=source, warning=warning)
        return _local_fallback(normalized_provider, normalized_kind, "empty_remote_models")
    except Exception:
        return _local_fallback(normalized_provider, normalized_kind, "catalog_service_unavailable")


def _local_fallback(provider: str, kind: str, warning: str) -> UIModelCatalogResult:
    """Resuelve catálogo local resiliente cuando discovery remoto falla."""
    if kind == "embedding":
        models = embedding_models_for_provider(provider)
    else:
        models = llm_models_for_provider(provider)
    return UIModelCatalogResult(models=models, source="fallback", warning=warning)
