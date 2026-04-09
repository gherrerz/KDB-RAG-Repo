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
from coderag.core.vertex_ai import resolve_vertex_auth_context


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
    """Lista modelos Vertex con estrategias remotas y fallback resiliente."""
    settings = get_settings()
    project_id = settings.vertex_ai_project_id.strip()
    location = settings.vertex_ai_location.strip() or "us-central1"
    if hasattr(settings, "resolve_vertex_credentials_reference"):
        credentials_source = str(
            settings.resolve_vertex_credentials_reference()
        ).strip()
    else:
        credentials_source = str(
            getattr(settings, "vertex_ai_service_account_json_b64", "")
        ).strip()

    is_configured = (
        settings.is_vertex_ai_configured()
        if hasattr(settings, "is_vertex_ai_configured")
        else bool(project_id and credentials_source)
    )
    if not is_configured or not project_id:
        return _fallback(
            "vertex_ai",
            kind,
            warning="missing_vertex_ai_api_key_or_project",
        )

    timeout = max(1.0, float(settings.discovery_timeout_seconds))
    publisher_warning: str | None = None
    auth_context = resolve_vertex_auth_context(credentials_source)

    try:
        names = _discover_vertex_publisher_names(
            project_id=project_id,
            location=location,
            timeout=timeout,
            bearer_token=auth_context.access_token,
            api_key=None,
        )
        models = _filter_models(names, kind, provider="vertex_ai")
        if models:
            return ModelDiscoveryResult(
                provider="vertex_ai",
                kind=kind,
                models=models,
                source="remote",
            )
        publisher_warning = "vertex_remote_catalog_empty"
    except Exception:
        publisher_warning = "vertex_remote_catalog_failed"

    # Fallback remoto compatible: usa catálogo Gemini REST (preferentemente GEMINI_API_KEY).
    for compatible_key in (settings.gemini_api_key.strip(),):
        if not compatible_key:
            continue
        try:
            names = _discover_gemini_rest_names(
                kind=kind,
                api_key=compatible_key,
                timeout=timeout,
            )
            models = _filter_models(names, kind, provider="vertex_ai")
            if models:
                return ModelDiscoveryResult(
                    provider="vertex_ai",
                    kind=kind,
                    models=models,
                    source="remote",
                    warning="vertex_catalog_via_gemini_rest",
                )
        except Exception:
            continue

    return _fallback("vertex_ai", kind, warning=publisher_warning or "vertex_remote_catalog_failed")


def _discover_vertex_publisher_names(
    *,
    project_id: str,
    location: str,
    timeout: float,
    bearer_token: str | None,
    api_key: str | None,
) -> list[str]:
    """Descubre nombres de modelos en Vertex publisher endpoint con paginación."""
    next_page_token: str | None = None
    entries: list[str] = []

    for _ in range(12):
        payload = _vertex_models_page(
            project_id=project_id,
            location=location,
            timeout=timeout,
            page_token=next_page_token,
            bearer_token=bearer_token,
            api_key=api_key,
        )
        raw_models = payload.get("models") or []
        for item in raw_models:
            if not isinstance(item, dict):
                continue
            full_name = str(item.get("name") or "").strip()
            model_name = full_name.split("/")[-1].strip()
            if model_name:
                entries.append(model_name)
        raw_token = str(payload.get("nextPageToken") or "").strip()
        if not raw_token:
            break
        next_page_token = raw_token

    return entries


def _vertex_models_page(
    *,
    project_id: str,
    location: str,
    timeout: float,
    page_token: str | None,
    bearer_token: str | None,
    api_key: str | None,
) -> dict:
    """Obtiene una página del catálogo de publisher models en Vertex."""
    url = (
        f"https://{location}-aiplatform.googleapis.com/v1/projects/{project_id}/"
        f"locations/{location}/publishers/google/models"
    )
    params: dict[str, str | int] = {"pageSize": 100}
    if page_token:
        params["pageToken"] = page_token
    if api_key:
        params["key"] = api_key

    headers: dict[str, str] = {}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    response = requests.get(url, headers=headers or None, params=params, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, dict) else {}


def _discover_gemini(kind: ModelKind) -> ModelDiscoveryResult:
    """Descubre modelos Gemini con REST primero y SDK como fallback."""
    settings = get_settings()
    api_key = settings.gemini_api_key.strip()
    if not api_key:
        return _fallback("gemini", kind, warning="missing_gemini_api_key")

    timeout = max(1.0, float(settings.discovery_timeout_seconds))
    rest_warning: str | None = None

    try:
        names = _discover_gemini_rest_names(kind=kind, api_key=api_key, timeout=timeout)
        models = _filter_models(names, kind, provider="gemini")
        if models:
            return ModelDiscoveryResult(
                provider="gemini",
                kind=kind,
                models=models,
                source="remote",
            )
        rest_warning = "gemini_rest_catalog_empty"
    except Exception:
        rest_warning = "gemini_rest_catalog_failed"

    if not settings.discovery_gemini_sdk_enabled:
        return _fallback(
            "gemini",
            kind,
            warning=rest_warning or "gemini_sdk_discovery_disabled",
        )

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
            if rest_warning:
                return _fallback("gemini", kind, warning="gemini_rest_and_sdk_empty")
            return _fallback("gemini", kind, warning="gemini_sdk_catalog_empty")
        return ModelDiscoveryResult(
            provider="gemini",
            kind=kind,
            models=models,
            source="remote",
        )
    except Exception:
        if rest_warning:
            return _fallback("gemini", kind, warning="gemini_rest_and_sdk_catalog_failed")
        return _fallback("gemini", kind, warning="gemini_sdk_catalog_failed")


def _discover_gemini_rest_names(*, kind: ModelKind, api_key: str, timeout: float) -> list[str]:
    """Obtiene nombres de modelos Gemini desde API REST con paginación."""
    next_page_token: str | None = None
    entries: list[str] = []
    # Evita bucles infinitos si el backend devuelve un token inconsistente.
    for _ in range(12):
        payload = _gemini_models_page(
            api_key=api_key,
            timeout=timeout,
            page_token=next_page_token,
        )
        raw_models = payload.get("models") or []
        for item in raw_models:
            model_name = _gemini_model_name_for_kind(item, kind=kind)
            if model_name:
                entries.append(model_name)
        raw_token = str(payload.get("nextPageToken") or "").strip()
        if not raw_token:
            break
        next_page_token = raw_token
    return entries


def _gemini_models_page(
    *,
    api_key: str,
    timeout: float,
    page_token: str | None,
) -> dict:
    """Solicita una página del catálogo Gemini por REST."""
    params: dict[str, str | int] = {
        "key": api_key,
        "pageSize": 100,
    }
    if page_token:
        params["pageToken"] = page_token

    response = requests.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        params=params,
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, dict) else {}


def _gemini_model_name_for_kind(item: object, *, kind: ModelKind) -> str | None:
    """Filtra un item de Gemini según tipo y devuelve nombre corto."""
    if not isinstance(item, dict):
        return None

    raw_name = str(item.get("name") or "").strip()
    if not raw_name:
        return None

    methods = _gemini_supported_methods(item)
    if kind == "embedding":
        if "embedcontent" not in methods and "batchembedcontents" not in methods:
            return None
    elif "generatecontent" not in methods:
        return None

    model_name = raw_name.split("/")[-1].strip()
    return model_name or None


def _gemini_supported_methods(item: dict) -> set[str]:
    """Normaliza métodos soportados desde SDK o REST a minúsculas."""
    methods = (
        item.get("supportedGenerationMethods")
        or item.get("supported_generation_methods")
        or []
    )
    return {str(method).strip().lower() for method in methods if str(method).strip()}


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
