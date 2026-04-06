"""Pruebas del adaptador tipado de capacidades para UI."""

from src.coderag.ui.provider_capabilities import (
    normalize_embedding_capability,
    normalize_llm_capability,
    readiness,
    resolve_embedding_capability,
    resolve_llm_capability,
)


class _SettingsWithoutCapabilities:
    """Settings de prueba sin métodos de capacidades para fallback seguro."""


class _SettingsWithCapabilities:
    """Settings de prueba con payloads parciales para normalización."""

    def embedding_provider_capabilities(self, provider: str) -> dict[str, object]:
        return {"provider": provider, "supported": False, "configured": False}

    def llm_provider_capabilities(self, provider: str) -> dict[str, object]:
        return {"provider": provider, "supported": True, "configured": False}


def test_normalize_embedding_capability_defaults_reason() -> None:
    """Normaliza reason por defecto cuando no viene informado."""
    capability = normalize_embedding_capability(
        "gemini",
        {"provider": "gemini", "supported": True, "configured": False},
    )

    assert capability["provider"] == "gemini"
    assert capability["supported"] is True
    assert capability["configured"] is False
    assert capability["reason"] == "not_configured"


def test_normalize_llm_capability_defaults_answer_verify() -> None:
    """Completa answer/verify como True si no se informan."""
    capability = normalize_llm_capability(
        "openai",
        {"provider": "openai", "supported": True, "configured": True},
    )

    assert capability["answer"] is True
    assert capability["verify"] is True
    assert capability["reason"] == "ok"


def test_resolve_capabilities_without_methods_returns_ready_defaults() -> None:
    """Si settings no expone métodos, conserva defaults seguros ready/ok."""
    settings = _SettingsWithoutCapabilities()

    embedding = resolve_embedding_capability(settings, "openai")
    llm = resolve_llm_capability(settings, "openai")

    assert embedding["supported"] is True
    assert embedding["configured"] is True
    assert llm["supported"] is True
    assert llm["configured"] is True


def test_resolve_capabilities_with_partial_payloads() -> None:
    """Normaliza payload parcial recibido desde settings custom de pruebas."""
    settings = _SettingsWithCapabilities()

    embedding = resolve_embedding_capability(settings, "anthropic")
    llm = resolve_llm_capability(settings, "anthropic")

    assert embedding["reason"] == "not_configured"
    assert llm["reason"] == "not_configured"


def test_readiness_uses_supported_and_configured() -> None:
    """Readiness depende de supported y configured, devolviendo reason."""
    embedding = normalize_embedding_capability(
        "anthropic",
        {
            "provider": "anthropic",
            "supported": False,
            "configured": False,
            "reason": "provider_without_embedding_backend",
        },
    )

    ready, reason = readiness(embedding)

    assert ready is False
    assert reason == "provider_without_embedding_backend"
