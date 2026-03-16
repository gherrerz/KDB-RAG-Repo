"""Pruebas unitarias para descubrimiento de modelos por provider."""

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
