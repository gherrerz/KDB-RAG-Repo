"""Pruebas unitarias para resolución compartida de estado UI por provider."""

from coderag.ui.provider_ui_state import (
    resolve_embedding_ui_state,
    resolve_llm_ui_state,
)


class _Settings:
    """Settings de prueba con capacidades configurables por provider."""

    def embedding_provider_capabilities(self, provider: str) -> dict[str, str | bool]:
        if provider == "openai":
            return {
                "provider": provider,
                "supported": False,
                "configured": False,
                "reason": "provider_without_embedding_backend",
            }
        if provider == "vertex":
            return {
                "provider": provider,
                "supported": True,
                "configured": False,
                "reason": "missing_vertex_ai_api_key_or_project",
            }
        return {
            "provider": provider,
            "supported": True,
            "configured": True,
            "reason": "ok",
        }

    def llm_provider_capabilities(self, provider: str) -> dict[str, str | bool]:
        if provider == "vertex":
            return {
                "provider": provider,
                "supported": True,
                "configured": False,
                "answer": True,
                "verify": True,
                "reason": "missing_vertex_ai_api_key_or_project",
            }
        return {
            "provider": provider,
            "supported": True,
            "configured": True,
            "answer": True,
            "verify": True,
            "reason": "ok",
        }


def test_resolve_embedding_ui_state_ready_query() -> None:
    """Resuelve defaults y readiness correcto para embeddings en consulta."""
    state = resolve_embedding_ui_state(_Settings(), "gemini", context="query")

    assert state.default_model == "text-embedding-004"
    assert state.warning == ""
    assert state.chip_state == "ready"
    assert state.ready is True
    assert state.reason == "ok"


def test_resolve_embedding_ui_state_unsupported_ingestion() -> None:
    """Marca warning con fallback cuando embeddings no tiene backend."""
    state = resolve_embedding_ui_state(_Settings(), "openai", context="ingestion")

    assert state.default_model == "text-embedding-3-small"
    assert "fallback" in state.warning.lower()
    assert state.chip_state == "warning"
    assert state.ready is False
    assert state.reason == "provider_without_embedding_backend"


def test_resolve_llm_ui_state_not_configured() -> None:
    """Resuelve bloqueo cuando LLM está soportado pero no configurado."""
    state = resolve_llm_ui_state(_Settings(), "vertex_ai")

    assert state.default_model == "gemini-2.0-flash"
    assert "no configurado" in state.warning.lower()
    assert state.chip_state == "blocked"
    assert state.ready is False
    assert state.reason == "missing_vertex_ai_api_key_or_project"
