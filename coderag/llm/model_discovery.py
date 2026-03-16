"""Descubrimiento dinámico de modelos por provider con fallback local."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from time import monotonic

import requests
from openai import OpenAI

from coderag.core.provider_model_catalog import (
    embedding_models_for_provider,
    llm_models_for_provider,
    normalize_provider_name,
)
from coderag.core.settings import ProviderName, get_settings


ModelKind = str


@dataclass(frozen=True)
class ModelDiscoveryResult:
    """Resultado consolidado del descubrimiento de modelos."""

    provider: str
    kind: ModelKind
    models: list[str]
    source: str
    warning: str | None = None


@dataclass
class _CacheEntry:
    """Entrada de cache para resultados de discovery."""

    fetched_at: float
    result: ModelDiscoveryResult


_cache_lock = Lock()
_cache: dict[tuple[str, str], _CacheEntry] = {}


def discover_models(
    provider: str,
    kind: ModelKind,
    *,
    force_refresh: bool = False,
) -> ModelDiscoveryResult:
    """Descubre modelos por provider/tipo usando remoto cuando es posible."""
    settings = get_settings()
    normalized_provider = normalize_provider_name(provider)
    normalized_kind = kind.strip().lower()

    if normalized_kind not in {"embedding", "llm"}:
        return ModelDiscoveryResult(
            provider=normalized_provider,
            kind=normalized_kind,
            models=[],
            source="error",
            warning="model_kind_invalid",
        )

    key = (normalized_provider, normalized_kind)
    now = monotonic()
    ttl = max(0, int(settings.discovery_cache_ttl_seconds))

    if not force_refresh and ttl > 0:
        with _cache_lock:
            cached = _cache.get(key)
            if cached and (now - cached.fetched_at) <= ttl:
                return ModelDiscoveryResult(
                    provider=cached.result.provider,
                    kind=cached.result.kind,
                    models=list(cached.result.models),
                    source="cache",
                    warning=cached.result.warning,
                )

    result = _discover_uncached(normalized_provider, normalized_kind)
    with _cache_lock:
        _cache[key] = _CacheEntry(fetched_at=now, result=result)
    return result


def _discover_uncached(provider: str, kind: ModelKind) -> ModelDiscoveryResult:
    """Resuelve discovery real por provider con fallback local."""
    if provider == "openai":
        return _discover_openai(kind)
    if provider == "vertex_ai":
        return _discover_vertex(kind)
    if provider == "gemini":
        return _discover_gemini(kind)
    if provider == "anthropic":
        return _fallback(provider, kind, warning="anthropic_catalog_fallback")
    return _fallback(provider, kind, warning="unknown_provider_catalog_fallback")


def _discover_openai(kind: ModelKind) -> ModelDiscoveryResult:
    """Lista modelos de OpenAI usando SDK cuando hay API key."""
    settings = get_settings()
    api_key = settings.openai_api_key.strip()
    if not api_key:
        return _fallback("openai", kind, warning="missing_openai_api_key")

    try:
        client = OpenAI(api_key=api_key)
        response = client.models.list()
        entries = [item.id for item in response.data if getattr(item, "id", "")]
        models = _filter_models(entries, kind, provider="openai")
        if not models:
            return _fallback("openai", kind, warning="openai_empty_remote_catalog")
        return ModelDiscoveryResult(
            provider="openai",
            kind=kind,
            models=models,
            source="remote",
        )
    except Exception:
        return _fallback("openai", kind, warning="openai_remote_catalog_failed")


def _discover_vertex(kind: ModelKind) -> ModelDiscoveryResult:
    """Lista modelos de Vertex AI via endpoint de publisher models."""
    settings = get_settings()
    token = settings.vertex_ai_api_key.strip()
    project_id = settings.vertex_ai_project_id.strip()
    location = settings.vertex_ai_location.strip() or "us-central1"

    if not token or not project_id:
        return _fallback("vertex_ai", kind, warning="missing_vertex_ai_api_key_or_project")

    timeout = max(1.0, float(settings.discovery_timeout_seconds))
    url = (
        f"https://{location}-aiplatform.googleapis.com/v1/projects/{project_id}/"
        f"locations/{location}/publishers/google/models"
    )
    try:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        raw_models = payload.get("models") or []
        names: list[str] = []
        for item in raw_models:
            if not isinstance(item, dict):
                continue
            full_name = str(item.get("name") or "")
            model_name = full_name.split("/")[-1]
            if model_name:
                names.append(model_name)
        models = _filter_models(names, kind, provider="vertex_ai")
        if not models:
            return _fallback("vertex_ai", kind, warning="vertex_remote_catalog_empty")
        return ModelDiscoveryResult(
            provider="vertex_ai",
            kind=kind,
            models=models,
            source="remote",
        )
    except Exception:
        return _fallback("vertex_ai", kind, warning="vertex_remote_catalog_failed")


def _discover_gemini(kind: ModelKind) -> ModelDiscoveryResult:
    """Intenta discovery SDK para Gemini y cae a fallback local si falla."""
    settings = get_settings()
    api_key = settings.gemini_api_key.strip()
    if not api_key:
        return _fallback("gemini", kind, warning="missing_gemini_api_key")

    if not settings.discovery_gemini_sdk_enabled:
        return _fallback("gemini", kind, warning="gemini_sdk_discovery_disabled")

    try:
        import google.generativeai as genai  # type: ignore[import-not-found]

        genai.configure(api_key=api_key)
        entries = list(genai.list_models())
        names: list[str] = []
        for item in entries:
            raw_name = getattr(item, "name", "")
            if not raw_name:
                continue
            model_name = raw_name.split("/")[-1]
            methods = set(getattr(item, "supported_generation_methods", []) or [])
            if kind == "embedding" and "embedContent" not in methods:
                continue
            if kind == "llm" and "generateContent" not in methods:
                continue
            names.append(model_name)

        models = _filter_models(names, kind, provider="gemini")
        if not models:
            return _fallback("gemini", kind, warning="gemini_sdk_catalog_empty")
        return ModelDiscoveryResult(
            provider="gemini",
            kind=kind,
            models=models,
            source="remote",
        )
    except Exception:
        return _fallback("gemini", kind, warning="gemini_sdk_catalog_failed")


def _filter_models(entries: list[str], kind: ModelKind, *, provider: ProviderName) -> list[str]:
    """Filtra y ordena modelos relevantes por tipo y provider."""
    settings = get_settings()
    max_items = max(1, int(settings.discovery_max_results))
    unique: dict[str, None] = {}
    for raw in entries:
        name = str(raw or "").strip()
        if not name:
            continue
        lowered = name.lower()
        if kind == "embedding":
            if "embed" not in lowered:
                continue
        else:
            if "embed" in lowered:
                continue
            if provider == "openai" and not (
                lowered.startswith("gpt")
                or lowered.startswith("o1")
                or lowered.startswith("o3")
            ):
                continue
            if provider in {"gemini", "vertex_ai"} and "gemini" not in lowered:
                continue
            if provider == "anthropic" and "claude" not in lowered:
                continue
        unique[name] = None
        if len(unique) >= max_items:
            break
    return sorted(unique.keys())


def _fallback(provider: str, kind: ModelKind, *, warning: str) -> ModelDiscoveryResult:
    """Retorna catálogo local por provider/tipo como fallback resiliente."""
    if kind == "embedding":
        models = embedding_models_for_provider(provider)
    else:
        models = llm_models_for_provider(provider)
    return ModelDiscoveryResult(
        provider=provider,
        kind=kind,
        models=models,
        source="fallback",
        warning=warning,
    )
