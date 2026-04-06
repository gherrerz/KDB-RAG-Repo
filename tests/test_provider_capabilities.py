"""Pruebas de capacidades por provider para embeddings y LLM."""

from src.coderag.core.settings import Settings


def test_embedding_capabilities_vertex_requires_project() -> None:
    """Vertex embeddings requiere token y project id para quedar configurado."""
    settings = Settings(
        VERTEX_AI_API_KEY="token",
        VERTEX_AI_PROJECT_ID="",
    )
    info = settings.embedding_provider_capabilities("vertex_ai")
    assert info["supported"] is True
    assert info["configured"] is False
    assert info["reason"] == "missing_vertex_ai_api_key_or_project"


def test_llm_capabilities_openai_missing_key() -> None:
    """OpenAI reporta no configurado cuando falta API key."""
    settings = Settings(OPENAI_API_KEY="")
    info = settings.llm_provider_capabilities("openai")
    assert info["supported"] is True
    assert info["configured"] is False
    assert info["reason"] == "missing_openai_api_key"


def test_embedding_capabilities_anthropic_not_supported() -> None:
    """Anthropic en embeddings se marca explícitamente como no soportado."""
    settings = Settings(ANTHROPIC_API_KEY="a")
    info = settings.embedding_provider_capabilities("anthropic")
    assert info["supported"] is False
    assert info["configured"] is False
    assert info["reason"] == "provider_without_embedding_backend"
