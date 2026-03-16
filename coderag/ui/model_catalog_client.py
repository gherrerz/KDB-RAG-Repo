"""Cliente UI para obtener catálogos de modelos desde la API con fallback local."""

from __future__ import annotations

from dataclasses import dataclass

import requests

from coderag.core.provider_model_catalog import (
    embedding_models_for_provider,
    llm_models_for_provider,
)
from coderag.core.settings import get_settings

API_BASE = "http://127.0.0.1:8000"


@dataclass(frozen=True)
class UIModelCatalogResult:
    """Resultado de catálogo de modelos consumible por vistas Qt."""

    models: list[str]
    source: str
    warning: str | None = None


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
