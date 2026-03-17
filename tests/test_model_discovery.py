"""Pruebas unitarias para descubrimiento de modelos por provider."""

from __future__ import annotations

from coderag.llm import model_discovery


class _Settings:
    """Configuración mínima para pruebas de discovery."""

    discovery_cache_ttl_seconds = 3600
    discovery_timeout_seconds = 1.0
    discovery_max_results = 50
    discovery_gemini_sdk_enabled = True
    openai_api_key = ""
    gemini_api_key = ""
    vertex_ai_api_key = ""
    vertex_ai_project_id = ""
    vertex_ai_location = "us-central1"


class _GeminiSettings(_Settings):
    """Configuración de pruebas para escenario Gemini."""

    gemini_api_key = "test-gemini-key"


def test_openai_missing_key_uses_fallback(monkeypatch) -> None:
    """Sin API key OpenAI, discovery cae a catálogo local."""
    monkeypatch.setattr(model_discovery, "get_settings", lambda: _Settings())

    result = model_discovery.discover_models("openai", "embedding", force_refresh=True)

    assert result.source == "fallback"
    assert "text-embedding-3-small" in result.models
    assert result.warning == "missing_openai_api_key"


def test_invalid_kind_returns_error_shape(monkeypatch) -> None:
    """Tipos inválidos devuelven error controlado sin excepción."""
    monkeypatch.setattr(model_discovery, "get_settings", lambda: _Settings())

    result = model_discovery.discover_models("openai", "unknown", force_refresh=True)

    assert result.source == "error"
    assert result.models == []
    assert result.warning == "model_kind_invalid"


def test_cache_returns_cached_source(monkeypatch) -> None:
    """La segunda lectura sin force_refresh usa caché."""
    calls = {"count": 0}

    def fake_discover_uncached(provider: str, kind: str):
        calls["count"] += 1
        return model_discovery.ModelDiscoveryResult(
            provider=provider,
            kind=kind,
            models=["gpt-4.1-mini"],
            source="remote",
            warning=None,
        )

    monkeypatch.setattr(model_discovery, "get_settings", lambda: _Settings())
    monkeypatch.setattr(model_discovery, "_discover_uncached", fake_discover_uncached)

    first = model_discovery.discover_models("openai", "llm", force_refresh=False)
    second = model_discovery.discover_models("openai", "llm", force_refresh=False)

    assert first.source == "remote"
    assert second.source == "cache"
    assert calls["count"] == 1


def test_vertex_alias_uses_vertex_ai_fallback(monkeypatch) -> None:
    """Alias `vertex` se normaliza y usa fallback canónico de Vertex AI."""
    monkeypatch.setattr(model_discovery, "get_settings", lambda: _Settings())

    result = model_discovery.discover_models("vertex", "embedding", force_refresh=True)

    assert result.provider == "vertex_ai"
    assert result.source == "fallback"
    assert "text-embedding-005" in result.models
    assert result.warning == "missing_vertex_ai_api_key_or_project"


def test_gemini_uses_rest_catalog_when_available(monkeypatch) -> None:
    """Gemini debe priorizar catálogo REST cuando devuelve resultados válidos."""
    settings = _GeminiSettings()
    settings.discovery_gemini_sdk_enabled = False

    monkeypatch.setattr(model_discovery, "get_settings", lambda: settings)
    monkeypatch.setattr(
        model_discovery,
        "_discover_gemini_rest_names",
        lambda **_: ["gemini-2.5-pro", "gemini-2.0-flash"],
    )

    result = model_discovery.discover_models("gemini", "llm", force_refresh=True)

    assert result.source == "remote"
    assert result.warning is None
    assert "gemini-2.5-pro" in result.models


def test_gemini_rest_failed_without_sdk_uses_fallback(monkeypatch) -> None:
    """Si REST falla y SDK está deshabilitado, retorna fallback local controlado."""
    settings = _GeminiSettings()
    settings.discovery_gemini_sdk_enabled = False

    monkeypatch.setattr(model_discovery, "get_settings", lambda: settings)

    def _raise_rest_error(**_kwargs):
        raise RuntimeError("rest unavailable")

    monkeypatch.setattr(
        model_discovery,
        "_discover_gemini_rest_names",
        _raise_rest_error,
    )

    result = model_discovery.discover_models("gemini", "llm", force_refresh=True)

    assert result.source == "fallback"
    assert result.warning == "gemini_rest_catalog_failed"
    assert "gemini" in result.provider


def test_gemini_rest_empty_without_sdk_uses_fallback(monkeypatch) -> None:
    """Si REST no trae modelos y SDK está deshabilitado, cae a fallback local."""
    settings = _GeminiSettings()
    settings.discovery_gemini_sdk_enabled = False

    monkeypatch.setattr(model_discovery, "get_settings", lambda: settings)
    monkeypatch.setattr(
        model_discovery,
        "_discover_gemini_rest_names",
        lambda **_: [],
    )

    result = model_discovery.discover_models("gemini", "embedding", force_refresh=True)

    assert result.source == "fallback"
    assert result.warning == "gemini_rest_catalog_empty"
    assert "text-embedding-004" in result.models


def test_gemini_rest_page_parser_handles_pagination(monkeypatch) -> None:
    """El parser REST de Gemini debe recorrer páginas y filtrar por método."""

    class _FakeResponse:
        """Respuesta HTTP simulada para requests.get."""

        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._payload

    pages = {
        "": {
            "models": [
                {
                    "name": "models/gemini-2.0-flash",
                    "supportedGenerationMethods": ["generateContent"],
                }
            ],
            "nextPageToken": "next-1",
        },
        "next-1": {
            "models": [
                {
                    "name": "models/text-embedding-004",
                    "supportedGenerationMethods": ["embedContent"],
                }
            ],
            "nextPageToken": "",
        },
    }

    def _fake_get(_url, *, params, timeout):
        assert timeout == 2.0
        assert params.get("key") == "test-key"
        token = str(params.get("pageToken") or "")
        page = pages[token]
        return _FakeResponse(page)

    monkeypatch.setattr(model_discovery.requests, "get", _fake_get)

    llm_names = model_discovery._discover_gemini_rest_names(
        kind="llm",
        api_key="test-key",
        timeout=2.0,
    )
    embedding_names = model_discovery._discover_gemini_rest_names(
        kind="embedding",
        api_key="test-key",
        timeout=2.0,
    )

    assert "gemini-2.0-flash" in llm_names
    assert "text-embedding-004" in embedding_names
